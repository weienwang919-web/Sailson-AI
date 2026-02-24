import os
import sys
import datetime
import time
import logging
import pandas as pd
from io import BytesIO
from PIL import Image
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
import google.generativeai as genai
from apify_client import ApifyClient
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils.dataframe import dataframe_to_rows

# 加载 .env 文件
load_dotenv()

# ============================================
# 日志配置 - 确保输出到 stdout
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ============================================
# 环境配置 - 优化版
# ============================================

# 清除代理设置（云端环境不需要代理）
for proxy_var in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if proxy_var in os.environ:
        del os.environ[proxy_var]
        logger.info(f"🧹 已清除代理设置: {proxy_var}")

# 加载环境变量
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
APIFY_TOKEN = os.environ.get('APIFY_TOKEN')
PORT = int(os.environ.get('PORT', 5001))

# 启动时输出配置状态
logger.info("=" * 60)
logger.info("🚀 Sailson AI 工作台启动中...")
logger.info(f"🔑 GOOGLE_API_KEY: {'✅ 已配置' if GOOGLE_API_KEY else '❌ 未配置'}")
logger.info(f"🔑 APIFY_TOKEN: {'✅ 已配置' if APIFY_TOKEN else '❌ 未配置'}")
logger.info(f"🌐 PORT: {PORT}")
logger.info(f"🐍 Python 版本: {sys.version}")
logger.info("=" * 60)

# 初始化 AI 引擎
if GOOGLE_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        logger.info("✅ Google Gemini API 初始化成功")
    except Exception as e:
        logger.error(f"❌ Google Gemini API 初始化失败: {e}")
else:
    logger.warning("⚠️ 警告: GOOGLE_API_KEY 未配置，AI 功能将不可用")

# 初始化爬虫引擎
if APIFY_TOKEN:
    try:
        apify_client = ApifyClient(APIFY_TOKEN)
        logger.info("✅ Apify 客户端初始化成功")
    except Exception as e:
        logger.error(f"❌ Apify 客户端初始化失败: {e}")
        apify_client = None
else:
    logger.warning("⚠️ 警告: APIFY_TOKEN 未配置，爬虫功能将不可用")
    apify_client = None

# Flask 应用初始化
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'sailson_secure_key')
HISTORY_DB = []
LATEST_ANALYSIS_RESULTS = {}  # 存储最新的分析结果，用于导出

# ============================================
# 核心工具函数
# ============================================

def call_gemini(prompt, image=None, timeout=60):
    """调用 Google Gemini API"""
    if not GOOGLE_API_KEY:
        error_msg = "❌ 错误：GOOGLE_API_KEY 未配置"
        logger.error(error_msg)
        return error_msg

    model_name = 'gemini-2.5-flash'

    try:
        logger.info(f"🤖 正在调用 Gemini 模型: {model_name}")
        logger.info(f"📏 Prompt 长度: {len(prompt)} 字符")

        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)

        result = response.text
        logger.info(f"✅ Gemini 调用成功，返回 {len(result)} 字符")
        return result

    except Exception as e:
        error_msg = f"⚠️ Gemini API 调用失败: {str(e)}"
        logger.error(error_msg)
        import traceback
        traceback.print_exc()
        return error_msg


def process_uploaded_file(file):
    """处理上传的文件（图片或表格）"""
    try:
        fname = file.filename.lower()
        logger.info(f"📁 处理文件: {fname}")

        if fname.endswith(('.png', '.jpg', '.jpeg', '.webp')):
            logger.info("🖼️ 识别为图片文件")
            return "IMAGE", Image.open(file)

        if fname.endswith(('.xlsx', '.csv')):
            logger.info("📊 识别为表格文件")
            if fname.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
            return "TEXT", df.to_string(index=False, max_rows=50)

        return "ERROR", "不支持的文件格式"

    except Exception as e:
        error_msg = f"文件处理失败: {str(e)}"
        logger.info(f"❌ {error_msg}")
        return "ERROR", error_msg


def save_history(title, result, type_tag):
    """保存到历史记录"""
    record = {
        'id': len(HISTORY_DB) + 1,
        'title': f"{title} [{datetime.datetime.now().strftime('%H:%M')}]",
        'result': result,
        'type': type_tag
    }
    HISTORY_DB.append(record)
    logger.info(f"💾 已保存历史记录 #{record['id']}: {title}")


