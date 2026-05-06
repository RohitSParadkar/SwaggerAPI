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
    # New: explicit per-project permissions  {pid: "read"|"write"}
    # Falls back to `projects` list (all read) if omitted
    project_permissions: dict = {}
    projects: list[str] = []   # legacy — kept for backward compat

class UserUpdate(BaseModel):
    password: Optional[str] = None
    project_permissions: Optional[dict] = None
    projects: Optional[list[str]] = None   # legacy


@router.get("/users")
def list_users(admin=Depends(require_admin)):
    return user_store.get_all_users()

@router.post("/users", status_code=201)
def create_user(body: UserCreate, admin=Depends(require_admin)):
    try:
        # Prefer explicit project_permissions; fall back to projects list
        perms = body.project_permissions or {}
        return user_store.create_user(
            body.username, body.password, body.role,
            projects=body.projects,
            project_permissions=perms if perms else None,
            created_by=admin["username"],
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, admin=Depends(require_admin)):
    try:
        kwargs = {}
        if body.project_permissions is not None:
            kwargs["project_permissions"] = body.project_permissions
        elif body.projects is not None:
            kwargs["projects"] = body.projects
        if body.password is not None:
            kwargs["password"] = body.password
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
    """
    Accepts either:
      { "project_ids": [...] }                  → all read (legacy)
      { "project_permissions": {pid: perm} }    → explicit
    """
    try:
        if "project_permissions" in body:
            return user_store.update_user(user_id,
                                          project_permissions=body["project_permissions"])
        return user_store.update_user(user_id,
                                      projects=body.get("project_ids", []))
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

    if _is_postman(content):
        col     = json.loads(content)
        spec    = postman_converter.convert(col)
        # Keep original filename, only swap extension to .yaml
        base    = fname.rsplit(".", 1)[0] if "." in fname else fname
        fname   = base + ".yaml"
        content = yaml.dump(spec, allow_unicode=True, sort_keys=False).encode("utf-8")
        return fname, content, True   # converted=True → UI shows "converted" badge

    if ext == ".json":
        try:
            json.loads(content)
        except Exception as e:
            raise ValueError(f"Invalid JSON: {e}")
        return fname, content, False

    if ext in (".yaml", ".yml"):
        try:
            yaml.safe_load(content)
        except Exception as e:
            raise ValueError(f"Invalid YAML: {e}")
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
        raise ValueError(f"Cannot parse '{fname}' — must be OpenAPI YAML/JSON or Postman collection")


