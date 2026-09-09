# -*- coding: utf-8 -*-
"""数据备份和恢复脚本。

用法：
  python backend/backup.py backup            备份 app.db 到 backups/
  python backend/backup.py restore <文件>     从备份恢复（覆盖当前 app.db）

备份走 SQLite 自带的 online backup，服务运行时也能安全执行。
恢复最好先停服务。
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "..", "app.db")
BACKUP_DIR = os.path.join(BACKEND_DIR, "..", "backups")


def _norm(p):
    return os.path.abspath(p)


def backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"app_{stamp}.db")
    src = sqlite3.connect(_norm(DB_PATH))
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    print(f"已备份到：{dest}")
    return dest


def restore(src_file):
    if not os.path.exists(src_file):
        print(f"备份文件不存在：{src_file}")
        sys.exit(1)
    # 恢复前先把当前库另存一份，防止误操作
    safety = DB_PATH + ".pre_restore.bak"
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, safety)
        print(f"恢复前已将当前库另存为：{safety}")
    src = sqlite3.connect(_norm(src_file))
    dst = sqlite3.connect(_norm(DB_PATH))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
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