def call_veo_api(prompt):
    """调用 Google Veo API（模拟）"""
    logger.info(f"🎬 模拟 Veo API 调用: {prompt[:50]}...")
    time.sleep(3)
    return "https://cdn.pixabay.com/video/2023/10/22/186115-877653483_large.mp4"

# ============================================
# 基础路由
# ============================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if username == 'admin' and password == '123456':
            session['logged_in'] = True
            session['session_id'] = f"{username}_{int(time.time())}"
            logger.info(f"✅ 用户登录成功: {username}")
            return redirect(url_for('home'))
        else:
            logger.info(f"❌ 登录失败: {username}")

    return render_template('login.html')


@app.route('/logout')
def logout():
    """登出"""
    session.pop('logged_in', None)
    logger.info("👋 用户已登出")
    return redirect(url_for('login'))


@app.route('/')
def home():
    """首页"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('index.html')


@app.route('/debug')
def debug_page():
    """调试页面"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    debug_info = {
        "status": "Online",
        "gemini_key": bool(GOOGLE_API_KEY),
        "apify_key": bool(APIFY_TOKEN),
        "port": PORT,
        "history_count": len(HISTORY_DB)
    }
    logger.info(f"🔍 调试信息: {debug_info}")
    return jsonify(debug_info)


@app.route('/health')
def health_check():
    """健康检查端点 - 用于 Render 监控"""
    return jsonify({"status": "ok", "service": "Sailson AI"}), 200

# ============================================
# 功能 1: 舆情分析
# ============================================

@app.route('/sentiment-tool')
def sentiment_tool():
    """舆情分析工具页面"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('analysis.html')


@app.route('/analyze', methods=['POST'])
def analyze():
    """舆情分析 API"""
    logger.info("\n" + "=" * 60)
    logger.info("📥 收到舆情分析请求")
    logger.info(f"🔑 GOOGLE_API_KEY: {'✅' if GOOGLE_API_KEY else '❌'}")
    logger.info(f"🔑 APIFY_TOKEN: {'✅' if APIFY_TOKEN else '❌'}")

    url = request.form.get('url')
    file = request.files.get('file')
    content = ""
    img = None
    source_title = "未知"

    try:
        # 路径 A: 文件上传分析
        if file:
            logger.info(f"📁 处理模式: 文件上传")
            mode, res = process_uploaded_file(file)

            if mode == "ERROR":
                logger.info(f"❌ 文件处理失败: {res}")
                return jsonify({'result': f"❌ {res}"})

            if mode == "IMAGE":
                img = res
                content = "分析图片中的反馈内容"
                logger.info("🖼️ 图片模式")
            else:
                content = res
                logger.info("📊 表格模式")

            source_title = f"文件: {file.filename[:15]}"

        # 路径 B: 社交媒体链接抓取分析
        elif url:
            logger.info(f"🔗 处理模式: 链接爬取")
            logger.info(f"🔗 目标 URL: {url}")

            if not apify_client:
                error_msg = "❌ 错误：APIFY_TOKEN 未配置，无法使用爬虫功能"
                logger.error(error_msg)
                return jsonify({'result': error_msg})

            try:
                logger.info(f"🕵️ 启动 Apify 爬虫...")
                run_input = {
                    "startUrls": [{"url": url}],
                    "resultsLimit": 1000,  # 这是正确的参数名
                    "maxComments": 1000,
                    "maxPostCount": 1,
                    "maxCommentsPerPost": 1000,
                    "maxRepliesPerComment": 0,  # 不抓取回复，只抓取主评论
                    "scrapeCommentReplies": False  # 不抓取回复
                }

                logger.info(f"📋 爬虫配置: {run_input}")

                # 使用 start() 启动爬虫
                run = apify_client.actor("apify/facebook-comments-scraper").start(run_input=run_input)
                logger.info(f"✅ 爬虫任务已启动，Run ID: {run['id']}")

                # 等待爬虫完成（正确的参数名）
                logger.info("⏳ 等待爬虫完成...")
                run = apify_client.run(run['id']).wait_for_finish(wait_secs=180)  # 增加到180秒
                logger.info(f"✅ 爬虫任务完成，状态: {run['status']}")

                if run['status'] != 'SUCCEEDED':
                    error_msg = f"❌ 爬虫任务失败，状态: {run['status']}"
                    logger.error(error_msg)
                    return jsonify({'result': error_msg})

                # 获取所有数据（可能需要分页）
                dataset_client = apify_client.dataset(run["defaultDatasetId"])
                items = []
                offset = 0
                limit = 1000

                while True:
                    batch = dataset_client.list_items(offset=offset, limit=limit).items
                    if not batch:
                        break
                    items.extend(batch)
                    logger.info(f"📦 已获取 {len(items)} 条数据（本批次: {len(batch)}）...")
                    if len(batch) < limit:
                        break
                    offset += limit

                logger.info(f"✅ 总共获取到 {len(items)} 条数据")

                # 调试：查看 run 的详细信息
                logger.info(f"🔍 Run 详情: status={run.get('status')}, stats={run.get('stats')}")

                if not items:
                    warning_msg = "⚠️ 抓取成功但未发现公开评论，请检查链接权限"
                    logger.warning(warning_msg)
                    return jsonify({'result': warning_msg})

                # 分批处理评论（每批 50 条）
                batch_size = 50
                all_results = []

                logger.info(f"📊 开始分批分析，每批 {batch_size} 条...")

                for i in range(0, len(items), batch_size):
                    batch = items[i:i+batch_size]
                    batch_num = i // batch_size + 1
                    total_batches = (len(items) + batch_size - 1) // batch_size

                    logger.info(f"🔄 处理第 {batch_num}/{total_batches} 批（{len(batch)} 条评论）...")

                    batch_content = "\n".join([f"用户{j}: {it.get('text', '')}" for j, it in enumerate(batch)])

                    # 简化的 Prompt，只做分类
                    batch_prompt = f"""
