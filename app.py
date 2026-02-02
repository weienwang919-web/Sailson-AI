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

if GOOGLE_API_KEY:
    print(f"✅ API Key 已加载: {GOOGLE_API_KEY[:5]}******")
    genai.configure(api_key=GOOGLE_API_KEY)
else:
    print("❌ 警告: 未找到 GOOGLE_API_KEY")

app = Flask(__name__)
app.secret_key = 'sailson_secure_key'
HISTORY_DB = []

# --- 2. 核心工具 ---
def call_gemini(prompt, image=None):
    if not GOOGLE_API_KEY: return "❌ 错误：API Key 未配置"
    models_to_try = ['models/gemini-2.5-flash', 'gemini-2.5-flash', 'models/gemini-1.5-flash', 'gemini-pro']
    for model_name in models_to_try:
        try:
            print(f"🤖 尝试连接: {model_name}")
            model = genai.GenerativeModel(model_name)
            if image and 'pro' in model_name and 'flash' not in model_name: continue
            response = model.generate_content([prompt, image]) if image else model.generate_content(prompt)
            print(f"✅ {model_name} 成功！")
            return response.text
        except: continue
    return "⚠️ 所有模型失败"

def process_uploaded_file(file):
    try:
        fname = file.filename.lower()
        if fname.endswith(('.png', '.jpg', '.jpeg', '.webp')): return "IMAGE", Image.open(file)
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
    # ... (舆情逻辑不变，省略以节省篇幅，用之前的即可) ...
    url = request.form.get('url'); file = request.files.get('file')
    content=""; img=None; source="未知"; source_link_text="本地文件"
    if file:
        mode, res = process_uploaded_file(file)
        if mode=="ERROR": return jsonify({'result': res})
        if mode=="IMAGE": img=res; content="分析图片"; source="📷 图片"; source_link_text="上传文件"
        else: content=res; source="📁 文件"; source_link_text="上传文件"
    elif url:
        content = """[1] 举报也没用，透视挂满大街都是。[2] 充值扣了钱点券没到账。[3] 服务器太卡了。[4] 皮肤抽奖概率太坑。[5] 建议出练习模式。"""
        source = f"🔗 {url[:20]}..."; source_link_text = url
    else: return jsonify({'result': "❌ 无输入"})
    
    prompt = f"""
    你是一个严格的数据处理引擎。分析评论：{content}
    【指令】：只输出 HTML 表格，无废话。
    【规则】：只选 [外挂作弊] [游戏优化] [BUG] [充值] [建议] [其他]。
    【表头】：来源({source_link_text})、原句、归类、情感、简要分析(15字内)。
    """
    res = call_gemini(prompt, img).replace('```html','').replace('```','')
    save_history(source, res, 'sentiment')
    return jsonify({'result': res})

@app.route('/competitor-tool')
def competitor_tool(): return render_template('competitor.html') if session.get('logged_in') else redirect(url_for('login'))
@app.route('/monitor_competitors', methods=['POST'])
def monitor_competitors(): 
    res = call_gemini(f"分析竞品: {request.json}").replace('```html','').replace('```','')
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

# ✨ 新增：需求提交路由
@app.route('/feature-request')
def feature_request():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template('request.html')

@app.route('/submit_feature_request', methods=['POST'])
def submit_feature_request():
    data = request.json
    # 存入历史记录，方便后台查看
    summary = f"需求: {data.get('toolType')} - {data.get('project')}"
    detail = f"业务流程: {data.get('workflow')}"
    save_history(summary, detail, 'request') # type='request'
    return jsonify({'status': 'success'})

@app.route('/get_history')
def get_history(): return jsonify(HISTORY_DB[::-1])
@app.route('/get_record/<int:id>')
def get_record(id): return jsonify(next((x for x in HISTORY_DB if x['id']==id), None))

if __name__ == '__main__': 
    # 获取云平台分配的端口，如果没有则默认 5001
    port = int(os.environ.get("PORT", 5001))
    # host='0.0.0.0' 是允许外网访问的关键！
    app.run(debug=False, host='0.0.0.0', port=port)