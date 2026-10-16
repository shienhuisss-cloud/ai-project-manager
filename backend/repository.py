"""项目数据访问与版本写入；事务由调用方开启、提交或回滚。"""

import hashlib
import json
import sqlite3
from fastapi import HTTPException

from . import config, prompts, storage

_db = storage.current
_now, _new_id = config.now, config.new_id


def _jload_reqs(value):
    return json.loads(value) if value is not None else []


def _jload_qs(value):
    return json.loads(value) if value is not None else []


def _get_project_row(pid: str) -> sqlite3.Row:
    row = _db().execute("SELECT * FROM project WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return row


# 认证与权限


def _load_sources(pid: str) -> list:
    rows = (
        _db().execute("SELECT * FROM source WHERE project_id = ? ORDER BY created_at ASC", (pid,)).fetchall()
    )
    return [dict(r) for r in rows]


def _load_draft(pid: str) -> dict | None:
    row = _db().execute("SELECT * FROM draft WHERE project_id = ?", (pid,)).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "parent_version_id": row["parent_version_id"],
        "requirements": _jload_reqs(row["requirements_json"]),
        "questions": _jload_qs(row["questions_json"]),
        "updated_at": row["updated_at"],
        "source_analysis_run_id": row["source_analysis_run_id"],
        "demo_mode": bool(row["demo_mode"]),
    }


def _save_draft(pid, parent_version_id, requirements, questions, run_id=None, demo_mode=False):
    existing = _load_draft(pid)
    did = existing["id"] if existing else _new_id("DRF")
    stamp = _now()
    _db().execute(
        """
        INSERT INTO draft (id, project_id, parent_version_id, requirements_json, questions_json,
                           updated_at, source_analysis_run_id, demo_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
          parent_version_id=excluded.parent_version_id, requirements_json=excluded.requirements_json,
          questions_json=excluded.questions_json, updated_at=excluded.updated_at,
          source_analysis_run_id=excluded.source_analysis_run_id, demo_mode=excluded.demo_mode
        """,
        (
            did,
            pid,
            parent_version_id,
            json.dumps(requirements, ensure_ascii=False),
            json.dumps(questions, ensure_ascii=False),
            stamp,
            run_id,
            int(demo_mode),
        ),
    )
    return _load_draft(pid)


def _load_versions(pid: str) -> list:
    rows = _db().execute("SELECT * FROM version WHERE project_id = ? ORDER BY seq ASC", (pid,)).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "seq": r["seq"],
                "parent_version_id": r["parent_version_id"],
                "source_analysis_run_id": r["source_analysis_run_id"],
                "confirmed_by": r["confirmed_by"],
                "confirmed_at": r["confirmed_at"],
                "snapshot": json.loads(r["snapshot_json"]),
            }
        )
    return out


