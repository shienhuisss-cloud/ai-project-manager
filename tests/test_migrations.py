import json
import sqlite3
from pathlib import Path

import pytest

from backend import storage


def test_legacy_upgrade_keeps_versions_and_archives_unscoped_keys(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_schema = (Path(__file__).parent / "fixtures" / "legacy_schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(path)
    conn.executescript(legacy_schema)
    conn.execute("INSERT INTO user VALUES ('u','legacy','hash','2026-01-01')")
    conn.execute("INSERT INTO project VALUES ('p','旧项目','u','t','t','v',1)")
    snapshot = json.dumps({"requirements": [{"id": "REQ-0001", "title": "旧版"}], "questions": []})
    conn.execute("INSERT INTO version VALUES ('v','p',1,NULL,?,NULL,'legacy','t')", (snapshot,))
    conn.execute("INSERT INTO idempotency VALUES ('old-key','{}','t')")
    conn.commit()
    conn.close()
    storage.initialize(str(path))
    storage.initialize(str(path))
    conn = storage.connect(str(path))
    assert conn.execute("SELECT snapshot_json FROM version").fetchone()[0] == snapshot
    assert conn.execute("SELECT request_id FROM idempotency_legacy").fetchone()[0] == "old-key"
    assert conn.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0] == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
    assert list(conn.execute("PRAGMA foreign_key_check")) == []
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO membership VALUES ('p','missing-user','member','t')")
    conn.close()


def test_failed_migration_rolls_back_without_losing_data(tmp_path):
    path = tmp_path / "orphan.db"
    conn = sqlite3.connect(path)
    conn.executescript((Path(__file__).parent / "fixtures" / "legacy_schema.sql").read_text(encoding="utf-8"))
    conn.execute("INSERT INTO membership VALUES ('missing-project','missing-user','member','t')")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="迁移已回滚"):
        storage.initialize(str(path))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM membership").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()
