from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import json, yaml

from middleware.auth import require_admin
import user_store
import project_store
import postman_converter

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ── Users ─────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    password: str
    role:     str = "entity"
    projects: list[str] = []

class UserUpdate(BaseModel):
    password: Optional[str] = None
    projects: Optional[list[str]] = None


@router.get("/users")
def list_users(admin=Depends(require_admin)):
    return user_store.get_all_users()

@router.post("/users", status_code=201)
def create_user(body: UserCreate, admin=Depends(require_admin)):
    try:
        return user_store.create_user(body.username, body.password, body.role,
                                      body.projects, created_by=admin["username"])
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, admin=Depends(require_admin)):
    try:
        kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
        return user_store.update_user(user_id, **kwargs)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str, admin=Depends(require_admin)):
    try:
        user_store.delete_user(user_id)
    except (KeyError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/users/{user_id}/projects")
def assign_projects(user_id: str, body: dict, admin=Depends(require_admin)):
    try:
        return user_store.update_user(user_id, projects=body.get("project_ids", []))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Projects ──────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: str = ""

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

@router.get("/projects")
def list_projects(admin=Depends(require_admin)):
    return project_store.list_projects()

@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, admin=Depends(require_admin)):
    return project_store.create_project(body.name, body.description, admin["username"])

@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate, admin=Depends(require_admin)):
    try:
        kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
        return project_store.update_project(project_id, **kwargs)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, admin=Depends(require_admin)):
    try:
        project_store.delete_project(project_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


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

    # Postman by content (any extension)
    if _is_postman(content):
        col     = json.loads(content)
        spec    = postman_converter.convert(col)
        base    = fname.rsplit(".", 1)[0] if "." in fname else fname
        fname   = base + "_converted.yaml"
        content = yaml.dump(spec, allow_unicode=True, sort_keys=False).encode("utf-8")
        return fname, content

    if ext == ".json":
        try:
            json.loads(content)
        except Exception as e:
            raise ValueError(f"Invalid JSON: {e}")
        return fname, content

    if ext in (".yaml", ".yml"):
        try:
            yaml.safe_load(content)
        except Exception as e:
            raise ValueError(f"Invalid YAML: {e}")
        return fname, content

    # Unknown extension — sniff content
    try:
        yaml.safe_load(content)
        return fname + ".yaml", content
    except Exception:
        pass
    try:
        json.loads(content)
        return fname + ".json", content
    except Exception:
        raise ValueError(f"Cannot parse '{fname}' — must be OpenAPI YAML/JSON or Postman collection")


# ── Spec upload ───────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/specs")
async def upload_spec(
    project_id: str,
    files:   list[UploadFile] = File(...),
    version: str = Form(default="1.0.0"),
    notes:   str = Form(default=""),
    admin=Depends(require_admin),
):
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
                                       uploaded_by=admin["username"],
                                       version=version, notes=notes)
        uploaded.append(info)
    return {"uploaded": uploaded}


@router.get("/projects/{project_id}/specs")
def list_project_specs(project_id: str, admin=Depends(require_admin)):
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_specs(project_id)


@router.get("/all-specs")
def all_specs(admin=Depends(require_admin)):
    result = []
    for proj in project_store.list_projects():
        for spec in project_store.list_specs(proj["id"]):
            result.append({**spec, "project_id": proj["id"], "project_name": proj["name"]})
    return result


@router.get("/projects/{project_id}/specs/{filename}")
def serve_spec(project_id: str, filename: str, admin=Depends(require_admin)):
    spec = project_store.get_spec_content(project_id, filename)
    if spec is None:
        raise HTTPException(status_code=404, detail="Spec not found")
    return JSONResponse(content=spec)


@router.delete("/projects/{project_id}/specs/{filename}", status_code=204)
def delete_spec(project_id: str, filename: str, admin=Depends(require_admin)):
    try:
        project_store.delete_spec(project_id, filename)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))