Analyze these comments and categorize them. Output ONLY a JSON array.

Comments:
{batch_content}

Categories (Chinese only):
1. 外挂作弊 - hackers, cheating
2. 游戏优化 - lag, crashes
3. 游戏Bug - glitches, errors
4. 充值退款 - payment issues
5. 新模式/地图/平衡性建议 - new content requests
6. 其他 - spam, praise

Output format (JSON array only, no markdown):
[
  {{
    "text": "comment text",
    "category": "外挂作弊",
    "sentiment": "负面",
    "language": "英语",
    "analysis": "详细分析内容"
  }},
  ...
]

IMPORTANT:
- Output ONLY valid JSON array
- Skip "其他" category
- Use Chinese for category, sentiment, language, and analysis
- Language options: 英语, 中文, 日语, 韩语, 泰语, 越南语, 其他
- Analysis requirements:
  * For short comments (< 30 chars): One sentence summary (15-20 Chinese characters)
  * For medium/long comments (>= 30 chars): Detailed analysis (40-50 Chinese characters)
  * Include: main issue, player emotion, key details
"""

                    result = call_gemini(batch_prompt, timeout=60)

                    # 解析 JSON 结果
                    try:
                        import json
                        import re
                        # 清理可能的 markdown 标记
                        clean_result = re.sub(r'```json\s*|\s*```', '', result).strip()
                        batch_data = json.loads(clean_result)
                        all_results.extend(batch_data)
                        logger.info(f"✅ 第 {batch_num} 批完成，获得 {len(batch_data)} 条有效结果")
                    except Exception as e:
                        logger.error(f"❌ 第 {batch_num} 批解析失败: {e}")
                        continue

                # 生成 HTML 表格
                logger.info(f"📝 生成最终报告，共 {len(all_results)} 条有效评论...")

                # 按分类排序
                category_order = ["外挂作弊", "游戏优化", "游戏Bug", "充值退款", "新模式/地图/平衡性建议"]
                all_results.sort(key=lambda x: category_order.index(x.get('category', '其他')) if x.get('category') in category_order else 999)

                # 生成 HTML
                html_rows = []
                for idx, item in enumerate(all_results, 1):
                    html_rows.append(f"""
                    <tr>
                        <td>{idx}</td>
                        <td style="white-space: pre-wrap; word-break: break-word;">{item.get('text', '')}</td>
                        <td><strong>{item.get('category', '')}</strong></td>
                        <td>{item.get('sentiment', '')}</td>
                        <td>{item.get('language', '')}</td>
                        <td style="white-space: pre-wrap; word-break: break-word;">{item.get('analysis', '')}</td>
                    </tr>
                    """)

                result = f"""
                <table class="table table-hover">
                    <thead>
                        <tr>
                            <th style="width:40px;">#</th>
                            <th style="width:25%;">原始评论</th>
                            <th style="width:100px;">归类</th>
                            <th style="width:70px;">情感</th>
                            <th style="width:60px;">语言</th>
                            <th>简要分析</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(html_rows)}
                    </tbody>
                </table>
                """

                # 保存结果用于导出
                LATEST_ANALYSIS_RESULTS[session.get('session_id', 'default')] = all_results

                source_title = f"FB: {url[:15]}..."

            except Exception as e:
                error_msg = f"❌ 爬虫任务失败: {str(e)}"
                logger.error(error_msg)
                import traceback
                traceback.print_exc()
                return jsonify({'result': error_msg})

        else:
            error_msg = "❌ 错误：请提供链接或文件"
            logger.error(error_msg)
            return jsonify({'result': error_msg})

        # 保存历史记录
        save_history(source_title, result, 'sentiment')

        logger.info("✅ 舆情分析完成")
        logger.info("=" * 60 + "\n")

        return jsonify({'result': result})

    except Exception as e:
        error_msg = f"❌ 系统错误: {str(e)}"
        logger.error(error_msg)
        import traceback
        traceback.print_exc()
        return jsonify({'result': error_msg})

# ============================================
# 功能 2: 竞品监控
# ============================================

@app.route('/competitor-tool')
def competitor_tool():
    """竞品监控工具页面"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('competitor.html')


