import os
import sys
import datetime
import time
import logging
import pandas as pd
import uuid
import threading
from io import BytesIO
from PIL import Image
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from flask_bcrypt import Bcrypt
from apify_client import ApifyClient
from dotenv import load_dotenv
from openai import OpenAI
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils.dataframe import dataframe_to_rows
from functools import wraps
import database as db

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
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')
APIFY_TOKEN = os.environ.get('APIFY_TOKEN')
PORT = int(os.environ.get('PORT', 5001))

# 启动时输出配置状态
logger.info("=" * 60)
logger.info("🚀 Sailson AI 工作台启动中...")
logger.info(f"🔑 DASHSCOPE_API_KEY: {'✅ 已配置' if DASHSCOPE_API_KEY else '❌ 未配置'}")
logger.info(f"🔑 APIFY_TOKEN: {'✅ 已配置' if APIFY_TOKEN else '❌ 未配置'}")
logger.info(f"🌐 PORT: {PORT}")
logger.info(f"🐍 Python 版本: {sys.version}")
logger.info("=" * 60)

# 初始化 AI 引擎
if DASHSCOPE_API_KEY:
    try:
        qwen_client = OpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        logger.info("✅ 通义千问 API 初始化成功")
    except Exception as e:
        logger.error(f"❌ 通义千问 API 初始化失败: {e}")
        qwen_client = None
else:
    logger.warning("⚠️ 警告: DASHSCOPE_API_KEY 未配置，AI 功能将不可用")
    qwen_client = None

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
bcrypt = Bcrypt(app)

# 内存存储（保留用于向后兼容）
HISTORY_DB = []
LATEST_ANALYSIS_RESULTS = {}  # 存储最新的分析结果，用于导出
# TASK_QUEUE 已迁移到数据库，不再使用内存字典

# 汇率配置
USD_TO_CNY = 7.2

# ============================================
# 任务恢复机制（定义，稍后调用）
# ============================================

def recover_interrupted_tasks():
    """恢复被中断的任务"""
    try:
        # 查找所有 processing 状态的任务（说明被中断了）
        interrupted_tasks = db.query_all("""
            SELECT task_id FROM task_queue
            WHERE status = 'processing'
            AND created_at > NOW() - INTERVAL '1 hour'
        """)

        if interrupted_tasks:
            logger.warning(f"⚠️ 发现 {len(interrupted_tasks)} 个被中断的任务，标记为失败")
            for task in interrupted_tasks:
                update_task(
                    task['task_id'],
                    status='failed',
                    error='服务重启导致任务中断',
                    progress='任务已中断'
                )
    except Exception as e:
        logger.error(f"❌ 恢复任务失败: {e}")

# ============================================
# 装饰器：权限控制
# ============================================

def login_required(f):
    """需要登录才能访问"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """需要管理员权限才能访问"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            return jsonify({'error': '需要管理员权限'}), 403
        return f(*args, **kwargs)
    return decorated_function

# ============================================
# 核心工具函数
# ============================================

def create_task(task_id, user_id, session_id):
    """创建任务记录"""
    try:
        db.execute("""
            INSERT INTO task_queue (task_id, user_id, session_id, status, progress)
            VALUES (%s, %s, %s, %s, %s)
        """, (task_id, user_id, session_id, 'pending', '任务已创建'))
        logger.info(f"✅ 任务 {task_id} 已写入数据库")
    except Exception as e:
        logger.error(f"❌ 创建任务失败: {e}")

def update_task(task_id, status=None, progress=None, result=None, error=None):
    """更新任务状态"""
    try:
        updates = []
        params = []

        if status is not None:
            updates.append("status = %s")
            params.append(status)
        if progress is not None:
            updates.append("progress = %s")
            params.append(progress)
        if result is not None:
            updates.append("result = %s")
            params.append(result)
        if error is not None:
            updates.append("error = %s")
            params.append(error)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(task_id)

            sql = f"UPDATE task_queue SET {', '.join(updates)} WHERE task_id = %s"
            db.execute(sql, tuple(params))
    except Exception as e:
        logger.error(f"❌ 更新任务状态失败: {e}")

def get_task(task_id):
    """获取任务状态"""
    try:
        task = db.query_one("""
            SELECT task_id, status, progress, result, error
            FROM task_queue
            WHERE task_id = %s
        """, (task_id,))
        return task
    except Exception as e:
        logger.error(f"❌ 获取任务状态失败: {e}")
        return None

