from copy import deepcopy

from backend import main
from conftest import auth
from test_regressions import project_with_draft, confirm


def test_production_mode_never_replaces_model_with_fixed_results(client, alice, monkeypatch):
    monkeypatch.setattr(main, "ENABLE_DEMO_MODE", False)
    monkeypatch.setenv("LLM_API_KEY", "")
    pid = client.post("/api/projects", headers=auth(alice), json={"name": "正式项目"}).json()["id"]
    source = client.post(
        f"/api/projects/{pid}/sources", headers=auth(alice), json={"text": main.DEMO_SOURCE_001}
    ).json()
    assert source["is_demo"] == 0
    result = client.post(
        f"/api/projects/{pid}/analyze", headers=auth(alice), json={"source_ids": [source["id"]]}
    ).json()
    assert result["status"] == "unconfigured"
    assert result["demo_mode"] is False
    assert "requirements" not in result
    assert "LLM_" not in result["message"] and "配置" not in result["message"]
    assert client.get("/api/demo").status_code == 404
    assert set(client.get("/api/health").json()) == {"ok", "version"}


def test_revision_from_current_baseline_preserves_ids_and_old_snapshot(client, alice):
    pid, _, _ = project_with_draft(client, alice)
    v1 = confirm(client, alice, pid).json()
    project = client.get(f"/api/projects/{pid}", headers=auth(alice)).json()
    snapshot = deepcopy(v1["snapshot"])
    snapshot["requirements"][0]["description"] = "人工修订后的正式需求"
    result = client.post(
        f"/api/projects/{pid}/draft",
        headers=auth(alice),
        json={
            **snapshot,
            "parent_version_id": v1["id"],
            "expected_updated_at": project["draft"]["updated_at"],
        },
    )
    assert result.status_code == 200
    v2 = confirm(client, alice, pid, "revision-v2").json()
    assert v1["snapshot"]["requirements"][0]["id"] == v2["snapshot"]["requirements"][0]["id"]
    assert v2["snapshot"]["requirements"][0]["description"] == "人工修订后的正式需求"
    old = client.get(f"/api/projects/{pid}/versions/{v1['id']}", headers=auth(alice)).json()
    assert old["snapshot"] == v1["snapshot"]
