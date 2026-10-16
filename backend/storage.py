"""SQLite 连接、版本迁移与请求事务。每个请求独立连接。"""

import sqlite3
from contextvars import ContextVar

SCHEMA_VERSION = 1
SCHEMA = {
    "user": """id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL""",
    "session": """token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES user(id),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL""",
    "project": """id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_id TEXT REFERENCES user(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            current_version_id TEXT REFERENCES version(id),
            requirement_seq INTEGER NOT NULL DEFAULT 0""",
    "membership": """project_id TEXT NOT NULL REFERENCES project(id),
            user_id TEXT NOT NULL REFERENCES user(id),
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL,
            PRIMARY KEY (project_id, user_id)""",
    "source": """id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES project(id),
            text TEXT NOT NULL,
            label TEXT,
            is_demo INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
source_type TEXT NOT NULL DEFAULT 'chat'""",
    "draft": """id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES project(id) UNIQUE,
            parent_version_id TEXT REFERENCES version(id),
            requirements_json TEXT NOT NULL,
            questions_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
source_analysis_run_id TEXT REFERENCES analysis_run(id),
demo_mode INTEGER NOT NULL DEFAULT 0""",
    "version": """id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES project(id),
            seq INTEGER NOT NULL,
            parent_version_id TEXT REFERENCES version(id),
            snapshot_json TEXT NOT NULL,
            source_analysis_run_id TEXT REFERENCES analysis_run(id),
            confirmed_by TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            UNIQUE(project_id, seq)""",
    "analysis_run": """id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES project(id),
            source_ids_json TEXT NOT NULL,
            baseline_version_id TEXT REFERENCES version(id),
            task_type TEXT NOT NULL,
            model_id TEXT,
            prompt_version TEXT,
            status TEXT NOT NULL,
            demo_mode INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            created_at TEXT NOT NULL,
actor_id TEXT REFERENCES user(id)""",
    "change_decision": """id TEXT PRIMARY KEY,
            analysis_run_id TEXT NOT NULL REFERENCES analysis_run(id),
            change_index INTEGER NOT NULL,
            decision TEXT NOT NULL,
            revised_after TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(analysis_run_id, change_index)""",
    "idempotency": """user_id TEXT NOT NULL REFERENCES user(id),
project_id TEXT NOT NULL REFERENCES project(id),
operation TEXT NOT NULL,
request_id TEXT NOT NULL,
request_hash TEXT NOT NULL,
response_json TEXT NOT NULL,
created_at TEXT NOT NULL,
PRIMARY KEY (user_id, project_id, operation, request_id)""",
    "rate_window": """bucket TEXT PRIMARY KEY, started_at REAL NOT NULL, hits INTEGER NOT NULL""",
}
request_connection = ContextVar("request_connection", default=None)


def connect(path):
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def initialize(path):
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("数据库版本高于当前程序，请使用匹配的新版本代码")
        if version == SCHEMA_VERSION:
            return
        # 首次升级在一笔事务内重建旧表。旧幂等记录无法推断权限域，保留为归档表。
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "project" in existing:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(project)")}
            if "owner_id" not in columns:
                conn.execute("ALTER TABLE project ADD COLUMN owner_id TEXT")
        if "idempotency" in existing:
            conn.execute("ALTER TABLE idempotency RENAME TO idempotency_legacy")
            existing.remove("idempotency")
        for name, columns in SCHEMA.items():
            if name not in existing:
                conn.execute(f"CREATE TABLE {name} ({columns})")
                continue
            replacement = name + "_migrating"
            conn.execute(f"CREATE TABLE {replacement} ({columns})")
            old_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({name})")}
            shared = [r[1] for r in conn.execute(f"PRAGMA table_info({replacement})") if r[1] in old_cols]
            names = ", ".join(shared)
            conn.execute(f"INSERT INTO {replacement} ({names}) SELECT {names} FROM {name}")
            conn.execute(f"DROP TABLE {name}")
            conn.execute(f"ALTER TABLE {replacement} RENAME TO {name}")
        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError("数据库存在孤立记录，迁移已回滚。请备份后修复关联数据。")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS analysis_project_time ON analysis_run(project_id, created_at)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS source_project_time ON source(project_id, created_at)")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def current():
    conn = request_connection.get()
    if conn is None:
        raise RuntimeError("数据库连接仅在请求上下文中使用")
    return conn
