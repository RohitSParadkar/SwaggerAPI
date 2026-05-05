"""
Project store — each project lives in data/projects/<project_id>/
  meta.json        — project metadata
  specs_meta.json  — per-file upload info (uploader, version, notes)
  specs/           — uploaded OpenAPI YAML/JSON files

Versioning: if a file with the same base name already exists, the new
upload is saved as  <stem>_v<N>.<ext>  (e.g. openapi_v2.yaml).
The specs_meta also tracks a `base_name` field so all versions of the
same document can be grouped together.
"""
import json
import uuid
import shutil
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import PROJECTS_DIR

ALLOWED_EXTS = {".yaml", ".yml", ".json"}
ALLOWED_REF_EXTS = {".pdf", ".md", ".txt", ".docx", ".doc", ".png", ".jpg", ".jpeg"}


# ── Path helpers ──────────────────────────────────────────────────────────────

def _project_dir(pid: str) -> Path:
    return PROJECTS_DIR / pid

def _meta_path(pid: str) -> Path:
    return _project_dir(pid) / "meta.json"

def _specs_dir(pid: str) -> Path:
    d = _project_dir(pid) / "specs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _refs_dir(pid: str) -> Path:
    """Reference docs (PDF, MD, etc.) live here."""
    d = _project_dir(pid) / "refs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _refs_meta_path(pid: str) -> Path:
    return _project_dir(pid) / "refs_meta.json"

