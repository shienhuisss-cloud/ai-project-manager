# -*- coding: utf-8 -*-
"""数据备份和恢复脚本。

用法：
  python backend/backup.py backup            备份 app.db 到 backups/
  python backend/backup.py restore <文件>     从备份恢复（覆盖当前 app.db）

备份走 SQLite 自带的 online backup，服务运行时也能安全执行。
恢复最好先停服务。
"""

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import DB_PATH

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.getenv("BACKUP_DIR", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups"))


def _norm(p):
    return os.path.abspath(p)


def backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not os.path.isfile(DB_PATH):
        raise FileNotFoundError("数据库不存在，不创建空备份")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = os.path.join(BACKUP_DIR, f"app_{stamp}.db")
    with (
        closing(sqlite3.connect(Path(DB_PATH).resolve().as_uri() + "?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(dest)) as dst,
    ):
        src.backup(dst)
    print(f"已备份到：{dest}")
    return dest


def restore(src_file):
    if not os.path.exists(src_file):
        print(f"备份文件不存在：{src_file}")
        sys.exit(1)
    if Path(src_file).resolve() == Path(DB_PATH).resolve():
        raise ValueError("恢复源不能是当前数据库")
    with closing(sqlite3.connect(Path(src_file).resolve().as_uri() + "?mode=ro", uri=True)) as source:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("备份完整性校验失败")
    # 先在线备份，包含 WAL 中已提交的数据，而非只复制主文件。
    safety = DB_PATH + "." + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".pre_restore.bak"
    if os.path.exists(DB_PATH):
        with closing(sqlite3.connect(DB_PATH)) as current, closing(sqlite3.connect(safety)) as safe:
            current.backup(safe)
        print(f"恢复前已将当前库另存为：{safety}")
    with (
        closing(sqlite3.connect(Path(src_file).resolve().as_uri() + "?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(DB_PATH)) as dst,
    ):
        src.backup(dst)
    print(f"已从 {src_file} 恢复完成")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd = args[0]
    if cmd == "backup":
        backup()
    elif cmd == "restore" and len(args) >= 2:
        restore(args[1])
    else:
        print(__doc__)
        sys.exit(1)
