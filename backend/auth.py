# -*- coding: utf-8 -*-
"""
认证与安全工具（M4）——只用标准库，不引入额外依赖。

- 密码哈希：PBKDF2-HMAC-SHA256（带盐、可配置迭代次数），绝不存明文。
- 会话令牌：secrets 生成的高熵随机 token，服务端保存，可撤销。
- 常量时间比较防时序攻击。
"""
import hashlib
import hmac
import secrets

# 密码哈希参数
PBKDF2_ITERATIONS = 120_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    """返回格式：pbkdf2_sha256$iterations$salt_hex$hash_hex"""
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
