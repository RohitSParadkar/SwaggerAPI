"""
Central configuration — reads .env, exposes typed constants and filesystem paths.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR / "data"
USERS_FILE    = DATA_DIR / "users" / "users.json"
PROJECTS_DIR  = DATA_DIR / "projects"
TEMPLATES_DIR = BASE_DIR / "templates"

# create on startup
for d in [DATA_DIR, DATA_DIR / "users", PROJECTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Auth ───────────────────────────────────────────────────────────────────────
JWT_SECRET        = os.getenv("JWT_SECRET", "change-this-secret-min-32-chars!!")
JWT_ALGORITHM     = "HS256"
JWT_EXPIRE_HOURS  = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

# ── Admin ─────────────────────────────────────────────────────────────────────
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@1234")

# ── Server ────────────────────────────────────────────────────────────────────
PORT = int(os.getenv("PORT", "8000"))
