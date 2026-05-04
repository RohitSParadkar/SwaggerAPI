from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
import json, yaml

from middleware.auth import get_current_user
import project_store
import user_store
import postman_converter

router = APIRouter(prefix="/api/entity", tags=["Entity"])


# ── Permission helpers ────────────────────────────────────────────────────────

def _get_project_permissions(current_user: dict) -> dict:
    """Returns {pid: 'read'|'write'} for the current user."""
    if current_user.get("role") == "admin":
        return {p["id"]: "write" for p in project_store.list_projects()}
    uid  = current_user.get("sub")
    user = user_store.get_user_by_id(uid) if uid else None
    if not user:
        return {}
    perms = user.get("project_permissions", {})
    # Back-fill: legacy users who only have projects list get read
    if not perms and user.get("projects"):
        perms = {pid: "read" for pid in user["projects"]}
    return perms


def _allowed_projects(current_user: dict) -> list:
    """All project IDs the user can access (any permission)."""
    return list(_get_project_permissions(current_user).keys())


def _writable_projects(current_user: dict) -> list:
    """Project IDs where the user has write permission."""
    return [pid for pid, perm in _get_project_permissions(current_user).items()
            if perm == "write"]


def _can_write(current_user: dict, project_id: str) -> bool:
    return project_id in _writable_projects(current_user)


# ── File processor ────────────────────────────────────────────────────────────

def _is_postman(content: bytes) -> bool:
    try:
        data = json.loads(content)
        return isinstance(data, dict) and "info" in data and "item" in data
    except Exception:
        return False


def _process_file(content: bytes, fname: str) -> tuple:
    from pathlib import Path as _P
    ext = _P(fname).suffix.lower()

    if _is_postman(content):
        col     = json.loads(content)
        spec    = postman_converter.convert(col)
        # Keep original filename, only change extension to .yaml
        base    = fname.rsplit(".", 1)[0] if "." in fname else fname
        fname   = base + ".yaml"
        content = yaml.dump(spec, allow_unicode=True, sort_keys=False).encode("utf-8")
        return fname, content, True   # converted=True → UI shows "converted" badge

    if ext == ".json":
        json.loads(content)
        return fname, content, False

    if ext in (".yaml", ".yml"):
        yaml.safe_load(content)
        return fname, content, False

    try:
        yaml.safe_load(content)
        return fname + ".yaml", content, False
    except Exception:
        pass
    try:
        json.loads(content)
        return fname + ".json", content, False
    except Exception:
        raise ValueError(f"Cannot parse '{fname}'")


# ── Projects ──────────────────────────────────────────────────────────────────

@router.get("/projects")
def my_projects(current_user=Depends(get_current_user)):
    """Return projects with permission level attached."""
    perms   = _get_project_permissions(current_user)
    allowed = list(perms.keys())
    result  = []
    for p in project_store.list_projects():
        if p["id"] in allowed:
            p["permission"] = perms[p["id"]]
            result.append(p)
    return result


# ── Specs (flat list) ─────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/specs")
def project_specs(project_id: str, current_user=Depends(get_current_user)):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied to this project")
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_specs(project_id)


# ── Documents (grouped by base name, with versions list) ─────────────────────

@router.get("/projects/{project_id}/documents")
def project_documents(project_id: str, current_user=Depends(get_current_user)):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied to this project")
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_documents(project_id)


# ── Single spec ───────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/specs/{filename}")
def get_spec(project_id: str, filename: str, current_user=Depends(get_current_user)):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    spec = project_store.get_spec_content(project_id, filename)
    if spec is None:
        raise HTTPException(status_code=404, detail="Spec not found")
    return JSONResponse(content=spec)


# ── Upload (write permission required) ───────────────────────────────────────

@router.post("/projects/{project_id}/specs")
async def entity_upload_spec(
    project_id: str,
    files:   list[UploadFile] = File(...),
    version: str = Form(default=""),
    notes:   str = Form(default=""),
    current_user=Depends(get_current_user),
):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied to this project")
    if not _can_write(current_user, project_id):
        raise HTTPException(status_code=403,
                            detail="Write permission required to upload documents")
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    uploaded = []
    for f in files:
        content = await f.read()
        fname   = f.filename or "upload"
        try:
            fname, content, was_converted = _process_file(content, fname)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        _, preview_num = project_store._next_versioned_filename(project_id, fname)
        effective_version = version.strip() if version.strip() else f"{preview_num}.0.0"

        info = project_store.save_spec(
            project_id, fname, content,
            uploaded_by=current_user.get("username", "entity"),
            version=effective_version,
            notes=notes,
            converted=was_converted,
        )
        uploaded.append(info)
    return {"uploaded": uploaded}