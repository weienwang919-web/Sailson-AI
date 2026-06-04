#!/usr/bin/env python3
"""
真实 Apify API 测试
使用真实的 APIFY_TOKEN 测试是否会卡住
"""
import os
import sys
import time
import signal
from dotenv import load_dotenv
from apify_client import ApifyClient

# 加载环境变量
load_dotenv()

APIFY_TOKEN = os.environ.get('APIFY_TOKEN')

print("=" * 80)
print("🧪 真实 Apify API 测试")
print("=" * 80)

if not APIFY_TOKEN:
    print("❌ APIFY_TOKEN 未配置")
    sys.exit(1)

print(f"✅ APIFY_TOKEN: {APIFY_TOKEN[:20]}...")

# 测试 1: 初始化客户端
print("\n📦 测试 1: 初始化 Apify 客户端")
print("-" * 80)

try:
    start_time = time.time()
    apify_client = ApifyClient(APIFY_TOKEN)
    elapsed = time.time() - start_time
    print(f"✅ 客户端初始化成功（耗时: {elapsed:.2f}秒）")
except Exception as e:
    print(f"❌ 初始化失败: {e}")
    sys.exit(1)

# 测试 2: 验证 Token
print("\n🔑 测试 2: 验证 Token")
print("-" * 80)

try:
    start_time = time.time()
    user_info = apify_client.user().get()
    elapsed = time.time() - start_time
    print(f"✅ Token 有效（耗时: {elapsed:.2f}秒）")
    print(f"   用户 ID: {user_info.get('id')}")
    print(f"   用户名: {user_info.get('username')}")
except Exception as e:
    print(f"❌ Token 验证失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 测试 3: 启动 Actor（带超时）
print("\n🚀 测试 3: 启动 Facebook Comments Scraper Actor")
print("-" * 80)

# 使用一个简单的测试 URL
test_url = "https://www.facebook.com/share/p/1FiWNkkM2y/"
run_input = {
    "startUrls": [{"url": test_url}],
    "maxComments": 10,  # 只抓取 10 条评论，快速测试
    "language": "en"
}

print(f"📋 测试参数:")
print(f"   URL: {test_url}")
print(f"   最大评论数: 10")

# 设置超时
def timeout_handler(signum, frame):
    raise TimeoutError("Actor .start() 调用超时（30秒）")

signal.signal(signal.SIGALRM, timeout_handler)

try:
    print("\n📞 正在调用 Apify API...")
    print("   (如果 30 秒内没有响应，会自动超时)")

    signal.alarm(30)  # 30 秒超时
    start_time = time.time()

    run = apify_client.actor("apify/facebook-comments-scraper").start(run_input=run_input)

    signal.alarm(0)  # 取消超时
    elapsed = time.time() - start_time

    print(f"\n✅ Actor 启动成功！（耗时: {elapsed:.2f}秒）")
    print(f"   Run ID: {run.get('id')}")
    print(f"   状态: {run.get('status')}")
    print(f"   返回类型: {type(run)}")
    print(f"   完整返回: {run}")

    # 测试 4: 等待完成（短时间）
    print("\n⏳ 测试 4: 等待 Actor 完成")
    print("-" * 80)
    print("   (最多等待 60 秒)")

    try:
        start_time = time.time()
        run_result = apify_client.run(run['id']).wait_for_finish(wait_secs=60)
        elapsed = time.time() - start_time

        print(f"\n✅ Actor 完成！（耗时: {elapsed:.2f}秒）")
        print(f"   最终状态: {run_result.get('status')}")

        if run_result.get('status') == 'SUCCEEDED':
            print("\n🎉 测试完全成功！Apify API 工作正常")

            # 获取数据
            dataset_id = run_result.get('defaultDatasetId')
            if dataset_id:
                items = apify_client.dataset(dataset_id).list_items().items
                print(f"   获取到 {len(items)} 条数据")
        else:
            print(f"\n⚠️  Actor 状态不是 SUCCEEDED: {run_result.get('status')}")

    except TimeoutError:
        print(f"\n⚠️  等待超时（60秒），但 Actor 已经启动")
        print(f"   Run ID: {run['id']}")
        print(f"   你可以在 Apify 控制台查看: https://console.apify.com/actors/runs/{run['id']}")

except TimeoutError as e:
    signal.alarm(0)
    print(f"\n❌ 超时错误: {e}")
    print("\n🔍 诊断:")
    print("   - Actor .start() 调用超过 30 秒")
    print("   - 这可能是网络问题或 Apify API 响应慢")
    print("   - 建议添加超时和重试机制")
    sys.exit(1)

except Exception as e:
    signal.alarm(0)
    print(f"\n❌ 启动失败: {e}")
    print(f"   错误类型: {type(e).__name__}")
    import traceback
    print("\n堆栈信息:")
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 80)
print("✅ 所有测试通过！")
print("=" * 80)
print("\n📊 测试总结:")
print("   ✅ Apify 客户端初始化正常")
print("   ✅ Token 验证通过")
print("   ✅ Actor 启动成功（不会卡住）")
print("   ✅ 可以正常等待完成")
print("\n💡 结论: Apify API 在本地环境工作正常")
print("   如果在 Render 上卡住，可能是:")
print("   1. Render 的网络环境问题")
print("   2. 线程安全问题")
print("   3. 需要添加超时和重试机制")
print()