# ============================================
# 启动时恢复被中断的任务
# ============================================
recover_interrupted_tasks()


def call_gemini(prompt, image=None, timeout=60):
    """调用通义千问 API"""
    if not qwen_client:
        error_msg = "❌ 错误：DASHSCOPE_API_KEY 未配置"
        logger.error(error_msg)
        return error_msg, 0

    model_name = 'qwen-turbo'

    try:
        logger.info(f"🤖 正在调用通义千问模型: {model_name}")
        logger.info(f"📏 Prompt 长度: {len(prompt)} 字符")

        response = qwen_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )

        result = response.choices[0].message.content
        tokens = response.usage.total_tokens if hasattr(response, 'usage') else 0
        logger.info(f"✅ 通义千问调用成功，返回 {len(result)} 字符，消耗 {tokens} tokens")
        return result, tokens

    except Exception as e:
        error_msg = f"⚠️ 通义千问 API 调用失败: {str(e)}"
        logger.error(error_msg)
        import traceback
        traceback.print_exc()
        return error_msg, 0


def process_uploaded_file(file_data):
    """处理上传的文件（图片或表格）

    Args:
        file_data: 字典，包含 filename, content, content_type
    """
    try:
        fname = file_data['filename'].lower()
        content = file_data['content']
        logger.info(f"📁 处理文件: {fname}")

        if fname.endswith(('.png', '.jpg', '.jpeg', '.webp')):
            logger.info("🖼️ 识别为图片文件")
            from io import BytesIO
            return "IMAGE", Image.open(BytesIO(content))

        if fname.endswith(('.xlsx', '.csv')):
            logger.info("📊 识别为表格文件")
            from io import BytesIO
            if fname.endswith('.csv'):
                df = pd.read_csv(BytesIO(content))
            else:
                df = pd.read_excel(BytesIO(content))
            return "TEXT", df.to_string(index=False, max_rows=50)

        return "ERROR", "不支持的文件格式"

    except Exception as e:
        error_msg = f"文件处理失败: {str(e)}"
        logger.info(f"❌ {error_msg}")
        return "ERROR", error_msg


def save_history(title, result, type_tag):
    """保存到历史记录（数据库）"""
    try:
        user_id = session.get('user_id')
        if not user_id:
            logger.warning("⚠️ 未登录用户，跳过保存历史记录")
            return

        # 保存到数据库
        db.execute("""
            INSERT INTO analysis_results (user_id, title, result, type)
            VALUES (%s, %s, %s, %s)
        """, (user_id, title, result, type_tag))

        logger.info(f"💾 已保存历史记录到数据库: {title}")

        # 同时保存到内存（向后兼容）
        record = {
            'id': len(HISTORY_DB) + 1,
            'title': f"{title} [{datetime.datetime.now().strftime('%H:%M')}]",
            'result': result,
            'type': type_tag
        }
        HISTORY_DB.append(record)

    except Exception as e:
        logger.error(f"❌ 保存历史记录失败: {e}")
        # 失败时至少保存到内存
        record = {
            'id': len(HISTORY_DB) + 1,
            'title': f"{title} [{datetime.datetime.now().strftime('%H:%M')}]",
            'result': result,
            'type': type_tag
        }
        HISTORY_DB.append(record)


def call_veo_api(prompt):
    """调用 Google Veo API（模拟）"""
    logger.info(f"🎬 模拟 Veo API 调用: {prompt[:50]}...")
    time.sleep(3)
    return "https://cdn.pixabay.com/video/2023/10/22/186115-877653483_large.mp4"