@app.route('/monitor_competitors', methods=['POST'])
def monitor_competitors():
    """竞品监控 API"""
    logger.info("\n" + "=" * 60)
    logger.info("📥 收到竞品监控请求")

    try:
        data = request.json
        target_url = data.get('competitor_name')
        start_dt_str = data.get('startDate')
        end_dt_str = data.get('endDate')

        logger.info(f"🎯 目标 URL: {target_url}")
        logger.info(f"📅 时间段: {start_dt_str} ~ {end_dt_str}")

        if not apify_client:
            error_msg = "❌ 错误：APIFY_TOKEN 未配置，无法使用爬虫功能"
            print(error_msg)
            return jsonify({'result': f"<div class='alert alert-danger'>{error_msg}</div>"})

        # 1. 日期转换
        target_start = datetime.datetime.strptime(start_dt_str, '%Y-%m-%d').date()
        target_end = datetime.datetime.strptime(end_dt_str, '%Y-%m-%d').date()
        logger.info(f"📆 解析日期: {target_start} ~ {target_end}")

        # 2. 云端抓取
        logger.info("🕵️ 启动 TikTok 爬虫...")
        run_input = {
            "profiles": [target_url],
            "resultsPerPage": 35,
            "oldestPostDate": start_dt_str,
            "shouldDownloadVideos": False
        }

        # 使用 start() 启动爬虫
        run = apify_client.actor("clockworks/tiktok-scraper").start(run_input=run_input)
        logger.info(f"✅ 爬虫任务已启动，Run ID: {run['id']}")

        # 等待爬虫完成（正确的参数名）
        logger.info("⏳ 等待爬虫完成...")
        run = apify_client.run(run['id']).wait_for_finish(wait_secs=180)
        logger.info(f"✅ 爬虫任务完成，状态: {run['status']}")

        if run['status'] != 'SUCCEEDED':
            error_msg = f"❌ 爬虫任务失败，状态: {run['status']}"
            logger.error(error_msg)
            return jsonify({'result': f"<div class='alert alert-danger'>{error_msg}</div>"})

        items = apify_client.dataset(run["defaultDatasetId"]).list_items().items
        logger.info(f"📦 获取到 {len(items)} 条原始数据")

        # 3. 本地时间过滤
        cleaned = []
        for it in items:
            raw_date = it.get("createTimeISO")
            if not raw_date:
                continue

            post_dt = datetime.datetime.fromisoformat(raw_date.replace('Z', '+00:00')).date()

            if target_start <= post_dt <= target_end:
                cleaned.append({
                    "desc": it.get("desc", "无描述"),
                    "likes": it.get("diggCount", 0),
                    "views": it.get("playCount", 0),
                    "comments": it.get("commentCount", 0),
                    "shares": it.get("shareCount", 0),
                    "collects": it.get("collectCount", 0),
                    "url": it.get("webVideoUrl"),
                    "date": str(post_dt)
                })

        logger.info(f"✅ 时间过滤后剩余 {len(cleaned)} 条数据")

        if not cleaned:
            warning_msg = f"<div class='alert alert-warning'>在此期间 ({start_dt_str} ~ {end_dt_str}) 未发现视频。</div>"
            logger.info("⚠️ 未发现符合条件的视频")
            return jsonify({'result': warning_msg})

        # 4. Gemini 生成报告
        prompt = f"""
You are a Data Entry Assistant. Please fill the following TikTok data into the PROVIDED HTML TEMPLATE.

【Data Source】: {cleaned}
【Period】: {start_dt_str} to {end_dt_str}

【STRICT TEMPLATE (Use this EXACT structure)】:
<div style="width:100%; font-family:sans-serif;">
    <h3 style="color:#D32F2F; border-bottom:2px solid #eee; padding-bottom:10px;">📊 数据概览表 ({start_dt_str} 至 {end_dt_str})</h3>
    <table class="table" style="width:100%; margin-bottom:30px; text-align:center; font-size:0.9rem;">
        <tr style="background:#f8f9fa;">
            <th>总播放</th><th>总互动</th><th>总点赞</th><th>总评论</th><th>总收藏</th><th>总转发</th>
        </tr>
        <tr>
            <td>[总播放数]</td><td>[总互动数]</td><td>[总点赞数]</td><td>[总评论数]</td><td>[总收藏数]</td><td>[总转发数]</td>
        </tr>
    </table>

    <h3 style="color:#D32F2F; border-bottom:2px solid #eee; padding-bottom:10px;">🔥 爆款视频精选</h3>
    <div style="background:#FFF9F9; border-left:5px solid #D32F2F; padding:20px; margin-bottom:15px; border-radius:8px;">
        <p><strong>视频描述：</strong> [描述内容]</p>
        <p><strong>核心指标：</strong> 播放: [播放数] | 点赞: [点赞数] | 互动: [评论数]评论 / [分享数]分享</p>
        <p><strong>查看详情：</strong> <a href="[webVideoUrl]" target="_blank" style="color:#2962FF;">点击进入 TikTok 观看原文链接</a></p>
    </div>
</div>

【Requirements】:
- 必须使用中文填充模板。
- 总互动 = 点赞 + 评论 + 收藏 + 转发的总和。
- 严禁添加模板之外的任何文字（包括分析、建议、前言、结语）。
- 仅输出 Raw HTML 代码，禁止 Markdown 代码块。
"""

        logger.info("🤖 开始调用 Gemini API 生成报告...")
        result = call_gemini(prompt)

        # 清理 Markdown 代码块标记
        result = result.replace('```html', '').replace('```', '').strip()

        # 保存历史记录
        save_history(f"竞品数据:{target_url[20:30]}", result, 'competitor')

        logger.info("✅ 竞品监控完成")
        logger.info("=" * 60 + "\n")

        return jsonify({'result': result})

    except Exception as e:
        error_msg = f"❌ 监控失败: {str(e)}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        return jsonify({'result': f"<div class='alert alert-danger'>{error_msg}</div>"})

