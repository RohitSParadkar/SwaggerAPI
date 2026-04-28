from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
import json, yaml

from middleware.auth import get_current_user
import project_store
import user_store
import postman_converter

router = APIRouter(prefix="/api/entity", tags=["Entity"])


def _allowed_projects(current_user: dict) -> list:
    if current_user.get("role") == "admin":
        return [p["id"] for p in project_store.list_projects()]
    uid  = current_user.get("sub")
    user = user_store.get_user_by_id(uid) if uid else None
    return (user or {}).get("projects", [])


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
        base    = fname.rsplit(".", 1)[0] if "." in fname else fname
        fname   = base + "_converted.yaml"
        content = yaml.dump(spec, allow_unicode=True, sort_keys=False).encode("utf-8")
        return fname, content

    if ext == ".json":
        json.loads(content)
        return fname, content

    if ext in (".yaml", ".yml"):
        yaml.safe_load(content)
        return fname, content

    try:
        yaml.safe_load(content)
        return fname + ".yaml", content
    except Exception:
        pass
    try:
        json.loads(content)
        return fname + ".json", content
    except Exception:
        raise ValueError(f"Cannot parse '{fname}'")


# ── Projects ──────────────────────────────────────────────────────────────────

@router.get("/projects")
def my_projects(current_user=Depends(get_current_user)):
    allowed = _allowed_projects(current_user)
    return [p for p in project_store.list_projects() if p["id"] in allowed]


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
    """
    Returns specs grouped by document (base name).
    Each entry has:  base_name, versions (list of spec objects), latest (spec object)
    """
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


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/specs")
async def entity_upload_spec(
    project_id: str,
    files:   list[UploadFile] = File(...),
    version: str = Form(default=""),   # optional; if blank, auto-infer from version_num
    notes:   str = Form(default=""),
    current_user=Depends(get_current_user),
):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied to this project")
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    uploaded = []
    for f in files:
        content = await f.read()
        fname   = f.filename or "upload"
        try:
            fname, content = _process_file(content, fname)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        # If the caller did not supply a version label, derive it from the
        # auto-incremented version_num that save_spec will assign.
        # We do a pre-check here to compute what version_num will be.
        _, preview_num = project_store._next_versioned_filename(project_id, fname)
        effective_version = version.strip() if version.strip() else f"{preview_num}.0.0"

        info = project_store.save_spec(
            project_id, fname, content,
            uploaded_by=current_user.get("username", "entity"),
            version=effective_version,
            notes=notes,
        )
        uploaded.append(info)
    return {"uploaded": uploaded}