# ── Spec upload ───────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/specs")
async def upload_spec(
    project_id: str,
    files:   list[UploadFile] = File(...),
    version: str = Form(default=""),
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
            fname, content, was_converted = _process_file(content, fname)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        _, preview_num = project_store._next_versioned_filename(project_id, fname)
        effective_version = version.strip() if version.strip() else f"{preview_num}.0.0"

        info = project_store.save_spec(project_id, fname, content,
                                       uploaded_by=admin["username"],
                                       version=effective_version,
                                       notes=notes,
                                       converted=was_converted)
        uploaded.append(info)
    return {"uploaded": uploaded}


@router.get("/projects/{project_id}/specs")
def list_project_specs(project_id: str, admin=Depends(require_admin)):
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_specs(project_id)


@router.get("/projects/{project_id}/documents")
def list_project_documents(project_id: str, admin=Depends(require_admin)):
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_documents(project_id)


@router.get("/all-specs")
def all_specs(admin=Depends(require_admin)):
    result = []
    for proj in project_store.list_projects():
        for spec in project_store.list_specs(proj["id"]):
            result.append({**spec, "project_id": proj["id"], "project_name": proj["name"]})
    return result


@router.get("/projects/{project_id}/specs/{filename}/endpoints")
def spec_endpoints(project_id: str, filename: str, admin=Depends(require_admin)):
    """
    Parse the stored OpenAPI spec and return a flat list of operations,
    each carrying request body examples and response examples so the
    admin UI can render them inline without a second API call.
    """
    spec = project_store.get_spec_content(project_id, filename)
    if spec is None:
        raise HTTPException(status_code=404, detail="Spec not found")

    endpoints = []
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete",
                                       "head", "options", "trace"):
                continue
            if not isinstance(operation, dict):
                continue

            tags = operation.get("tags") or []

            # ── Request body examples ────────────────────────────────────────
            req_examples = []   # [{name, value}]
            rb = operation.get("requestBody") or {}
            for ct, media in (rb.get("content") or {}).items():
                if not isinstance(media, dict):
                    continue
                # named examples map  (our converter always produces this)
                for ex_name, ex_obj in (media.get("examples") or {}).items():
                    val = ex_obj.get("value") if isinstance(ex_obj, dict) else ex_obj
                    req_examples.append({"name": ex_name, "value": val, "content_type": ct})
                # singular example fallback
                if "example" in media and not media.get("examples"):
                    req_examples.append({"name": "Example", "value": media["example"], "content_type": ct})

            # ── Response examples ────────────────────────────────────────────
            resp_examples = []  # [{status_code, name, value, content_type}]
            for status_code, resp_obj in (operation.get("responses") or {}).items():
                if not isinstance(resp_obj, dict):
                    continue
                for ct, media in (resp_obj.get("content") or {}).items():
                    if not isinstance(media, dict):
                        continue
                    for ex_name, ex_obj in (media.get("examples") or {}).items():
                        val = ex_obj.get("value") if isinstance(ex_obj, dict) else ex_obj
                        resp_examples.append({
                            "status_code":  status_code,
                            "name":         ex_name,
                            "value":        val,
                            "content_type": ct,
                        })
                    if "example" in media and not media.get("examples"):
                        resp_examples.append({
                            "status_code":  status_code,
                            "name":         f"{status_code} Response",
                            "value":        media["example"],
                            "content_type": ct,
                        })

            # ── Parameters ───────────────────────────────────────────────────
            parameters = []
            for p in (operation.get("parameters") or []):
                if not isinstance(p, dict):
                    continue
                parameters.append({
                    "name":        p.get("name", ""),
                    "in":          p.get("in", ""),
                    "required":    p.get("required", False),
                    "description": p.get("description", ""),
                    "example":     (p.get("schema") or {}).get("example", ""),
                })

            endpoints.append({
                "method":        method.upper(),
                "path":          path,
                "summary":       operation.get("summary") or operation.get("description") or "",
                "description":   operation.get("description") or "",
                "tag":           tags[0] if tags else "",
                "operation_id":  operation.get("operationId") or "",
                "parameters":    parameters,
                "req_examples":  req_examples,
                "resp_examples": resp_examples,
            })
    return endpoints


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


# ── Reference docs ────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/refs")
def list_refs(project_id: str, admin=Depends(require_admin)):
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_refs(project_id)


@router.post("/projects/{project_id}/refs")
async def upload_ref(
    project_id: str,
    files:       list[UploadFile] = File(...),
    linked_spec: str = Form(default=""),
    description: str = Form(default=""),
    admin=Depends(require_admin),
):
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    from pathlib import Path as _P
    allowed = {".pdf", ".md", ".txt", ".docx", ".doc", ".png", ".jpg", ".jpeg"}
    uploaded = []
    for f in files:
        ext = _P(f.filename or "").suffix.lower()
        if ext not in allowed:
            raise HTTPException(status_code=422,
                detail=f"'{f.filename}' — allowed formats: PDF, MD, TXT, DOCX, PNG, JPG")
        content = await f.read()
        info = project_store.save_ref(
            project_id, f.filename or "document",
            content, uploaded_by=admin["username"],
            linked_spec=linked_spec, description=description,
        )
        uploaded.append(info)
    return {"uploaded": uploaded}


@router.get("/projects/{project_id}/refs/{filename}")
def serve_ref(project_id: str, filename: str, admin=Depends(require_admin)):
    from fastapi.responses import FileResponse
    p = project_store.get_ref_path(project_id, filename)
    if not p:
        raise HTTPException(status_code=404, detail="Reference doc not found")
    return FileResponse(str(p), filename=filename)


@router.delete("/projects/{project_id}/refs/{filename}", status_code=204)
def delete_ref(project_id: str, filename: str, admin=Depends(require_admin)):
    try:
        project_store.delete_ref(project_id, filename)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Serve ref with inline/attachment header (for PDF preview) ────────────────

