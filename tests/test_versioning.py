# -*- coding: utf-8 -*-
"""版本规则测试：版本不可原地修改、确认幂等、过期分析拦截。

这些测试用固定示例材料（demo 模式），不依赖真实模型，因此可在无密钥环境下运行。
"""
from backend.main import DEMO_SOURCE_001, DEMO_SOURCE_002

from conftest import auth


def _create_project(client, token, name="版本测试"):
    r = client.post("/api/projects", json={"name": name}, headers=auth(token))
    assert r.status_code == 200
    return r.json()["id"]


def _add_source(client, token, pid, text, label):
    r = client.post(f"/api/projects/{pid}/sources", json={"text": text, "label": label}, headers=auth(token))
    assert r.status_code == 200
    return r.json()["id"]


def _analyze(client, token, pid, source_ids):
    r = client.post(f"/api/projects/{pid}/analyze", json={"source_ids": source_ids}, headers=auth(token))
    assert r.status_code == 200
    assert r.json()["demo_mode"] is True
    return r.json()


def test_confirm_creates_immutable_snapshot(client, alice):
    pid = _create_project(client, alice)
    sid = _add_source(client, alice, pid, DEMO_SOURCE_001, "SRC-001")
    _analyze(client, alice, pid, [sid])

    v1 = client.post(f"/api/projects/{pid}/confirm", json={"request_id": "r1"}, headers=auth(alice)).json()
    assert v1["seq"] == 1
    assert v1["parent_version_id"] is None
    assert len(v1["snapshot"]["requirements"]) == 8
    # 稳定 ID 已分配
    ids = [r["id"] for r in v1["snapshot"]["requirements"]]
    assert all(i and i.startswith("REQ-") for i in ids)


def test_v1_unchanged_after_v2(client, alice):
    # 确认 V2 之后，V1 的快照和稳定 ID 都不变
    pid = _create_project(client, alice)
    sid1 = _add_source(client, alice, pid, DEMO_SOURCE_001, "SRC-001")
    _analyze(client, alice, pid, [sid1])
    v1 = client.post(f"/api/projects/{pid}/confirm", json={"request_id": "r1"}, headers=auth(alice)).json()

    v1_before = v1["snapshot"]
    v1_ids_before = [r["id"] for r in v1_before["requirements"]]

    sid2 = _add_source(client, alice, pid, DEMO_SOURCE_002, "SRC-002")
    cmp = client.post(f"/api/projects/{pid}/compare", json={"source_ids": [sid2]}, headers=auth(alice)).json()
    decisions = [{"change_index": c["change_index"], "decision": "accept"} for c in cmp["changes"]]
    v2 = client.post(f"/api/projects/{pid}/apply",
                     json={"run_id": cmp["run_id"], "request_id": "r2", "decisions": decisions},
                     headers=auth(alice)).json()
    assert v2["seq"] == 2
    assert v2["parent_version_id"] == v1["id"]

    # 重新拉取项目，检查 V1 快照与稳定 ID 均未变
    proj = client.get(f"/api/projects/{pid}", headers=auth(alice)).json()
    v1_after = proj["versions"][0]["snapshot"]
    assert v1_after == v1_before
    assert [r["id"] for r in v1_after["requirements"]] == v1_ids_before


def test_confirm_idempotent_by_request_id(client, alice):
    # 同一个 request_id 重复确认，只应该有一个版本
    pid = _create_project(client, alice)
    sid = _add_source(client, alice, pid, DEMO_SOURCE_001, "SRC-001")
    _analyze(client, alice, pid, [sid])

    v1a = client.post(f"/api/projects/{pid}/confirm", json={"request_id": "same"}, headers=auth(alice)).json()
    v1b = client.post(f"/api/projects/{pid}/confirm", json={"request_id": "same"}, headers=auth(alice)).json()
    assert v1a["id"] == v1b["id"]

    proj = client.get(f"/api/projects/{pid}", headers=auth(alice)).json()
    assert len(proj["versions"]) == 1


def test_apply_rejects_stale_baseline(client, alice):
    # 基线已经产生新版本时，再拿旧分析结果去采纳，应该被拒
    pid = _create_project(client, alice)
    sid1 = _add_source(client, alice, pid, DEMO_SOURCE_001, "SRC-001")
    _analyze(client, alice, pid, [sid1])
    v1 = client.post(f"/api/projects/{pid}/confirm", json={"request_id": "r1"}, headers=auth(alice)).json()

    sid2 = _add_source(client, alice, pid, DEMO_SOURCE_002, "SRC-002")
    cmp = client.post(f"/api/projects/{pid}/compare", json={"source_ids": [sid2]}, headers=auth(alice)).json()
    decisions = [{"change_index": c["change_index"], "decision": "accept"} for c in cmp["changes"]]

    # 第一次采纳成功，生成 V2
    client.post(f"/api/projects/{pid}/apply",
                json={"run_id": cmp["run_id"], "request_id": "r2", "decisions": decisions},
                headers=auth(alice))

    # 再用同一个（已过期的）run_id 采纳，应被拒绝
    r = client.post(f"/api/projects/{pid}/apply",
                    json={"run_id": cmp["run_id"], "request_id": "r3", "decisions": decisions},
                    headers=auth(alice))
    assert r.status_code == 409
