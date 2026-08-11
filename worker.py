"""
独立 Worker 进程 —— 轮询 task_queue 表，执行后台长任务。

支持的任务类型：
  - sentiment   : 舆情分析（调用 process_analysis_task）
  - competitor  : 竞品监控（调用 process_competitor_task）
  - fb_scrape   : FB/SPD 舆情看板抓取（调用 tasks.scrape_fb_comments）
  - thai_scrape : 泰国专题抓取（调用 tasks.run_thai_scrape_job）
  - topic_monitor_run: 全平台（FB/IG/TikTok）话题监控发现+抓取（调用 tasks.run_topic_monitor_job）
  - etl_hashtag : Excel 工具 Hashtag 发现导出（etl_jobs.run_etl_hashtag_task）
  - etl_comments: Excel 工具链接批量抓评论导出（etl_jobs.run_etl_comments_task）
  - etl_video_metrics: Excel 工具视频链接批量拉指标写回（etl_jobs.run_etl_video_metrics_task）
  - profile_video_sync: 主页视频数据定时同步到飞书多维表格
  - tiktok_official_refresh: TikTok 官号监控官方 API 刷新

启动方式：
  python worker.py

Render 部署时作为 Background Worker service 运行，
与 Web service 共享同一个 PostgreSQL 数据库。
"""

import os
import sys
import time
import json
import base64
import logging
import signal
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from dotenv import load_dotenv
load_dotenv()

# ============================================
# 日志配置
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [worker] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ============================================
# 环境变量
# ============================================
POLL_INTERVAL = int(os.environ.get('WORKER_POLL_INTERVAL', '3'))  # 秒
WORKER_MAX_IDLE = int(os.environ.get('WORKER_MAX_IDLE', '0'))     # 0 = 永不退出
# 定时/自检批量任务和用户即时触发任务分开排队、预留独立并发槽位，
# 避免一批定时任务（比如86个矩阵账号的视频发现自检）把并发槽位占满，
# 导致用户点"立即执行"的即时任务要排队等待。
WORKER_CONCURRENCY_INTERACTIVE = max(1, int(os.environ.get('WORKER_CONCURRENCY_INTERACTIVE', '6')))
WORKER_CONCURRENCY_SCHEDULED = max(1, int(os.environ.get('WORKER_CONCURRENCY_SCHEDULED', '2')))
WORKER_CONCURRENCY = WORKER_CONCURRENCY_INTERACTIVE + WORKER_CONCURRENCY_SCHEDULED
WORKER_ID = os.environ.get('WORKER_ID') or f"worker-{os.getpid()}"

# ============================================
# 导入业务模块
# ============================================
import database as db
import tasks
import usage_service

# 标记为 worker 进程，避免 app 模块启动 APScheduler
os.environ['_IS_WORKER'] = 'true'

# 延迟导入 app 模块（它会初始化 Flask、AI 客户端等）
# 仅导入所需函数，不启动 Flask 服务器
logger.info("🔧 Worker 正在加载 app 模块...")
from app import (
    process_analysis_task,
    process_competitor_task,
    update_task,
    run_scheduled_tiktok_official_daily_sync,
    run_scheduled_tiktok_official_publish_window_capture,
    run_scheduled_tiktok_official_video_discovery,
    run_scheduled_mail_blaster_reply_poll,
    run_scheduled_tiktok_official_ad_spend_sync,
)
logger.info("✅ app 模块加载完成")

# ============================================
# TikTok 官号矩阵每日同步：自检触发
# 原来挂在 web 服务的 APScheduler cron（3:30am）上，但 web 服务是 Render
# 免费套餐，空闲 15 分钟会休眠，经常错过这个时间点且没有任何报错日志。
# worker 进程是常驻的 starter 套餐，改为在主循环里自检：北京时间进入
# 3:30 之后，查一下今天（UTC 日期，跟 enqueue_daily_sync 的 session_id
# 用同一个日期基准）是否已经有过批次，没有就补跑一次。即使这次检查被
# 跳过或 worker 短暂重启，下一次轮询几秒后还会再检查，不会像 cron 那样
# 错过窗口就要等到第二天。
# ============================================
_last_daily_sync_check_key = None


# ============================================
# 自检触发的两道守卫
#
# 原来的去重键只存在内存里（_last_*_check_key），worker 每次重启都会归零，
# 下一轮立刻把整批任务再造一遍。88 个官号 × 每次全量入队，一天几次部署就能
# 堆出几百个重复任务，把队列堵死、把交互式任务饿死。
# 这里改成查库判断，重启不再重复；再加一道积压熔断，上一批没跑完就不再加码。
# ============================================

_BACKLOG_LIMIT = 60

# 任务卡死检测。线程池里的任务如果阻塞在系统调用上（比如 DNS 解析——requests 的
# timeout 不覆盖它），future 永远不会完成，那个并发槽位就永久丢失，而且日志上
# 一声不响，表现只是"任务一直 processing"。
# Python 杀不掉阻塞在系统调用里的线程，所以这里不做"回收"，只做**告警**：
# 超过阈值就每轮打一条 ERROR，让它在日志里显形，而不是静默烂掉。
_TASK_STUCK_SECONDS = int(os.environ.get('WORKER_TASK_STUCK_SECONDS', '1800'))
_stuck_warned: set = set()


