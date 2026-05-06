"""
Project store — each project lives in data/projects/<project_id>/
  meta.json          — project metadata
  specs_meta.json    — per-file upload info
  refs_meta.json     — per-ref upload info
  access_meta.json   — document-level access: {"specs":{fn:[uid,...]}, "refs":{fn:[uid,...]}}
  specs/             — active OpenAPI YAML/JSON files
  refs/              — active reference documents
  backup/specs/      — archived API contract versions
  backup/refs/       — archived reference documents
"""
import json, uuid, shutil, re
from datetime import datetime
from pathlib import Path
from typing import Optional
from config import PROJECTS_DIR

ALLOWED_EXTS     = {".yaml", ".yml", ".json"}
ALLOWED_REF_EXTS = {".pdf", ".md", ".txt", ".docx", ".doc", ".png", ".jpg", ".jpeg"}

# ── Path helpers ──────────────────────────────────────────────────────────────
def _project_dir(pid): return PROJECTS_DIR / pid
def _meta_path(pid):   return _project_dir(pid) / "meta.json"

def _specs_dir(pid):
    d = _project_dir(pid) / "specs"; d.mkdir(parents=True, exist_ok=True); return d

def _refs_dir(pid):
    d = _project_dir(pid) / "refs"; d.mkdir(parents=True, exist_ok=True); return d

def _backup_specs_dir(pid):
    d = _project_dir(pid) / "backup" / "specs"; d.mkdir(parents=True, exist_ok=True); return d

def _backup_refs_dir(pid):
    d = _project_dir(pid) / "backup" / "refs"; d.mkdir(parents=True, exist_ok=True); return d

# ── Meta loaders/savers ───────────────────────────────────────────────────────
def _jload(p): return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
def _jsave(p, d): p.write_text(json.dumps(d, indent=2), encoding="utf-8")

def _load_meta(pid):
    p = _meta_path(pid); return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
def _save_meta(pid, m): _meta_path(pid).write_text(json.dumps(m, indent=2), encoding="utf-8")

def _load_spec_meta(pid):  return _jload(_project_dir(pid) / "specs_meta.json")
def _save_spec_meta(pid,m): _jsave(_project_dir(pid) / "specs_meta.json", m)

def _load_refs_meta(pid):  return _jload(_project_dir(pid) / "refs_meta.json")
def _save_refs_meta(pid,m): _jsave(_project_dir(pid) / "refs_meta.json", m)

def _load_bak_spec_meta(pid):
    return _jload(_project_dir(pid) / "backup" / "specs_meta.json")
def _save_bak_spec_meta(pid, m):
    p = _project_dir(pid) / "backup" / "specs_meta.json"
    p.parent.mkdir(parents=True, exist_ok=True); _jsave(p, m)

def _load_bak_refs_meta(pid):
    return _jload(_project_dir(pid) / "backup" / "refs_meta.json")
def _save_bak_refs_meta(pid, m):
    p = _project_dir(pid) / "backup" / "refs_meta.json"
    p.parent.mkdir(parents=True, exist_ok=True); _jsave(p, m)

# ── Document-level access ─────────────────────────────────────────────────────
def _load_access(pid):
    d = _jload(_project_dir(pid) / "access_meta.json")
    d.setdefault("specs", {}); d.setdefault("refs", {}); return d
def _save_access(pid, m): _jsave(_project_dir(pid) / "access_meta.json", m)

def get_access_meta(pid): return _load_access(pid)

def set_doc_access(pid, kind, filename, user_ids):
    if kind not in ("specs","refs"): raise ValueError("kind must be specs or refs")
    m = _load_access(pid)
    if user_ids: m[kind][filename] = list(set(user_ids))
    else: m[kind].pop(filename, None)
    _save_access(pid, m)

def get_accessible_specs(pid, user_id):
    m = _load_access(pid)
    return [s["filename"] for s in list_specs(pid)
            if not m["specs"].get(s["filename"]) or user_id in m["specs"][s["filename"]]]

def get_accessible_refs(pid, user_id):
    m = _load_access(pid)
    return [r["filename"] for r in list_refs(pid)
            if not m["refs"].get(r["filename"]) or user_id in m["refs"][r["filename"]]]

# ── Version helpers ───────────────────────────────────────────────────────────
def _base_stem(stem): return re.sub(r"_v\d+$", "", stem)

