import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))

print("🔍 正在检索您的可用模型列表...")
try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"✅ 可用模型: {m.name}")
except Exception as e:
    print(f"❌ 无法获取列表，请检查网络或 API Key。原因: {e}")