# ============================================
# 功能 3: 视频生成
# ============================================

@app.route('/video-tool')
def video_tool():
    """视频生成工具页面"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('video.html')


@app.route('/generate_video', methods=['POST'])
def generate_video():
    """视频生成 API"""
    logger.info("\n" + "=" * 60)
    logger.info("📥 收到视频生成请求")

    try:
        prompt = request.json.get('prompt')
        logger.info(f"🎬 Prompt: {prompt[:50]}...")

        video_url = call_veo_api(prompt)
        save_history(f"视频: {prompt[:10]}", video_url, 'video')

        logger.info("✅ 视频生成完成")
        logger.info("=" * 60 + "\n")

        return jsonify({'video_url': video_url})

    except Exception as e:
        error_msg = f"❌ 视频生成失败: {str(e)}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        return jsonify({'error': error_msg})

# ============================================
# 历史记录管理
# ============================================

@app.route('/get_history')
def get_history():
    """获取历史记录"""
    return jsonify(HISTORY_DB[::-1])


@app.route('/get_record/<int:id>')
def get_record(id):
    """获取单条记录"""
    record = next((x for x in HISTORY_DB if x['id'] == id), None)
    return jsonify(record)

# ============================================
# Excel 导出功能
# ============================================

def create_excel_by_language(results):
    """按语言分类生成 Excel"""
    wb = Workbook()
    wb.remove(wb.active)  # 删除默认 sheet

    # 按语言分组
    language_groups = {}
    for item in results:
        lang = item.get('language', '其他')
        if lang not in language_groups:
            language_groups[lang] = []
        language_groups[lang].append(item)

    # 为每个语言创建 Sheet
    for lang, items in sorted(language_groups.items()):
        ws = wb.create_sheet(title=lang)

        # 表头
        headers = ['序号', '原始评论', '归类', '情感倾向', '语言', '简要分析']
        ws.append(headers)

        # 设置表头样式
        header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
        header_font = Font(bold=True, color='FFFFFF')
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')

        # 添加数据
        for idx, item in enumerate(items, 1):
            ws.append([
                idx,
                item.get('text', ''),
                item.get('category', ''),
                item.get('sentiment', ''),
                item.get('language', ''),
                item.get('analysis', '')
            ])

        # 设置列宽
        ws.column_dimensions['A'].width = 8
        ws.column_dimensions['B'].width = 50
        ws.column_dimensions['C'].width = 20
        ws.column_dimensions['D'].width = 12
        ws.column_dimensions['E'].width = 10
        ws.column_dimensions['F'].width = 40

        # 冻结首行
        ws.freeze_panes = 'A2'

    return wb


def create_excel_by_category(results):
    """按分类生成 Excel"""
    wb = Workbook()
    wb.remove(wb.active)

    # 按分类分组
    category_groups = {}
    for item in results:
        cat = item.get('category', '其他')
        if cat not in category_groups:
            category_groups[cat] = []
        category_groups[cat].append(item)

    # 分类顺序
    category_order = ['外挂作弊', '游戏优化', '游戏Bug', '充值退款', '新模式/地图/平衡性建议']

    # 为每个分类创建 Sheet
    for cat in category_order:
        if cat not in category_groups:
            continue

        items = category_groups[cat]
        ws = wb.create_sheet(title=cat)

        # 表头
        headers = ['序号', '原始评论', '归类', '情感倾向', '语言', '简要分析']
        ws.append(headers)

        # 设置表头样式
        header_fill = PatternFill(start_color='D32F2F', end_color='D32F2F', fill_type='solid')
        header_font = Font(bold=True, color='FFFFFF')
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')

        # 添加数据
        for idx, item in enumerate(items, 1):
            ws.append([
                idx,
                item.get('text', ''),
                item.get('category', ''),
                item.get('sentiment', ''),
                item.get('language', ''),
                item.get('analysis', '')
            ])

        # 设置列宽
        ws.column_dimensions['A'].width = 8
        ws.column_dimensions['B'].width = 50
        ws.column_dimensions['C'].width = 20
        ws.column_dimensions['D'].width = 12
        ws.column_dimensions['E'].width = 10
        ws.column_dimensions['F'].width = 40

        # 冻结首行
        ws.freeze_panes = 'A2'

    return wb


@app.route('/export_by_language')
def export_by_language():
    """按语言导出 Excel"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    session_id = session.get('session_id', 'default')
    results = LATEST_ANALYSIS_RESULTS.get(session_id, [])

    if not results:
        return jsonify({'error': '没有可导出的数据'}), 400

    try:
        wb = create_excel_by_language(results)

        # 生成文件
        output = BytesIO()
        wb.save(output)
        output.seek(0)

        # 生成文件名
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
        filename = f'语言分类报告_{timestamp}.xlsx'

        logger.info(f"📥 导出语言分类报告: {len(results)} 条数据")

        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"❌ 导出失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/export_by_category')
def export_by_category():
    """按分类导出 Excel"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    session_id = session.get('session_id', 'default')
    results = LATEST_ANALYSIS_RESULTS.get(session_id, [])

    if not results:
        return jsonify({'error': '没有可导出的数据'}), 400

    try:
        wb = create_excel_by_category(results)

        # 生成文件
        output = BytesIO()
        wb.save(output)
        output.seek(0)

        # 生成文件名
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
        filename = f'问题分类报告_{timestamp}.xlsx'

        logger.info(f"📥 导出问题分类报告: {len(results)} 条数据")

        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"❌ 导出失败: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================
# 应用启动
# ============================================

if __name__ == '__main__':
    logger.info("\n" + "=" * 60)
    logger.info("🎉 Sailson AI 工作台已启动")
    logger.info(f"🌐 访问地址: http://0.0.0.0:{PORT}")
    logger.info("=" * 60 + "\n")

    app.run(debug=False, host='0.0.0.0', port=PORT)
