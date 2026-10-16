# -*- coding: utf-8 -*-
"""认证相关测试：注册、登录、登出、会话失效、密码哈希。"""

from backend import auth


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def test_password_hash_roundtrip():
    hashed = auth.hash_password("secret123")
    assert hashed.startswith("pbkdf2_sha256$")
    assert hashed != "secret123"  # 不是明文
    assert auth.verify_password("secret123", hashed) is True
    assert auth.verify_password("wrong", hashed) is False


def test_password_hash_salted():
    """相同密码两次哈希应不同（有随机盐）。"""
    h1 = auth.hash_password("same-password")
    h2 = auth.hash_password("same-password")
    assert h1 != h2
    assert auth.verify_password("same-password", h1)
    assert auth.verify_password("same-password", h2)


def test_register_and_login(client):
    r = client.post("/api/auth/register", json={"username": "tester", "password": "pass123"})
    assert r.status_code == 200
    assert r.json()["username"] == "tester"

    r = client.post("/api/auth/login", json={"username": "tester", "password": "pass123"})
    assert r.status_code == 200
    assert "token" in r.json()


def test_register_duplicate_rejected(client):
    client.post("/api/auth/register", json={"username": "dup", "password": "pass123"})
    r = client.post("/api/auth/register", json={"username": "dup", "password": "pass123"})
    assert r.status_code == 409


def test_login_wrong_password(client):
    client.post("/api/auth/register", json={"username": "wp", "password": "pass123"})
    r = client.post("/api/auth/login", json={"username": "wp", "password": "wrong"})
    assert r.status_code == 401


def test_unauthenticated_401(client):
    r = client.get("/api/projects")
    assert r.status_code == 401


def test_logout_invalidates_token(client, alice):
    r = client.get("/api/projects", headers=_hdr(alice))
    assert r.status_code == 200

    client.post("/api/auth/logout", headers=_hdr(alice))
    r = client.get("/api/projects", headers=_hdr(alice))
    assert r.status_code == 401
