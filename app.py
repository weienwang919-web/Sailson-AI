import os
import sys
import datetime
import time
import logging
import pandas as pd
from PIL import Image
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import google.generativeai as genai
from apify_client import ApifyClient
from dotenv import load_dotenv

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

# ============================================
# 核心工具函数
# ============================================

def call_gemini(prompt, image=None, timeout=120):
    """调用 Google Gemini API"""
    if not GOOGLE_API_KEY:
        error_msg = "❌ 错误：GOOGLE_API_KEY 未配置"
        logger.error(error_msg)
        return error_msg

    model_name = 'gemini-2.5-flash'

    try:
        logger.info(f"🤖 正在调用 Gemini 模型: {model_name}")
        logger.info(f"🔑 API Key 前缀: {GOOGLE_API_KEY[:15]}...")

        model = genai.GenerativeModel(model_name)

        if image:
            logger.info("📸 包含图片输入")
            response = model.generate_content([prompt, image], request_options={"timeout": timeout})
        else:
            logger.info("📝 纯文本输入")
            logger.info(f"📏 Prompt 长度: {len(prompt)} 字符")

            # 添加重试机制
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    logger.info(f"🔄 尝试 {attempt + 1}/{max_retries}...")
                    response = model.generate_content(
                        prompt,
                        generation_config=genai.types.GenerationConfig(
                            temperature=0.7,
                            max_output_tokens=8192,
                        ),
                        request_options={"timeout": timeout}
                    )
                    break
                except Exception as retry_error:
                    if attempt < max_retries - 1:
                        logger.warning(f"⚠️ 尝试 {attempt + 1} 失败: {str(retry_error)}, 重试中...")
                        time.sleep(2)
                    else:
                        raise

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
                    "maxComments": 1000,  # 设置一个较大的数值
                    "maxPostCount": 1,
                    "maxCommentsPerPost": 1000,
                    "maxRepliesPerComment": 0  # 不抓取回复，只抓取主评论
                }

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

                content = "\n".join([f"用户{i}: {it.get('text', '')}" for i, it in enumerate(items)])
                source_title = f"FB: {url[:15]}..."

                if not content:
                    warning_msg = "⚠️ 抓取成功但未发现公开评论，请检查链接权限"
                    logger.warning(warning_msg)
                    return jsonify({'result': warning_msg})

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

        # 调用 Gemini 进行分析
        prompt = f"""
You are a Senior Game Operations Data Scientist. Analyze the player feedback provided and output ONLY a raw HTML <table>.

【Input Data】:
{content}

【STRICT Categorization Rules (CRITICAL)】:
You MUST assign each review to EXACTLY ONE of the following categories. Output ONLY the Chinese term.

1. 外挂作弊: Any mention of hackers, aimbots, wallhacks, cheating, or scripts.
2. 游戏优化: Issues related to lag, high ping, server disconnects, FPS drops, or crashes.
3. 游戏Bug: Technical glitches in gameplay, stuck in textures, UI errors, or broken mechanics.
4. 充值退款: Missing rewards (including leaderboard/event rewards), payment issues, shop errors, or refund requests.
5. 新模式/地图/平衡性建议: Requests for new content, map changes, balance adjustments, or new game modes.
6. 其他: Generic praise, insults without specific context, greetings, or irrelevant spam.

【CRITICAL FILTERING】:
- **EXCLUDE all reviews categorized as "其他"** - DO NOT include them in the output table.
- Only output reviews from categories 1-5.

【Output Format】:
- Return ONLY the raw HTML <table> with class "table table-hover". No markdown code blocks.
- **SORT the rows by category**: Group all "外挂作弊" together, then "游戏优化", then "游戏Bug", then "充值退款", then "新模式/地图/平衡性建议".
- Columns:
    1. 来源 (Source)
    2. 原始评论 (Original Review)
    3. 归类 (Category - MUST use the 5 Chinese terms above, NO "其他")
    4. 情感倾向 (Sentiment - 正面/负面/中性)
    5. 简要分析 (Analysis - Concise Chinese insight)
"""

        logger.info("🤖 开始调用 Gemini API...")
        result = call_gemini(prompt, img)

        # 清理 Markdown 代码块标记
        result = result.replace('```html', '').replace('```', '').strip()

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
# 应用启动
# ============================================

if __name__ == '__main__':
    logger.info("\n" + "=" * 60)
    logger.info("🎉 Sailson AI 工作台已启动")
    logger.info(f"🌐 访问地址: http://0.0.0.0:{PORT}")
    logger.info("=" * 60 + "\n")

    app.run(debug=False, host='0.0.0.0', port=PORT)
