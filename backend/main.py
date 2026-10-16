"""FastAPI 路由与用例编排。数据迁移、模型类型和变更应用分别维护。"""

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import auth, config, demo, llm, prompts, storage, validators
from .changes import apply_decisions
from .repository import (
    _get_project_row,
    _load_sources,
    _load_draft,
    _save_draft,
    _load_versions,
    _project_full,
    _touch_project,
    _snapshot_of,
    _idem_get,
    _idem_save,
    _store_analysis,
    _write_version,
)
from .schemas import (
    AnalyzeRequest,
    ApplyRequest,
    CompareRequest,
    ConfirmRequest,
    DraftUpdate,
    LoginRequest,
    MemberAddRequest,
    ProjectCreate,
    RegisterRequest,
    SourceCreate,
    ExtractResult,
    CompareResult,
    output_schema,
)

DB_PATH = config.DB_PATH
MAX_SOURCE_LENGTH = int(os.getenv("MAX_SOURCE_LENGTH", "50000"))
MAX_TOTAL_SOURCE_LENGTH = int(os.getenv("MAX_TOTAL_SOURCE_LENGTH", "200000"))
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "20"))
AUTH_RATE_LIMIT_MAX = int(os.getenv("AUTH_RATE_LIMIT_MAX", "20"))
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "168"))
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", "2000000"))
ENABLE_DEMO_MODE = os.getenv("ENABLE_DEMO_MODE", "false").lower() == "true"
_db_lock = threading.RLock()
_now, _new_id = config.now, config.new_id
DEMO_SOURCE_001, DEMO_SOURCE_002 = demo.DEMO_SOURCE_001, demo.DEMO_SOURCE_002


def _db():
    return storage.current()


