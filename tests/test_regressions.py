"""面向真实用户路径的回归测试：修改保存、材料选择、版本与权限。"""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

from backend import main, validators
from conftest import auth


def project_with_draft(client, token):
    pid = client.post("/api/projects", headers=auth(token), json={"name": "审核项目"}).json()["id"]
    sid = client.post(
        f"/api/projects/{pid}/sources",
        headers=auth(token),
        json={"text": main.DEMO_SOURCE_001, "label": "初次需求"},
    ).json()["id"]
    result = client.post(
        f"/api/projects/{pid}/analyze", headers=auth(token), json={"source_ids": [sid]}
    ).json()
    assert result["status"] == "ok"
    return pid, sid, result["draft"]


def confirm(client, token, pid, key="v1"):
    return client.post(f"/api/projects/{pid}/confirm", headers=auth(token), json={"request_id": key})


def comparison(client, token, pid):
    sid = client.post(
        f"/api/projects/{pid}/sources",
        headers=auth(token),
        json={"text": main.DEMO_SOURCE_002, "label": "范围变更"},
    ).json()["id"]
    r = client.post(f"/api/projects/{pid}/compare", headers=auth(token), json={"source_ids": [sid]}).json()
    assert r["status"] == "ok", r
    return r


def test_register_returns_working_session(client):
    r = client.post("/api/auth/register", json={"username": "newuser", "password": "pass123"}).json()
    assert client.get("/api/auth/me", headers=auth(r["token"])).json()["user"] == r["user"]
    assert (
        client.post("/api/projects", headers=auth(r["token"]), json={"name": "我的项目"}).status_code == 200
    )


def test_edit_and_remove_are_frozen_in_version(client, alice):
    pid, sid, draft = project_with_draft(client, alice)
    reqs = deepcopy(draft["requirements"])
    reqs[0]["description"] = "人工修订：官网支持客户联系"
    reqs.pop(1)
    saved = client.post(
        f"/api/projects/{pid}/draft",
        headers=auth(alice),
        json={
            "requirements": reqs,
            "questions": ["请确认上线范围"],
            "expected_updated_at": draft["updated_at"],
            "parent_version_id": None,
        },
    )
    assert saved.status_code == 200
    version = client.post(
        f"/api/projects/{pid}/confirm",
        headers=auth(alice),
        json={"request_id": "review", "expected_updated_at": saved.json()["updated_at"]},
    ).json()
    assert len(version["snapshot"]["requirements"]) == 7
    assert version["snapshot"]["requirements"][0]["description"] == reqs[0]["description"]
    assert version["snapshot"]["questions"] == ["请确认上线范围"]
    assert version["source_analysis_run_id"] == draft["source_analysis_run_id"]
    sources = client.get(f"/api/projects/{pid}", headers=auth(alice)).json()["sources"]
    for r in version["snapshot"]["requirements"]:
        validators.validate_evidence(r["evidence"], sources)


def test_stale_draft_and_confirm_are_rejected(client, alice):
    pid, sid, draft = project_with_draft(client, alice)
    payload = {
        "requirements": draft["requirements"],
        "questions": [],
        "expected_updated_at": draft["updated_at"],
        "parent_version_id": None,
    }
    assert client.post(f"/api/projects/{pid}/draft", headers=auth(alice), json=payload).status_code == 200
    assert client.post(f"/api/projects/{pid}/draft", headers=auth(alice), json=payload).status_code == 409
    assert (
        client.post(
            f"/api/projects/{pid}/confirm",
            headers=auth(alice),
            json={"request_id": "stale", "expected_updated_at": draft["updated_at"]},
        ).status_code
        == 409
    )


def test_compare_only_sends_selected_materials(client, alice, monkeypatch):
    pid, sid, draft = project_with_draft(client, alice)
    confirm(client, alice, pid)
    new = client.post(
        f"/api/projects/{pid}/sources", headers=auth(alice), json={"text": "客户：增加订单查询。"}
    ).json()
    captured = []

    def fake_model(messages, **kwargs):
        import json

        captured.append(json.loads(messages[-1]["content"])["new_sources"])
        return {"ok": True, "kind": "ok", "data": {"changes": []}}

    monkeypatch.setattr(main.llm, "chat_json", fake_model)
    result = client.post(
        f"/api/projects/{pid}/compare", headers=auth(alice), json={"source_ids": [new["id"]]}
    ).json()
    assert result["status"] == "ok"
    assert captured == [[{"source_id": new["id"], "text": new["text"]}]]
    assert (
        client.post(
            f"/api/projects/{pid}/compare", headers=auth(alice), json={"source_ids": [sid, "not-owned"]}
        ).status_code
        == 422
    )


def test_idempotency_isolated_by_user_project_and_payload(client, alice, bob):
    a, _, _ = project_with_draft(client, alice)
    b, _, _ = project_with_draft(client, bob)
    va = confirm(client, alice, a, "shared").json()
    vb = confirm(client, bob, b, "shared").json()
    assert va["id"] != vb["id"]
    assert confirm(client, alice, a, "shared").json()["id"] == va["id"]
    changed = client.post(
        f"/api/projects/{a}/confirm",
        headers=auth(alice),
        json={"request_id": "shared", "expected_updated_at": "different"},
    )
    assert changed.status_code == 409
    c, _, _ = project_with_draft(client, alice)
    assert confirm(client, alice, c, "shared").json()["id"] not in {va["id"], vb["id"]}