def _check_stuck_futures(future_meta):
    """对超时未完成的任务打告警。future_meta: {future: (task_id, func_type, 提交时刻)}"""
    now = time.time()
    for future, (task_id, func_type, started) in list(future_meta.items()):
        if future.done():
            continue
        elapsed = now - started
        if elapsed < _TASK_STUCK_SECONDS:
            continue
        # 每个任务每 10 分钟最多提醒一次，别把日志刷爆
        bucket = int(elapsed // 600)
        key = (task_id, bucket)
        if key in _stuck_warned:
            continue
        _stuck_warned.add(key)
        logger.error(
            f"🚨 任务疑似卡死：task_id={task_id} type={func_type} 已运行 {int(elapsed / 60)} 分钟"
            f"（阈值 {_TASK_STUCK_SECONDS // 60} 分钟）。线程无法强制中断，该并发槽位已丢失，"
            f"需要重启 worker 回收。常见原因：外部 API 阻塞在 DNS 解析或 TLS 握手上。"
        )


def _already_enqueued_since(function_type: str, since) -> bool:
    """本轮时间桶内是否已经入过队。查库，所以重启也不会重复。"""
    try:
        row = db.query_one(
            "SELECT 1 FROM task_queue WHERE function_type = %s AND created_at >= %s LIMIT 1",
            (function_type, since),
        )
        return row is not None
    except Exception as e:
        logger.warning(f"⚠️ 检查 {function_type} 是否已入队失败，保守跳过本轮: {e}")
        return True     # 查不了就别造任务，宁可少跑一轮也别堆积压


def _backlog_too_deep(function_type: str) -> bool:
    """上一批还堵着就别再加码了。"""
    try:
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM task_queue "
            "WHERE function_type = %s AND status IN ('pending', 'claimed')",
            (function_type,),
        )
        n = (row or {}).get("n", 0)
        if n >= _BACKLOG_LIMIT:
            logger.warning(f"⏸️ {function_type} 还有 {n} 个待处理，本轮跳过入队")
            return True
        return False
    except Exception:
        return False


def _maybe_trigger_tiktok_daily_sync():
    global _last_daily_sync_check_key
    now_bj = datetime.utcnow() + timedelta(hours=8)
    if now_bj.hour != 3 or now_bj.minute < 30:
        _last_daily_sync_check_key = None
        return
    # 3:30-3:59 这个窗口内每分钟最多查一次库，避免每 3 秒轮询都打一次 DB
    check_key = now_bj.strftime('%Y-%m-%d %H:%M')
    if check_key == _last_daily_sync_check_key:
        return
    _last_daily_sync_check_key = check_key
    session_id = f"tiktok_official_daily_sync_{now_bj.date().isoformat()}"
    try:
        row = db.query_one(
            "SELECT 1 FROM task_queue WHERE function_type = 'tiktok_official_refresh' "
            "AND task_params::json->>'session_id' = %s LIMIT 1",
            (session_id,),
        )
        if row:
            return
    except Exception as e:
        logger.warning(f"⚠️ 检查 TikTok 官号每日同步是否已触发失败: {e}")
        return
    logger.info(f"⏰ Worker 触发 TikTok 官号矩阵每日同步: session_id={session_id}")
    try:
        run_scheduled_tiktok_official_daily_sync()
    except Exception as e:
        logger.error(f"❌ Worker 触发 TikTok 官号矩阵每日同步失败: {e}")

# ============================================
# TikTok 投流消耗每日同步：自检触发
# 紧跟在官号矩阵每日同步（3:30）之后，让视频/主页数据先同步完。
# 拉的是"昨天"一天的消耗/转化数据，广告平台数据本身有结算延迟，
# 不追求当天数据当天准确。
# ============================================
_last_ad_spend_sync_check_key = None


def _maybe_trigger_tiktok_ad_spend_sync():
    global _last_ad_spend_sync_check_key
    now_bj = datetime.utcnow() + timedelta(hours=8)
    if now_bj.hour != 4 or now_bj.minute < 30:
        _last_ad_spend_sync_check_key = None
        return
    check_key = now_bj.strftime('%Y-%m-%d %H:%M')
    if check_key == _last_ad_spend_sync_check_key:
        return
    _last_ad_spend_sync_check_key = check_key
    session_id = f"tiktok_ad_spend_sync_{now_bj.date().isoformat()}"
    try:
        row = db.query_one(
            "SELECT 1 FROM task_queue WHERE function_type = 'tiktok_official_ad_spend_sync' "
            "AND task_params::json->>'session_id' = %s LIMIT 1",
            (session_id,),
        )
        if row:
            return
    except Exception as e:
        logger.warning(f"⚠️ 检查 TikTok 投流消耗每日同步是否已触发失败: {e}")
        return
    logger.info(f"⏰ Worker 触发 TikTok 投流消耗每日同步: session_id={session_id}")
    try:
        run_scheduled_tiktok_official_ad_spend_sync()
    except Exception as e:
        logger.error(f"❌ Worker 触发 TikTok 投流消耗每日同步失败: {e}")

# ============================================
# TikTok 发布后3/24/48/72小时时间点快照：自检触发
# 跟每日同步不同，这个不是"一天一个固定窗口"，而是每隔几分钟查一次
# 有没有到期未采集的占位行（tiktok_official_video_publish_window_snapshots
# 的 due_at <= NOW() AND captured_at IS NULL），幂等：某一行只要还没
# captured_at 就会一直在下次检查里出现，不怕漏检或 worker 重启。
# ============================================
_last_publish_window_check_key = None
_PUBLISH_WINDOW_CHECK_INTERVAL_MINUTES = 5