def _next_versioned_filename(pid, filename):
    stem = Path(filename).stem; ext = Path(filename).suffix.lower(); base = _base_stem(stem)
    existing = []
    for f in _specs_dir(pid).iterdir():
        if f.suffix.lower() not in ALLOWED_EXTS or _base_stem(f.stem) != base: continue
        mm = re.search(r"_v(\d+)$", f.stem)
        existing.append(int(mm.group(1)) if mm else 1)
    if not existing: return filename, 1
    nv = max(existing)+1; return f"{base}_v{nv}{ext}", nv

# ── Projects ──────────────────────────────────────────────────────────────────
def list_projects():
    out = []
    if not PROJECTS_DIR.exists(): return out
    for d in sorted(PROJECTS_DIR.iterdir()):
        if d.is_dir():
            m = _load_meta(d.name)
            if m: m["spec_count"] = len(list_specs(d.name)); out.append(m)
    return out

def get_project(pid):
    m = _load_meta(pid)
    if m: m["spec_count"] = len(list_specs(pid))
    return m

def create_project(name, description="", created_by="admin"):
    pid = str(uuid.uuid4()); _project_dir(pid).mkdir(parents=True, exist_ok=True); _specs_dir(pid)
    meta = {"id":pid,"name":name,"description":description,"created_at":datetime.utcnow().isoformat(),"created_by":created_by}
    _save_meta(pid, meta); return meta

def update_project(pid, **kwargs):
    meta = _load_meta(pid)
    if not meta: raise KeyError(f"Project {pid} not found")
    meta.update(kwargs); _save_meta(pid, meta); return meta

def delete_project(pid):
    d = _project_dir(pid)
    if not d.exists(): raise KeyError("Project not found")
    shutil.rmtree(d)

# ── Specs ─────────────────────────────────────────────────────────────────────
def list_specs(pid):
    sd = _specs_dir(pid); sm = _load_spec_meta(pid); out = []
    for f in sorted(sd.iterdir()):
        if f.suffix.lower() not in ALLOWED_EXTS or f.name == "specs_meta.json": continue
        s = f.stat(); m = sm.get(f.name, {})
        out.append({"filename":f.name,"stem":f.stem,"base_name":_base_stem(f.stem),
            "size":s.st_size,"modified":datetime.fromtimestamp(s.st_mtime).isoformat(),
            "uploaded_by":m.get("uploaded_by","unknown"),"uploaded_at":m.get("uploaded_at",datetime.fromtimestamp(s.st_mtime).isoformat()),
            "version":m.get("version","1.0.0"),"version_num":m.get("version_num",1),
            "notes":m.get("notes",""),"converted":m.get("converted",False),
            "format":f.suffix.lower().lstrip(".")})
    return out

def list_documents(pid):
    specs = list_specs(pid); docs = {}
    for s in specs: docs.setdefault(s["base_name"],[]).append(s)
    result = []
    for base, versions in sorted(docs.items()):
        vs = sorted(versions, key=lambda x: x["version_num"])
        result.append({"base_name":base,"versions":vs,"latest":vs[-1]})
    return result

def save_spec(pid, filename, content, uploaded_by="admin", version="1.0.0", notes="", converted=False):
    if not _load_meta(pid): raise KeyError(f"Project {pid} not found")
    ff, vnum = _next_versioned_filename(pid, filename)
    dest = _specs_dir(pid) / ff; dest.write_bytes(content)
    s = dest.stat(); sm = _load_spec_meta(pid); now = datetime.utcnow().isoformat()
    sm[ff] = {"uploaded_by":uploaded_by,"uploaded_at":now,"version":version,"version_num":vnum,
              "notes":notes,"converted":converted,"original_filename":filename}
    _save_spec_meta(pid, sm)
    return {"filename":dest.name,"stem":dest.stem,"base_name":_base_stem(dest.stem),"size":s.st_size,
            "modified":datetime.fromtimestamp(s.st_mtime).isoformat(),"uploaded_by":uploaded_by,
            "uploaded_at":now,"version":version,"version_num":vnum,"notes":notes,"converted":converted,
            "format":dest.suffix.lower().lstrip(".")}

def get_spec_path(pid, filename):
    p = _specs_dir(pid) / filename; return p if p.exists() else None

