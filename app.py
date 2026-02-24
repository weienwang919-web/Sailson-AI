import os

# 清除任何可能存在的代理设置（Render 云端不需要代理）
# 本地开发时，请通过系统环境变量或终端设置代理，不要在代码中硬编码
if os.getenv('HTTP_PROXY'):
    del os.environ['HTTP_PROXY']
if os.getenv('HTTPS_PROXY'):
    del os.environ['HTTPS_PROXY']
import datetime
import time
import pandas as pd
from PIL import Image
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from dotenv import load_dotenv
import google.generativeai as genai
from apify_client import ApifyClient # 导入 Apify 客户端

# --- 1. 配置加载 ---
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
APIFY_TOKEN = os.getenv('APIFY_TOKEN')
port = int(os.environ.get("PORT", 5001))

# 初始化 AI 引擎
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

# 初始化爬虫引擎
apify_client = ApifyClient(APIFY_TOKEN) if APIFY_TOKEN else None

app = Flask(__name__)
app.secret_key = 'sailson_secure_key'
HISTORY_DB = []

# --- 2. 核心工具函数 ---

def call_gemini(prompt, image=None):
    if not GOOGLE_API_KEY:
        print("❌ 错误：GOOGLE_API_KEY 未配置")
        return "❌ 错误：API Key 未配置。"

    # 优先尝试 gemini-1.5-flash-latest，若环境不支持可改为 1.5-flash
    model_name = 'gemini-2.5-flash'

    try:
        print(f"🤖 正在调用模型: {model_name} ...")
        print(f"🔑 API Key 前缀: {GOOGLE_API_KEY[:10]}...")
        model = genai.GenerativeModel(model_name)

        if image:
            response = model.generate_content([prompt, image])
        else:
            response = model.generate_content(prompt)

        print("✅ 模型调用成功")
        return response.text

    except Exception as e:
        error_msg = f"⚠️ 模型调用失败。原因: {str(e)}"
        print(f"❌ Gemini API 错误: {str(e)}")
        return error_msg

def process_uploaded_file(file):
    try:
        fname = file.filename.lower()
        if fname.endswith(('.png', '.jpg', '.jpeg', '.webp')): 
            return "IMAGE", Image.open(file)
        if fname.endswith(('.xlsx', '.csv')): 
            df = pd.read_csv(file) if fname.endswith('.csv') else pd.read_excel(file)
            return "TEXT", df.to_string(index=False, max_rows=50)
        return "ERROR", "不支持的文件格式"
    except Exception as e: return "ERROR", str(e)

def save_history(title, result, type_tag):
    HISTORY_DB.append({
        'id': len(HISTORY_DB)+1, 
        'title': f"{title} [{datetime.datetime.now().strftime('%H:%M')}]", 
        'result': result, 
        'type': type_tag
    })

def call_veo_api(prompt):
    time.sleep(3) 
    return "https://cdn.pixabay.com/video/2023/10/22/186115-877653483_large.mp4"

# --- 3. 基础路由 ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method=='POST' and request.form['username']=='admin' and request.form['password']=='123456':
        session['logged_in'] = True
        return redirect(url_for('home'))
    return render_template('login.html')

@app.route('/logout')
def logout(): session.pop('logged_in', None); return redirect(url_for('login'))

@app.route('/')
def home(): return render_template('index.html') if session.get('logged_in') else redirect(url_for('login'))

@app.route('/debug')
def debug_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return jsonify({
        "status": "Online",
        "gemini_key": bool(GOOGLE_API_KEY),
        "apify_key": bool(APIFY_TOKEN)
    })

# === 4. 核心业务功能 ===

# --- 工具 1：舆情分析 (对接 Facebook 爬虫) ---
@app.route('/sentiment-tool')
def sentiment_tool(): 
    return render_template('analysis.html') if session.get('logged_in') else redirect(url_for('login'))