def log_usage(user_id, username, department, function_type, comments_count, ai_tokens):
    """记录使用情况和成本"""
    try:
        # 计算成本
        ai_cost = ai_tokens * 0.008 / 1000  # 通义千问定价

        # 根据功能类型计算 Apify 成本
        if function_type == 'sentiment':
            # Facebook 评论：$2.50/1000条
            apify_cost_usd = comments_count * 2.50 / 1000
        elif function_type == 'competitor':
            # TikTok 数据：$3.70/1000条
            apify_cost_usd = comments_count * 3.70 / 1000
        else:
            apify_cost_usd = 0

        apify_cost = apify_cost_usd * USD_TO_CNY  # 转换为人民币
        total_cost = ai_cost + apify_cost

        # 保存到数据库
        db.execute("""
            INSERT INTO usage_logs
            (user_id, username, department, function_type, comments_count,
             ai_tokens, ai_cost, apify_cost, total_cost)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (user_id, username, department, function_type, comments_count,
              ai_tokens, ai_cost, apify_cost, total_cost))

        logger.info(f"💰 成本记录: AI={ai_cost:.4f}元 + Apify={apify_cost:.4f}元 = 总计{total_cost:.4f}元")

        return total_cost

    except Exception as e:
        logger.error(f"❌ 记录使用情况失败: {e}")
        return 0


def process_analysis_task(task_id, url, file_data, session_id, user_id, username, department):
    """异步处理分析任务"""
    # 用户信息已从主线程传入，不再从 session 获取

    logger.info(f"🔄 后台线程已启动，任务ID: {task_id}")
    logger.info(f"👤 用户信息: user_id={user_id}, username={username}, department={department}")
    logger.info(f"📋 任务参数: url={url}, has_file={file_data is not None}")

    # 追踪成本数据
    total_tokens = 0
    total_comments = 0

    try:
        logger.info(f"📝 更新任务状态为 processing...")
        update_task(task_id, status='processing', progress='正在初始化...')
        logger.info(f"✅ 任务状态更新成功")

        content = ""
        img = None
        source_title = "未知"

        # 路径 A: 文件上传分析
        if file_data:
            update_task(task_id, progress='正在处理文件...')
            mode, res = process_uploaded_file(file_data)

            if mode == "ERROR":
                update_task(task_id, status='failed', error=res)
                return

            if mode == "IMAGE":
                img = res
                content = "分析图片中的反馈内容"
            else:
                content = res

            source_title = f"文件: {file_data.filename[:15]}"

        # 路径 B: 社交媒体链接抓取分析
        elif url:
            logger.info(f"🌐 开始处理 URL: {url}")
            update_task(task_id, progress='正在抓取社媒数据...')

            if not apify_client:
                logger.error("❌ Apify 客户端未初始化")
                update_task(task_id, status='failed', error="APIFY_TOKEN 未配置")
                return

            try:
                logger.info("📋 准备 Apify 爬虫参数...")
                run_input = {
                    "startUrls": [{"url": url}],
                    "resultsLimit": 1000,
                    "maxComments": 1000,
                    "maxPostCount": 1,
                    "maxCommentsPerPost": 1000,
                    "maxRepliesPerComment": 0,
                    "scrapeCommentReplies": False
                }

                logger.info("🚀 启动 Apify 爬虫...")
                try:
                    run = apify_client.actor("apify/facebook-comments-scraper").start(run_input=run_input)
                    logger.info(f"✅ 爬虫任务已启动，Run ID: {run['id']}")
                except Exception as start_error:
                    error_msg = f"启动爬虫失败: {str(start_error)}"
                    logger.error(f"❌ {error_msg}")
                    logger.error(f"❌ 错误类型: {type(start_error).__name__}")
                    import traceback
                    logger.error(f"❌ 堆栈:\n{traceback.format_exc()}")
                    update_task(task_id, status='failed', error=error_msg)
                    return

                logger.info("⏳ 等待爬虫完成（最长 180 秒）...")
                update_task(task_id, progress='等待爬虫完成（约30-60秒）...')
                run = apify_client.run(run['id']).wait_for_finish(wait_secs=180)
                logger.info(f"✅ 爬虫完成，状态: {run['status']}")

                if run['status'] != 'SUCCEEDED':
                    logger.error(f"❌ 爬虫任务失败: {run['status']}")
                    update_task(task_id, status='failed', error=f"爬虫任务失败: {run['status']}")
                    return

                # 获取数据
                logger.info("📦 开始获取爬虫数据...")
                dataset_client = apify_client.dataset(run["defaultDatasetId"])
                items = []
                offset = 0
                limit = 1000

                while True:
                    batch = dataset_client.list_items(offset=offset, limit=limit).items
                    if not batch:
                        break
                    items.extend(batch)
                    if len(batch) < limit:
                        break
                    offset += limit

                logger.info(f"✅ 总共获取到 {len(items)} 条数据")
                total_comments = len(items)  # 记录评论数

                if not items:
                    update_task(task_id, status='failed', error="未发现公开评论")
                    return

                # 分批处理评论
                batch_size = 50
                all_results = []
                total_batches = (len(items) + batch_size - 1) // batch_size

                for i in range(0, len(items), batch_size):
                    batch = items[i:i+batch_size]
                    batch_num = i // batch_size + 1

                    update_task(task_id, progress=f'AI 分析中：第 {batch_num}/{total_batches} 批...')
                    logger.info(f"🔄 处理第 {batch_num}/{total_batches} 批（{len(batch)} 条评论）...")

                    batch_content = "\n".join([f"用户{j}: {it.get('text', '')}" for j, it in enumerate(batch)])

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
  {{{{
    "text": "comment text",
    "category": "外挂作弊",
    "sentiment": "负面",
    "language": "英语",
    "analysis": "详细分析内容"
  }}}},
  ...
]

IMPORTANT:
- Output ONLY valid JSON array
- Skip "其他" category
- Use Chinese for category, sentiment, language, and analysis
- Language options (MUST be one of these): 英语, 菲律宾语, 泰语, 越南语, 印尼语, 马来语
- Identify the language accurately based on the text
- Analysis requirements:
  * For short comments (< 30 chars): One sentence summary (15-20 Chinese characters)
  * For medium/long comments (>= 30 chars): Detailed analysis (40-50 Chinese characters)
  * Include: main issue, player emotion, key details
"""

                    result, tokens = call_gemini(batch_prompt, timeout=60)
                    total_tokens += tokens

                    # 解析 JSON 结果
                    try:
                        import json
                        import re
                        clean_result = re.sub(r'```json\\s*|\\s*```', '', result).strip()
                        batch_data = json.loads(clean_result)
                        all_results.extend(batch_data)
                        logger.info(f"✅ 第 {batch_num} 批完成，获得 {len(batch_data)} 条有效结果")
                    except Exception as e:
                        logger.error(f"❌ 第 {batch_num} 批解析失败: {e}")
                        continue

                # 生成 HTML 表格
                update_task(task_id, progress='生成报告...')
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
                LATEST_ANALYSIS_RESULTS[session_id] = all_results
                source_title = f"FB: {url[:15]}..."

            except Exception as e:
                error_msg = f"爬虫任务失败: {str(e)}"
                logger.error(f"❌ {error_msg}")
                logger.error(f"❌ 错误类型: {type(e).__name__}")
                import traceback
                logger.error(f"❌ 完整堆栈:\n{traceback.format_exc()}")

                update_task(task_id, status='failed', error=error_msg)
                return

        else:
            update_task(task_id, status='failed', error="请提供链接或文件")
            return

        # 保存历史记录
        save_history(source_title, result, 'sentiment')

        # 记录使用成本
        if user_id:
            log_usage(user_id, username, department, 'sentiment', total_comments, total_tokens)

        # 任务完成
        update_task(task_id, status='completed', result=result, progress='分析完成！')
        logger.info(f"✅ 任务 {task_id} 完成")

    except Exception as e:
        error_msg = f"系统错误: {str(e)}"
        logger.error(f"❌ 任务 {task_id} 失败: {e}")
        logger.error(f"❌ 错误类型: {type(e).__name__}")
        import traceback
        logger.error(f"❌ 完整堆栈:\n{traceback.format_exc()}")

        update_task(task_id, status='failed', error=error_msg, progress='任务失败')

# ============================================
# 基础路由
# ============================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        try:
            # 从数据库查询用户
            user = db.query_one(
                "SELECT * FROM users WHERE username = %s",
                (username,)
            )

            if user and bcrypt.check_password_hash(user['password_hash'], password):
                session['logged_in'] = True
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['real_name'] = user['real_name']
                session['department'] = user['department']
                session['role'] = user['role']
                session['session_id'] = f"{username}_{int(time.time())}"
                logger.info(f"✅ 用户登录成功: {username} ({user['real_name']})")
                return redirect(url_for('home'))
            else:
                logger.info(f"❌ 登录失败: {username}")
                return render_template('login.html', error='用户名或密码错误')

        except Exception as e:
            logger.error(f"❌ 登录异常: {e}")
            return render_template('login.html', error='系统错误，请稍后重试')

    return render_template('login.html')


@app.route('/logout')
def logout():
    """登出"""
    username = session.get('username', '未知用户')
    session.clear()
    logger.info(f"👋 用户已登出: {username}")
    return redirect(url_for('login'))


@app.route('/')
@login_required
def home():
    """首页"""
    return render_template('index.html', user=session)


@app.route('/dashboard_stats')
@login_required
def dashboard_stats():
    """首页数据看板 API"""
    try:
        current_month = datetime.datetime.now().strftime('%Y-%m')

        # 本月数据
        current_data = db.query_one("""
            SELECT
                COALESCE(SUM(comments_count), 0) as total_comments,
                COUNT(*) as total_analyses,
                COALESCE(SUM(ai_tokens), 0) as total_tokens
            FROM usage_logs
            WHERE TO_CHAR(created_at, 'YYYY-MM') = %s
        """, (current_month,))

        # 上月数据（用于计算增长率）
        last_month = (datetime.datetime.now().replace(day=1) - datetime.timedelta(days=1)).strftime('%Y-%m')
        last_data = db.query_one("""
            SELECT COALESCE(SUM(comments_count), 0) as total_comments
            FROM usage_logs
            WHERE TO_CHAR(created_at, 'YYYY-MM') = %s
        """, (last_month,))

        # 计算增长率
        growth = 0
        if last_data and last_data['total_comments'] > 0:
            growth = ((current_data['total_comments'] - last_data['total_comments']) / last_data['total_comments']) * 100

        return jsonify({
            'comments': int(current_data['total_comments']),
            'analyses': int(current_data['total_analyses']),
            'tokens': int(current_data['total_tokens']),
            'growth': round(growth, 1)
        })

    except Exception as e:
        logger.error(f"❌ 获取数据看板失败: {e}")
        # 返回默认值
        return jsonify({
            'comments': 0,
            'analyses': 0,
            'tokens': 0,
            'growth': 0
        })


@app.route('/debug')
@login_required
def debug_page():
    """调试页面"""
    debug_info = {
        "status": "Online",
        "qwen_key": bool(DASHSCOPE_API_KEY),
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


@app.route('/submit_feedback', methods=['POST'])
def submit_feedback():
    """接收用户反馈并发送邮件"""
    try:
        data = request.json
        project_name = data.get('project_name')
        feedback = data.get('feedback')

        if not project_name or not feedback:
            return jsonify({'error': '请填写完整信息'}), 400

        # 记录到日志
        logger.info(f"📧 收到用户反馈")
        logger.info(f"   项目名称: {project_name}")
        logger.info(f"   反馈内容: {feedback}")

        # 保存到数据库（可选）
        try:
            db.execute("""
                INSERT INTO feedback (user_email, content, created_at)
                VALUES (%s, %s, NOW())
            """, (project_name, feedback))
        except Exception as db_error:
            # 如果表不存在，只记录日志
            logger.warning(f"⚠️ 保存反馈到数据库失败（表可能不存在）: {db_error}")

        # TODO: 发送邮件通知管理员
        # 这里可以集成邮件服务（如 SendGrid, AWS SES）
        # send_email(
        #     to="admin@sailson.com",
        #     subject=f"新用户反馈 - {project_name}",
        #     body=feedback
        # )

        return jsonify({'success': True, 'message': '感谢您的反馈！'})

    except Exception as e:
        logger.error(f"❌ 处理反馈失败: {e}")
        return jsonify({'error': '系统错误，请稍后重试'}), 500


# ============================================
# 功能 1: 舆情分析
# ============================================

@app.route('/sentiment-tool')
@login_required
def sentiment_tool():
    """舆情分析工具页面"""
    return render_template('analysis.html')


@app.route('/analyze', methods=['POST'])
def analyze():
    """舆情分析 API - 异步版本"""
    logger.info("\n" + "=" * 60)
    logger.info("📥 收到舆情分析请求")
    logger.info(f"🔑 DASHSCOPE_API_KEY: {'✅' if DASHSCOPE_API_KEY else '❌'}")
    logger.info(f"🔑 APIFY_TOKEN: {'✅' if APIFY_TOKEN else '❌'}")

    url = request.form.get('url')
    file = request.files.get('file')

    # 生成任务 ID
    task_id = str(uuid.uuid4())
    session_id = session.get('session_id', 'default')

    # 在主线程中提取用户信息（避免线程安全问题）
    user_id = session.get('user_id')
    username = session.get('username', 'unknown')
    department = session.get('department', '未知')

    # 在主线程中读取文件内容（避免跨线程访问 Flask FileStorage 对象）
    file_data = None
    if file:
        try:
            file_data = {
                'filename': file.filename,
                'content': file.read(),  # 读取文件内容到内存
                'content_type': file.content_type
            }
            logger.info(f"📁 已读取文件: {file.filename}, 大小: {len(file_data['content'])} 字节")
        except Exception as e:
            logger.error(f"❌ 读取文件失败: {e}")
            return jsonify({'error': f'读取文件失败: {str(e)}'}), 400

    # 创建任务记录到数据库
    create_task(task_id, user_id, session_id)

    # 启动后台线程处理任务
    thread = threading.Thread(
        target=process_analysis_task,
        args=(task_id, url, file_data, session_id, user_id, username, department)
    )
    thread.daemon = True
    thread.start()

    logger.info(f"✅ 任务 {task_id} 已创建并启动")

    # 立即返回任务 ID
    return jsonify({
        'task_id': task_id,
        'status': 'pending',
        'message': '任务已提交，正在后台处理...'
    })


@app.route('/task_status/<task_id>')
def task_status(task_id):
    """查询任务状态"""
    task = get_task(task_id)

    if not task:
        return jsonify({'error': '任务不存在'}), 404

    return jsonify({
        'status': task['status'],
        'progress': task['progress'],
        'result': task['result'],
        'error': task['error']
    })

# ============================================
# 功能 2: 竞品监控
# ============================================

@app.route('/competitor-tool')
@login_required
def competitor_tool():
    """竞品监控工具页面"""
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
        result, tokens = call_gemini(prompt)

        # 清理 Markdown 代码块标记
        result = result.replace('```html', '').replace('```', '').strip()

        # 保存历史记录
        save_history(f"竞品数据:{target_url[20:30]}", result, 'competitor')

        # 记录使用成本
        if session.get('user_id'):
            log_usage(
                session.get('user_id'),
                session.get('username', 'unknown'),
                session.get('department', '未知'),
                'competitor',
                len(cleaned),  # TikTok 视频数量
                tokens
            )

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
@login_required
def video_tool():
    """视频生成工具页面"""
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
@login_required
def get_history():
    """获取历史记录（从数据库）"""
    try:
        user_id = session.get('user_id')

        # 从数据库读取用户的历史记录
        records = db.query_all("""
            SELECT id, title, result, type, created_at
            FROM analysis_results
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 50
        """, (user_id,))

        # 转换为前端需要的格式
        result = []
        for record in records:
            result.append({
                'id': record['id'],
                'title': f"{record['title']} [{record['created_at'].strftime('%H:%M')}]",
                'result': record['result'],
                'type': record['type']
            })

        return jsonify(result)

    except Exception as e:
        logger.error(f"❌ 获取历史记录失败: {e}")
        # 失败时返回内存中的记录
        return jsonify(HISTORY_DB[::-1])


@app.route('/get_record/<int:id>')
@login_required
def get_record(id):
    """获取单条记录（从数据库）"""
    try:
        user_id = session.get('user_id')

        # 从数据库读取（确保只能读取自己的记录）
        record = db.query_one("""
            SELECT id, title, result, type, created_at
            FROM analysis_results
            WHERE id = %s AND user_id = %s
        """, (id, user_id))

        if record:
            return jsonify({
                'id': record['id'],
                'title': record['title'],
                'result': record['result'],
                'type': record['type']
            })
        else:
            return jsonify({'error': '记录不存在'}), 404

    except Exception as e:
        logger.error(f"❌ 获取记录失败: {e}")
        # 失败时从内存查找
        record = next((x for x in HISTORY_DB if x['id'] == id), None)
        return jsonify(record) if record else jsonify({'error': '记录不存在'}), 404

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

    # 分类顺序和 Sheet 名称映射（去掉特殊字符）
    category_mapping = {
        '外挂作弊': '外挂作弊',
        '游戏优化': '游戏优化',
        '游戏Bug': '游戏Bug',
        '充值退款': '充值退款',
        '新模式/地图/平衡性建议': '新模式地图平衡性建议'  # 去掉斜杠
    }

    # 为每个分类创建 Sheet
    for cat, sheet_name in category_mapping.items():
        if cat not in category_groups:
            continue

        items = category_groups[cat]
        ws = wb.create_sheet(title=sheet_name)

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
@login_required
def export_by_language():
    """按语言导出 Excel"""
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
@login_required
def export_by_category():
    """按分类导出 Excel"""
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
# 统计功能
# ============================================

@app.route('/my-stats')
@login_required
def my_stats():
    """个人统计页面"""
    user_id = session.get('user_id')

    # 获取本月统计数据
    current_month = datetime.datetime.now().strftime('%Y-%m')

    stats_data = db.query_one("""
        SELECT
            COUNT(*) as count,
            COALESCE(SUM(comments_count), 0) as comments,
            COALESCE(SUM(total_cost), 0) as cost
        FROM usage_logs
        WHERE user_id = %s
          AND TO_CHAR(created_at, 'YYYY-MM') = %s
    """, (user_id, current_month))

    stats = {
        'count': stats_data['count'] if stats_data else 0,
        'comments': stats_data['comments'] if stats_data else 0,
        'cost': float(stats_data['cost']) if stats_data else 0.0,
        'avg_cost': float(stats_data['cost']) / stats_data['count'] if stats_data and stats_data['count'] > 0 else 0.0
    }

    # 获取最近20条使用记录
    logs = db.query_all("""
        SELECT *
        FROM usage_logs
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 20
    """, (user_id,))

    return render_template('my_stats.html', stats=stats, logs=logs, user=session)


@app.route('/admin')
@admin_required
def admin_panel():
    """管理后台页面"""
    current_month = datetime.datetime.now().strftime('%Y-%m')

    # 全局统计
    global_data = db.query_one("""
        SELECT
            COALESCE(SUM(total_cost), 0) as total_cost,
            COUNT(DISTINCT user_id) as active_users,
            COUNT(*) as total_count
        FROM usage_logs
        WHERE TO_CHAR(created_at, 'YYYY-MM') = %s
    """, (current_month,))

    global_stats = {
        'total_cost': float(global_data['total_cost']) if global_data else 0.0,
        'active_users': global_data['active_users'] if global_data else 0,
        'total_count': global_data['total_count'] if global_data else 0,
        'avg_cost': float(global_data['total_cost']) / global_data['total_count'] if global_data and global_data['total_count'] > 0 else 0.0
    }

    # 部门统计
    dept_stats = db.query_all("""
        SELECT
            department,
            COALESCE(SUM(total_cost), 0) as cost,
            COUNT(*) as count
        FROM usage_logs
        WHERE TO_CHAR(created_at, 'YYYY-MM') = %s
        GROUP BY department
        ORDER BY cost DESC
    """, (current_month,))

    # 用户统计（Top 10）
    user_stats = db.query_all("""
        SELECT
            u.real_name,
            u.department,
            COALESCE(SUM(l.total_cost), 0) as cost,
            COUNT(l.id) as count
        FROM users u
        LEFT JOIN usage_logs l ON u.id = l.user_id
            AND TO_CHAR(l.created_at, 'YYYY-MM') = %s
        GROUP BY u.id, u.real_name, u.department
        ORDER BY cost DESC
        LIMIT 10
    """, (current_month,))

    # 所有用户列表
    all_users = db.query_all("""
        SELECT * FROM users ORDER BY created_at DESC
    """)

    return render_template('admin.html',
                         global_stats=global_stats,
                         dept_stats=dept_stats,
                         user_stats=user_stats,
                         all_users=all_users,
                         user=session)


@app.route('/admin/add_user', methods=['POST'])
@admin_required
def add_user():
    """添加新用户"""
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')
        real_name = data.get('real_name')
        department = data.get('department')
        role = data.get('role', 'user')

        # 检查用户名是否已存在
        existing = db.query_one("SELECT id FROM users WHERE username = %s", (username,))
        if existing:
            return jsonify({'error': '用户名已存在'}), 400

        # 加密密码
        password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

        # 插入用户
        db.execute("""
            INSERT INTO users (username, password_hash, real_name, department, role)
            VALUES (%s, %s, %s, %s, %s)
        """, (username, password_hash, real_name, department, role))

        logger.info(f"✅ 管理员添加新用户: {username} ({real_name})")

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"❌ 添加用户失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/admin/delete_user/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_user(user_id):
    """删除用户"""
    try:
        # 不允许删除管理员账号
        user = db.query_one("SELECT username FROM users WHERE id = %s", (user_id,))
        if user and user['username'] == 'admin':
            return jsonify({'error': '不能删除管理员账号'}), 403

        # 删除用户
        db.execute("DELETE FROM users WHERE id = %s", (user_id,))

        logger.info(f"✅ 管理员删除用户: ID={user_id}")

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"❌ 删除用户失败: {e}")
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
