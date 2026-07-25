import os
from pathlib import Path

os.environ.setdefault("RETELL_API_KEY", "test-api-key")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")

_TEST_DB_PATH = Path(__file__).parent / "tests" / "test_retell_leads.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB_PATH}")

if _TEST_DB_PATH.exists():
    _TEST_DB_PATH.unlink()
