"""环境变量优先于项目根目录 .env。"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)
DB_PATH = os.getenv("DB_PATH", str(ROOT / "app.db"))


def now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def new_id(prefix):
    import uuid

    return f"{prefix}-{uuid.uuid4().hex}"