def test_member_rate_limit_counts_the_member(client, alice, bob, monkeypatch):
    pid, sid, draft = project_with_draft(client, alice)
    client.post(f"/api/projects/{pid}/members", headers=auth(alice), json={"username": "bob"})
    monkeypatch.setattr(main, "RATE_LIMIT_MAX", 1)
    payload = {"source_ids": [sid], "expected_updated_at": draft["updated_at"]}
    assert client.post(f"/api/projects/{pid}/analyze", headers=auth(bob), json=payload).status_code == 200
    assert client.post(f"/api/projects/{pid}/analyze", headers=auth(bob), json=payload).status_code == 429


def test_login_rate_limit_and_health_disclosure(client, monkeypatch):
    monkeypatch.setattr(main, "AUTH_RATE_LIMIT_MAX", 1)
    credentials = {"username": "unknown", "password": "wrong"}
    assert client.post("/api/auth/login", json=credentials).status_code == 401
    assert client.post("/api/auth/login", json=credentials).status_code == 429
    health = client.get("/api/health")
    assert "db" not in health.json()
    assert "Content-Security-Policy" in health.headers
    assert (
        "access-control-allow-origin"
        not in client.get("/api/health", headers={"Origin": "https://untrusted.example"}).headers
    )


def test_apply_preserves_stable_id_and_corrects_scope(client, alice):
    pid, _, _ = project_with_draft(client, alice)
    v1 = confirm(client, alice, pid).json()
    review = comparison(client, alice, pid)
    decisions = [{"change_index": c["change_index"], "decision": "accept"} for c in review["changes"]]
    result = client.post(
        f"/api/projects/{pid}/apply",
        headers=auth(alice),
        json={"run_id": review["run_id"], "request_id": "apply", "decisions": decisions},
    )
    assert result.status_code == 200
    v2 = result.json()
    old_member = next(r for r in v1["snapshot"]["requirements"] if "会员" in r["title"])
    new_member = next(r for r in v2["snapshot"]["requirements"] if r["id"] == old_member["id"])
    assert new_member["intent"] == "requested"
    assert new_member["title"] == "会员登录与专属价格"
    assert len(v1["snapshot"]["requirements"]) == len(v2["snapshot"]["requirements"])
    assert "在线支付仍为明确排除项，不纳入本期。" not in v2["snapshot"]["questions"]
    assert "不同老客户看到的价格是否一样？" in v2["snapshot"]["questions"]
    before = client.get(
        f"/api/projects/{pid}/export", headers=auth(alice), params={"version_id": v1["id"]}
    ).text
    assert "老客户登录后" not in before
    assert "V2" not in before
    after = client.get(f"/api/projects/{pid}/export", headers=auth(alice)).text
    assert "示例模式" in after and "采纳/modified" in after


def test_all_ignored_saves_review_without_empty_version(client, alice):
    pid, _, _ = project_with_draft(client, alice)
    confirm(client, alice, pid)
    review = comparison(client, alice, pid)
    payload = {
        "run_id": review["run_id"],
        "request_id": "ignore-all",
        "decisions": [{"change_index": c["change_index"], "decision": "ignore"} for c in review["changes"]],
    }
    r = client.post(f"/api/projects/{pid}/apply", headers=auth(alice), json=payload).json()
    assert r["created"] is False
    project = client.get(f"/api/projects/{pid}", headers=auth(alice)).json()
    assert len(project["versions"]) == 1
    assert project["review"]["applied"] is True
    assert client.post(f"/api/projects/{pid}/apply", headers=auth(alice), json=payload).json() == r


def test_concurrent_apply_cannot_use_stale_baseline(client, alice):
    pid, _, _ = project_with_draft(client, alice)
    confirm(client, alice, pid)
    review = comparison(client, alice, pid)

    def submit(key):
        return client.post(
            f"/api/projects/{pid}/apply",
            headers=auth(alice),
            json={
                "run_id": review["run_id"],
                "request_id": key,
                "decisions": [
                    {"change_index": c["change_index"], "decision": "accept"} for c in review["changes"]
                ],
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(submit, ["concurrent-1", "concurrent-2"]))
    assert sorted(codes) == [200, 409]
    assert len(client.get(f"/api/projects/{pid}", headers=auth(alice)).json()["versions"]) == 2


def test_public_demo_has_real_evidence_and_no_account_write(client):
    r = client.get("/api/demo")
    assert r.status_code == 200
    demo = r.json()
    for requirement in demo["versions"][0]["snapshot"]["requirements"]:
        validators.validate_evidence(requirement["evidence"], demo["sources"])
    assert demo["read_only"] is True
    assert client.post("/api/projects/DEMO/sources", json={"text": "x"}).status_code == 401


def test_request_body_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_BODY_BYTES", 64)
    assert client.post("/api/auth/register", content=b"x" * 65).status_code == 413
