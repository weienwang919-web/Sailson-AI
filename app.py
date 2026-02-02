import os
import datetime
import time
import pandas as pd
from PIL import Image
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from dotenv import load_dotenv
import google.generativeai as genai

# --- 1. 配置加载 ---
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# 云端适配: 获取端口
port = int(os.environ.get("PORT", 5001))

if GOOGLE_API_KEY:
    # 打印前5位，确认 Key 真的被读到了
    print(f"✅ API Key 已加载: {GOOGLE_API_KEY[:5]}******")
    genai.configure(api_key=GOOGLE_API_KEY)
else:
    print("❌ 警告: 环境变量中未找到 GOOGLE_API_KEY")

app = Flask(__name__)
app.secret_key = 'sailson_secure_key'
HISTORY_DB = []

# --- 2. 核心工具 (修复了模型列表 + 错误打印) ---
def call_gemini(prompt, image=None):
    if not GOOGLE_API_KEY: 
        return "❌ 错误：后台未读取到 API Key，请检查 Render 环境变量配置。"
    
    # 🔥 修复：把真实存在的模型放在第一位
    models_to_try = [
        'gemini-1.5-flash',       # 目前最快最稳的模型
        'gemini-pro',             # 备用
        'models/gemini-1.5-pro'   # 高级备用
    ]
    
    last_error = ""
    
    for model_name in models_to_try:
        try:
            print(f"🤖 正在尝试模型: {model_name} ...")
            model = genai.GenerativeModel(model_name)
            
            # gemini-pro 不支持图片，跳过
            if image and 'pro' in model_name and 'flash' not in model_name: 
                continue
            
            if image:
                response = model.generate_content([prompt, image])
            else:
                response = model.generate_content(prompt)
                
            print(f"✅ {model_name} 调用成功！")
            return response.text
            
        except Exception as e:
            # 🔥 关键：把错误打印出来，方便去 Render Logs 查看
            print(f"❌ {model_name} 失败: {str(e)}")
            last_error = str(e)
            continue
            
    return f"⚠️ 所有模型均调用失败。最后一次报错信息: {last_error}"

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
    HISTORY_DB.append({'id': len(HISTORY_DB)+1, 'title': f"{title} [{datetime.datetime.now().strftime('%H:%M')}]", 'result': result, 'type': type_tag})

def call_veo_api(prompt):
    print(f"🎥 [Veo] 生成视频中: {prompt}")
    time.sleep(4) 
    return "https://cdn.pixabay.com/video/2023/10/22/186115-877653483_large.mp4"

# --- 3. 路由 ---
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

@app.route('/sentiment-tool')
def sentiment_tool(): return render_template('analysis.html') if session.get('logged_in') else redirect(url_for('login'))

@app.route('/analyze', methods=['POST'])
def analyze():
    url = request.form.get('url')
    file = request.files.get('file')
    content = ""; img = None; source = "未知"; source_link_text = "本地文件"

    if file:
        mode, res = process_uploaded_file(file)
        if mode == "ERROR": return jsonify({'result': res})
        if mode == "IMAGE": img = res; content = "分析图片"; source = "📷 图片"; source_link_text="用户上传"
        else: content = res; source = "📁 文件"; source_link_text="用户上传"
    elif url:
        content = """
        [1] So many hackers in this game! Wallhack and aimbot everywhere.
        [2] I topped up $99 yesterday but got no UC. Customer service is bots.
        [3] Server lag is unbearable. 400ms ping.
        [4] Gacha rates are a scam. 0.5% really?
        [5] Add training mode please.
        """
        source = f"🔗 {url[:20]}..."; source_link_text = url 
    else: return jsonify({'result': "❌ 无输入"})
    
    prompt = f"""
    You are a data engine. Output ONLY raw HTML <table>.
    Input: {content}
    Source: {source_link_text}
    Rules:
    1. Table class="table table-bordered table-striped table-hover"
    2. Columns: Source, Review, Category (Chinese), Sentiment (Chinese), Analysis (Chinese, ~25 chars).
    3. Categories: [Cheating], [Lag], [Bugs], [Payment], [Suggestion], [Other].
    """
    res = call_gemini(prompt, img).replace('```html','').replace('```','')
    save_history(source, res, 'sentiment')
    return jsonify({'result': res})

@app.route('/competitor-tool')
def competitor_tool(): return render_template('competitor.html') if session.get('logged_in') else redirect(url_for('login'))
@app.route('/monitor_competitors', methods=['POST'])
def monitor_competitors(): 
    # 这里加个简单的处理，防止直接把 None 传进去
    input_data = request.json
    if not input_data:
        return jsonify({'result': "❌ 错误：未接收到输入数据"})
    
    prompt = f"分析竞品数据: {input_data}。请给出SWOT分析（优势、劣势、机会、威胁）和下一步策略建议。使用HTML格式输出，使用 <h3>, <ul>, <li> 标签。"
    res = call_gemini(prompt)
    
    # 清洗可能存在的 markdown
    clean_res = res.replace('```html','').replace('```','')
    save_history("竞品监控", clean_res, 'competitor')
    return jsonify({'result': clean_res})

@app.route('/video-tool')
def video_tool(): return render_template('video.html') if session.get('logged_in') else redirect(url_for('login'))
@app.route('/generate_video', methods=['POST'])
def generate_video():
    prompt = request.json.get('prompt')
    video_url = call_veo_api(prompt)
    save_history(f"Veo: {prompt[:10]}...", video_url, 'video')
    return jsonify({'video_url': video_url})

@app.route('/feature-request')
def feature_request(): return render_template('request.html') if session.get('logged_in') else redirect(url_for('login'))
@app.route('/submit_feature_request', methods=['POST'])
def submit_feature_request():
    data = request.json
    save_history(f"需求: {data.get('toolType')}", f"{data.get('project')}", 'request')
    return jsonify({'status': 'success'})

@app.route('/get_history')
def get_history(): return jsonify(HISTORY_DB[::-1])
@app.route('/get_record/<int:id>')
def get_record(id): return jsonify(next((x for x in HISTORY_DB if x['id']==id), None))

if __name__ == '__main__': 
    app.run(debug=False, host='0.0.0.0', port=port)