@router.get("/projects/{project_id}/refs/{filename}/view")
def view_ref(project_id: str, filename: str, admin=Depends(require_admin)):
    """Serve ref file inline (for browser PDF preview)."""
    from fastapi.responses import FileResponse
    import mimetypes
    p = project_store.get_ref_path(project_id, filename)
    if not p:
        raise HTTPException(status_code=404, detail="Reference doc not found")
    mt, _ = mimetypes.guess_type(filename)
    return FileResponse(str(p), media_type=mt or "application/octet-stream",
                        headers={"Content-Disposition": f"inline; filename=\"{filename}\""})


# ── Backup: API contracts ─────────────────────────────────────────────────────

@router.post("/projects/{project_id}/specs/{filename}/backup")
def backup_spec(project_id: str, filename: str, admin=Depends(require_admin)):
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return project_store.backup_spec(project_id, filename, archived_by=admin["username"])
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/projects/{project_id}/backups/specs")
def list_backup_specs(project_id: str, admin=Depends(require_admin)):
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_backup_specs(project_id)

@router.post("/projects/{project_id}/backups/specs/{backup_filename}/restore")
def restore_spec(project_id: str, backup_filename: str, admin=Depends(require_admin)):
    try:
        return project_store.restore_spec(project_id, backup_filename)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/projects/{project_id}/backups/specs/{backup_filename}", status_code=204)
def delete_backup_spec(project_id: str, backup_filename: str, admin=Depends(require_admin)):
    try:
        project_store.delete_backup_spec(project_id, backup_filename)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/projects/{project_id}/backups/specs/{backup_filename}/download")
def download_backup_spec(project_id: str, backup_filename: str, admin=Depends(require_admin)):
    from fastapi.responses import FileResponse
    p = project_store.get_backup_spec_path(project_id, backup_filename)
    if not p:
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(str(p), filename=backup_filename)


# ── Backup: Reference docs ────────────────────────────────────────────────────

@router.post("/projects/{project_id}/refs/{filename}/backup")
def backup_ref(project_id: str, filename: str, admin=Depends(require_admin)):
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return project_store.backup_ref(project_id, filename, archived_by=admin["username"])
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/projects/{project_id}/backups/refs")
def list_backup_refs(project_id: str, admin=Depends(require_admin)):
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_backup_refs(project_id)

@router.post("/projects/{project_id}/backups/refs/{backup_filename}/restore")
def restore_ref_backup(project_id: str, backup_filename: str, admin=Depends(require_admin)):
    try:
        return project_store.restore_ref(project_id, backup_filename)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/projects/{project_id}/backups/refs/{backup_filename}", status_code=204)
def delete_backup_ref(project_id: str, backup_filename: str, admin=Depends(require_admin)):
    try:
        project_store.delete_backup_ref(project_id, backup_filename)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/projects/{project_id}/backups/refs/{backup_filename}/download")
def download_backup_ref(project_id: str, backup_filename: str, admin=Depends(require_admin)):
    from fastapi.responses import FileResponse
    p = project_store.get_backup_ref_path(project_id, backup_filename)
    if not p:
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(str(p), filename=backup_filename)


# ── Document-level access permissions ────────────────────────────────────────

@router.get("/projects/{project_id}/access")
def get_access(project_id: str, admin=Depends(require_admin)):
    """Get full document-level access map for a project."""
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.get_access_meta(project_id)

@router.put("/projects/{project_id}/access")
def set_access(project_id: str, body: dict, admin=Depends(require_admin)):
    """
    Full replace of the document-level access map.
    Body: {"specs": {"filename": ["uid",...]}, "refs": {"filename": ["uid",...]}}
    Empty list [] = unrestricted (all project users can see it).
    Files absent from body are also set to unrestricted.
    """
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    # Build a clean access map — start from scratch
    new_access = {"specs": {}, "refs": {}}

    for kind in ("specs", "refs"):
        for filename, user_ids in (body.get(kind) or {}).items():
            clean_ids = [uid for uid in (user_ids or []) if uid]
            if clean_ids:
                new_access[kind][filename] = clean_ids
            # empty list = unrestricted = not stored (omitted from map)

    # Save the entire map at once (atomic replace)
    from project_store import _save_access
    _save_access(project_id, new_access)
    return project_store.get_access_meta(project_id)