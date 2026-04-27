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


@router.get("/projects")
def my_projects(current_user=Depends(get_current_user)):
    allowed = _allowed_projects(current_user)
    return [p for p in project_store.list_projects() if p["id"] in allowed]


@router.get("/projects/{project_id}/specs")
def project_specs(project_id: str, current_user=Depends(get_current_user)):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied to this project")
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_specs(project_id)


@router.get("/projects/{project_id}/specs/{filename}")
def get_spec(project_id: str, filename: str, current_user=Depends(get_current_user)):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    spec = project_store.get_spec_content(project_id, filename)
    if spec is None:
        raise HTTPException(status_code=404, detail="Spec not found")
    return JSONResponse(content=spec)


@router.post("/projects/{project_id}/specs")
async def entity_upload_spec(
    project_id: str,
    files:   list[UploadFile] = File(...),
    version: str = Form(default="1.0.0"),
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
        info = project_store.save_spec(project_id, fname, content,
                                       uploaded_by=current_user.get("username", "entity"),
                                       version=version, notes=notes)
        uploaded.append(info)
    return {"uploaded": uploaded}