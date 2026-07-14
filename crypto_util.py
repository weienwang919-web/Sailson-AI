"""
敏感字段（TikTok access_token / refresh_token 等）的对称加密工具
"""
from __future__ import annotations

import os
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_fernet = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ.get('TIKTOK_TOKEN_ENC_KEY', '').strip()
        if not key:
            raise RuntimeError(
                "TIKTOK_TOKEN_ENC_KEY 未配置，无法加解密 token。"
                "请用 `python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` 生成一个密钥并配置到环境变量。"
            )
        _fernet = Fernet(key.encode())
    return _fernet


def encrypt(plaintext: str | None) -> str | None:
    """明文转密文，供入库前调用。None/空字符串原样返回。"""
    if not plaintext:
        return plaintext
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    """密文转明文，供读库后调用。None/空字符串原样返回。

    兼容迁移期：如果传入的值不是本工具加密过的密文（比如历史遗留的明文数据，
    迁移脚本跑之前的旧行），解密失败时原样返回，避免整个读取流程报错。
    """
    if not ciphertext:
        return ciphertext
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return ciphertext