def _get_current_user(authorization: str | None) -> dict:
    """从 Authorization: Bearer <token> 解析当前用户。未登录/过期返回 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization[len("Bearer ") :].strip()
    row = (
        _db()
        .execute(
            "SELECT s.token, s.user_id, s.expires_at, u.username FROM session s "
            "JOIN user u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        )
        .fetchone()
    )
    if row is None:
        raise HTTPException(status_code=401, detail="会话无效，请重新登录")
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        _db().execute("DELETE FROM session WHERE token = ?", (token,))
        _db().commit()
        raise HTTPException(status_code=401, detail="会话已过期，请重新登录")
    return {"id": row["user_id"], "username": row["username"]}


def _require_project_access(pid: str, user: dict) -> sqlite3.Row:
    """项目级访问控制：owner 或 membership 成员才可访问。否则 404（不泄露存在性）。"""
    row = _get_project_row(pid)
    if row["owner_id"] == user["id"]:
        return row
    m = (
        _db()
        .execute("SELECT 1 FROM membership WHERE project_id = ? AND user_id = ?", (pid, user["id"]))
        .fetchone()
    )
    if m is not None:
        return row
    # 用 404 而非 403，避免向他人泄露项目是否存在
    raise HTTPException(status_code=404, detail="项目不存在")


def _is_project_owner(pid: str, user: dict) -> bool:
    return _get_project_row(pid)["owner_id"] == user["id"]


# 请求限额（分析类接口）


def _reserve_limit(bucket, maximum, window=60):
    conn = _db()
    with _db_lock:
        conn.execute("BEGIN IMMEDIATE")
        stamp = time.time()
        row = conn.execute("SELECT * FROM rate_window WHERE bucket = ?", (bucket,)).fetchone()
        if row and stamp - row["started_at"] < window and row["hits"] >= maximum:
            conn.rollback()
            raise HTTPException(429, "请求过于频繁，请稍后重试", headers={"Retry-After": str(window)})
        if not row or stamp - row["started_at"] >= window:
            conn.execute("INSERT OR REPLACE INTO rate_window VALUES (?, ?, 1)", (bucket, stamp))
        else:
            conn.execute("UPDATE rate_window SET hits = hits + 1 WHERE bucket = ?", (bucket,))
        conn.execute("DELETE FROM rate_window WHERE started_at < ?", (stamp - 86400,))
        conn.commit()


def _rate_limit(user_id):
    _reserve_limit("analysis:" + user_id, RATE_LIMIT_MAX)


@asynccontextmanager
async def lifespan(app):
    storage.initialize(DB_PATH)
    yield


app = FastAPI(title="需记 · 项目需求协作平台", version="0.3.0", lifespan=lifespan)
origins = [s.strip() for s in os.getenv("CORS_ORIGINS", "").split(",") if s.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    conn = None
    token = None
    if request.url.path.startswith("/api/"):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                return Response('{"detail":"请求内容过大"}', status_code=413, media_type="application/json")
        request._body = bytes(body)
        conn = storage.connect(DB_PATH)
        token = storage.request_connection.set(conn)
    try:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        if conn is not None:
            conn.rollback()
            conn.close()
            storage.request_connection.reset(token)


def _session(user):
    token = auth.new_session_token()
    stamp = _now()
    expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    _db().execute("INSERT INTO session VALUES (?, ?, ?, ?)", (token, user["id"], stamp, expires))
    return {"token": token, "user": {"id": user["id"], "username": user["username"]}}


def _selected_sources(pid, ids):
    sources = _load_sources(pid)
    if not ids or len(ids) != len(set(ids)):
        raise HTTPException(422, "请选择不重复的材料")
    chosen = [s for s in sources if s["id"] in ids]
    if len(chosen) != len(ids):
        raise HTTPException(422, "材料不存在或不属于本项目")
    if sum(len(s["text"]) for s in chosen) > MAX_TOTAL_SOURCE_LENGTH:
        raise HTTPException(422, "所选材料过长，请分批分析")
    return chosen


def _guard_draft(pid, expected, parent=None, check_parent=False):
    draft = _load_draft(pid)
    revision = draft["updated_at"] if draft else None
    if expected != revision:
        raise HTTPException(409, "草稿已变化，请刷新项目后重新审核")
    if check_parent and _get_project_row(pid)["current_version_id"] != parent:
        raise HTTPException(409, "当前版本已变化，请刷新项目")
    return draft


@app.get("/api/health")
def health():
    _db().execute("SELECT 1")
    return {"ok": True, "version": app.version}


@app.post("/api/auth/register")
def register(body: RegisterRequest, request: Request):
    _reserve_limit("auth:" + (request.client.host if request.client else "unknown"), AUTH_RATE_LIMIT_MAX)
    username = body.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_一-鿿]{3,50}", username):
        raise HTTPException(422, "用户名需为 3–50 位中文、字母、数字或下划线")
    password_hash = auth.hash_password(body.password)
    with _db_lock:
        _db().execute("BEGIN IMMEDIATE")
        if _db().execute("SELECT 1 FROM user WHERE username=?", (username,)).fetchone():
            raise HTTPException(409, "用户名已存在")
        user = {"id": _new_id("USR"), "username": username}
        _db().execute("INSERT INTO user VALUES (?, ?, ?, ?)", (user["id"], username, password_hash, _now()))
        result = _session(user)
        _db().commit()
    return {**user, **result}


@app.post("/api/auth/login")
def login(body: LoginRequest, request: Request):
    _reserve_limit("auth:" + (request.client.host if request.client else "unknown"), AUTH_RATE_LIMIT_MAX)
    row = _db().execute("SELECT * FROM user WHERE username=?", (body.username.strip(),)).fetchone()
    if not row or not auth.verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    result = _session(row)
    _db().commit()
    return result


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        _db().execute("DELETE FROM session WHERE token=?", (authorization[7:].strip(),))
        _db().commit()
    return {"ok": True}


@app.get("/api/auth/me")
def me(authorization: str | None = Header(default=None)):
    return {"user": _get_current_user(authorization)}


@app.get("/api/projects")
def list_projects(authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    rows = (
        _db()
        .execute(
            """
        SELECT p.*, (SELECT MAX(seq) FROM version v WHERE v.project_id=p.id) AS current_version_seq
        FROM project p WHERE p.owner_id=? OR p.id IN (SELECT project_id FROM membership WHERE user_id=?)
        ORDER BY p.updated_at DESC""",
            (user["id"], user["id"]),
        )
        .fetchall()
    )
    return {"projects": [dict(r) for r in rows]}


@app.post("/api/projects")
def create_project(body: ProjectCreate, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    pid, stamp = _new_id("PRJ"), _now()
    _db().execute(
        "INSERT INTO project VALUES (?, ?, ?, ?, ?, NULL, 0)", (pid, body.name, user["id"], stamp, stamp)
    )
    _db().commit()
    return _project_full(pid)


@app.get("/api/projects/{pid}")
def get_project(pid: str, authorization: str | None = Header(default=None)):
    _require_project_access(pid, _get_current_user(authorization))
    return _project_full(pid)


@app.get("/api/projects/{pid}/members")
def list_members(pid: str, authorization: str | None = Header(default=None)):
    _require_project_access(pid, _get_current_user(authorization))
    return {"members": _project_full(pid)["members"]}


@app.post("/api/projects/{pid}/members")
def add_member(pid: str, body: MemberAddRequest, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    if not _is_project_owner(pid, user):
        raise HTTPException(403, "仅项目所有者可添加成员")
    target = _db().execute("SELECT id FROM user WHERE username=?", (body.username.strip(),)).fetchone()
    if not target:
        raise HTTPException(404, "用户不存在")
    _db().execute("INSERT OR IGNORE INTO membership VALUES (?, ?, 'member', ?)", (pid, target["id"], _now()))
    _db().commit()
    return {"ok": True}


@app.post("/api/projects/{pid}/sources")
def add_source(pid: str, body: SourceCreate, authorization: str | None = Header(default=None)):
    _require_project_access(pid, _get_current_user(authorization))
    text = body.text.strip()
    if not text or len(text) > MAX_SOURCE_LENGTH:
        raise HTTPException(422, f"材料不能为空，且不得超过 {MAX_SOURCE_LENGTH} 字符")
    sid = _new_id("SRC")
    is_demo = ENABLE_DEMO_MODE and text in (DEMO_SOURCE_001, DEMO_SOURCE_002)
    _db().execute(
        """
        INSERT INTO source (id, project_id, text, label, is_demo, created_at, source_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (sid, pid, text, body.label.strip() or "未命名材料", int(is_demo), _now(), body.source_type),
    )
    _touch_project(pid)
    _db().commit()
    return dict(_db().execute("SELECT * FROM source WHERE id=?", (sid,)).fetchone())


