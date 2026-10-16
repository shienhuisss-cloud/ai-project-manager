import sqlite3
from contextlib import closing
from pathlib import Path

from backend import backup


def test_backup_and_restore_include_wal_and_keep_safety_copy(tmp_path, monkeypatch):
    db = tmp_path / "custom.db"
    monkeypatch.setattr(backup, "DB_PATH", str(db))
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "copies"))
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE records (value TEXT)")
        conn.execute("INSERT INTO records VALUES ('before')")
        conn.commit()
        saved = backup.backup()
        conn.execute("INSERT INTO records VALUES ('after')")
        conn.commit()
    backup.restore(saved)
    with closing(sqlite3.connect(db)) as restored:
        assert restored.execute("SELECT value FROM records").fetchall() == [("before",)]
    copies = list(tmp_path.glob("*.pre_restore.bak"))
    assert len(copies) == 1
    with closing(sqlite3.connect(copies[0])) as safety:
        assert safety.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    assert Path(saved).parent == tmp_path / "copies"
