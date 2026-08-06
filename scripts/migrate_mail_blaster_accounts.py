#!/usr/bin/env python3
"""把老 mail-blaster（~/mail-blaster，SQLite）的发件账号迁进 flaskproject 的 Postgres。

两边用的加密密钥不同：
  老库 —— MAILER_SECRET_KEY（~/mail-blaster/.env）
  新库 —— TIKTOK_TOKEN_ENC_KEY（flaskproject/.env，crypto_util 用的那个）
所以要「用老密钥解开、用新密钥重新加密」。**全过程在一个进程的内存里完成，
不打印明文、不落中间文件。**

用法：
    cd ~/Desktop/flaskproject
    set -a && source .env && set +a
    ./.venv/bin/python scripts/migrate_mail_blaster_accounts.py           # 预演，只看会迁什么
    ./.venv/bin/python scripts/migrate_mail_blaster_accounts.py --apply   # 真正写入

重复执行安全：按邮箱判断，已存在的会跳过（除非加 --overwrite）。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

OLD_ROOT = Path.home() / "mail-blaster"
OLD_DB = OLD_ROOT / "data" / "mail.db"


def _old_fernet():
    """老库的 Fernet，密钥来自 ~/mail-blaster/.env 的 MAILER_SECRET_KEY。"""
    from cryptography.fernet import Fernet

    key = (os.environ.get("MAILER_SECRET_KEY") or "").strip()
    if not key:
        for line in (OLD_ROOT / ".env").read_text().splitlines():
            if line.startswith("MAILER_SECRET_KEY="):
                key = line.split("=", 1)[1].strip()
                break
    if not key:
        raise SystemExit(f"找不到 MAILER_SECRET_KEY（看 {OLD_ROOT / '.env'}），无法解密老库")
    return Fernet(key.encode())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入，不加就是预演")
    ap.add_argument("--overwrite", action="store_true", help="邮箱已存在时也覆盖")
    args = ap.parse_args()

    if not OLD_DB.exists():
        raise SystemExit(f"找不到老库 {OLD_DB}")

    import crypto_util
    import database as db
    import mail_blaster_service as mb

    fernet = _old_fernet()

    def reencrypt(blob):
        """老密钥解 → 新密钥加。明文只在这一行的作用域里存在。"""
        if not blob:
            return None
        try:
            return crypto_util.encrypt(fernet.decrypt(blob.encode()).decode())
        except Exception as exc:
            raise ValueError(f"解密失败（老库密钥可能不对）：{exc}") from exc

    conn = sqlite3.connect(OLD_DB)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM sender_accounts ORDER BY sort_order, id")]
    conn.close()

    existing = {r["email"] for r in db.query_all("SELECT email FROM mb_sender_accounts")}

    print(f"老库 {len(rows)} 个账号，新库已有 {len(existing)} 个\n")
    todo, skip, bad = [], [], []
    for r in rows:
        if r["email"] in existing and not args.overwrite:
            skip.append(r["email"])
            continue
        try:
            todo.append({
                "email": r["email"],
                "display_name": r["display_name"],
                "signature_name": r["signature_name"],
                "provider": r["provider"],
                "smtp_host": r["smtp_host"],
                "smtp_port": r["smtp_port"],
                "smtp_username": r["smtp_username"],
                "use_ssl": bool(r["use_ssl"]),
                "use_tls": bool(r["use_tls"]),
                "auth_mode": r["auth_mode"] or "password",
                "enabled": bool(r["enabled"]),
                "sort_order": r["sort_order"] or 0,
                "daily_limit": r["daily_limit"] or mb.DEFAULT_DAILY_LIMIT,
                # 老库测过的状态直接沿用，省得 30 个账号重测一遍
                "status": r["status"] or "draft",
                "pwd": reencrypt(r["encrypted_password"]),
                "cid": reencrypt(r["encrypted_client_id"]),
                "rtok": reencrypt(r["encrypted_refresh_token"]),
            })
        except ValueError as exc:
            bad.append(f"{r['email']}: {exc}")

    for a in todo:
        creds = "+".join(k for k, v in
                         (("密码", a["pwd"]), ("client_id", a["cid"]), ("refresh_token", a["rtok"]))
                         if v)
        print(f"  迁移  {a['email']:38} {a['auth_mode']:8} [{creds}]")
    for e in skip:
        print(f"  跳过  {e:38} 新库已存在")
    for e in bad:
        print(f"  ❌    {e}")

    if not args.apply:
        print(f"\n预演结束。会迁 {len(todo)} 个，跳过 {len(skip)} 个。加 --apply 才真正写入。")
        return

    for a in todo:
        db.execute("""
            INSERT INTO mb_sender_accounts
                (email, display_name, signature_name, provider, smtp_host, smtp_port,
                 smtp_username, use_ssl, use_tls, enabled, sort_order, daily_limit,
                 auth_mode, status, encrypted_password, encrypted_client_id, encrypted_refresh_token)
            VALUES (%(email)s, %(display_name)s, %(signature_name)s, %(provider)s, %(smtp_host)s,
                    %(smtp_port)s, %(smtp_username)s, %(use_ssl)s, %(use_tls)s, %(enabled)s,
                    %(sort_order)s, %(daily_limit)s, %(auth_mode)s, %(status)s,
                    %(pwd)s, %(cid)s, %(rtok)s)
            ON CONFLICT (email) DO UPDATE SET
                provider = EXCLUDED.provider, smtp_host = EXCLUDED.smtp_host,
                smtp_port = EXCLUDED.smtp_port, use_ssl = EXCLUDED.use_ssl,
                use_tls = EXCLUDED.use_tls, auth_mode = EXCLUDED.auth_mode,
                encrypted_password = EXCLUDED.encrypted_password,
                encrypted_client_id = EXCLUDED.encrypted_client_id,
                encrypted_refresh_token = EXCLUDED.encrypted_refresh_token
        """, a)

    total = db.query_one("SELECT COUNT(*) AS c FROM mb_sender_accounts")["c"]
    print(f"\n✅ 写入完成。新库现在共 {total} 个账号。")
    print("   下一步：到 /mail-blaster 的「发件账号池」逐个点「测试」验证 OAuth 能不能换到令牌。")


if __name__ == "__main__":
    main()
