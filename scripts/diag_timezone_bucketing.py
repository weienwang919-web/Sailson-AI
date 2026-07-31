"""
一次性诊断脚本：检查 7-24/7-26 "只抓到少量视频" 是否为 UTC/中国时区分桶错位导致。
只读，不写。用法：
    source .env && python3 scripts/diag_timezone_bucketing.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db

ACCOUNTS = [
    ("rakaw9697", "-000tjA-YWtYe4_52bkq2ZMDsp7iddR3qxev"),
    ("gamgamliel", "-0004cmIzOc0goB56M5A3QksYv-bHAqLZhtI"),
    ("1ndhh___", "-000LD_TFCY-h03rIeBhpgmRyIot4zyj-tEX"),
    ("fanbaserics", "-0003gRq1a4enR73unz7BFyOFlpKjKdSi7D6"),
]

for username, bid in ACCOUNTS:
    print("=" * 70)
    print(f"账号: {username}  business_id: {bid}")
    rows = db.query_all(
        """
        SELECT
            create_time::date AS utc_date,
            (create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai')::date AS cn_date,
            create_time,
            item_id
        FROM tiktok_official_video_snapshots
        WHERE business_id = %s
          AND create_time >= '2026-07-22'::date
          AND create_time < '2026-07-28'::date
        ORDER BY create_time
        """,
        (bid,),
    )
    if not rows:
        print("  (7-22 ~ 7-27 之间无视频)")
        continue
    for r in rows:
        shifted = "  <-- UTC/中国日期不一致" if r["utc_date"] != r["cn_date"] else ""
        print(f"  item_id={r['item_id']}  create_time(UTC)={r['create_time']}  utc_date={r['utc_date']}  cn_date={r['cn_date']}{shifted}")

    from collections import Counter
    utc_counts = Counter(r["utc_date"] for r in rows)
    cn_counts = Counter(r["cn_date"] for r in rows)
    print(f"  按UTC日期计数: {dict(sorted(utc_counts.items()))}")
    print(f"  按中国日期计数: {dict(sorted(cn_counts.items()))}")