def delete_spec(pid, filename):
    p = _specs_dir(pid) / filename
    if not p.exists(): raise KeyError("Spec not found")
    p.unlink(); sm = _load_spec_meta(pid); sm.pop(filename,None); _save_spec_meta(pid,sm)
    am = _load_access(pid); am["specs"].pop(filename,None); _save_access(pid,am)

def get_spec_content(pid, filename):
    import yaml as _yaml, json as _json
    p = get_spec_path(pid, filename)
    if not p: return None
    text = p.read_text(encoding="utf-8")
    try: return _json.loads(text) if p.suffix.lower()==".json" else _yaml.safe_load(text)
    except Exception as e: raise ValueError(f"Cannot parse {filename}: {e}")

# ── Backup: specs ─────────────────────────────────────────────────────────────
def backup_spec(pid, filename, archived_by="admin"):
    src = _specs_dir(pid) / filename
    if not src.exists(): raise KeyError(f"Spec '{filename}' not found")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest_name = f"{Path(filename).stem}_bak_{ts}{Path(filename).suffix}"
    dest = _backup_specs_dir(pid) / dest_name; shutil.move(str(src), str(dest))
    sm = _load_spec_meta(pid); orig = sm.pop(filename, {}); _save_spec_meta(pid, sm)
    bm = _load_bak_spec_meta(pid)
    bm[dest_name] = {**orig,"original_filename":filename,"archived_by":archived_by,"archived_at":datetime.utcnow().isoformat()}
    _save_bak_spec_meta(pid, bm)
    am = _load_access(pid); am["specs"].pop(filename,None); _save_access(pid,am)
    return {"backup_filename":dest_name,"original":filename}

def list_backup_specs(pid):
    bd = _backup_specs_dir(pid); meta = _load_bak_spec_meta(pid); out = []
    for f in sorted(bd.iterdir()):
        if not f.is_file(): continue
        s = f.stat(); m = meta.get(f.name,{})
        out.append({"backup_filename":f.name,"original_filename":m.get("original_filename",f.name),
            "size":s.st_size,"archived_by":m.get("archived_by","unknown"),"archived_at":m.get("archived_at",""),
            "version":m.get("version",""),"uploaded_by":m.get("uploaded_by",""),"format":f.suffix.lower().lstrip(".")})
    return out

def restore_spec(pid, backup_filename):
    src = _backup_specs_dir(pid) / backup_filename
    if not src.exists(): raise KeyError(f"Backup '{backup_filename}' not found")
    bm = _load_bak_spec_meta(pid); m = bm.pop(backup_filename,{})
    original = m.get("original_filename", backup_filename)
    dest = _specs_dir(pid) / original
    if dest.exists():
        _, vnum = _next_versioned_filename(pid, original)
        original = f"{_base_stem(Path(original).stem)}_v{vnum}{Path(original).suffix}"
        dest = _specs_dir(pid) / original
    shutil.move(str(src), str(dest)); _save_bak_spec_meta(pid, bm)
    sm = _load_spec_meta(pid)
    sm[original] = {"uploaded_by":m.get("uploaded_by","admin"),"uploaded_at":datetime.utcnow().isoformat(),
                    "version":m.get("version","1.0.0"),"version_num":m.get("version_num",1),
                    "notes":f"Restored from {backup_filename}","converted":m.get("converted",False)}
    _save_spec_meta(pid, sm); return {"restored_filename":original}

def delete_backup_spec(pid, backup_filename):
    p = _backup_specs_dir(pid) / backup_filename
    if not p.exists(): raise KeyError("Backup not found")
    p.unlink(); bm = _load_bak_spec_meta(pid); bm.pop(backup_filename,None); _save_bak_spec_meta(pid,bm)

def get_backup_spec_path(pid, filename):
    p = _backup_specs_dir(pid) / filename; return p if p.exists() else None

# ── Refs ──────────────────────────────────────────────────────────────────────
def list_refs(pid):
    rd = _refs_dir(pid); meta = _load_refs_meta(pid); out = []
    for f in sorted(rd.iterdir()):
        if not f.is_file() or f.suffix.lower() not in ALLOWED_REF_EXTS: continue
        s = f.stat(); m = meta.get(f.name,{})
        out.append({"filename":f.name,"size":s.st_size,
            "uploaded_by":m.get("uploaded_by","unknown"),"uploaded_at":m.get("uploaded_at",datetime.fromtimestamp(s.st_mtime).isoformat()),
            "linked_spec":m.get("linked_spec",""),"description":m.get("description",""),
            "format":f.suffix.lower().lstrip(".")})
    return out