def _analyze_model(task, chosen, baseline, project):
    if ENABLE_DEMO_MODE and task == "extract" and len(chosen) == 1 and chosen[0]["text"] == DEMO_SOURCE_001:
        data = demo.extraction(chosen[0])
        return {"ok": True, "data": data, "demo_mode": True, "duration_ms": 0, "attempts": 0}
    if ENABLE_DEMO_MODE and task == "compare" and len(chosen) == 1 and chosen[0]["text"] == DEMO_SOURCE_002:
        return {
            "ok": True,
            "data": demo.comparison(baseline["snapshot"], chosen[0]),
            "demo_mode": True,
            "duration_ms": 0,
            "attempts": 0,
        }
    payload = [{"source_id": s["id"], "text": s["text"]} for s in chosen]
    messages = (
        prompts.build_extract_messages({"project_name": project["name"]}, payload)
        if task == "extract"
        else prompts.build_compare_messages({"version_id": baseline["id"], **baseline["snapshot"]}, payload)
    )
    schema = output_schema(ExtractResult if task == "extract" else CompareResult)
    validate = (
        (lambda data: validators.validate_extract(data, chosen))
        if task == "extract"
        else (lambda data: validators.validate_compare(data, baseline["snapshot"], chosen))
    )
    return {**llm.chat_json(messages, schema=schema, validate=validate), "demo_mode": False}


