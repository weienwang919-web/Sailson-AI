import google.generativeai as genai

# 👇 这里填入你的真实 Key
api_key = "AIzaSyD_K8PfPsd6pcCXPyffU-NSs2kTIfOhruo"

genai.configure(api_key=api_key)

print("正在向 Google 询问你的账号能用哪些模型...")

try:
    # 列出所有支持生成的模型
    available_models = []
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"✅ 发现可用模型: {m.name}")
            available_models.append(m.name)

    if not available_models:
        print("❌ 连接成功，但没有发现可用模型。可能是 API Key 权限问题。")
    else:
        print("\n🎉 成功！请告诉我上面列出了哪些名字，我们选一个填进去就行！")

except Exception as e:
    print(f"\n❌ 查询出错: {e}")
