# -*- coding: utf-8 -*-
"""项目级访问控制测试（T16）：数据隔离、成员协作。"""
from conftest import auth


def _mk_project(client, token, name):
    r = client.post("/api/projects", json={"name": name}, headers=auth(token))
    assert r.status_code == 200
    return r.json()["id"]


def test_cross_user_cannot_access(client, alice, bob):
    """T16：用户 B 用用户 A 的项目 ID 调用接口，服务端应拒绝且不泄露内容。"""
    pid = _mk_project(client, alice, "A 的私有项目")

    # 读取、写入、确认、导出全部应 404
    assert client.get(f"/api/projects/{pid}", headers=auth(bob)).status_code == 404
    assert client.post(f"/api/projects/{pid}/sources",
                       json={"text": "bob 试图写入", "label": "x"},
                       headers=auth(bob)).status_code == 404
    assert client.post(f"/api/projects/{pid}/confirm",
                       json={"request_id": "x"}, headers=auth(bob)).status_code == 404
    assert client.get(f"/api/projects/{pid}/export", headers=auth(bob)).status_code == 404
    assert client.get(f"/api/projects/{pid}/versions/whatever", headers=auth(bob)).status_code == 404


def test_project_list_isolated(client, alice, bob):
    _mk_project(client, alice, "A 的项目")
    a_list = client.get("/api/projects", headers=auth(alice)).json()["projects"]
    b_list = client.get("/api/projects", headers=auth(bob)).json()["projects"]
    assert len(a_list) == 1
    assert len(b_list) == 0


def test_member_can_access_after_invite(client, alice, bob):
    pid = _mk_project(client, alice, "协作项目")

    # 非 owner 不能邀请成员
    r = client.post(f"/api/projects/{pid}/members", json={"username": "alice"}, headers=auth(bob))
    assert r.status_code == 403

    # owner 邀请 bob
    r = client.post(f"/api/projects/{pid}/members", json={"username": "bob"}, headers=auth(alice))
    assert r.status_code == 200

    # bob 现在可以读取
    assert client.get(f"/api/projects/{pid}", headers=auth(bob)).status_code == 200

    # 成员列表包含 bob
    members = client.get(f"/api/projects/{pid}/members", headers=auth(alice)).json()["members"]
    assert any(m["username"] == "bob" for m in members)


def test_member_cannot_invite_others(client, alice, bob):
    pid = _mk_project(client, alice, "协作项目2")
    client.post(f"/api/projects/{pid}/members", json={"username": "bob"}, headers=auth(alice))
    # 先注册第三个人
    client.post("/api/auth/register", json={"username": "carol", "password": "carol123"})
    r = client.post(f"/api/projects/{pid}/members", json={"username": "carol"}, headers=auth(bob))
    assert r.status_code == 403
