# -*- coding: utf-8 -*-
"""pytest 共享 fixture 与导入路径配置。"""

import os
import sys

# 把项目根目录加入 sys.path，使 `from backend import main` 可用
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 在导入 backend.main 之前，把数据库指向临时测试库，避免污染真实的 app.db
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每项测试独立临时库，不读取或清空用户数据库。"""
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(main, "ENABLE_DEMO_MODE", True)
    with TestClient(main.app) as c:
        yield c


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def alice(client):
    """注册并登录一个用户，返回 (client, token, username)。"""
    client.post("/api/auth/register", json={"username": "alice", "password": "alice123"})
    r = client.post("/api/auth/login", json={"username": "alice", "password": "alice123"})
    return r.json()["token"]


@pytest.fixture()
def bob(client):
    client.post("/api/auth/register", json={"username": "bob", "password": "bob123"})
    r = client.post("/api/auth/login", json={"username": "bob", "password": "bob123"})
    return r.json()["token"]