def _project_full(pid: str) -> dict:
    row = _get_project_row(pid)
    versions = _load_versions(pid)
    latest = versions[-1] if versions else None
    members = (
        _db()
        .execute(
            "SELECT m.user_id, m.role, u.username FROM membership m JOIN user u ON u.id = m.user_id "
            "WHERE m.project_id = ?",
            (pid,),
        )
        .fetchall()
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "owner_id": row["owner_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "current_version_id": row["current_version_id"],
        "current_version_seq": latest["seq"] if latest else None,
        "sources": _load_sources(pid),
        "draft": _load_draft(pid) or {"requirements": [], "questions": [], "updated_at": None},
        "versions": versions,
        "review": _latest_review(pid),
        "members": [{"user_id": m["user_id"], "username": m["username"], "role": m["role"]} for m in members],
    }


def _touch_project(pid: str):
    _db().execute("UPDATE project SET updated_at = ? WHERE id = ?", (_now(), pid))


# 版本快照工具


def _snapshot_of(draft_reqs, draft_qs) -> dict:
    return {
        "requirements": [dict(r) for r in draft_reqs],
        "questions": list(draft_qs),
    }


def _assign_requirement_ids(conn, pid: str, requirements: list) -> None:
    """为没有 id 的需求分配稳定 ID，并推进项目级序号。"""
    row = conn.execute("SELECT requirement_seq FROM project WHERE id = ?", (pid,)).fetchone()
    seq = row["requirement_seq"]
    for r in requirements:
        if not r.get("id"):
            seq += 1
            r["id"] = f"REQ-{seq:04d}"
    conn.execute("UPDATE project SET requirement_seq = ? WHERE id = ?", (seq, pid))


def _latest_review(pid):
    row = (
        _db()
        .execute(
            "SELECT * FROM analysis_run WHERE project_id=? AND task_type='compare' AND status='ok' ORDER BY created_at DESC LIMIT 1",
            (pid,),
        )
        .fetchone()
    )
    if row is None:
        return None
    result = json.loads(row["result_json"])
    decisions = [
        dict(d)
        for d in _db().execute(
            "SELECT change_index, decision, revised_after FROM change_decision WHERE analysis_run_id=?",
            (row["id"],),
        )
    ]
    return {
        **result,
        "run_id": row["id"],
        "demo_mode": bool(row["demo_mode"]),
        "baseline_version_id": row["baseline_version_id"],
        "decisions": decisions,
        "applied": bool(decisions)
        or _db().execute("SELECT 1 FROM version WHERE source_analysis_run_id=?", (row["id"],)).fetchone()
        is not None,
    }


def _idem_get(user, pid, operation, request_id, payload):
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    row = (
        _db()
        .execute(
            "SELECT * FROM idempotency WHERE user_id=? AND project_id=? AND operation=? AND request_id=?",
            (user["id"], pid, operation, request_id),
        )
        .fetchone()
    )
    if row:
        if row["request_hash"] != digest:
            raise HTTPException(409, "同一个请求标识不能用于不同内容")
        return json.loads(row["response_json"]), digest
    return None, digest


def _idem_save(user, pid, operation, request_id, digest, result):
    _db().execute(
        "INSERT INTO idempotency VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user["id"], pid, operation, request_id, digest, json.dumps(result, ensure_ascii=False), _now()),
    )


def _store_analysis(pid, source_ids, task_type, baseline, status, is_demo, result, user, model=None):
    rid = _new_id("RUN")
    _db().execute(
        """
        INSERT INTO analysis_run
          (id, project_id, source_ids_json, baseline_version_id, task_type, model_id, prompt_version,
           status, demo_mode, result_json, created_at, actor_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rid,
            pid,
            json.dumps(source_ids),
            baseline,
            task_type,
            model,
            prompts.PROMPT_VERSION,
            status,
            int(is_demo),
            json.dumps(result, ensure_ascii=False) if result else None,
            _now(),
            user["id"],
        ),
    )
    return rid


def _write_version(pid, snapshot, user, run_id=None):
    conn = _db()
    project = _get_project_row(pid)
    latest = conn.execute("SELECT MAX(seq) FROM version WHERE project_id=?", (pid,)).fetchone()[0] or 0
    _assign_requirement_ids(conn, pid, snapshot["requirements"])
    version = {
        "id": _new_id("VER"),
        "seq": latest + 1,
        "parent_version_id": project["current_version_id"],
        "snapshot": snapshot,
        "source_analysis_run_id": run_id,
        "confirmed_by": user["username"],
        "confirmed_at": _now(),
    }
    conn.execute(
        "INSERT INTO version VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            version["id"],
            pid,
            version["seq"],
            version["parent_version_id"],
            json.dumps(snapshot, ensure_ascii=False),
            run_id,
            user["username"],
            version["confirmed_at"],
        ),
    )
    conn.execute(
        "UPDATE project SET current_version_id=?, updated_at=? WHERE id=?",
        (version["id"], version["confirmed_at"], pid),
    )
    conn.execute(
        "UPDATE draft SET parent_version_id=?, requirements_json='[]', questions_json='[]', demo_mode=0, source_analysis_run_id=NULL, updated_at=? WHERE project_id=?",
        (version["id"], _now(), pid),
    )
    return version
