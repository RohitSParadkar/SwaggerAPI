"""
User store — persists users to data/users/users.json.
Passwords are stored as bcrypt hashes.

project_permissions field (entity users only):
  {
    "<project_id>": "read" | "write",
    ...
  }

The legacy `projects` list field is kept for backwards compatibility —
it is always derived from project_permissions on read/write.
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


def _normalise_permissions(raw_perms, legacy_projects=None) -> dict:
    """
    Accepts either:
      - dict  {pid: "read"|"write"}            → returned as-is (validated)
      - list  [pid, ...]                        → each pid gets "read" permission
      - None  with legacy_projects list         → migrate legacy list → read perms
    Returns a clean {pid: "read"|"write"} dict.
    """
    if isinstance(raw_perms, dict):
        return {k: v if v in ("read", "write") else "read"
                for k, v in raw_perms.items()}
    if isinstance(raw_perms, list):
        return {pid: "read" for pid in raw_perms}
    if legacy_projects:
        return {pid: "read" for pid in legacy_projects}
    return {}


def _projects_list(perms: dict) -> list:
    """Flat list of project IDs — any permission level."""
    return list(perms.keys())


def _write_projects(perms: dict) -> list:
    """Project IDs where the user has write permission."""
    return [pid for pid, perm in perms.items() if perm == "write"]


# ── Bootstrap admin ───────────────────────────────────────────────────────────

def bootstrap_admin() -> None:
    """Create the admin user from .env if it doesn't already exist."""
    users = _load()
    if any(u.get("role") == "admin" for u in users.values()):
        return
    admin_id = str(uuid.uuid4())
    users[admin_id] = {
        "id":                  admin_id,
        "username":            ADMIN_USERNAME,
        "password":            _hash(ADMIN_PASSWORD),
        "role":                "admin",
        "project_permissions": {},
        "projects":            [],
        "created_at":          datetime.utcnow().isoformat(),
        "created_by":          "system",
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
    u = _load().get(user_id)
    return _safe(u) if u else None


def create_user(username: str, password: str, role: str = "entity",
                projects: list[str] = None,
                project_permissions: dict = None,
                created_by: str = "admin") -> dict:
    users = _load()
    if any(u["username"] == username for u in users.values()):
        raise ValueError(f"Username '{username}' already exists")
    uid = str(uuid.uuid4())

    # Resolve permissions: explicit dict wins, then list, then empty
    perms = _normalise_permissions(project_permissions, projects)

    user = {
        "id":                  uid,
        "username":            username,
        "password":            _hash(password),
        "role":                role,
        "project_permissions": perms,
        "projects":            _projects_list(perms),   # legacy compat
        "created_at":          datetime.utcnow().isoformat(),
        "created_by":          created_by,
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

    # Handle project_permissions update
    if "project_permissions" in kwargs:
        perms = _normalise_permissions(kwargs["project_permissions"])
        kwargs["project_permissions"] = perms
        kwargs["projects"] = _projects_list(perms)   # keep legacy field in sync
    elif "projects" in kwargs:
        # legacy caller — treat all as read
        perms = _normalise_permissions(kwargs["projects"])
        kwargs["project_permissions"] = perms
        kwargs["projects"] = _projects_list(perms)

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
    """Return user dict without the password hash, with permissions always present."""
    out = {k: v for k, v in u.items() if k != "password"}
    # Back-fill for users created before this version
    if "project_permissions" not in out:
        out["project_permissions"] = _normalise_permissions(None, out.get("projects", []))
    if "projects" not in out:
        out["projects"] = _projects_list(out["project_permissions"])
    return out