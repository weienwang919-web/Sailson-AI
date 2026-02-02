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
port = int(os.environ.get("PORT", 5001))

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

app = Flask(__name__)
app.secret_key = 'sailson_secure_key'
HISTORY_DB = []

# --- 2. 核心工具 ---
def call_gemini(prompt, image=None):
    if not GOOGLE_API_KEY: 
        return "❌ 错误：API Key 未配置。"

    # 🔥 核心修改：使用你诊断列表里确认存在的模型
    # models/gemini-2.5-flash 是目前列表里最适合生产环境的
    model_name = 'models/gemini-2.5-flash'
    
    try:
        print(f"🤖 正在调用模型: {model_name} ...")
        model = genai.GenerativeModel(model_name)
        
        if image:
            # Gemini 2.5 Flash 完美支持图片，直接传！
            response = model.generate_content([prompt, image])
        else:
            response = model.generate_content(prompt)
            
        return response.text
        
    except Exception as e:
        # 如果 2.5 偶尔失败，打印具体原因
        return f"⚠️ 模型调用失败 ({model_name})。原因: {str(e)}"

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
    time.sleep(3) 
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

# 诊断页面 (保留着，以后查问题方便)
@app.route('/debug')
def debug_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return jsonify({
        "status": "Online",
        "current_model": "models/gemini-2.5-flash",
        "key_configured": bool(GOOGLE_API_KEY)
    })

# === 业务功能 ===
@app.route('/sentiment-tool')
def sentiment_tool(): return render_template('analysis.html') if session.get('logged_in') else redirect(url_for('login'))

@app.route('/analyze', methods=['POST'])
def analyze():
    url = request.form.get('url'); file = request.files.get('file')
    content = ""; img = None; source = "未知"; source_link_text = "本地文件"
    if file:
        mode, res = process_uploaded_file(file)
        if mode == "ERROR": return jsonify({'result': res})
        if mode == "IMAGE": img = res; content = "分析图片"; source = "📷 图片"; source_link_text="用户上传"
        else: content = res; source = "📁 文件"; source_link_text="用户上传"
    elif url:
        content = "[1] So many hackers! [2] Lag is bad. [3] Refund please."; source = f"🔗 {url[:20]}..."; source_link_text = url 
    else: return jsonify({'result': "❌ 无输入"})
    
    prompt = f"""
    Output ONLY raw HTML <table>. Input: {content}. Source: {source_link_text}.
    Columns: Source, Review, Category (Chinese), Sentiment (Chinese), Analysis (Chinese).
    Categories: [Cheating], [Lag], [Bugs], [Payment], [Other].
    """
    res = call_gemini(prompt, img).replace('```html','').replace('```','')
    save_history(source, res, 'sentiment')
    return jsonify({'result': res})

@app.route('/competitor-tool')
def competitor_tool(): return render_template('competitor.html') if session.get('logged_in') else redirect(url_for('login'))
@app.route('/monitor_competitors', methods=['POST'])
def monitor_competitors(): 
    input_data = request.json
    if not input_data: return jsonify({'result': "❌ 错误：请输入竞品名称"})
    prompt = f"分析竞品 '{input_data}'。使用HTML格式(<h3>,<ul>)列出优势、劣势和对策。"
    res = call_gemini(prompt).replace('```html','').replace('```','')
    save_history("竞品监控", res, 'competitor')
    return jsonify({'result': res})

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