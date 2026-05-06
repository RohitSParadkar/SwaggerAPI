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
    uid = current_user.get("sub", "")
    if current_user.get("role") == "admin":
        return project_store.list_specs(project_id)
    accessible = set(project_store.get_accessible_specs(project_id, uid))
    return [s for s in project_store.list_specs(project_id) if s["filename"] in accessible]


# ── Documents (grouped by base name, with versions list) ─────────────────────

@router.get("/projects/{project_id}/documents")
def project_documents(project_id: str, current_user=Depends(get_current_user)):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied to this project")
    if not project_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    uid = current_user.get("sub", "")
    if current_user.get("role") == "admin":
        return project_store.list_documents(project_id)
    accessible = set(project_store.get_accessible_specs(project_id, uid))
    all_docs = project_store.list_documents(project_id)
    filtered = []
    for doc in all_docs:
        vs = [v for v in doc["versions"] if v["filename"] in accessible]
        if vs:
            filtered.append({"base_name": doc["base_name"], "versions": vs, "latest": vs[-1]})
    return filtered


@router.get("/projects/{project_id}/refs")
def list_refs(project_id: str, current_user=Depends(get_current_user)):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    uid = current_user.get("sub", "")
    if current_user.get("role") == "admin":
        return project_store.list_refs(project_id)
    accessible = set(project_store.get_accessible_refs(project_id, uid))
    return [r for r in project_store.list_refs(project_id) if r["filename"] in accessible]


@router.get("/projects/{project_id}/refs/{filename}/view")
def view_ref_inline(project_id: str, filename: str, current_user=Depends(get_current_user)):
    """Serve ref file inline for browser preview (PDF viewer, images)."""
    from fastapi.responses import FileResponse
    import mimetypes
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    # Check doc-level access
    uid = current_user.get("sub", "")
    if current_user.get("role") != "admin":
        accessible = set(project_store.get_accessible_refs(project_id, uid))
        if filename not in accessible:
            raise HTTPException(status_code=403, detail="Access denied to this document")
    p = project_store.get_ref_path(project_id, filename)
    if not p:
        raise HTTPException(status_code=404, detail="Reference doc not found")
    mt, _ = mimetypes.guess_type(filename)
    return FileResponse(str(p), media_type=mt or "application/octet-stream",
                        headers={"Content-Disposition": f"inline; filename=\"{filename}\""})


@router.get("/projects/{project_id}/refs/{filename}/view")
def view_ref_inline(project_id: str, filename: str, current_user=Depends(get_current_user)):
    """Serve ref file inline for browser PDF/image preview."""
    from fastapi.responses import FileResponse
    import mimetypes
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    uid = current_user.get("sub", "")
    if current_user.get("role") != "admin":
        accessible = set(project_store.get_accessible_refs(project_id, uid))
        if filename not in accessible:
            raise HTTPException(status_code=403, detail="Access denied to this document")
    p = project_store.get_ref_path(project_id, filename)
    if not p:
        raise HTTPException(status_code=404, detail="Reference doc not found")
    mt, _ = mimetypes.guess_type(filename)
    return FileResponse(str(p), media_type=mt or "application/octet-stream",
                        headers={"Content-Disposition": f"inline; filename=\"{filename}\""})


def get_spec_endpoints(project_id: str, filename: str, current_user=Depends(get_current_user)):
    """Return flat list of API endpoints with request/response examples from the stored spec."""
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
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

            req_examples = []
            rb = operation.get("requestBody") or {}
            for ct, media in (rb.get("content") or {}).items():
                if not isinstance(media, dict):
                    continue
                for ex_name, ex_obj in (media.get("examples") or {}).items():
                    val = ex_obj.get("value") if isinstance(ex_obj, dict) else ex_obj
                    req_examples.append({"name": ex_name, "value": val, "content_type": ct})
                if "example" in media and not media.get("examples"):
                    req_examples.append({"name": "Example", "value": media["example"], "content_type": ct})

            resp_examples = []
            for status_code, resp_obj in (operation.get("responses") or {}).items():
                if not isinstance(resp_obj, dict):
                    continue
                for ct, media in (resp_obj.get("content") or {}).items():
                    if not isinstance(media, dict):
                        continue
                    for ex_name, ex_obj in (media.get("examples") or {}).items():
                        val = ex_obj.get("value") if isinstance(ex_obj, dict) else ex_obj
                        resp_examples.append({"status_code": status_code, "name": ex_name,
                                              "value": val, "content_type": ct})
                    if "example" in media and not media.get("examples"):
                        resp_examples.append({"status_code": status_code,
                                              "name": f"{status_code} Response",
                                              "value": media["example"], "content_type": ct})

            parameters = []
            for p in (operation.get("parameters") or []):
                if isinstance(p, dict):
                    parameters.append({
                        "name": p.get("name", ""), "in": p.get("in", ""),
                        "required": p.get("required", False),
                        "description": p.get("description", ""),
                        "example": (p.get("schema") or {}).get("example", ""),
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


# ── Reference docs ────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/refs")
async def upload_ref(
    project_id: str,
    files:       list[UploadFile] = File(...),
    linked_spec: str = Form(default=""),
    description: str = Form(default=""),
    current_user=Depends(get_current_user),
):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    if not _can_write(current_user, project_id):
        raise HTTPException(status_code=403, detail="Write permission required to upload reference docs")
    from pathlib import Path as _P
    allowed = {".pdf", ".md", ".txt", ".docx", ".doc", ".png", ".jpg", ".jpeg"}
    uploaded = []
    for f in files:
        ext = _P(f.filename or "").suffix.lower()
        if ext not in allowed:
            raise HTTPException(status_code=422,
                detail=f"'{f.filename}' — allowed: PDF, MD, TXT, DOCX, PNG, JPG")
        content = await f.read()
        info = project_store.save_ref(
            project_id, f.filename or "document",
            content, uploaded_by=current_user.get("username", "entity"),
            linked_spec=linked_spec, description=description,
        )
        uploaded.append(info)
    return {"uploaded": uploaded}


@router.get("/projects/{project_id}/refs/{filename}")
def serve_ref(project_id: str, filename: str, current_user=Depends(get_current_user)):
    from fastapi.responses import FileResponse
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    uid = current_user.get("sub", "")
    if current_user.get("role") != "admin":
        accessible = set(project_store.get_accessible_refs(project_id, uid))
        if filename not in accessible:
            raise HTTPException(status_code=403, detail="Access denied to this document")
    p = project_store.get_ref_path(project_id, filename)
    if not p:
        raise HTTPException(status_code=404, detail="Reference doc not found")
    return FileResponse(str(p), filename=filename)


@router.delete("/projects/{project_id}/refs/{filename}", status_code=204)
def delete_ref(project_id: str, filename: str, current_user=Depends(get_current_user)):
    if project_id not in _allowed_projects(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    if not _can_write(current_user, project_id):
        raise HTTPException(status_code=403, detail="Write permission required")
    try:
        project_store.delete_ref(project_id, filename)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))