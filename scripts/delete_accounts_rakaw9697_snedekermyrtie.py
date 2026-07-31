"""
一次性脚本：删除两个账号（rakaw9697[别名精确匹配] 和 snedekermyrtie）及其在所有相关表里的数据。
用法：
    source .env && python3 scripts/delete_accounts_rakaw9697_snedekermyrtie.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db

TARGETS = [
    ("rakaw9697", "-000TWmMhdVsvXBIG_oxRpC2y40zwhWH9s8X"),
    ("snedekermyrtie", "-000Bwu5OW3oALAwfwfYg5B1HlUOQ9CyO16l"),
]

BUSINESS_ID_TABLES = [
    "tiktok_official_video_daily_snapshots",
    "tiktok_official_video_publish_window_snapshots",
    "tiktok_official_video_snapshots",
    "tiktok_official_profile_daily_metrics",
    "tiktok_official_tokens",
]

for alias, bid in TARGETS:
    print("=" * 70)
    print(f"删除账号: alias={alias}  business_id={bid}")

    for table in BUSINESS_ID_TABLES:
        col = "open_id" if table == "tiktok_official_tokens" else "business_id"
        deleted = db.execute(f"DELETE FROM {table} WHERE {col} = %s", (bid,))
        print(f"  {table}: 删除 {deleted} 行")

    deleted = db.execute("DELETE FROM tiktok_official_invites WHERE account_alias = %s", (alias,))
    print(f"  tiktok_official_invites: 删除 {deleted} 行")

    deleted = db.execute("DELETE FROM tiktok_official_accounts WHERE business_id = %s", (bid,))
    print(f"  tiktok_official_accounts: 删除 {deleted} 行")

print("=" * 70)
print("完成")