def save_ref(pid, filename, content, uploaded_by="admin", linked_spec="", description=""):
    if not _load_meta(pid): raise KeyError(f"Project {pid} not found")
    dest = _refs_dir(pid) / filename
    if dest.exists():
        stem, ext = (filename.rsplit(".",1) if "." in filename else (filename,""))
        i = 2
        while dest.exists(): dest = _refs_dir(pid)/(f"{stem}_{i}.{ext}" if ext else f"{stem}_{i}"); i+=1
    dest.write_bytes(content); s = dest.stat(); now = datetime.utcnow().isoformat()
    rm = _load_refs_meta(pid); rm[dest.name] = {"uploaded_by":uploaded_by,"uploaded_at":now,"linked_spec":linked_spec,"description":description}
    _save_refs_meta(pid, rm)
    return {"filename":dest.name,"size":s.st_size,"uploaded_by":uploaded_by,"uploaded_at":now,"linked_spec":linked_spec,"description":description,"format":dest.suffix.lower().lstrip(".")}

def get_ref_path(pid, filename):
    p = _refs_dir(pid) / filename; return p if p.exists() else None

def delete_ref(pid, filename):
    p = _refs_dir(pid) / filename
    if not p.exists(): raise KeyError("Reference doc not found")
    p.unlink(); rm = _load_refs_meta(pid); rm.pop(filename,None); _save_refs_meta(pid,rm)
    am = _load_access(pid); am["refs"].pop(filename,None); _save_access(pid,am)

# ── Backup: refs ──────────────────────────────────────────────────────────────
def backup_ref(pid, filename, archived_by="admin"):
    src = _refs_dir(pid) / filename
    if not src.exists(): raise KeyError(f"Ref '{filename}' not found")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest_name = f"{Path(filename).stem}_bak_{ts}{Path(filename).suffix}"
    dest = _backup_refs_dir(pid) / dest_name; shutil.move(str(src), str(dest))
    rm = _load_refs_meta(pid); orig = rm.pop(filename,{}); _save_refs_meta(pid,rm)
    bm = _load_bak_refs_meta(pid)
    bm[dest_name] = {**orig,"original_filename":filename,"archived_by":archived_by,"archived_at":datetime.utcnow().isoformat()}
    _save_bak_refs_meta(pid,bm)
    am = _load_access(pid); am["refs"].pop(filename,None); _save_access(pid,am)
    return {"backup_filename":dest_name,"original":filename}

def list_backup_refs(pid):
    bd = _backup_refs_dir(pid); meta = _load_bak_refs_meta(pid); out = []
    for f in sorted(bd.iterdir()):
        if not f.is_file(): continue
        s = f.stat(); m = meta.get(f.name,{})
        out.append({"backup_filename":f.name,"original_filename":m.get("original_filename",f.name),
            "size":s.st_size,"archived_by":m.get("archived_by","unknown"),"archived_at":m.get("archived_at",""),
            "description":m.get("description",""),"format":f.suffix.lower().lstrip(".")})
    return out

def restore_ref(pid, backup_filename):
    src = _backup_refs_dir(pid) / backup_filename
    if not src.exists(): raise KeyError(f"Backup '{backup_filename}' not found")
    bm = _load_bak_refs_meta(pid); m = bm.pop(backup_filename,{})
    original = m.get("original_filename", backup_filename)
    dest = _refs_dir(pid) / original
    if dest.exists():
        stem, ext = (original.rsplit(".",1) if "." in original else (original,""))
        dest = _refs_dir(pid) / f"{stem}_restored.{ext}"
    shutil.move(str(src), str(dest)); _save_bak_refs_meta(pid,bm)
    rm = _load_refs_meta(pid); rm[dest.name] = {**m,"uploaded_at":datetime.utcnow().isoformat(),"description":m.get("description","")+" (restored)"}
    _save_refs_meta(pid,rm); return {"restored_filename":dest.name}

def delete_backup_ref(pid, backup_filename):
    p = _backup_refs_dir(pid) / backup_filename
    if not p.exists(): raise KeyError("Backup not found")
    p.unlink(); bm = _load_bak_refs_meta(pid); bm.pop(backup_filename,None); _save_bak_refs_meta(pid,bm)

def get_backup_ref_path(pid, filename):
    p = _backup_refs_dir(pid) / filename; return p if p.exists() else None