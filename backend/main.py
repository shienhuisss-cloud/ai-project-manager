# -*- coding: utf-8 -*-
"""
AI 项目需求管家后端。

FastAPI 应用，包含用户认证、项目权限、需求提取与变更比较、版本管理。
数据存 SQLite。模型调用见 llm.py，校验见 validators.py。
"""

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth
from . import llm
from . import prompts
from . import validators

# 配置

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "app.db"))

# 输入上限（字符数）
MAX_SOURCE_LENGTH = int(os.environ.get("MAX_SOURCE_LENGTH", "50000"))
MAX_TOTAL_SOURCE_LENGTH = int(os.environ.get("MAX_TOTAL_SOURCE_LENGTH", "200000"))

# 请求限额（分析类接口：每分钟每用户次数）
RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_MAX = int(os.environ.get("RATE_LIMIT_MAX", "20"))

SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "168"))  # 默认 7 天

_db_lock = threading.RLock()
_conn = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS user (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS session (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS project (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            current_version_id TEXT,
            requirement_seq INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS membership (
            project_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL,
            PRIMARY KEY (project_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS source (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            text TEXT NOT NULL,
            label TEXT,
            is_demo INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS draft (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL UNIQUE,
            parent_version_id TEXT,
            requirements_json TEXT NOT NULL,
            questions_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS version (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            parent_version_id TEXT,
            snapshot_json TEXT NOT NULL,
            source_analysis_run_id TEXT,
            confirmed_by TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            UNIQUE(project_id, seq)
        );

        CREATE TABLE IF NOT EXISTS analysis_run (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_ids_json TEXT NOT NULL,
            baseline_version_id TEXT,
            task_type TEXT NOT NULL,
            model_id TEXT,
            prompt_version TEXT,
            status TEXT NOT NULL,
            demo_mode INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS change_decision (
            id TEXT PRIMARY KEY,
            analysis_run_id TEXT NOT NULL,
            change_index INTEGER NOT NULL,
            decision TEXT NOT NULL,
            revised_after TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(analysis_run_id, change_index)
        );

        CREATE TABLE IF NOT EXISTS idempotency (
            request_id TEXT PRIMARY KEY,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    # 迁移：老库的 project 表补 owner_id 列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(project)").fetchall()}
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE project ADD COLUMN owner_id TEXT")
    conn.commit()


# 工具

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _jload(s, default):
    if s is None:
        return default
    return json.loads(s)


def _jload_reqs(s):
    return _jload(s, [])


def _jload_qs(s):
    return _jload(s, [])


# 内置示例材料与演示结果（用于未配置模型时体验完整流程）

DEMO_SOURCE_001 = (
    "客户：做一个官网，展示我们的产品，让客户能联系我们。\n"
    "开发者：第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？\n"
    "客户：对。先不做在线支付，也不做会员。\n"
    "开发者：产品信息需要你们自己在后台维护吗？\n"
    "客户：这个还没想好，我们下次确定。"
)

DEMO_SOURCE_002 = (
    "客户：现在想加一个会员登录，老客户登录后能看到专属价格。\n"
    "客户：在线支付以后可能再考虑，这次先不要做。\n"
    "开发者：不同老客户看到的价格一样吗？\n"
    "客户：还没定，我问一下销售。"
)

DEMO_EXTRACTION = {
    "requirements": [
        {
            "title": "官网展示产品并可联系",
            "description": "做一个官网，展示产品，让客户能联系到我们。",
            "intent": "requested",
            "stated_acceptance_criteria": [],
            "evidence": [
                {"source_id": "SRC-001",
                 "quote": "做一个官网，展示我们的产品，让客户能联系我们。"}
            ],
            "questions": [],
        },
        {"title": "首页", "description": "官网第一版包含首页页面。", "intent": "requested",
         "stated_acceptance_criteria": [],
         "evidence": [{"source_id": "SRC-001",
                       "quote": "第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？"}],
         "questions": []},
        {"title": "产品列表", "description": "官网第一版包含产品列表页面。", "intent": "requested",
         "stated_acceptance_criteria": [],
         "evidence": [{"source_id": "SRC-001",
                       "quote": "第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？"}],
         "questions": []},
        {"title": "产品详情", "description": "官网第一版包含产品详情页面。", "intent": "requested",
         "stated_acceptance_criteria": [],
         "evidence": [{"source_id": "SRC-001",
                       "quote": "第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？"}],
         "questions": []},
        {"title": "联系我们", "description": "官网第一版包含联系我们页面。", "intent": "requested",
         "stated_acceptance_criteria": [],
         "evidence": [{"source_id": "SRC-001",
                       "quote": "第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？"}],
         "questions": []},
        {"title": "明确排除：在线支付", "description": "第一版不做在线支付。", "intent": "excluded",
         "stated_acceptance_criteria": [],
         "evidence": [{"source_id": "SRC-001", "quote": "对。先不做在线支付，也不做会员。"}],
         "questions": []},
        {"title": "明确排除：会员", "description": "第一版不做会员功能。", "intent": "excluded",
         "stated_acceptance_criteria": [],
         "evidence": [{"source_id": "SRC-001", "quote": "对。先不做在线支付，也不做会员。"}],
         "questions": []},
        {"title": "产品信息后台维护方式", "description": "产品信息是否由客户自己在后台维护，尚未确定。",
         "intent": "unclear",
         "stated_acceptance_criteria": [],
         "evidence": [{"source_id": "SRC-001", "quote": "这个还没想好，我们下次确定。"}],
         "questions": ["产品信息需要你们自己在后台维护吗？"]},
    ],
    "questions": ["产品信息需要你们自己在后台维护吗？"],
}


def _find_req_by_title(snapshot: dict, keyword: str):
    for r in snapshot.get("requirements", []):
        if keyword in (r.get("title") or ""):
            return r
    return None


def build_demo_comparison(baseline_snapshot: dict) -> list:
    """生成固定示例的变更候选，并把 related_requirement_ids 关联到基线版本真实 ID。"""
    member_req = _find_req_by_title(baseline_snapshot, "会员")
    payment_req = _find_req_by_title(baseline_snapshot, "在线支付")

    changes = [
        {
            "kind": "added",
            "related_requirement_ids": [member_req["id"]] if member_req else [],
            "proposed_after": "新增会员登录功能，老客户登录后可查看专属价格。",
            "reason": "新材料提出会员登录，与 V1 中“明确排除：会员”存在冲突，需人工决定是否纳入本期范围。",
            "evidence": [
                {"source_id": "SRC-002",
                 "quote": "客户：现在想加一个会员登录，老客户登录后能看到专属价格。"}
            ],
            "questions": ["是否推翻 V1 的“会员排除”，将会员登录纳入本期？"],
        },
        {
            "kind": "clarification",
            "related_requirement_ids": [],
            "proposed_after": "老客户专属价格规则待确认（不同老客户价格是否一致）。",
            "reason": "新材料提到专属价格，但价格规则尚未确定。",
            "evidence": [
                {"source_id": "SRC-002",
                 "quote": "开发者：不同老客户看到的价格一样吗？客户：还没定，我问一下销售。"}
            ],
            "questions": ["不同老客户看到的价格是否一样？"],
        },
        {
            "kind": "unchanged",
            "related_requirement_ids": [payment_req["id"]] if payment_req else [],
            "proposed_after": "在线支付仍为明确排除项，不纳入本期。",
            "reason": "新材料说明“在线支付以后可能再考虑，这次先不要做”，维持排除。",
            "evidence": [
                {"source_id": "SRC-002",
                 "quote": "客户：在线支付以后可能再考虑，这次先不要做。"}
            ],
            "questions": [],
        },
    ]
    for i, c in enumerate(changes):
        c["change_index"] = i
    return changes


# 数据访问辅助

def _get_project_row(pid: str) -> sqlite3.Row:
    row = _db().execute("SELECT * FROM project WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return row


# 认证与权限

def _get_current_user(authorization: str | None) -> dict:
    """从 Authorization: Bearer <token> 解析当前用户。未登录/过期返回 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization[len("Bearer "):].strip()
    row = _db().execute(
        "SELECT s.token, s.user_id, s.expires_at, u.username FROM session s "
        "JOIN user u ON u.id = s.user_id WHERE s.token = ?",
        (token,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="会话无效，请重新登录")
    if row["expires_at"] < _now():
        _db().execute("DELETE FROM session WHERE token = ?", (token,))
        _db().commit()
        raise HTTPException(status_code=401, detail="会话已过期，请重新登录")
    return {"id": row["user_id"], "username": row["username"]}


def _require_project_access(pid: str, user: dict) -> sqlite3.Row:
    """项目级访问控制：owner 或 membership 成员才可访问。否则 404（不泄露存在性）。"""
    row = _get_project_row(pid)
    if row["owner_id"] == user["id"]:
        return row
    m = _db().execute(
        "SELECT 1 FROM membership WHERE project_id = ? AND user_id = ?", (pid, user["id"])
    ).fetchone()
    if m is not None:
        return row
    # 用 404 而非 403，避免向他人泄露项目是否存在
    raise HTTPException(status_code=404, detail="项目不存在")


def _is_project_owner(pid: str, user: dict) -> bool:
    return _get_project_row(pid)["owner_id"] == user["id"]


# 请求限额（分析类接口）

def _rate_limit(user_id: str) -> None:
    """按用户限流：60 秒内分析类请求不超过 RATE_LIMIT_MAX 次。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=RATE_LIMIT_WINDOW_SEC)).isoformat()
    # 用内存中的 analysis_run 表做粗粒度计数（本地单实例足够）
    row = _db().execute(
        "SELECT COUNT(*) AS c FROM analysis_run WHERE project_id IN "
        "(SELECT id FROM project WHERE owner_id = ?) AND created_at > ?",
        (user_id, cutoff),
    ).fetchone()
    if row["c"] >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请稍后再试（每 {RATE_LIMIT_WINDOW_SEC} 秒最多 {RATE_LIMIT_MAX} 次分析）。",
        )


def _load_sources(pid: str) -> list:
    rows = _db().execute(
        "SELECT * FROM source WHERE project_id = ? ORDER BY created_at ASC", (pid,)
    ).fetchall()
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
    }


def _save_draft(pid: str, parent_version_id, requirements, questions) -> dict:
    conn = _db()
    existing = conn.execute("SELECT id FROM draft WHERE project_id = ?", (pid,)).fetchone()
    did = existing["id"] if existing else _new_id("DRF")
    now = _now()
    conn.execute(
        """
        INSERT INTO draft (id, project_id, parent_version_id, requirements_json, questions_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            parent_version_id = excluded.parent_version_id,
            requirements_json = excluded.requirements_json,
            questions_json = excluded.questions_json,
            updated_at = excluded.updated_at
        """,
        (did, pid, parent_version_id, json.dumps(requirements, ensure_ascii=False),
         json.dumps(questions, ensure_ascii=False), now),
    )
    conn.commit()
    return {
        "id": did, "parent_version_id": parent_version_id,
        "requirements": requirements, "questions": questions, "updated_at": now,
    }


def _load_versions(pid: str) -> list:
    rows = _db().execute(
        "SELECT * FROM version WHERE project_id = ? ORDER BY seq ASC", (pid,)
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "seq": r["seq"], "parent_version_id": r["parent_version_id"],
            "source_analysis_run_id": r["source_analysis_run_id"],
            "confirmed_by": r["confirmed_by"], "confirmed_at": r["confirmed_at"],
            "snapshot": json.loads(r["snapshot_json"]),
        })
    return out


def _project_full(pid: str) -> dict:
    row = _get_project_row(pid)
    versions = _load_versions(pid)
    latest = versions[-1] if versions else None
    members = _db().execute(
        "SELECT m.user_id, m.role, u.username FROM membership m JOIN user u ON u.id = m.user_id "
        "WHERE m.project_id = ?", (pid,)
    ).fetchall()
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
        "members": [{"user_id": m["user_id"], "username": m["username"], "role": m["role"]} for m in members],
    }


def _touch_project(pid: str):
    _db().execute("UPDATE project SET updated_at = ? WHERE id = ?", (_now(), pid))


# 版本快照工具

def _norm_req(r: dict) -> dict:
    return {
        "title": r.get("title"),
        "description": r.get("description"),
        "intent": r.get("intent"),
        "stated_acceptance_criteria": r.get("stated_acceptance_criteria") or [],
        "evidence": r.get("evidence") or [],
        "questions": r.get("questions") or [],
    }


def _snapshot_of(draft_reqs, draft_qs) -> dict:
    return {
        "requirements": [dict(r) for r in draft_reqs],
        "questions": list(draft_qs),
    }


def _snapshots_equal(a: dict, b: dict) -> bool:
    na = {"requirements": [_norm_req(r) for r in a.get("requirements", [])],
          "questions": list(a.get("questions", []))}
    nb = {"requirements": [_norm_req(r) for r in b.get("requirements", [])],
          "questions": list(b.get("questions", []))}
    return na == nb


def _assign_requirement_ids(conn, pid: str, requirements: list) -> None:
    """为没有 id 的需求分配稳定 ID，并推进项目级序号。"""
    row = conn.execute("SELECT requirement_seq FROM project WHERE id = ?", (pid,)).fetchone()
    seq = row["requirement_seq"]
    for r in requirements:
        if not r.get("id"):
            seq += 1
            r["id"] = f"REQ-{seq:04d}"
    conn.execute("UPDATE project SET requirement_seq = ? WHERE id = ?", (seq, pid))


# 应用与请求模型

app = FastAPI(title="AI 项目需求管家")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=200)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class MemberAddRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)


class SourceCreate(BaseModel):
    text: str = Field(min_length=1)
    label: str = ""


class DraftUpdate(BaseModel):
    requirements: list = []
    questions: list = []


class AnalyzeRequest(BaseModel):
    source_ids: list[str] = []


class ConfirmRequest(BaseModel):
    request_id: str | None = None


class CompareRequest(BaseModel):
    source_ids: list[str] = []
    baseline_version_id: str | None = None


class ApplyRequest(BaseModel):
    run_id: str
    request_id: str | None = None
    decisions: list[dict] = []


def _is_demo_source(text: str) -> bool:
    t = _normalize(text)
    return t == _normalize(DEMO_SOURCE_001) or t == _normalize(DEMO_SOURCE_002)


def _friendly_error(resp: dict) -> str:
    """把 llm.chat_json 的错误结果转成用户可读的中文提示。"""
    kind = resp.get("kind")
    if kind == "unconfigured":
        return "尚未配置模型。请在项目根目录创建 .env 文件（参考 .env.example），填写 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 后重启服务。你的输入已被保留。"
    if kind == "timeout":
        return "模型请求超时或网络错误，已自动重试一次仍失败。请稍后重试。你的输入已被保留。"
    if kind == "http":
        return f"模型服务返回错误（{resp.get('error')}）。请检查密钥是否有效、额度是否充足。你的输入已被保留。"
    if kind == "invalid":
        return "模型返回的内容无法解析为有效结果，已重试一次仍失败。你的输入已被保留，可再次尝试。"
    return f"模型调用失败：{resp.get('error')}。你的输入已被保留。"


def _store_analysis(pid, source_ids, task_type, baseline_version_id, status, demo_mode, result,
                    model_id=None, prompt_version=None) -> str:
    rid = _new_id("RUN")
    _db().execute(
        """
        INSERT INTO analysis_run
        (id, project_id, source_ids_json, baseline_version_id, task_type, model_id, prompt_version,
         status, demo_mode, result_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (rid, pid, json.dumps(source_ids, ensure_ascii=False), baseline_version_id,
         task_type, model_id, prompt_version, status, 1 if demo_mode else 0,
         json.dumps(result, ensure_ascii=False) if result is not None else None, _now()),
    )
    _db().commit()
    return rid


# 路由

@app.get("/api/health")
def health():
    return {"ok": True, "db": os.path.abspath(DB_PATH),
            "llm": llm.config_summary()}


# 认证

@app.post("/api/auth/register")
def register(body: RegisterRequest):
    username = body.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_一-鿿]{3,50}", username):
        raise HTTPException(status_code=422, detail="用户名只能包含字母、数字、下划线或中文，3–50 个字符")
    with _db_lock:
        existing = _db().execute("SELECT 1 FROM user WHERE username = ?", (username,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="用户名已存在")
        uid = _new_id("USR")
        _db().execute(
            "INSERT INTO user (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (uid, username, auth.hash_password(body.password), _now()),
        )
        _db().commit()
    return {"id": uid, "username": username}


@app.post("/api/auth/login")
def login(body: LoginRequest):
    row = _db().execute(
        "SELECT * FROM user WHERE username = ?", (body.username.strip(),)
    ).fetchone()
    if row is None or not auth.verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = auth.new_session_token()
    now = _now()
    expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    _db().execute(
        "INSERT INTO session (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, row["id"], now, expires),
    )
    _db().commit()
    return {"token": token, "user": {"id": row["id"], "username": row["username"]}}


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):].strip()
        _db().execute("DELETE FROM session WHERE token = ?", (token,))
        _db().commit()
    return {"ok": True}


@app.get("/api/auth/me")
def me(authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    return {"user": user}


# 项目

@app.get("/api/projects")
def list_projects(authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    rows = _db().execute(
        """
        SELECT p.* FROM project p
        WHERE p.owner_id = ? OR p.id IN (SELECT project_id FROM membership WHERE user_id = ?)
        ORDER BY p.updated_at DESC
        """,
        (user["id"], user["id"]),
    ).fetchall()
    items = []
    for p in rows:
        versions = _load_versions(p["id"])
        latest = versions[-1] if versions else None
        items.append({
            "id": p["id"], "name": p["name"], "created_at": p["created_at"],
            "current_version_seq": latest["seq"] if latest else None,
            "updated_at": p["updated_at"],
        })
    return {"projects": items}


@app.post("/api/projects")
def create_project(body: ProjectCreate, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    pid = _new_id("PRJ")
    now = _now()
    _db().execute(
        "INSERT INTO project (id, name, owner_id, created_at, updated_at, current_version_id, requirement_seq) "
        "VALUES (?, ?, ?, ?, ?, NULL, 0)",
        (pid, body.name.strip(), user["id"], now, now),
    )
    _db().commit()
    return _project_full(pid)


@app.get("/api/projects/{pid}")
def get_project(pid: str, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    return _project_full(pid)


@app.get("/api/projects/{pid}/members")
def list_members(pid: str, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    return {"members": _project_full(pid)["members"]}


@app.post("/api/projects/{pid}/members")
def add_member(pid: str, body: MemberAddRequest, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    if not _is_project_owner(pid, user):
        raise HTTPException(status_code=403, detail="仅项目所有者可添加成员")
    target = _db().execute("SELECT * FROM user WHERE username = ?", (body.username.strip(),)).fetchone()
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _db().execute(
        "INSERT OR IGNORE INTO membership (project_id, user_id, role, created_at) VALUES (?, ?, 'member', ?)",
        (pid, target["id"], _now()),
    )
    _db().commit()
    return {"ok": True}


@app.post("/api/projects/{pid}/sources")
def add_source(pid: str, body: SourceCreate, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="材料内容不能为空")
    if len(text) > MAX_SOURCE_LENGTH:
        raise HTTPException(status_code=422, detail=f"材料过长，单条材料上限 {MAX_SOURCE_LENGTH} 字符，请分批提交")
    src_id = _new_id("SRC")
    _db().execute(
        "INSERT INTO source (id, project_id, text, label, is_demo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (src_id, pid, text, body.label.strip() or "未命名材料", 1 if _is_demo_source(text) else 0, _now()),
    )
    _db().commit()
    _touch_project(pid)
    _db().commit()
    return dict(_db().execute("SELECT * FROM source WHERE id = ?", (src_id,)).fetchone())


@app.post("/api/projects/{pid}/analyze")
def analyze(pid: str, body: AnalyzeRequest, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    _rate_limit(user["id"])
    source_ids = body.source_ids
    sources = _load_sources(pid)
    chosen = [s for s in sources if s["id"] in source_ids]
    if not chosen:
        raise HTTPException(status_code=422, detail="请先添加或选择要分析的原始材料")
    total_len = sum(len(s["text"]) for s in chosen)
    if total_len > MAX_TOTAL_SOURCE_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"待分析材料总长度超过上限 {MAX_TOTAL_SOURCE_LENGTH} 字符，请分批处理（不能悄悄截断）。",
        )

    # 固定示例材料走内置结果（明确标注示例模式）
    all_demo = all(s["is_demo"] for s in chosen)
    if all_demo:
        reqs = [dict(r) for r in DEMO_EXTRACTION["requirements"]]
        qs = list(DEMO_EXTRACTION["questions"])
        _store_analysis(pid, source_ids, "extract", None, "ok", True, {"requirements": reqs, "questions": qs})
        _save_draft(pid, None, reqs, qs)
        _touch_project(pid)
        _db().commit()
        return {"demo_mode": True, "requirements": reqs, "questions": qs}

    # 真实模型分析
    project_context = {"project_id": pid, "project_name": _get_project_row(pid)["name"]}
    sources_payload = [{"source_id": s["id"], "text": s["text"]} for s in chosen]
    messages = prompts.build_extract_messages(project_context, sources_payload)
    resp = llm.chat_json(messages, retry=1)

    if not resp["ok"]:
        # 保留输入，明确报错，不伪装成功
        _store_analysis(pid, source_ids, "extract", None, resp["kind"] or "error", False, None,
                        model_id=resp.get("model"), prompt_version=prompts.PROMPT_VERSION)
        _touch_project(pid)
        _db().commit()
        return {
            "demo_mode": False, "status": resp["kind"],
            "message": _friendly_error(resp),
            "requirements": [], "questions": [],
        }

    # 结构化校验 + 证据/ID 检查
    try:
        cleaned = validators.validate_extract(resp["data"], chosen)
    except validators.ValidationError as e:
        _store_analysis(pid, source_ids, "extract", None, "invalid", False, None,
                        model_id=resp.get("model"), prompt_version=prompts.PROMPT_VERSION)
        _touch_project(pid)
        _db().commit()
        return {
            "demo_mode": False, "status": "invalid",
            "message": f"模型输出校验失败：{e}（原始输入已保留，可重试）",
            "requirements": [], "questions": [],
        }

    stored = dict(cleaned)
    stored["_usage"] = resp.get("usage")
    stored["_duration_ms"] = resp.get("duration_ms")
    _store_analysis(pid, source_ids, "extract", None, "ok", False, stored,
                    model_id=resp.get("model"), prompt_version=prompts.PROMPT_VERSION)
    _save_draft(pid, None, cleaned["requirements"], cleaned["questions"])
    _touch_project(pid)
    _db().commit()
    return {"demo_mode": False, "status": "ok",
            "requirements": cleaned["requirements"], "questions": cleaned["questions"],
            "model": resp.get("model"), "usage": resp.get("usage"), "duration_ms": resp.get("duration_ms")}


@app.post("/api/projects/{pid}/draft")
def update_draft(pid: str, body: DraftUpdate, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    draft = _save_draft(pid, None, body.requirements, body.questions)
    _touch_project(pid)
    _db().commit()
    return draft


@app.post("/api/projects/{pid}/confirm")
def confirm_version(pid: str, body: ConfirmRequest, authorization: str | None = Header(default=None)):
    """人工确认：把当前草稿冻结为不可修改的正式版本（事务 + 幂等）。"""
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    conn = _db()
    row = _get_project_row(pid)

    with _db_lock:
        # 幂等：同一 request_id 的重复提交直接返回已保存的完整结果（必须先于任何校验）
        if body.request_id:
            prev = conn.execute(
                "SELECT response_json FROM idempotency WHERE request_id = ?", (body.request_id,)
            ).fetchone()
            if prev:
                return json.loads(prev["response_json"])

        draft = _load_draft(pid)
        if not draft or not draft.get("requirements"):
            raise HTTPException(status_code=422, detail="草稿为空，请先整理需求再确认版本")

        # 内容去重：草稿与最新版本相同则不创建新版本
        versions = _load_versions(pid)
        if versions:
            latest = versions[-1]
            draft_snap = _snapshot_of(draft["requirements"], draft["questions"])
            if _snapshots_equal(latest["snapshot"], draft_snap):
                resp = latest
                if body.request_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO idempotency (request_id, response_json, created_at) VALUES (?, ?, ?)",
                        (body.request_id, json.dumps(resp, ensure_ascii=False), _now()),
                    )
                    conn.commit()
                return resp

        # 生成版本
        snapshot = _snapshot_of(draft["requirements"], draft["questions"])
        _assign_requirement_ids(conn, pid, snapshot["requirements"])
        seq = len(versions) + 1
        parent_id = row["current_version_id"]
        version_id = _new_id("VER")
        confirmed_at = _now()
        conn.execute(
            """
            INSERT INTO version (id, project_id, seq, parent_version_id, snapshot_json,
                                 source_analysis_run_id, confirmed_by, confirmed_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (version_id, pid, seq, parent_id, json.dumps(snapshot, ensure_ascii=False),
             user["username"], confirmed_at),
        )
        conn.execute("UPDATE project SET current_version_id = ?, updated_at = ? WHERE id = ?",
                     (version_id, confirmed_at, pid))
        # 确认后清空草稿，避免重复确认再次创建版本（草稿已被冻结为正式版本）
        conn.execute(
            "UPDATE draft SET parent_version_id = ?, requirements_json = '[]', questions_json = '[]', updated_at = ? WHERE project_id = ?",
            (version_id, confirmed_at, pid),
        )
        version = {
            "id": version_id, "seq": seq, "parent_version_id": parent_id,
            "source_analysis_run_id": None, "confirmed_by": user["username"],
            "confirmed_at": confirmed_at, "snapshot": snapshot,
        }
        if body.request_id:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency (request_id, response_json, created_at) VALUES (?, ?, ?)",
                (body.request_id, json.dumps(version, ensure_ascii=False), _now()),
            )
        conn.commit()

    return version


@app.post("/api/projects/{pid}/compare")
def compare(pid: str, body: CompareRequest, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    _rate_limit(user["id"])
    source_ids = body.source_ids
    sources = _load_sources(pid)
    chosen = [s for s in sources if s["id"] in source_ids]
    if not chosen:
        raise HTTPException(status_code=422, detail="请先添加或选择要分析的新材料")
    total_len = sum(len(s["text"]) for s in chosen)
    if total_len > MAX_TOTAL_SOURCE_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"待分析材料总长度超过上限 {MAX_TOTAL_SOURCE_LENGTH} 字符，请分批处理。",
        )

    baseline_version_id = body.baseline_version_id or _get_project_row(pid)["current_version_id"]
    if not baseline_version_id:
        raise HTTPException(status_code=422, detail="尚无已确认版本，请先整理并确认 V1")

    versions = _load_versions(pid)
    baseline = next((v for v in versions if v["id"] == baseline_version_id), None)
    if baseline is None:
        raise HTTPException(status_code=404, detail="基线版本不存在")

    # 固定示例材料走内置结果（明确标注示例模式）
    all_demo = all(s["is_demo"] for s in chosen)
    if all_demo:
        changes = build_demo_comparison(baseline["snapshot"])
        run_id = _store_analysis(pid, source_ids, "compare", baseline_version_id, "ok", True,
                                 {"changes": changes})
        _touch_project(pid)
        _db().commit()
        return {"demo_mode": True, "status": "ok", "run_id": run_id,
                "baseline_version_id": baseline_version_id, "changes": changes}

    # 真实模型比较
    baseline_payload = {
        "version_id": baseline["id"],
        "requirements": baseline["snapshot"]["requirements"],
        "questions": baseline["snapshot"]["questions"],
    }
    sources_payload = [{"source_id": s["id"], "text": s["text"]} for s in chosen]
    messages = prompts.build_compare_messages(baseline_payload, sources_payload)
    resp = llm.chat_json(messages, retry=1)

    if not resp["ok"]:
        run_id = _store_analysis(pid, source_ids, "compare", baseline_version_id,
                                 resp["kind"] or "error", False, None,
                                 model_id=resp.get("model"), prompt_version=prompts.PROMPT_VERSION)
        _touch_project(pid)
        _db().commit()
        return {
            "demo_mode": False, "status": resp["kind"], "run_id": run_id,
            "message": _friendly_error(resp), "changes": [],
            "baseline_version_id": baseline_version_id,
        }

    try:
        changes = validators.validate_compare(resp["data"], baseline["snapshot"], chosen)
    except validators.ValidationError as e:
        run_id = _store_analysis(pid, source_ids, "compare", baseline_version_id,
                                 "invalid", False, None,
                                 model_id=resp.get("model"), prompt_version=prompts.PROMPT_VERSION)
        _touch_project(pid)
        _db().commit()
        return {
            "demo_mode": False, "status": "invalid", "run_id": run_id,
            "message": f"模型输出校验失败：{e}（原始输入已保留，可重试）", "changes": [],
            "baseline_version_id": baseline_version_id,
        }

    stored = {"changes": changes, "_usage": resp.get("usage"), "_duration_ms": resp.get("duration_ms")}
    run_id = _store_analysis(pid, source_ids, "compare", baseline_version_id,
                             "ok", False, stored,
                             model_id=resp.get("model"), prompt_version=prompts.PROMPT_VERSION)
    _touch_project(pid)
    _db().commit()
    return {"demo_mode": False, "status": "ok", "run_id": run_id,
            "baseline_version_id": baseline_version_id, "changes": changes,
            "model": resp.get("model"), "usage": resp.get("usage"), "duration_ms": resp.get("duration_ms")}


@app.post("/api/projects/{pid}/apply")
def apply_changes(pid: str, body: ApplyRequest, authorization: str | None = Header(default=None)):
    """根据采纳项生成新版本：在同一事务内检查基线、生成快照、写版本、保存选择。"""
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    conn = _db()
    row = _get_project_row(pid)
    run_row = conn.execute("SELECT * FROM analysis_run WHERE id = ? AND project_id = ?",
                           (body.run_id, pid)).fetchone()
    if run_row is None:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    if run_row["task_type"] != "compare":
        raise HTTPException(status_code=422, detail="该分析记录不是变更比较")

    baseline_version_id = run_row["baseline_version_id"]
    result = json.loads(run_row["result_json"] or "{}")
    changes = result.get("changes", [])
    change_by_index = {c.get("change_index"): c for c in changes}

    accepted = []
    for d in body.decisions:
        idx = d.get("change_index")
        c = change_by_index.get(idx)
        if c and d.get("decision") == "accept":
            accepted.append((idx, c, d.get("revised_after")))

    if not accepted:
        raise HTTPException(status_code=422, detail="没有选择任何采纳项")

    with _db_lock:
        # 幂等：同一 request_id 重复提交直接返回已保存版本
        if body.request_id:
            prev = conn.execute(
                "SELECT response_json FROM idempotency WHERE request_id = ?", (body.request_id,)
            ).fetchone()
            if prev:
                return json.loads(prev["response_json"])

        # 采纳前确认基线仍是当前版本，防用过期分析覆盖新版本
        if row["current_version_id"] != baseline_version_id:
            raise HTTPException(
                status_code=409,
                detail="当前版本已变化，本次分析基于旧版本，请重新“分析新沟通”。",
            )

        versions = _load_versions(pid)
        baseline = next((v for v in versions if v["id"] == baseline_version_id), None)
        base_reqs = [dict(r) for r in baseline["snapshot"]["requirements"]]
        base_qs = list(baseline["snapshot"]["questions"])
        base_by_id = {r.get("id"): r for r in base_reqs}

        # 逐条采纳，应用到快照副本
        new_reqs = list(base_reqs)
        new_qs = list(base_qs)
        applied_notes = []

        for idx, c, revised in accepted:
            kind = c.get("kind")
            proposed = revised if revised else c.get("proposed_after", "")
            rel_ids = c.get("related_requirement_ids") or []
            if kind == "added":
                new_reqs.append({
                    "title": c.get("title") or "新增需求",
                    "description": proposed,
                    "intent": "requested",
                    "stated_acceptance_criteria": [],
                    "evidence": c.get("evidence") or [],
                    "questions": c.get("questions") or [],
                })
            elif kind == "modified":
                target = base_by_id.get(rel_ids[0]) if rel_ids else None
                if target:
                    target["description"] = proposed
            elif kind == "removed":
                new_reqs = [r for r in new_reqs if r.get("id") not in rel_ids]
            elif kind in ("clarification", "uncertain", "unchanged"):
                if proposed and proposed not in new_qs:
                    new_qs.append(proposed)
            applied_notes.append({"change_index": idx, "kind": kind, "decision": "accept",
                                  "proposed_after": proposed})

        # 分配新稳定 ID
        _assign_requirement_ids(conn, pid, new_reqs)

        # 写入版本
        snapshot = {"requirements": new_reqs, "questions": new_qs}
        seq = len(versions) + 1
        version_id = _new_id("VER")
        confirmed_at = _now()
        conn.execute(
            """
            INSERT INTO version (id, project_id, seq, parent_version_id, snapshot_json,
                                 source_analysis_run_id, confirmed_by, confirmed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (version_id, pid, seq, baseline_version_id, json.dumps(snapshot, ensure_ascii=False),
             body.run_id, user["username"], confirmed_at),
        )
        conn.execute("UPDATE project SET current_version_id = ?, updated_at = ? WHERE id = ?",
                     (version_id, confirmed_at, pid))

        # 确认后清空草稿，避免重复确认再次创建版本
        conn.execute(
            "UPDATE draft SET parent_version_id = ?, requirements_json = '[]', questions_json = '[]', updated_at = ? WHERE project_id = ?",
            (version_id, confirmed_at, pid),
        )

        # 保存所有人工选择（含忽略项，便于审计）
        decided = {d.get("change_index"): d for d in body.decisions}
        for idx, c in change_by_index.items():
            d = decided.get(idx)
            if d:
                conn.execute(
                    """
                    INSERT INTO change_decision (id, analysis_run_id, change_index, decision, revised_after, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (_new_id("DEC"), body.run_id, idx, d.get("decision", "ignore"),
                     d.get("revised_after"), _now()),
                )

        version = {
            "id": version_id, "seq": seq, "parent_version_id": baseline_version_id,
            "source_analysis_run_id": body.run_id, "confirmed_by": user["username"],
            "confirmed_at": confirmed_at, "snapshot": snapshot,
        }
        if body.request_id:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency (request_id, response_json, created_at) VALUES (?, ?, ?)",
                (body.request_id, json.dumps(version, ensure_ascii=False), _now()),
            )
        conn.commit()

    return version


@app.get("/api/projects/{pid}/versions/{vid}")
def get_version(pid: str, vid: str, authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    row = _db().execute("SELECT * FROM version WHERE id = ? AND project_id = ?", (vid, pid)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {
        "id": row["id"], "seq": row["seq"], "parent_version_id": row["parent_version_id"],
        "source_analysis_run_id": row["source_analysis_run_id"],
        "confirmed_by": row["confirmed_by"], "confirmed_at": row["confirmed_at"],
        "snapshot": json.loads(row["snapshot_json"]),
    }


# Markdown 导出

INTENT_LABEL = {"requested": "提出", "hypothetical": "设想", "excluded": "排除", "unclear": "待澄清"}


def _fmt_dt(iso):
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def _build_markdown(pid: str, version_id: str | None) -> str:
    proj = _project_full(pid)
    versions = proj["versions"]
    if not versions:
        raise HTTPException(status_code=404, detail="尚无版本可导出")

    if version_id:
        target = next((v for v in versions if v["id"] == version_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="版本不存在")
    else:
        target = versions[-1]

    snap = target["snapshot"]
    reqs = snap.get("requirements", [])
    qs = snap.get("questions", [])

    lines = []
    lines.append(f"# {proj['name']} — 需求文档")
    lines.append("")
    lines.append(f"- 导出时间：{_fmt_dt(_now())}")
    lines.append(f"- 版本：V{target['seq']}")
    lines.append(f"- 确认时间：{_fmt_dt(target['confirmed_at'])}")
    lines.append(f"- 确认操作人：{target['confirmed_by']}")
    lines.append("")

    lines.append("## 需求清单")
    lines.append("")
    if not reqs:
        lines.append("（空）")
    for i, r in enumerate(reqs, 1):
        intent = INTENT_LABEL.get(r.get("intent"), r.get("intent", ""))
        lines.append(f"### {i}. {r.get('title', '')}  `{intent}`")
        lines.append("")
        lines.append(f"- 需求 ID：{r.get('id', '—')}")
        if r.get("description"):
            lines.append(f"- 说明：{r['description']}")
        crit = r.get("stated_acceptance_criteria") or []
        if crit:
            lines.append("- 明确验收条件：" + "；".join(crit))
        ev = r.get("evidence") or []
        if ev:
            lines.append("- 证据：")
            for e in ev:
                lines.append(f"  - {e.get('source_id', '')}：「{e.get('quote', '')}」")
        rq = r.get("questions") or []
        if rq:
            lines.append("- 待澄清：" + "；".join(rq))
        lines.append("")

    lines.append("## 项目级待澄清问题")
    lines.append("")
    if not qs:
        lines.append("（无）")
    for q in qs:
        lines.append(f"- {q}")
    lines.append("")

    # 历史版本
    lines.append("## 历史版本")
    lines.append("")
    for v in versions:
        mark = "（当前）" if v["id"] == proj["current_version_id"] else ""
        lines.append(f"- V{v['seq']} — 确认于 {_fmt_dt(v['confirmed_at'])} {mark}")
    lines.append("")

    # 变更记录（来自分析记录 + 人工选择）
    runs = _db().execute(
        "SELECT * FROM analysis_run WHERE project_id = ? AND task_type = 'compare' ORDER BY created_at ASC",
        (pid,),
    ).fetchall()
    if runs:
        lines.append("## 变更记录")
        lines.append("")
        for run in runs:
            result = json.loads(run["result_json"] or "{}")
            changes = result.get("changes", [])
            dec_rows = _db().execute(
                "SELECT * FROM change_decision WHERE analysis_run_id = ?", (run["id"],)
            ).fetchall()
            dec_by_idx = {d["change_index"]: d for d in dec_rows}
            for c in changes:
                d = dec_by_idx.get(c.get("change_index"))
                if d is not None:
                    decision = "采纳" if d["decision"] == "accept" else "忽略"
                else:
                    decision = "未决定"
                lines.append(f"- [{c.get('kind')}] {c.get('proposed_after', '')}")
                suffix = ""
                if d is not None and d["revised_after"]:
                    suffix = f"，修订：{d['revised_after']}"
                lines.append(f"  - 人工决定：{decision}{suffix}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


@app.get("/api/projects/{pid}/export")
def export_markdown(pid: str, version_id: str | None = Query(default=None),
                    authorization: str | None = Header(default=None)):
    user = _get_current_user(authorization)
    _require_project_access(pid, user)
    md = _build_markdown(pid, version_id)
    return Response(content=md, media_type="text/markdown; charset=utf-8")


# 静态前端
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