def _maybe_trigger_publish_window_capture():
    global _last_publish_window_check_key
    now = datetime.utcnow()
    bucket = now.replace(
        minute=(now.minute // _PUBLISH_WINDOW_CHECK_INTERVAL_MINUTES) * _PUBLISH_WINDOW_CHECK_INTERVAL_MINUTES,
        second=0,
        microsecond=0,
    )
    check_key = bucket.strftime('%Y-%m-%d %H:%M')
    if check_key == _last_publish_window_check_key:
        return
    _last_publish_window_check_key = check_key
    if _already_enqueued_since('tiktok_official_publish_window_capture', bucket):
        return
    if _backlog_too_deep('tiktok_official_publish_window_capture'):
        return
    try:
        row = db.query_one(
            "SELECT 1 FROM tiktok_official_video_publish_window_snapshots "
            "WHERE due_at <= NOW() AND captured_at IS NULL LIMIT 1"
        )
        if not row:
            return
    except Exception as e:
        logger.warning(f"⚠️ 检查 TikTok 发布后时间点数据是否有待采集失败: {e}")
        return
    logger.info("⏰ Worker 触发 TikTok 发布后时间点数据采集")
    try:
        run_scheduled_tiktok_official_publish_window_capture()
    except Exception as e:
        logger.error(f"❌ Worker 触发 TikTok 发布后时间点数据采集失败: {e}")

# ============================================
# TikTok 新视频发现：自检触发
# 每日全量同步一天只跑一次，新视频从发布到被发现最多要等近24小时——
# 3h 窗口的 due_at 早在发现前就已过期，5分钟的采集轮询一上来就会把
# "发布后6-9小时"的状态误标成"3h"数据。这里加一个更高频、更轻量的
# 发现轮询（只拉第1页视频列表，不刷新主页数据），把发现延迟压到30分钟内。
# ============================================
_last_video_discovery_check_key = None
_VIDEO_DISCOVERY_CHECK_INTERVAL_MINUTES = 30


def _maybe_trigger_video_discovery():
    global _last_video_discovery_check_key
    now = datetime.utcnow()
    bucket = now.replace(
        minute=(now.minute // _VIDEO_DISCOVERY_CHECK_INTERVAL_MINUTES) * _VIDEO_DISCOVERY_CHECK_INTERVAL_MINUTES,
        second=0,
        microsecond=0,
    )
    check_key = bucket.strftime('%Y-%m-%d %H:%M')
    if check_key == _last_video_discovery_check_key:
        return
    _last_video_discovery_check_key = check_key
    # 内存那道只挡住同一进程内的重复；跨重启要靠查库
    if _already_enqueued_since('tiktok_official_video_discovery', bucket):
        return
    if _backlog_too_deep('tiktok_official_video_discovery'):
        return
    logger.info("⏰ Worker 触发 TikTok 新视频发现")
    try:
        run_scheduled_tiktok_official_video_discovery()
    except Exception as e:
        logger.error(f"❌ Worker 触发 TikTok 新视频发现失败: {e}")


# KOL 建联收回信。放 worker 不放 APScheduler：web 会休眠，定时点会静默错过。
_last_reply_poll_check_key = None
_REPLY_POLL_INTERVAL_MINUTES = int(os.environ.get('MAIL_BLASTER_REPLY_POLL_MINUTES') or 15)


def _maybe_trigger_reply_poll():
    global _last_reply_poll_check_key
    interval = max(1, _REPLY_POLL_INTERVAL_MINUTES)
    now = datetime.utcnow()
    bucket = now.replace(minute=(now.minute // interval) * interval,
                         second=0, microsecond=0)
    check_key = bucket.strftime('%Y-%m-%d %H:%M')
    if check_key == _last_reply_poll_check_key:
        return
    _last_reply_poll_check_key = check_key
    # 内存那道只挡同一进程内的重复；跨重启要靠查库，否则一重启就重新入队整批
    if _already_enqueued_since('mail_blaster_poll_replies', bucket):
        return
    if _backlog_too_deep('mail_blaster_poll_replies'):
        return
    try:
        run_scheduled_mail_blaster_reply_poll()
        logger.info("⏰ Worker 触发 KOL 建联收回信")
    except Exception as e:
        logger.error(f"❌ Worker 触发收回信失败: {e}")


# ============================================
# 优雅退出
# ============================================
_shutdown = False
_running_task_ids = set()  # 当前正在处理的 task_id，用于 SIGTERM 时回写状态
_running_task_types = {}

COSTLY_APIFY_TASK_TYPES = {
    'feishu_profile_video_sync',
    'profile_video_sync',
    'etl_video_metrics',
    'etl_hashtag',
    'etl_comments',
    'fb_scrape',
    'thai_scrape',
    'topic_monitor_run',
    'competitor',
    'sentiment',
}

def _signal_handler(signum, frame):
    global _shutdown
    logger.info(f"📛 收到信号 {signum}，准备优雅退出...")
    _shutdown = True
    # Render 重新部署/暂停时会发 SIGTERM。高成本 Apify 任务不能自动重试，
    # 否则已经启动的 actor runs 可能继续计费，新 Worker 又会再开一批。
    for cur in list(_running_task_ids):
        try:
            func_type = _running_task_types.get(cur)
            if func_type in COSTLY_APIFY_TASK_TYPES:
                update_task(
                    cur,
                    status='failed',
                    error='Worker 停止，任务已中断且不会自动重试',
                    progress='Worker 停止，任务已中断',
                )
                logger.warning(f"⚠️ 高成本任务 {cur} type={func_type} 已标记失败，不自动重试")
            else:
                update_task(
                    cur,
                    status='pending',
                    error='',
                    progress='服务重启，任务已重新排队',
                )
                logger.warning(f"⚠️ 当前任务 {cur} 已回退为 pending（等待新 Worker 重试）")
            db.execute("UPDATE task_queue SET worker_id = NULL WHERE task_id = %s", (cur,))
        except Exception as e:
            logger.error(f"❌ SIGTERM 时回写任务状态失败: {e}")

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ============================================
# 任务拾取与分发
# ============================================

def claim_task(lane):
    """从 task_queue 中拾取一条指定 lane 的 pending 任务（先进先出）。

    lane='interactive'/'scheduled' 各自独立领取，配合 main() 里分开的并发槽位，
    实现定时批量任务和用户即时任务互不抢占。
    """
    try:
        row = db.execute_and_fetch_one("""
            UPDATE task_queue
            SET status = 'claimed',
                worker_id = %s,
                attempts = COALESCE(attempts, 0) + 1,
                started_at = COALESCE(started_at, NOW()),
                finished_at = NULL,
                updated_at = NOW()
            WHERE task_id = (
                SELECT task_id FROM task_queue
                WHERE status = 'pending'
                  AND task_params IS NOT NULL
                  AND lane = %s
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING task_id, function_type, task_params, user_id, session_id
        """, (WORKER_ID, lane))
        return row
    except Exception as e:
        logger.error(f"❌ 拾取任务失败: {e}")
        return None


def _handle_mail_blaster_send(task_id, params):
    """mail-blaster 发信。

    放在 worker 而不是 web：web 是 Render free 套餐，空闲 15 分钟休眠会把
    后台线程连同进程一起杀掉（和当初 TikTok 每日同步踩的是同一个坑），
    而且 gunicorn 那边 workers=1/threads=1，一个跑 SMTP 循环的线程会把它堵死。
    """
    try:
        import mail_blaster_service as mb
    except Exception as e:
        logger.error(f"mail-blaster 模块不可用: {e}")
        update_task(task_id, status='failed', error=f'mail-blaster 模块不可用: {e}')
        return

    job_id = params.get('job_id')
    item_id = params.get('item_id')
    if not job_id:
        update_task(task_id, status='failed', error='缺少 job_id')
        return

    try:
        if item_id:
            # 单封重发
            outcome = mb.send_item(int(job_id), int(item_id))
            update_task(task_id, status='completed',
                        progress=f'重发完成：{outcome}',
                        result=json.dumps({'outcome': outcome}, ensure_ascii=False))
            return

        def _progress(text):
            update_task(task_id, status='processing', progress=text)

        summary = mb.run_job(int(job_id), progress=_progress)
        update_task(task_id, status='completed',
                    progress=f"共 {summary['total']} 封　成功 {summary['sent']}　失败 {summary['failed']}",
                    result=json.dumps(summary, ensure_ascii=False))
    except Exception as e:
        logger.error(f"mail-blaster 发送任务失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=f'发送失败: {str(e)[:500]}')


def _handle_mail_blaster_poll_replies(task_id, params):
    """KOL 建联收回信：拉 IMAP → 匹配 → AI 分类/抽报价。

    AI 走 app 的 call_gemini 注入进去——mail_inbox_service 不能 import app（会循环），
    这和 sentiment_insight 的既有写法一致。
    """
    try:
        import mail_inbox_service as inbox
    except Exception as e:
        logger.error(f"mail-inbox 模块不可用: {e}")
        update_task(task_id, status='failed', error=f'mail-inbox 模块不可用: {e}')
        return

    def _progress(text):
        update_task(task_id, status='processing', progress=text)

    def _ai(prompt, timeout=90):
        from app import call_gemini
        return call_gemini(prompt, timeout=timeout,
                           model=os.environ.get('MAIL_BLASTER_AI_MODEL') or inbox.AI_MODEL,
                           temperature=0)

    try:
        fetched = inbox.fetch_all(progress=_progress)
        analyzed = inbox.analyze_pending(ai_call=_ai, progress=_progress)
        summary = {**fetched, 'analyzed': analyzed}
        note = f"收到 {fetched['new']} 封　已分析 {analyzed['done']}"
        if fetched['errors']:
            note += f"　{len(fetched['errors'])} 个账号收信出错"
        update_task(task_id, status='completed', progress=note,
                    result=json.dumps(summary, ensure_ascii=False))
    except Exception as e:
        logger.error(f"mail-blaster 收回信任务失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=f'收回信失败: {str(e)[:500]}')


def _handle_mail_blaster_ocr(task_id, params):
    try:
        import mail_blaster_service as mb
    except Exception as e:
        logger.error(f"mail-blaster 模块不可用: {e}")
        update_task(task_id, status='failed', error=f'mail-blaster 模块不可用: {e}')
        return

    job_id = params.get('job_id')
    if not job_id:
        update_task(task_id, status='failed', error='缺少 job_id')
        return
    try:
        summary = mb.run_ocr_for_job(int(job_id),
                                     progress=lambda t: update_task(task_id, status='processing',
                                                                    progress=t))
        update_task(task_id, status='completed',
                    progress=f"识别 {summary['processed']} 行，补上 {summary['filled']} 行",
                    result=json.dumps(summary, ensure_ascii=False))
    except Exception as e:
        logger.error(f"mail-blaster OCR 失败: {e}")
        import traceback
        traceback.print_exc()
        try:
            db.execute("UPDATE mb_jobs SET ocr_status = 'failed' WHERE id = %s", (int(job_id),))
        except Exception:
            pass
        update_task(task_id, status='failed', error=f'识别失败: {str(e)[:500]}')


def dispatch_task(task_row):
    """根据 function_type 分发到对应的处理函数。"""
    task_id = task_row['task_id']
    func_type = task_row['function_type']
    raw_params = task_row['task_params']

    try:
        params = json.loads(raw_params) if raw_params else {}
    except json.JSONDecodeError as e:
        logger.error(f"❌ 任务 {task_id} 的 task_params JSON 解析失败: {e}")
        update_task(task_id, status='failed', error=f'参数解析失败: {e}')
        return

    logger.info(f"🚀 开始处理任务 {task_id}  type={func_type} worker={WORKER_ID}")
    _running_task_ids.add(task_id)
    _running_task_types[task_id] = func_type
    update_task(task_id, status='processing', progress=f'Worker 执行中（{WORKER_ID}）')

    try:
        if func_type == 'sentiment':
            _handle_sentiment(task_id, params)
        elif func_type == 'competitor':
            _handle_competitor(task_id, params)
        elif func_type == 'fb_scrape':
            _handle_fb_scrape(task_id, params)
        elif func_type == 'thai_scrape':
            _handle_thai_scrape(task_id, params)
        elif func_type == 'topic_monitor_run':
            _handle_topic_monitor_run(task_id, params)
        elif func_type == 'etl_hashtag':
            _handle_etl_hashtag(task_id, params)
        elif func_type == 'etl_comments':
            _handle_etl_comments(task_id, params)
        elif func_type == 'etl_video_metrics':
            _handle_etl_video_metrics(task_id, params)
        elif func_type == 'profile_video_sync':
            _handle_profile_video_sync(task_id, params)
        elif func_type == 'feishu_profile_video_sync':
            _handle_feishu_profile_video_sync(task_id, params)
        elif func_type == 'tiktok_official_refresh':
            _handle_tiktok_official_refresh(task_id, params)
        elif func_type == 'tiktok_official_publish_window_capture':
            _handle_tiktok_official_publish_window_capture(task_id, params)
        elif func_type == 'tiktok_official_video_discovery':
            _handle_tiktok_official_video_discovery(task_id, params)
        elif func_type == 'tiktok_official_ad_spend_sync':
            _handle_tiktok_official_ad_spend_sync(task_id, params)
        elif func_type == 'mail_blaster_send':
            _handle_mail_blaster_send(task_id, params)
        elif func_type == 'mail_blaster_ocr':
            _handle_mail_blaster_ocr(task_id, params)
        elif func_type == 'mail_blaster_poll_replies':
            _handle_mail_blaster_poll_replies(task_id, params)
        else:
            logger.warning(f"⚠️ 未知任务类型: {func_type}，标记为失败")
            update_task(task_id, status='failed', error=f'未知任务类型: {func_type}')
    except Exception as e:
        logger.error(f"❌ 任务 {task_id} 执行异常: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=f'Worker 异常: {str(e)[:500]}')
    finally:
        _running_task_ids.discard(task_id)
        _running_task_types.pop(task_id, None)


# ============================================
# 各类型任务处理器
# ============================================

def _handle_sentiment(task_id, params):
    """舆情分析"""
    # 还原 file_data（如果有）
    file_data = None
    if params.get('file_data'):
        fd = params['file_data']
        file_data = {
            'filename': fd['filename'],
            'content': base64.b64decode(fd['content']),
            'content_type': fd['content_type'],
        }

    process_analysis_task(
        task_id=task_id,
        url=params.get('url'),
        urls=params.get('urls'),
        file_data=file_data,
        session_id=params.get('session_id', 'worker'),
        user_id=params.get('user_id'),
        username=params.get('username', 'unknown'),
        department=params.get('department', '未知'),
        project=params.get('project', 'CFL'),
        comments_per_post_limit=params.get('comments_per_post_limit'),
    )


def _handle_competitor(task_id, params):
    """竞品监控"""
    process_competitor_task(
        task_id=task_id,
        target_url=params.get('target_url'),
        urls=params.get('urls'),
        start_dt_str=params.get('start_dt_str'),
        end_dt_str=params.get('end_dt_str'),
        user_id=params.get('user_id'),
        username=params.get('username', 'unknown'),
        department=params.get('department', '未知'),
        session_id=params.get('session_id', 'worker'),
        project=params.get('project', 'CFL'),
        generate_report=params.get('generate_report', False),
        enable_video_vision=params.get('enable_video_vision', False),
    )


def _handle_fb_scrape(task_id, params):
    """FB 舆情看板抓取"""
    scrape_task_id = params.get('scrape_task_id')
    post_urls = params.get('post_urls')
    task_queries = params.get('task_queries') or []
    seed_tags = params.get('seed_tags')
    platforms = params.get('platforms')
    crawl_scope = params.get('crawl_scope', 'both')
    discover_max_posts = params.get('discover_max_posts', 300)
    days_back = params.get('days_back', 7)
    results_limit = params.get('results_limit', 2500)
    enable_ai_analysis = params.get('enable_ai_analysis', True)
    max_ai_comments = params.get('max_ai_comments', 1200)
    skip_discover = params.get('skip_discover', False)
    top_n = max(1, min(int(params.get('top_n', 5)), 20))
    min_comments_for_actor = params.get('min_comments_for_actor')

    def _set_scrape_summary(message):
        if not scrape_task_id:
            return
        try:
            db.execute(
                "UPDATE scrape_tasks SET status = 'running', result_summary = %s WHERE id = %s",
                (str(message)[:500], scrape_task_id)
            )
        except Exception as e:
            logger.warning(f"⚠️ 更新 scrape_tasks 进度失败: {e}")

    try:
        merged_posts = []

        if skip_discover and not post_urls:
            import datetime as _dt
            end_dt = _dt.datetime.now()
            start_dt = end_dt - _dt.timedelta(days=days_back)
            update_task(task_id, progress=f'从 DB 读取 Top{top_n} 帖子...')
            top_rows = db.query_all("""
                SELECT post_url, engagement, comments_count
                FROM fb_post_metrics
                WHERE post_date >= %s AND post_date <= %s
                  AND COALESCE(comments_count, 0) > 0
                ORDER BY comments_count DESC
                LIMIT %s
            """, (start_dt.strftime('%Y-%m-%d'), end_dt.strftime('%Y-%m-%d'), top_n))
            if not top_rows:
                raise RuntimeError(f'fb_post_metrics 中无 {start_dt.date()}~{end_dt.date()} 有评论的帖子')
            post_urls = [r['post_url'] for r in top_rows]
            _set_scrape_summary(f"skip_discover: Top{top_n} 帖子（评论数: {[r.get('comments_count',0) for r in top_rows]}）")
            logger.info(f"📊 skip_discover: Top{top_n} by comments_count: {[(r.get('post_url','')[-30:], r.get('comments_count')) for r in top_rows]}")

        elif (not post_urls) and task_queries:
            merged_urls = []
            seen = set()
            discover_stats = []
            for query in task_queries:
                task_name = query.get('task_name', 'unknown')
                update_task(task_id, progress=f'按标签发现帖子中（{task_name}）')
                discover_result = tasks.discover_posts_by_tags(
                    seed_tags=query.get('seed_tags') or [],
                    platforms=platforms,
                    days_back=days_back,
                    max_posts=discover_max_posts,
                    boolean_rule=query.get('boolean_rule')
                )
                if discover_result.get('status') != 'success':
                    raise RuntimeError(f"{task_name} discover failed: {discover_result.get('message') or 'unknown'}")
                task_urls = discover_result.get('post_urls') or []
                task_posts = discover_result.get('posts') or []
                discover_stats.append((task_name, len(task_urls)))
                _set_scrape_summary(f"发现帖子中：{task_name} {len(task_urls)} 条")
                for url in task_urls:
                    key = str(url).strip().lower()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    merged_urls.append(url)
                merged_posts.extend(task_posts)
            post_urls = merged_urls
            if not post_urls:
                raise RuntimeError(f'{crawl_scope} 分任务 discover 未找到可抓取帖子')
            stats_text = " | ".join([f"{name}:{count}" for name, count in discover_stats]) if discover_stats else "无"
            _set_scrape_summary(f"发现完成：{stats_text} | 合并去重后 {len(post_urls)} 条")
            logger.info(f"✅ Discover URLs (merged): {len(post_urls)}")
        elif (not post_urls) and seed_tags:
            update_task(task_id, progress='按标签发现帖子中')
            discover_result = tasks.discover_posts_by_tags(
                seed_tags=seed_tags,
                platforms=platforms,
                days_back=days_back,
                max_posts=discover_max_posts
            )
            if discover_result.get('status') != 'success':
                raise RuntimeError(discover_result.get('message') or 'discover failed')
            post_urls = discover_result.get('post_urls') or []
            merged_posts = discover_result.get('posts') or []
            if not post_urls:
                raise RuntimeError('discover 未找到可抓取帖子')
            _set_scrape_summary(f"发现完成：SPD {len(post_urls)} 条")
            logger.info(f"✅ Discover URLs: {len(post_urls)}")

        update_task(task_id, progress=f'抓取评论中（{len(post_urls or [])} 条帖子）')
        _set_scrape_summary(f"抓取评论中：共 {len(post_urls or [])} 条帖子")
        result = tasks.scrape_fb_comments(
            post_urls=post_urls,
            discovered_posts=merged_posts,
            days_back=days_back,
            task_id=scrape_task_id,
            results_limit=results_limit,
            enable_ai_analysis=enable_ai_analysis,
            max_ai_comments=max_ai_comments,
            allow_fallback_to_config=False,
            min_comments_for_actor=min_comments_for_actor,
        )
        if result.get('status') != 'success':
            raise RuntimeError(result.get('message') or 'fb scrape failed')
        if scrape_task_id:
            try:
                final_summary = (
                    f"完成 | 新增 {result.get('new_comments', 0)}，"
                    f"已存在 {result.get('existing_comments', 0)}，"
                    f"跳过不支持URL {result.get('skipped_unsupported_urls', 0)}，"
                    f"超时URL {result.get('timed_out_urls', 0)}，"
                    f"AI分析 {result.get('ai_processed_total', 0)}"
                )
                db.execute(
                    "UPDATE scrape_tasks SET result_summary = %s WHERE id = %s",
                    (final_summary[:500], scrape_task_id)
                )
            except Exception as e:
                logger.warning(f"⚠️ 写入完成摘要失败: {e}")
        logger.info(f"✅ FB 抓取完成: {result}")
        usage_service.record_usage_event(
            module='fb_scrape',
            user_id=params.get('user_id'),
            task_id=task_id,
            item_count=int(result.get('new_comments') or 0) + int(result.get('existing_comments') or 0),
            crawler_items=int(result.get('new_comments') or 0) + int(result.get('existing_comments') or 0),
            ai_tokens=0,
            source='actual',
            detail={
                'post_url_count': len(post_urls or []),
                'new_comments': result.get('new_comments', 0),
                'existing_comments': result.get('existing_comments', 0),
                'ai_processed_total': result.get('ai_processed_total', 0),
                'enable_ai_analysis': enable_ai_analysis,
                'results_limit': results_limit,
            },
        )
        update_task(task_id, status='completed', progress='抓取完成')
    except Exception as e:
        logger.error(f"❌ FB 抓取失败: {e}")
        update_task(task_id, status='failed', error=str(e)[:500])
        # 同步更新 scrape_tasks 表
        if scrape_task_id:
            try:
                db.execute(
                    "UPDATE scrape_tasks SET status = 'failed', completed_at = NOW(), error_message = %s WHERE id = %s",
                    (str(e)[:500], scrape_task_id)
                )
            except:
                pass


def _handle_thai_scrape(task_id, params):
    """泰国专题：数据集发现 + 评论抓取（与 Web 端 /api/thai_schedule 逻辑一致）。"""
    scrape_task_id = params.get('scrape_task_id')
    if not scrape_task_id:
        logger.error("❌ thai_scrape 缺少 scrape_task_id")
        update_task(task_id, status='failed', error='缺少 scrape_task_id')
        return
    try:
        update_task(task_id, progress='泰国专题：Worker 执行中...')
        result = tasks.run_thai_scrape_job(
            scrape_task_id=scrape_task_id,
            game_type=(params.get('game_type') or 'MLBB').strip().upper(),
            dataset_name=(params.get('dataset_name') or '').strip(),
            skip_discover=bool(params.get('skip_discover')),
            seed_tags=params.get('seed_tags') or [],
            platforms=params.get('platforms') or ['facebook', 'instagram'],
            days_back=int(params.get('days_back', 7)),
            results_limit=int(params.get('results_limit', 5000)),
            max_ai_comments=int(params.get('max_ai_comments', 5000)),
            discover_max_posts=int(params.get('discover_max_posts', 3000)),
            min_comments_for_actor=int(params.get('min_comments_for_actor', 0)),
            source_dataset_name=(params.get('source_dataset_name') or '').strip() or None,
            dataset_start=(params.get('dataset_start') or '').strip() or None,
            dataset_end=(params.get('dataset_end') or '').strip() or None,
            re_raise=True,
        ) or {}
        usage_service.record_usage_event(
            module='thai_scrape',
            user_id=params.get('user_id'),
            task_id=task_id,
            item_count=int(result.get('new_comments') or 0) + int(result.get('existing_comments') or 0),
            crawler_items=int(result.get('new_comments') or 0) + int(result.get('existing_comments') or 0),
            source='actual',
            detail={
                'scrape_task_id': scrape_task_id,
                'dataset_name': params.get('dataset_name'),
                'skip_discover': bool(params.get('skip_discover')),
                'ai_processed_total': result.get('ai_processed_total', 0),
                'results_limit': params.get('results_limit'),
            },
        )
        update_task(task_id, status='completed', progress='泰国抓取完成')
    except Exception as e:
        logger.error(f"❌ 泰国专题 Worker 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


def _handle_topic_monitor_run(task_id, params):
    """全平台话题监控：按话题配置发现帖子（FB/IG/TikTok）+ 抓评论 + AI 分析。"""
    topic_id = params.get('topic_id')
    scrape_task_id = params.get('scrape_task_id')
    if not topic_id or not scrape_task_id:
        logger.error("❌ topic_monitor_run 缺少 topic_id/scrape_task_id")
        update_task(task_id, status='failed', error='缺少 topic_id/scrape_task_id')
        return
    try:
        update_task(task_id, progress='话题监控：Worker 执行中...')
        result = tasks.run_topic_monitor_job(
            topic_id=int(topic_id),
            scrape_task_id=scrape_task_id,
            re_raise=True,
        ) or {}
        comments_result = result.get('comments') or {}
        usage_service.record_usage_event(
            module='topic_monitor_run',
            user_id=params.get('user_id'),
            task_id=task_id,
            item_count=int(comments_result.get('new_comments') or 0) + int(comments_result.get('existing_comments') or 0),
            crawler_items=int(comments_result.get('new_comments') or 0),
            source='actual',
            detail={
                'topic_id': topic_id,
                'scrape_task_id': scrape_task_id,
                'ai_processed_total': comments_result.get('ai_processed_total', 0),
            },
        )
        update_task(task_id, status='completed', progress='话题监控抓取完成')
    except Exception as e:
        logger.error(f"❌ 话题监控 Worker 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


def _handle_etl_hashtag(task_id, params):
    """Excel 工具：Hashtag 发现导出。"""
    import etl_jobs
    try:
        etl_jobs.run_etl_hashtag_task(task_id, params, update_task)
    except Exception as e:
        logger.error(f"❌ etl_hashtag 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


def _handle_etl_comments(task_id, params):
    """Excel 工具：链接批量抓评论导出。"""
    import etl_jobs
    try:
        etl_jobs.run_etl_comments_task(task_id, params, update_task)
    except Exception as e:
        logger.error(f"❌ etl_comments 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


def _handle_etl_video_metrics(task_id, params):
    """Excel 工具：视频链接批量拉指标写回。"""
    import etl_jobs
    try:
        etl_jobs.run_etl_video_metrics_task(task_id, params, update_task)
    except Exception as e:
        logger.error(f"❌ etl_video_metrics 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


def _handle_profile_video_sync(task_id, params):
    """主页视频数据：批量抓取并写入飞书多维表格。"""
    import profile_video_scheduler
    try:
        profile_video_scheduler.run_profile_video_sync_task(task_id, params, update_task)
    except Exception as e:
        logger.error(f"❌ profile_video_sync 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


def _handle_feishu_profile_video_sync(task_id, params):
    """飞书配置驱动：主页视频数据同步到最新表、快照表、日志表。"""
    import profile_video_scheduler
    try:
        profile_video_scheduler.run_feishu_profile_video_sync_task(task_id, params, update_task)
    except Exception as e:
        logger.error(f"❌ feishu_profile_video_sync 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


def _handle_tiktok_official_refresh(task_id, params):
    """TikTok 官号监控：官方 API 刷新。"""
    import tiktok_official_service
    try:
        tiktok_official_service.run_refresh_task(task_id, params, update_task)
    except Exception as e:
        logger.error(f"❌ tiktok_official_refresh 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


def _handle_tiktok_official_publish_window_capture(task_id, params):
    """TikTok 官号矩阵：发布后3/24/48/72小时时间点数据采集。"""
    import tiktok_official_service
    try:
        tiktok_official_service.run_publish_window_capture(task_id, params, update_task)
    except Exception as e:
        logger.error(f"❌ tiktok_official_publish_window_capture 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


def _handle_tiktok_official_video_discovery(task_id, params):
    """TikTok 官号矩阵：轻量新视频发现（每30分钟）。"""
    import tiktok_official_service
    try:
        tiktok_official_service.run_video_discovery_task(task_id, params, update_task)
    except Exception as e:
        logger.error(f"❌ tiktok_official_video_discovery 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


def _handle_tiktok_official_ad_spend_sync(task_id, params):
    """TikTok 投流消耗每日同步。"""
    import tiktok_official_service
    try:
        tiktok_official_service.run_ad_spend_sync_task(task_id, params, update_task)
    except Exception as e:
        logger.error(f"❌ tiktok_official_ad_spend_sync 失败: {e}")
        import traceback
        traceback.print_exc()
        update_task(task_id, status='failed', error=str(e)[:500])


# ============================================
# 主循环
# ============================================

def main():
    logger.info("=" * 60)
    logger.info("🏭 Worker 进程启动")
    logger.info(f"   轮询间隔: {POLL_INTERVAL}s")
    logger.info(f"   Worker ID: {WORKER_ID}")
    logger.info(f"   并发数: 即时 {WORKER_CONCURRENCY_INTERACTIVE} / 定时 {WORKER_CONCURRENCY_SCHEDULED}")
    logger.info("=" * 60)

    # 启动时只回退本 worker 上次残留的 claimed / processing，避免误抢其他实例正在跑的任务。
    try:
        reset_count = db.execute("""
            UPDATE task_queue
            SET status = 'pending',
                worker_id = NULL,
                error = NULL,
                progress = COALESCE(progress, '等待 Worker 重试'),
                updated_at = NOW()
            WHERE status IN ('claimed', 'processing')
              AND (worker_id = %s OR worker_id IS NULL OR updated_at < NOW() - INTERVAL '5 minutes')
        """, (WORKER_ID,))
        if reset_count:
            logger.info(f"♻️ 回退了 {reset_count} 个 claimed/processing 任务到 pending")
    except Exception as e:
        logger.warning(f"⚠️ 回退残留任务失败: {e}")

    # mail-blaster：复位上次被杀时卡在 sending 的信。
    # 刻意标成 failed 而不是 pending —— 那封信可能已经送达，
    # 自动重发会让收件人收到两封，宁可让人来判断。
    try:
        import mail_blaster_service as _mb
        stuck = _mb.reset_stuck_items()
        if stuck:
            logger.info(f"♻️ mail-blaster 复位了 {stuck} 封卡在发送中的信（投递结果未知，已标失败待人工确认）")
    except Exception as e:
        logger.warning(f"⚠️ mail-blaster 复位失败（不影响其他任务）: {e}")

    idle_count = 0
    futures_interactive = set()
    futures_scheduled = set()
    future_meta = {}          # future -> (task_id, function_type, 提交时刻)，用于卡死检测
    with ThreadPoolExecutor(max_workers=WORKER_CONCURRENCY, thread_name_prefix='task-worker') as executor:
        while not _shutdown:
            for futures in (futures_interactive, futures_scheduled):
                done = {future for future in futures if future.done()}
                for future in done:
                    futures.discard(future)
                    future_meta.pop(future, None)
                    try:
                        future.result()
                    except Exception as exc:
                        logger.error(f"❌ 并发任务 Future 异常: {exc}")

            try:
                _check_stuck_futures(future_meta)
            except Exception as exc:
                logger.warning(f"⚠️ 卡死检测异常: {exc}")

            try:
                _maybe_trigger_tiktok_daily_sync()
            except Exception as exc:
                logger.error(f"❌ TikTok 官号每日同步自检异常: {exc}")

            try:
                _maybe_trigger_tiktok_ad_spend_sync()
            except Exception as exc:
                logger.error(f"❌ TikTok 投流消耗每日同步自检异常: {exc}")

            try:
                _maybe_trigger_publish_window_capture()
            except Exception as exc:
                logger.error(f"❌ TikTok 发布后时间点数据采集自检异常: {exc}")

            try:
                _maybe_trigger_video_discovery()
            except Exception as exc:
                logger.error(f"❌ TikTok 新视频发现自检异常: {exc}")

            try:
                _maybe_trigger_reply_poll()
            except Exception as exc:
                logger.error(f"❌ KOL 建联收回信自检异常: {exc}")

            claimed_any = False
            while len(futures_interactive) < WORKER_CONCURRENCY_INTERACTIVE:
                task_row = claim_task('interactive')
                if not task_row:
                    break
                claimed_any = True
                fut = executor.submit(dispatch_task, task_row)
                future_meta[fut] = (task_row.get('task_id'), task_row.get('function_type'), time.time())
                futures_interactive.add(fut)
            while len(futures_scheduled) < WORKER_CONCURRENCY_SCHEDULED:
                task_row = claim_task('scheduled')
                if not task_row:
                    break
                claimed_any = True
                fut = executor.submit(dispatch_task, task_row)
                future_meta[fut] = (task_row.get('task_id'), task_row.get('function_type'), time.time())
                futures_scheduled.add(fut)

            all_futures = futures_interactive | futures_scheduled
            if claimed_any:
                idle_count = 0
            elif all_futures:
                wait(all_futures, timeout=POLL_INTERVAL, return_when=FIRST_COMPLETED)
            else:
                idle_count += 1
                if idle_count % 20 == 0:
                    logger.info(f"💤 空闲中... (已空转 {idle_count * POLL_INTERVAL}s)")
                time.sleep(POLL_INTERVAL)

        all_futures = futures_interactive | futures_scheduled
        if all_futures:
            logger.info(f"⏳ 正在等待 {len(all_futures)} 个运行中任务结束...")
            wait(all_futures, timeout=30)

    logger.info("👋 Worker 进程已退出")


if __name__ == '__main__':
    main()