def _run_analysis(pid, body, user, task):
    project = _require_project_access(pid, user)
    chosen = _selected_sources(pid, body.source_ids)
    _rate_limit(user["id"])
    baseline = None
    draft_before = _load_draft(pid)
    revision = draft_before["updated_at"] if draft_before else None
    if task == "extract":
        if body.expected_updated_at != revision:
            raise HTTPException(409, "草稿已变化，请刷新后再整理")
    else:
        baseline_id = body.baseline_version_id or project["current_version_id"]
        baseline = next((v for v in _load_versions(pid) if v["id"] == baseline_id), None)
        if baseline is None:
            raise HTTPException(422, "请先确认需求版本，再分析新沟通")
    resp = _analyze_model(task, chosen, baseline, project)
    is_demo = resp["demo_mode"]
    status, cleaned = resp.get("kind", "ok"), None
    if resp["ok"]:
        try:
            cleaned = (
                validators.validate_extract(resp["data"], chosen)
                if task == "extract"
                else {"changes": validators.validate_compare(resp["data"], baseline["snapshot"], chosen)}
            )
            status = "ok"
        except validators.ValidationError:
            status = "invalid"
    metrics = {k: resp.get(k) for k in ("model", "usage", "duration_ms", "attempts", "cost", "currency")}
    with _db_lock:
        _db().execute("BEGIN IMMEDIATE")
        # 模型等待期间如有人修改了草稿/基线，保留分析记录但不覆盖人工结果。
        if status == "ok" and task == "extract":
            current = _load_draft(pid)
            if (current["updated_at"] if current else None) != revision or _get_project_row(pid)[
                "current_version_id"
            ] != project["current_version_id"]:
                status = "stale"
        rid = _store_analysis(
            pid,
            body.source_ids,
            task,
            baseline["id"] if baseline else None,
            status,
            is_demo,
            {**(cleaned or {}), **metrics},
            user,
            resp.get("model"),
        )
        draft = None
        if status == "ok" and task == "extract":
            draft = _save_draft(
                pid,
                project["current_version_id"],
                cleaned["requirements"],
                cleaned["questions"],
                rid,
                is_demo,
            )
        _touch_project(pid)
        _db().commit()
    errors = {
        "unconfigured": "智能整理暂不可用，材料已保存。请稍后重试，或在需求清单中手动整理。",
        "timeout": "模型请求超时，材料已保留，请稍后重试。",
        "http": "智能整理暂时无法完成，材料已保存。请稍后重试或联系系统管理员。",
        "invalid": "模型结果未通过格式或证据校验，材料已保留，可重试。",
        "stale": "分析期间草稿或版本已被修改，请刷新后重试。",
    }
    return {
        "status": status,
        "demo_mode": is_demo,
        "run_id": rid,
        **metrics,
        **(cleaned if status == "ok" else {}),
        "draft": draft,
        "baseline_version_id": baseline["id"] if baseline else None,
        "message": errors.get(status, "分析完成" if status == "ok" else "分析失败，材料已保留"),
    }


@app.post("/api/projects/{pid}/analyze")
def analyze(pid: str, body: AnalyzeRequest, authorization: str | None = Header(default=None)):
    return _run_analysis(pid, body, _get_current_user(authorization), "extract")


@app.post("/api/projects/{pid}/compare")
def compare(pid: str, body: CompareRequest, authorization: str | None = Header(default=None)):
    return _run_analysis(pid, body, _get_current_user(authorization), "compare")


