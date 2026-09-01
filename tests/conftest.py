from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-unit-tests")
os.environ.setdefault(
    "ENCRYPTION_KEY",
    "dGVzdC1rZXktZm9yLXVuaXQtdGVzdHMtb25seS0xMjM0NTY=",
)
os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OIDC_ENABLED", "false")
