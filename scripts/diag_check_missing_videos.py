"""
一次性诊断脚本：在生产环境（有 TIKTOK_TOKEN_ENC_KEY）里直接调 TikTok Business API
拉这4个账号的真实视频列表，和我们数据库里存的做对比，确认 7-24/7-26 是否真的没有
被我们漏抓的视频。

用法（生产 Shell）：
    python3 scripts/diag_check_missing_videos.py

跑完直接把输出贴回来即可，此脚本只读，不写任何数据。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import tiktok_official_service as svc

ACCOUNTS = [
    ("rakaw9697", "-000tjA-YWtYe4_52bkq2ZMDsp7iddR3qxev"),
    ("gamgamliel", "-0004cmIzOc0goB56M5A3QksYv-bHAqLZhtI"),
    ("1ndhh___", "-000LD_TFCY-h03rIeBhpgmRyIot4zyj-tEX"),
    ("fanbaserics", "-0003gRq1a4enR73unz7BFyOFlpKjKdSi7D6"),
]

for username, bid in ACCOUNTS:
    print("=" * 70)
    print(f"账号: {username}  business_id: {bid}")
    try:
        token = svc.get_access_token(bid)
        if not token:
            print("  !! 没拿到 token，跳过")
            continue
        videos, meta = svc.fetch_videos(token, bid, max_pages=3)
    except Exception as e:
        print(f"  !! fetch_videos 失败: {e}")
        continue

    print(f"  TikTok API 返回 {len(videos)} 条视频, meta={meta}")

    db_rows = db.query_all(
        "SELECT item_id, create_time FROM tiktok_official_video_snapshots WHERE business_id = %s",
        (bid,),
    )
    db_item_ids = {r["item_id"] for r in db_rows}

    print("  --- TikTok API 返回的视频（按时间倒序） ---")
    missing_in_db = []
    for v in videos:
        item_id = str(v.get("item_id"))
        ct = svc._epoch_to_dt(v.get("create_time"))
        in_db = item_id in db_item_ids
        flag = "" if in_db else "  <-- 数据库里没有这条！"
        print(f"    item_id={item_id}  create_time(UTC)={ct}{flag}")
        if not in_db:
            missing_in_db.append((item_id, ct))

    print(f"  汇总: TikTok返回{len(videos)}条 / 库里{len(db_item_ids)}条 / 漏抓{len(missing_in_db)}条")
    if missing_in_db:
        print("  !!! 发现漏抓，需要进一步排查 upsert 逻辑 !!!")
        for item_id, ct in missing_in_db:
            print(f"      漏抓: item_id={item_id} create_time={ct}")