@app.post("/api/projects/{pid}/draft")
def update_draft(pid: str, body: DraftUpdate, authorization: str | None = Header(default=None)):
    _require_project_access(pid, _get_current_user(authorization))
    reqs = [r.model_dump(exclude_none=True) for r in body.requirements]
    try:
        for r in reqs:
            validators.validate_evidence(r["evidence"], _load_sources(pid))
    except validators.ValidationError as e:
        raise HTTPException(422, str(e)) from e
    with _db_lock:
        _db().execute("BEGIN IMMEDIATE")
        previous = _guard_draft(pid, body.expected_updated_at, body.parent_version_id, check_parent=True)
        # 稳定 ID 只能沿用当前草稿中的 ID。
        allowed = {r.get("id") for r in (previous or {}).get("requirements", []) if r.get("id")}
        if body.parent_version_id:
            baseline = next(v for v in _load_versions(pid) if v["id"] == body.parent_version_id)
            allowed.update(r["id"] for r in baseline["snapshot"]["requirements"])
        supplied = [r["id"] for r in reqs if r.get("id")]
        if len(set(supplied)) != len(supplied) or any(rid not in allowed for rid in supplied):
            raise HTTPException(422, "需求 ID 无效或重复")
        draft = _save_draft(
            pid,
            body.parent_version_id,
            reqs,
            body.questions,
            (previous or {}).get("source_analysis_run_id"),
            (previous or {}).get("demo_mode", False),
        )
        _touch_project(pid)
        _db().commit()
    return draft


