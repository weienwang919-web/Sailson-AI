#!/usr/bin/env python3
"""
测试 Apify REST API 轮询机制
使用真实的 API 验证轮询逻辑
"""
import os
import sys
import time
import requests
from dotenv import load_dotenv

load_dotenv()

APIFY_TOKEN = os.environ.get('APIFY_TOKEN')

print("=" * 80)
print("🧪 测试 Apify REST API 轮询机制")
print("=" * 80)

if not APIFY_TOKEN:
    print("❌ APIFY_TOKEN 未配置")
    sys.exit(1)

# 步骤 1: 启动任务
print("\n📞 步骤 1: 启动 Apify 任务...")
run_input = {
    "startUrls": [{"url": "https://www.facebook.com/share/p/1FiWNkkM2y/"}],
    "maxComments": 10,  # 只抓 10 条，快速测试
    "maxPostCount": 1
}

try:
    response = requests.post(
        "https://api.apify.com/v2/acts/apify~facebook-comments-scraper/runs",
        json=run_input,
        headers={
            "Authorization": f"Bearer {APIFY_TOKEN}",
            "Content-Type": "application/json"
        },
        timeout=30
    )

    if response.status_code != 201:
        print(f"❌ 启动失败: {response.status_code}")
        print(response.text)
        sys.exit(1)

    run = response.json()['data']
    run_id = run['id']
    print(f"✅ 任务已启动，Run ID: {run_id}")

except Exception as e:
    print(f"❌ 启动失败: {e}")
    sys.exit(1)

# 步骤 2: 轮询任务状态
print("\n📡 步骤 2: 轮询任务状态...")
start_time = time.time()
max_wait_time = 120  # 最多等待 2 分钟
poll_interval = 5  # 每 5 秒轮询一次

api_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
headers = {"Authorization": f"Bearer {APIFY_TOKEN}"}

try:
    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait_time:
            print(f"❌ 超时（{max_wait_time}秒）")
            break

        # 轮询状态
        print(f"   轮询... (已等待 {elapsed:.0f}秒)")
        response = requests.get(api_url, headers=headers, timeout=10)

        if response.status_code != 200:
            print(f"❌ 获取状态失败: {response.status_code}")
            break

        run_data = response.json()['data']
        status = run_data['status']
        print(f"   当前状态: {status}")

        if status in ['SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT']:
            # 任务完成
            elapsed = time.time() - start_time
            print(f"\n✅ 任务完成！")
            print(f"   最终状态: {status}")
            print(f"   总耗时: {elapsed:.1f}秒")

            if status == 'SUCCEEDED':
                # 步骤 3: 获取数据
                print("\n📦 步骤 3: 获取数据...")
                dataset_id = run_data.get('defaultDatasetId')
                if dataset_id:
                    dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
                    response = requests.get(dataset_url, headers=headers, timeout=10)

                    if response.status_code == 200:
                        items = response.json()
                        print(f"✅ 成功获取 {len(items)} 条数据")
                        print("\n🎉 所有测试通过！轮询机制工作正常")
                        sys.exit(0)
                    else:
                        print(f"❌ 获取数据失败: {response.status_code}")
                        sys.exit(1)
            else:
                print(f"⚠️  任务状态不是 SUCCEEDED: {status}")
                sys.exit(1)

            break

        # 等待后继续轮询
        time.sleep(poll_interval)

except KeyboardInterrupt:
    print("\n⚠️  用户中断")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ 轮询失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n❌ 测试未完成")
sys.exit(1)
