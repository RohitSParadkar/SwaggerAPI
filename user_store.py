"""
User store — persists users to data/users/users.json.
Passwords are stored as bcrypt hashes.
"""
import json
import uuid
from datetime import datetime
from typing import Optional
import bcrypt

from config import USERS_FILE, ADMIN_USERNAME, ADMIN_PASSWORD


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load() -> dict:
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    USERS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── Bootstrap admin ───────────────────────────────────────────────────────────

def bootstrap_admin() -> None:
    """Create the admin user from .env if it doesn't already exist."""
    users = _load()
    # Check if an admin already exists
    if any(u.get("role") == "admin" for u in users.values()):
        return
    admin_id = str(uuid.uuid4())
    users[admin_id] = {
        "id":         admin_id,
        "username":   ADMIN_USERNAME,
        "password":   _hash(ADMIN_PASSWORD),
        "role":       "admin",
        "projects":   [],          # admin sees everything
        "created_at": datetime.utcnow().isoformat(),
        "created_by": "system",
    }
    _save(users)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def get_all_users() -> list[dict]:
    users = _load()
    return [_safe(u) for u in users.values()]


def get_user_by_username(username: str) -> Optional[dict]:
    for u in _load().values():
        if u["username"] == username:
            return u
    return None


def get_user_by_id(user_id: str) -> Optional[dict]:
    return _load().get(user_id)


def create_user(username: str, password: str, role: str = "entity",
                projects: list[str] = None, created_by: str = "admin") -> dict:
    users = _load()
    if any(u["username"] == username for u in users.values()):
        raise ValueError(f"Username '{username}' already exists")
    uid = str(uuid.uuid4())
    user = {
        "id":         uid,
        "username":   username,
        "password":   _hash(password),
        "role":       role,
        "projects":   projects or [],
        "created_at": datetime.utcnow().isoformat(),
        "created_by": created_by,
    }
    users[uid] = user
    _save(users)
    return _safe(user)


def update_user(user_id: str, **kwargs) -> dict:
    users = _load()
    if user_id not in users:
        raise KeyError(f"User {user_id} not found")
    u = users[user_id]
    if "password" in kwargs:
        kwargs["password"] = _hash(kwargs["password"])
    if "username" in kwargs and kwargs["username"] != u["username"]:
        if any(x["username"] == kwargs["username"] for x in users.values()):
            raise ValueError(f"Username '{kwargs['username']}' already taken")
    u.update(kwargs)
    _save(users)
    return _safe(u)


def delete_user(user_id: str) -> None:
    users = _load()
    if user_id not in users:
        raise KeyError("User not found")
    if users[user_id].get("role") == "admin":
        raise PermissionError("Cannot delete admin user")
    del users[user_id]
    _save(users)


def authenticate(username: str, password: str) -> Optional[dict]:
    u = get_user_by_username(username)
    if u and _verify(password, u["password"]):
        return _safe(u)
    return None


def _safe(u: dict) -> dict:
    """Return user dict without the password hash."""
    return {k: v for k, v in u.items() if k != "password"}