@app.post("/api/projects/{pid}/confirm")
def confirm_version(pid: str, body: ConfirmRequest, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    with _db_lock:
        _db().execute("BEGIN IMMEDIATE")
        prev, digest = _idem_get(
            user, pid, "confirm", body.request_id, body.model_dump(exclude={"request_id"})
        )
        if prev:
            return prev
        draft = _load_draft(pid)
        if not draft or not draft["requirements"]:
            raise HTTPException(422, "草稿为空，请先整理需求")
        if body.expected_updated_at is not None and body.expected_updated_at != draft["updated_at"]:
            raise HTTPException(409, "草稿已变化，请刷新后重新审核")
        if draft["parent_version_id"] != _get_project_row(pid)["current_version_id"]:
            raise HTTPException(409, "草稿基线已过期，请重新整理需求")
        version = _write_version(
            pid,
            _snapshot_of(draft["requirements"], draft["questions"]),
            user,
            draft["source_analysis_run_id"],
        )
        _idem_save(user, pid, "confirm", body.request_id, digest, version)
        _db().commit()
    return version


@app.post("/api/projects/{pid}/apply")
def apply_changes(pid: str, body: ApplyRequest, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    with _db_lock:
        _db().execute("BEGIN IMMEDIATE")
        prev, digest = _idem_get(user, pid, "apply", body.request_id, body.model_dump(exclude={"request_id"}))
        if prev:
            return prev
        run = (
            _db()
            .execute("SELECT * FROM analysis_run WHERE id=? AND project_id=?", (body.run_id, pid))
            .fetchone()
        )
        if not run:
            raise HTTPException(404, "分析记录不存在")
        if run["task_type"] != "compare" or run["status"] != "ok":
            raise HTTPException(422, "该记录不是有效的变更分析")
        if run["baseline_version_id"] != _get_project_row(pid)["current_version_id"]:
            raise HTTPException(409, "当前版本已变化，请重新分析新沟通")
        if _db().execute("SELECT 1 FROM change_decision WHERE analysis_run_id=?", (body.run_id,)).fetchone():
            raise HTTPException(409, "这次变更已审核，请重新分析")
        baseline = next(v for v in _load_versions(pid) if v["id"] == run["baseline_version_id"])
        changes = json.loads(run["result_json"])["changes"]
        if len(body.decisions) != len(changes):
            raise HTTPException(422, "请审核每一条变更候选")
        snapshot = apply_decisions(baseline["snapshot"], changes, [d.model_dump() for d in body.decisions])
        # 全部忽略或仅确认“未变”时，只保存审核结果，不制造空版本。
        version = (
            baseline if snapshot == baseline["snapshot"] else _write_version(pid, snapshot, user, body.run_id)
        )
        for d in body.decisions:
            _db().execute(
                "INSERT INTO change_decision VALUES (?, ?, ?, ?, ?, ?)",
                (_new_id("DEC"), body.run_id, d.change_index, d.decision, d.revised_after, _now()),
            )
        version = {**version, "created": version["id"] != baseline["id"]}
        _idem_save(user, pid, "apply", body.request_id, digest, version)
        _db().commit()
    return version


@app.get("/api/projects/{pid}/versions/{vid}")
def get_version(pid: str, vid: str, authorization: str | None = Header(default=None)):
    _require_project_access(pid, _get_current_user(authorization))
    version = next((v for v in _load_versions(pid) if v["id"] == vid), None)
    if version is None:
        raise HTTPException(404, "版本不存在")
    return version


@app.get("/api/projects/{pid}/export")
def export_markdown(
    pid: str, version_id: str | None = Query(default=None), authorization: str | None = Header(default=None)
):
    from .exports import build_markdown

    _require_project_access(pid, _get_current_user(authorization))
    project = _project_full(pid)
    version = next(
        (v for v in project["versions"] if v["id"] == (version_id or project["current_version_id"])), None
    )
    if not version:
        raise HTTPException(404, "尚无指定版本可导出")
    # 只导出目标版本及其祖先关联的审核记录，旧版本不会混入后续变更。
    lineage, cursor = [], version
    versions = {v["id"]: v for v in project["versions"]}
    while cursor:
        lineage.append(cursor)
        cursor = versions.get(cursor["parent_version_id"])
    runs = []
    for v in reversed(lineage):
        if v["source_analysis_run_id"]:
            run = (
                _db()
                .execute("SELECT * FROM analysis_run WHERE id=?", (v["source_analysis_run_id"],))
                .fetchone()
            )
            if run:
                decisions = [
                    dict(d)
                    for d in _db().execute(
                        "SELECT * FROM change_decision WHERE analysis_run_id=?", (run["id"],)
                    )
                ]
                runs.append({**dict(run), "decisions": decisions})
    return Response(
        build_markdown(project, version, lineage, runs), media_type="text/markdown; charset=utf-8"
    )


@app.get("/api/demo")
def public_demo():
    if not ENABLE_DEMO_MODE:
        raise HTTPException(404, "页面不存在")
    source1 = {
        "id": "DEMO-SRC-1",
        "text": DEMO_SOURCE_001,
        "label": "初始官网沟通",
        "source_type": "chat",
        "is_demo": True,
        "created_at": "2026-10-01T08:00:00+00:00",
    }
    source2 = {
        "id": "DEMO-SRC-2",
        "text": DEMO_SOURCE_002,
        "label": "第二次范围沟通",
        "source_type": "chat",
        "is_demo": True,
        "created_at": "2026-10-02T08:00:00+00:00",
    }
    snapshot = validators.validate_extract(demo.extraction(source1), [source1])
    for i, r in enumerate(snapshot["requirements"], 1):
        r["id"] = f"REQ-{i:04d}"
    version = {
        "id": "DEMO-V1",
        "seq": 1,
        "snapshot": snapshot,
        "parent_version_id": None,
        "confirmed_by": "示例项目负责人",
        "confirmed_at": source1["created_at"],
    }
    changes = validators.validate_compare(demo.comparison(snapshot, source2), snapshot, [source2])
    return {
        "id": "DEMO",
        "name": "品牌官网 · 示例项目",
        "owner_id": None,
        "current_version_id": version["id"],
        "sources": [source1, source2],
        "versions": [version],
        "members": [],
        "draft": {"requirements": [], "questions": [], "updated_at": None},
        "review": {
            "run_id": "DEMO-RUN",
            "baseline_version_id": version["id"],
            "changes": changes,
            "demo_mode": True,
            "decisions": [],
            "applied": False,
        },
        "read_only": True,
    }


app.mount("/", StaticFiles(directory=str(config.ROOT / "frontend"), html=True), name="frontend")
