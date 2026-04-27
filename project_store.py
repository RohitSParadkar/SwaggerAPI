"""
Project store — each project lives in data/projects/<project_id>/
  meta.json        — project metadata
  specs_meta.json  — per-file upload info (uploader, version, notes)
  specs/           — uploaded OpenAPI YAML/JSON files
"""
import json
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import PROJECTS_DIR

ALLOWED_EXTS = {".yaml", ".yml", ".json"}


# ── Path helpers ──────────────────────────────────────────────────────────────

def _project_dir(pid: str) -> Path:
    return PROJECTS_DIR / pid

def _meta_path(pid: str) -> Path:
    return _project_dir(pid) / "meta.json"

def _specs_dir(pid: str) -> Path:
    d = _project_dir(pid) / "specs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _load_meta(pid: str) -> Optional[dict]:
    p = _meta_path(pid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

def _save_meta(pid: str, meta: dict) -> None:
    _meta_path(pid).write_text(json.dumps(meta, indent=2), encoding="utf-8")

def _spec_meta_path(pid: str) -> Path:
    return _project_dir(pid) / "specs_meta.json"

def _load_spec_meta(pid: str) -> dict:
    p = _spec_meta_path(pid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def _save_spec_meta(pid: str, meta: dict) -> None:
    _spec_meta_path(pid).write_text(json.dumps(meta, indent=2), encoding="utf-8")


# ── Projects CRUD ─────────────────────────────────────────────────────────────

def list_projects() -> list[dict]:
    out = []
    if not PROJECTS_DIR.exists():
        return out
    for d in sorted(PROJECTS_DIR.iterdir()):
        if d.is_dir():
            m = _load_meta(d.name)
            if m:
                m["spec_count"] = len(list_specs(d.name))
                out.append(m)
    return out

def get_project(pid: str) -> Optional[dict]:
    m = _load_meta(pid)
    if m:
        m["spec_count"] = len(list_specs(pid))
    return m

def create_project(name: str, description: str = "", created_by: str = "admin") -> dict:
    pid = str(uuid.uuid4())
    _project_dir(pid).mkdir(parents=True, exist_ok=True)
    _specs_dir(pid)
    meta = {
        "id":          pid,
        "name":        name,
        "description": description,
        "created_at":  datetime.utcnow().isoformat(),
        "created_by":  created_by,
    }
    _save_meta(pid, meta)
    return meta

def update_project(pid: str, **kwargs) -> dict:
    meta = _load_meta(pid)
    if not meta:
        raise KeyError(f"Project {pid} not found")
    meta.update(kwargs)
    _save_meta(pid, meta)
    return meta

def delete_project(pid: str) -> None:
    d = _project_dir(pid)
    if not d.exists():
        raise KeyError("Project not found")
    shutil.rmtree(d)


# ── Specs CRUD ────────────────────────────────────────────────────────────────

def list_specs(pid: str) -> list[dict]:
    sd = _specs_dir(pid)
    spec_meta = _load_spec_meta(pid)
    out = []
    for f in sorted(sd.iterdir()):
        # accept any recognised extension
        if f.suffix.lower() not in ALLOWED_EXTS:
            continue
        if f.name == "specs_meta.json":
            continue
        s = f.stat()
        m = spec_meta.get(f.name, {})
        out.append({
            "filename":    f.name,
            "stem":        f.stem,
            "size":        s.st_size,
            "modified":    datetime.fromtimestamp(s.st_mtime).isoformat(),
            "uploaded_by": m.get("uploaded_by", "unknown"),
            "uploaded_at": m.get("uploaded_at", datetime.fromtimestamp(s.st_mtime).isoformat()),
            "version":     m.get("version", "1.0.0"),
            "notes":       m.get("notes", ""),
            "format":      f.suffix.lower().lstrip("."),
        })
    return out


def save_spec(pid: str, filename: str, content: bytes,
              uploaded_by: str = "admin",
              version: str = "1.0.0",
              notes: str = "") -> dict:
    if not _load_meta(pid):
        raise KeyError(f"Project {pid} not found")
    dest = _specs_dir(pid) / filename
    dest.write_bytes(content)
    s = dest.stat()
    spec_meta = _load_spec_meta(pid)
    now = datetime.utcnow().isoformat()
    spec_meta[filename] = {
        "uploaded_by": uploaded_by,
        "uploaded_at": now,
        "version":     version,
        "notes":       notes,
    }
    _save_spec_meta(pid, spec_meta)
    return {
        "filename":    dest.name,
        "stem":        dest.stem,
        "size":        s.st_size,
        "modified":    datetime.fromtimestamp(s.st_mtime).isoformat(),
        "uploaded_by": uploaded_by,
        "uploaded_at": now,
        "version":     version,
        "notes":       notes,
        "format":      dest.suffix.lower().lstrip("."),
    }


def get_spec_path(pid: str, filename: str) -> Optional[Path]:
    p = _specs_dir(pid) / filename
    return p if p.exists() else None


def delete_spec(pid: str, filename: str) -> None:
    p = _specs_dir(pid) / filename
    if not p.exists():
        raise KeyError("Spec not found")
    p.unlink()
    spec_meta = _load_spec_meta(pid)
    spec_meta.pop(filename, None)
    _save_spec_meta(pid, spec_meta)


def get_spec_content(pid: str, filename: str):
    """Return parsed spec as dict — supports YAML and JSON."""
    import yaml as _yaml, json as _json
    p = get_spec_path(pid, filename)
    if not p:
        return None
    text = p.read_text(encoding="utf-8")
    try:
        if p.suffix.lower() == ".json":
            return _json.loads(text)
        return _yaml.safe_load(text)
    except Exception as e:
        raise ValueError(f"Cannot parse {filename}: {e}")