@app.route('/analyze', methods=['POST'])
def analyze():
    print("=" * 50)
    print("📥 收到分析请求")
    print(f"🔑 GOOGLE_API_KEY 状态: {'已配置' if GOOGLE_API_KEY else '未配置'}")
    print(f"🔑 APIFY_TOKEN 状态: {'已配置' if APIFY_TOKEN else '未配置'}")

    url = request.form.get('url')
    file = request.files.get('file')
    content = ""; img = None; source_title = "未知"

    # 路径 A：文件上传分析
    if file:
        mode, res = process_uploaded_file(file)
        if mode == "ERROR": return jsonify({'result': res})
        if mode == "IMAGE": 
            img = res
            content = "分析图片中的反馈内容"
        else: 
            content = res
        source_title = f"文件: {file.filename[:15]}"
    
    # 路径 B：社交媒体链接抓取分析
    elif url:
        print(f"🔗 处理链接: {url}")
        if not apify_client:
            print("❌ APIFY_TOKEN 未配置")
            return jsonify({'result': "❌ 错误：APIFY_TOKEN 未在环境变量中配置"})

        try:
            print(f"🕵️ 启动云端抓取: {url}")
            # 调用 Facebook Comments Scraper (支持无需 Cookie 的公开抓取测试)
            run_input = { "startUrls": [{ "url": url }], "maxComments": 20 }
            run = apify_client.actor("apify/facebook-comments-scraper").call(run_input=run_input)

            # 提取评论文本并合并
            items = apify_client.dataset(run["defaultDatasetId"]).list_items().items
            content = "\n".join([f"用户{i}: {it.get('text','')}" for i, it in enumerate(items)])
            source_title = f"FB: {url[:15]}..."
            print(f"✅ 抓取成功，获得 {len(items)} 条评论")

            if not content:
                print("⚠️ 未发现公开评论")
                return jsonify({'result': "⚠️ 抓取成功但未发现公开评论，请检查链接权限。"})

        except Exception as e:
            print(f"❌ 抓取失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({'result': f"❌ 抓取任务失败: {str(e)}"})
    else:
        print("❌ 未提供链接或文件")
        return jsonify({'result': "❌ 错误：请提供链接或文件"})

    # --- 核心修改：定义级 Prompt 约束 ---
    prompt = f"""
    You are a Senior Game Operations Data Scientist. Analyze the player feedback provided and output ONLY a raw HTML <table>.
    
    【Input Data】:
    {content}

    【STRICT Categorization Rules (CRITICAL)】:
    You MUST assign each review to EXACTLY ONE of the following 6 categories. Output ONLY the Chinese term.
    
    1. 外挂作弊: Any mention of hackers, aimbots, wallhacks, cheating, or scripts.
    2. 游戏优化: Issues related to lag, high ping, server disconnects, FPS drops, or crashes.
    3. 游戏Bug: Technical glitches in gameplay, stuck in textures, UI errors, or broken mechanics.
    4. 充值退款: Missing rewards (including leaderboard/event rewards), payment issues, shop errors, or refund requests.
    5. 玩家建议: Requests for new content, map changes, balance adjustments, or new game modes.
    6. 其他: Generic praise, insults without specific context, greetings, or irrelevant spam.

    【Output Format】:
    - Return ONLY the raw HTML <table> with class "table table-hover". No markdown code blocks.
    - Columns: 
        1. 来源 (Source)
        2. 原始评论 (Original Review)
        3. 归类 (Category - MUST use the 6 Chinese terms above)
        4. 情感倾向 (Sentiment - 正面/负面/中性)
        5. 简要分析 (Analysis - Concise Chinese insight)
    """

    print("🤖 开始调用 Gemini API...")
    res = call_gemini(prompt, img)
    print(f"📤 Gemini 返回结果长度: {len(res)} 字符")

    res = res.replace('```html','').replace('```','')
    save_history(source_title, res, 'sentiment')
    print("✅ 分析完成")
    print("=" * 50)
    return jsonify({'result': res})


# --- 工具 2：竞品监控 (对接 TikTok 爬虫) ---
@app.route('/competitor-tool')
def competitor_tool(): 
    return render_template('competitor.html') if session.get('logged_in') else redirect(url_for('login'))

# --- 工具 2：竞品监控 (针对 Apify 真实字段优化版) ---
# --- app.py 中的 monitor_competitors 路由升级版 ---

# --- 工具 2：竞品监控 (去噪 + 锁定中文 + 宽屏版) ---
@app.route('/monitor_competitors', methods=['POST'])
def monitor_competitors():
    data = request.json
    target_url = data.get('competitor_name')
    start_dt_str = data.get('startDate') # 2026-02-01
    end_dt_str = data.get('endDate')     # 2026-02-07
    
    try:
        # 1. 物理日期转换：确保 2026 年时区比对 100% 准确
        target_start = datetime.datetime.strptime(start_dt_str, '%Y-%m-%d').date()
        target_end = datetime.datetime.strptime(end_dt_str, '%Y-%m-%d').date()
        
        print(f"📱 启动精准时段探测: {target_url} ({target_start} ~ {target_end})")
        
        # 2. 云端同步：通过 oldestPostDate 初步截断
        run_input = { 
            "profiles": [target_url], 
            "resultsPerPage": 35,
            "oldestPostDate": start_dt_str, 
            "shouldDownloadVideos": False
        }
        run = apify_client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
        items = apify_client.dataset(run["defaultDatasetId"]).list_items().items
        
        # 3. 严格本地滤网：使用 datetime 对象进行双向物理剔除
        cleaned = []
        for it in items:
            raw_date = it.get("createTimeISO")
            if not raw_date: continue
            
            # 转化为本地日期对象，彻底过滤掉 7 号之后的数据
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

        if not cleaned:
            return jsonify({'result': f"<div class='alert alert-warning'>在此期间 ({start_dt_str} ~ {end_dt_str}) 未发现视频。</div>"})

        # 4. 🔥 视觉保险：通过“HTML 骨架”锁定 UI 呈现
        prompt = f"""
        You are a Data Entry Assistant. Please fill the following TikTok data into the PROVIDED HTML TEMPLATE.
        
        【Data Source】: {cleaned}
        【Period】: {start_dt_str} to {end_dt_str}

        【STRICT TEMPLATE (Use this EXACT structure)】:
        <div style="width:100%; font-family:sans-serif;">
            <h3 style="color:#D32F2F; border-bottom:2px solid #eee; padding-bottom:10px;">📊 数据概览表 ({start_dt_str} 至 {end_dt_str})</h3>
            <table class="table" style="width:100%; margin-bottom:30px; text-align:center;">
                <tr style="background:#f8f9fa;">
                    <th>视频总数</th><th>总点赞</th><th>总播放</th><th>平均互动率</th>
                </tr>
                <tr>
                    <td>[视频总数]</td><td>[总点赞]</td><td>[总播放]</td><td>[总互动率]%</td>
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
        - 严禁添加模板之外的任何文字（包括分析、建议、前言、结语）。
        - 仅输出 Raw HTML 代码，禁止 Markdown 代码块。
        """
        
        res = call_gemini(prompt).replace('```html','').replace('```','')
        save_history(f"竞品数据:{target_url[20:30]}", res, 'competitor')
        return jsonify({'result': res})
        
    except Exception as e:
        return jsonify({'result': f"❌ 监控失败: {str(e)}"})

# --- 5. 其他功能 (保持不变) ---

@app.route('/video-tool')
def video_tool(): return render_template('video.html') if session.get('logged_in') else redirect(url_for('login'))

@app.route('/generate_video', methods=['POST'])
def generate_video():
    prompt = request.json.get('prompt')
    video_url = call_veo_api(prompt)
    save_history(f"视频: {prompt[:10]}", video_url, 'video')
    return jsonify({'video_url': video_url})

@app.route('/get_history')
def get_history(): return jsonify(HISTORY_DB[::-1])

@app.route('/get_record/<int:id>')
def get_record(id): return jsonify(next((x for x in HISTORY_DB if x['id']==id), None))

if __name__ == '__main__': 
    app.run(debug=False, host='0.0.0.0', port=port)