def _load_refs_meta(pid: str) -> dict:
    p = _refs_meta_path(pid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def _save_refs_meta(pid: str, meta: dict) -> None:
    _refs_meta_path(pid).write_text(json.dumps(meta, indent=2), encoding="utf-8")

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


# ── Version helpers ───────────────────────────────────────────────────────────

def _base_stem(stem: str) -> str:
    """Strip _v<N> suffix to get the canonical document name."""
    return re.sub(r"_v\d+$", "", stem)


def _next_versioned_filename(pid: str, filename: str) -> tuple[str, int]:
    """
    Given an original filename, find the highest existing version of that
    document in the project and return (new_filename, version_number).

    Rules
    -----
    * First upload of a document  → keep original filename, version_num = 1
    * Second upload (same name)   → <stem>_v2.<ext>, version_num = 2
    * Third upload                → <stem>_v3.<ext>, etc.
    """
    from pathlib import Path as _P
    stem = _P(filename).stem
    ext  = _P(filename).suffix.lower()
    base = _base_stem(stem)

    sd = _specs_dir(pid)
    existing_versions: list[int] = []

    for f in sd.iterdir():
        if f.suffix.lower() not in ALLOWED_EXTS:
            continue
        s = _base_stem(f.stem)
        if s != base:
            continue
        # version 1 has no _vN suffix
        m = re.search(r"_v(\d+)$", f.stem)
        existing_versions.append(int(m.group(1)) if m else 1)

    if not existing_versions:
        return filename, 1          # brand-new document
    next_ver = max(existing_versions) + 1
    return f"{base}_v{next_ver}{ext}", next_ver


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
        if f.suffix.lower() not in ALLOWED_EXTS:
            continue
        if f.name == "specs_meta.json":
            continue
        s = f.stat()
        m = spec_meta.get(f.name, {})
        out.append({
            "filename":    f.name,
            "stem":        f.stem,
            "base_name":   _base_stem(f.stem),
            "size":        s.st_size,
            "modified":    datetime.fromtimestamp(s.st_mtime).isoformat(),
            "uploaded_by": m.get("uploaded_by", "unknown"),
            "uploaded_at": m.get("uploaded_at", datetime.fromtimestamp(s.st_mtime).isoformat()),
            "version":     m.get("version", "1.0.0"),
            "version_num": m.get("version_num", 1),
            "notes":       m.get("notes", ""),
            "converted":   m.get("converted", False),   # True if source was Postman/JSON → converted to YAML
            "format":      f.suffix.lower().lstrip("."),
        })
    return out


def list_documents(pid: str) -> list[dict]:
    """
    Return one entry per unique base document name, with a list of all
    versions sorted ascending.  Useful for the cascading dropdowns.
    """
    specs = list_specs(pid)
    docs: dict[str, list] = {}
    for s in specs:
        docs.setdefault(s["base_name"], []).append(s)
    result = []
    for base, versions in sorted(docs.items()):
        versions_sorted = sorted(versions, key=lambda x: x["version_num"])
        result.append({
            "base_name": base,
            "versions":  versions_sorted,
            "latest":    versions_sorted[-1],
        })
    return result


def save_spec(pid: str, filename: str, content: bytes,
              uploaded_by: str = "admin",
              version: str = "1.0.0",
              notes: str = "",
              converted: bool = False) -> dict:
    if not _load_meta(pid):
        raise KeyError(f"Project {pid} not found")

    final_filename, version_num = _next_versioned_filename(pid, filename)

    dest = _specs_dir(pid) / final_filename
    dest.write_bytes(content)
    s = dest.stat()
    spec_meta = _load_spec_meta(pid)
    now = datetime.utcnow().isoformat()
    spec_meta[final_filename] = {
        "uploaded_by":       uploaded_by,
        "uploaded_at":       now,
        "version":           version,
        "version_num":       version_num,
        "notes":             notes,
        "converted":         converted,
        "original_filename": filename,
    }
    _save_spec_meta(pid, spec_meta)
    return {
        "filename":    dest.name,
        "stem":        dest.stem,
        "base_name":   _base_stem(dest.stem),
        "size":        s.st_size,
        "modified":    datetime.fromtimestamp(s.st_mtime).isoformat(),
        "uploaded_by": uploaded_by,
        "uploaded_at": now,
        "version":     version,
        "version_num": version_num,
        "notes":       notes,
        "converted":   converted,
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


# ── Reference docs CRUD ───────────────────────────────────────────────────────
# Reference docs are PDFs, Markdown files, Word docs etc. uploaded alongside
# API contracts for human-readable documentation.

def list_refs(pid: str) -> list[dict]:
    rd = _refs_dir(pid)
    meta = _load_refs_meta(pid)
    out = []
    for f in sorted(rd.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in ALLOWED_REF_EXTS:
            continue
        s = f.stat()
        m = meta.get(f.name, {})
        out.append({
            "filename":    f.name,
            "size":        s.st_size,
            "uploaded_by": m.get("uploaded_by", "unknown"),
            "uploaded_at": m.get("uploaded_at", datetime.fromtimestamp(s.st_mtime).isoformat()),
            "linked_spec": m.get("linked_spec", ""),   # optional: tied to a spec filename
            "description": m.get("description", ""),
            "format":      f.suffix.lower().lstrip("."),
        })
    return out


def save_ref(pid: str, filename: str, content: bytes,
             uploaded_by: str = "admin",
             linked_spec: str = "",
             description: str = "") -> dict:
    if not _load_meta(pid):
        raise KeyError(f"Project {pid} not found")
    dest = _refs_dir(pid) / filename
    # Simple deduplication: if file exists, add _2, _3 suffix
    if dest.exists():
        stem, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
        i = 2
        while dest.exists():
            new_name = f"{stem}_{i}.{ext}" if ext else f"{stem}_{i}"
            dest = _refs_dir(pid) / new_name
            i += 1
    dest.write_bytes(content)
    s = dest.stat()
    now = datetime.utcnow().isoformat()
    refs_meta = _load_refs_meta(pid)
    refs_meta[dest.name] = {
        "uploaded_by": uploaded_by,
        "uploaded_at": now,
        "linked_spec": linked_spec,
        "description": description,
    }
    _save_refs_meta(pid, refs_meta)
    return {
        "filename":    dest.name,
        "size":        s.st_size,
        "uploaded_by": uploaded_by,
        "uploaded_at": now,
        "linked_spec": linked_spec,
        "description": description,
        "format":      dest.suffix.lower().lstrip("."),
    }


def get_ref_path(pid: str, filename: str) -> Optional[Path]:
    p = _refs_dir(pid) / filename
    return p if p.exists() else None


def delete_ref(pid: str, filename: str) -> None:
    p = _refs_dir(pid) / filename
    if not p.exists():
        raise KeyError("Reference doc not found")
    p.unlink()
    meta = _load_refs_meta(pid)
    meta.pop(filename, None)
    _save_refs_meta(pid, meta)