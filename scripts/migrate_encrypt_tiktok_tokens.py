"""
一次性迁移脚本：把 tiktok_official_tokens 里历史明文的 access_token / refresh_token 加密。

跑法（在项目根目录，确保 DATABASE_URL / TIKTOK_TOKEN_ENC_KEY 已配置）：
    python scripts/migrate_encrypt_tiktok_tokens.py

幂等：crypto_util.decrypt 对已经是密文的值会正常解出明文再重新加密，
对本来就是明文的历史行会原样返回后重新加密；重复运行不会出错，但没有必要重复跑。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import crypto_util


def main():
    rows = db.query_all("SELECT id, open_id, access_token, refresh_token FROM tiktok_official_tokens") or []
    if not rows:
        print("tiktok_official_tokens 无数据，无需迁移")
        return

    migrated = 0
    for row in rows:
        access_token = row.get("access_token")
        refresh_token = row.get("refresh_token")
        new_access = crypto_util.encrypt(crypto_util.decrypt(access_token)) if access_token else access_token
        new_refresh = crypto_util.encrypt(crypto_util.decrypt(refresh_token)) if refresh_token else refresh_token
        db.execute(
            "UPDATE tiktok_official_tokens SET access_token = %s, refresh_token = %s WHERE id = %s",
            (new_access, new_refresh, row["id"]),
        )
        migrated += 1
        print(f"已迁移 open_id={row.get('open_id')} (id={row['id']})")

    print(f"完成，共迁移 {migrated} 行")


if __name__ == "__main__":
    main()
