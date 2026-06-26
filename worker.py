"""
独立 Worker 进程 —— 轮询 task_queue 表，执行后台长任务。

支持的任务类型：
  - sentiment   : 舆情分析（调用 process_analysis_task）
  - competitor  : 竞品监控（调用 process_competitor_task）
  - fb_scrape   : FB/SPD 舆情看板抓取（调用 tasks.scrape_fb_comments）
  - thai_scrape : 泰国专题抓取（调用 tasks.run_thai_scrape_job）
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
WORKER_CONCURRENCY = max(1, int(os.environ.get('WORKER_CONCURRENCY', '2')))
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
)
logger.info("✅ app 模块加载完成")

# ============================================
# 优雅退出
# ============================================
_shutdown = False
_running_task_ids = set()  # 当前正在处理的 task_id，用于 SIGTERM 时回写状态

def _signal_handler(signum, frame):
    global _shutdown
    logger.info(f"📛 收到信号 {signum}，准备优雅退出...")
    _shutdown = True
    # Render 重新部署时会发 SIGTERM：把当前任务回退到 pending，新 Worker 会自动重试
    for cur in list(_running_task_ids):
        try:
            update_task(
                cur,
                status='pending',
                error='',
                progress='服务重启，任务已重新排队',
            )
            db.execute("UPDATE task_queue SET worker_id = NULL WHERE task_id = %s", (cur,))
            logger.warning(f"⚠️ 当前任务 {cur} 已回退为 pending（等待新 Worker 重试）")
        except Exception as e:
            logger.error(f"❌ SIGTERM 时回写任务状态失败: {e}")

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ============================================
# 任务拾取与分发
# ============================================

def claim_task():
    """从 task_queue 中拾取一条 pending 任务（先进先出）。"""
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
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING task_id, function_type, task_params, user_id, session_id
        """, (WORKER_ID,))
        return row
    except Exception as e:
        logger.error(f"❌ 拾取任务失败: {e}")
        return None


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


# ============================================
# 主循环
# ============================================

def main():
    logger.info("=" * 60)
    logger.info("🏭 Worker 进程启动")
    logger.info(f"   轮询间隔: {POLL_INTERVAL}s")
    logger.info(f"   Worker ID: {WORKER_ID}")
    logger.info(f"   并发数: {WORKER_CONCURRENCY}")
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

    idle_count = 0
    futures = set()
    with ThreadPoolExecutor(max_workers=WORKER_CONCURRENCY, thread_name_prefix='task-worker') as executor:
        while not _shutdown:
            done = {future for future in futures if future.done()}
            for future in done:
                futures.discard(future)
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"❌ 并发任务 Future 异常: {exc}")

            claimed_any = False
            while len(futures) < WORKER_CONCURRENCY:
                task_row = claim_task()
                if not task_row:
                    break
                claimed_any = True
                futures.add(executor.submit(dispatch_task, task_row))

            if claimed_any:
                idle_count = 0
            elif futures:
                wait(futures, timeout=POLL_INTERVAL, return_when=FIRST_COMPLETED)
            else:
                idle_count += 1
                if idle_count % 20 == 0:
                    logger.info(f"💤 空闲中... (已空转 {idle_count * POLL_INTERVAL}s)")
                time.sleep(POLL_INTERVAL)

        if futures:
            logger.info(f"⏳ 正在等待 {len(futures)} 个运行中任务结束...")
            wait(futures, timeout=30)

    logger.info("👋 Worker 进程已退出")


if __name__ == '__main__':
    main()
