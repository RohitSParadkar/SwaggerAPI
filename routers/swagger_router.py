import json
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from middleware.auth import get_current_user
import project_store, user_store

router = APIRouter(tags=["Swagger UI"])
CDN = "https://unpkg.com/swagger-ui-dist@5.17.14"


@router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def swagger_ui(request: Request):
    try:
        user = await get_current_user(request)
    except Exception:
        return _login_redirect()

    token = (request.query_params.get("token") or
             request.headers.get("Authorization", "").replace("Bearer ", "") or
             request.cookies.get("token", ""))

    # Resolve allowed projects + permissions
    if user.get("role") == "admin":
        all_projects = project_store.list_projects()
        perms = {p["id"]: "write" for p in all_projects}
    else:
        uid  = user.get("sub")
        u    = user_store.get_user_by_id(uid) if uid else None
        perms = {}
        if u:
            perms = u.get("project_permissions", {})
            if not perms and u.get("projects"):
                perms = {pid: "read" for pid in u["projects"]}
        allowed_pids = list(perms.keys())
        all_projects = [p for p in project_store.list_projects() if p["id"] in allowed_pids]

    base = str(request.base_url).rstrip("/")
    uid  = user.get("sub", "")
    is_admin = user.get("role") == "admin"

    nav_data = []
    for proj in all_projects:
        # Get only documents this user is allowed to see
        if is_admin:
            all_docs = project_store.list_documents(proj["id"])
        else:
            accessible = set(project_store.get_accessible_specs(proj["id"], uid))
            all_specs  = [s for s in project_store.list_specs(proj["id"])
                          if s["filename"] in accessible]
            # Re-group into documents structure
            docs_map: dict[str, list] = {}
            for s in all_specs:
                docs_map.setdefault(s["base_name"], []).append(s)
            all_docs = []
            for base_name, versions in sorted(docs_map.items()):
                vs = sorted(versions, key=lambda x: x["version_num"])
                all_docs.append({"base_name": base_name, "versions": vs, "latest": vs[-1]})

        if not all_docs:
            continue

        proj_entry = {
            "id": proj["id"],
            "name": proj["name"],
            "permission": perms.get(proj["id"], "read"),
            "documents": []
        }
        # Pre-load refs for this project so we can embed them per-version
        if is_admin:
            all_refs = project_store.list_refs(proj["id"])
        else:
            accessible_refs = set(project_store.get_accessible_refs(proj["id"], uid))
            all_refs = [r for r in project_store.list_refs(proj["id"])
                        if r["filename"] in accessible_refs]
        # Build a map: spec_filename -> list of ref objects
        refs_by_spec: dict[str, list] = {}
        for r in all_refs:
            ls = r.get("linked_spec", "")
            if ls:
                refs_by_spec.setdefault(ls, []).append({
                    "filename":    r["filename"],
                    "format":      r["format"],
                    "description": r.get("description", ""),
                    "view_url":    f"{base}/api/entity/projects/{proj['id']}/refs/{r['filename']}/view?token={token}" if token else f"{base}/api/entity/projects/{proj['id']}/refs/{r['filename']}/view",
                    "download_url": f"{base}/api/entity/projects/{proj['id']}/refs/{r['filename']}?token={token}" if token else f"{base}/api/entity/projects/{proj['id']}/refs/{r['filename']}",
                })

        for doc in all_docs:
            doc_entry = {"base_name": doc["base_name"], "versions": []}
            for v in doc["versions"]:
                url = f"{base}/api/entity/projects/{proj['id']}/specs/{v['filename']}"
                if token:
                    url += f"?token={token}"
                doc_entry["versions"].append({
                    "filename":    v["filename"],
                    "version":     v["version"],
                    "version_num": v["version_num"],
                    "url":         url,
                    "label":       f"v{v['version_num']} — {v['version']}",
                    "refs":        refs_by_spec.get(v["filename"], []),
                })
            proj_entry["documents"].append(doc_entry)
        nav_data.append(proj_entry)

    write_projects = [
        {"id": p["id"], "name": p["name"]}
        for p in all_projects
        if perms.get(p["id"]) == "write"
    ]

    if not nav_data and not write_projects:
        return HTMLResponse(_no_specs_page(user["username"]), status_code=200)

    return HTMLResponse(_swagger_page(nav_data, write_projects, user, token, base))


def _swagger_page(nav_data, write_projects, user, token, base):
    preauth = (f"ui.preauthorizeApiKey && ui.preauthorizeApiKey('BearerAuth', '{token}');"
               if token else "")
    role_badge_colors = {"admin": "#6366f1", "entity": "#0ea5e9"}
    badge_color = role_badge_colors.get(user.get("role", "entity"), "#64748b")
    nav_json        = json.dumps(nav_data)
    write_proj_json = json.dumps(write_projects)
    has_write       = len(write_projects) > 0
    upload_btn_html = (
        '<button id="upload-toggle-btn" onclick="toggleUpload()" '
        'style="padding:5px 13px;background:#10b981;color:#fff;border:none;'
        'border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;'
        'display:flex;align-items:center;gap:5px;white-space:nowrap">'
        '<span style="font-size:14px">⬆</span> Upload Docs'
        '</button>'
    ) if has_write else ""

    all_latest = []
    for proj in nav_data:
        for doc in proj["documents"]:
            latest = doc["versions"][-1]
            all_latest.append({"url": latest["url"],
                                "name": f"{proj['name']} / {doc['base_name']}"})

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>API Hub — {user['username']}</title>
  <link rel="stylesheet" href="{CDN}/swagger-ui.css"/>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc}}
    #topbar{{background:#0f172a;color:#fff;padding:0 20px;height:54px;display:flex;align-items:center;gap:10px;justify-content:space-between;position:sticky;top:0;z-index:200}}
    #topbar h1{{font-size:15px;font-weight:600;letter-spacing:-.2px}}
    #topbar .sub{{font-size:11px;opacity:.5;margin-top:2px}}
    .role-badge{{font-size:11px;padding:3px 10px;border-radius:20px;font-weight:500;background:{badge_color};color:#fff;flex-shrink:0}}
    .logout-btn{{background:rgba(255,255,255,.1);border:none;color:#fff;font-size:12px;padding:5px 12px;border-radius:6px;cursor:pointer;flex-shrink:0}}
    .logout-btn:hover{{background:rgba(255,255,255,.2)}}
    #selector-bar{{background:#fff;border-bottom:1px solid #e2e8f0;padding:8px 20px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;position:sticky;top:54px;z-index:199;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
    .bar-label{{font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap}}
    .sel{{padding:6px 10px;border:1px solid #d1d5db;border-radius:7px;font-size:13px;color:#0f172a;background:#fff;outline:none;min-width:155px;cursor:pointer;transition:border-color .15s}}
    .sel:focus{{border-color:#6366f1;box-shadow:0 0 0 3px rgba(99,102,241,.1)}}
    .sel:disabled{{background:#f8fafc;color:#94a3b8;cursor:default}}
    .arrow{{font-size:11px;color:#cbd5e1;flex-shrink:0}}
    #load-btn{{padding:6px 13px;background:#6366f1;color:#fff;border:none;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap;flex-shrink:0}}
    #load-btn:hover{{background:#4f46e5}}
    #load-btn:disabled{{background:#94a3b8;cursor:default}}
    #spec-label{{font-size:11px;color:#94a3b8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:160px}}
    .bar-spacer{{flex:1}}
    #upload-drawer{{background:#f0fdf4;border-bottom:2px solid #6ee7b7;overflow:hidden;max-height:0;transition:max-height .35s cubic-bezier(.4,0,.2,1);position:sticky;top:calc(54px + 45px);z-index:198}}
    #upload-drawer.open{{max-height:260px}}
    .drawer-inner{{padding:14px 20px}}
    .drawer-title{{font-size:13px;font-weight:700;color:#065f46;margin-bottom:11px;display:flex;align-items:center;gap:6px}}
    .drawer-fields{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}}
    .d-field{{display:flex;flex-direction:column;gap:3px;flex:1;min-width:140px}}
    .d-field label{{font-size:10px;font-weight:700;color:#374151;text-transform:uppercase;letter-spacing:.05em}}
    .d-field select,.d-field input{{padding:7px 10px;border:1px solid #d1d5db;border-radius:7px;font-size:13px;background:#fff;color:#0f172a;outline:none;width:100%}}
    .d-field select:focus,.d-field input:focus{{border-color:#10b981;box-shadow:0 0 0 3px rgba(16,185,129,.1)}}
    .drop-zone{{border:2px dashed #6ee7b7;border-radius:8px;padding:10px 16px;cursor:pointer;transition:all .2s;background:#fff;display:flex;align-items:center;gap:12px;flex:1;min-width:220px}}
    .drop-zone:hover,.drop-zone.drag{{border-color:#10b981;background:#ecfdf5}}
    .drop-zone .dz-icon{{font-size:22px;flex-shrink:0}}
    .drop-zone .dz-text strong{{display:block;font-size:13px;color:#0f172a}}
    .drop-zone .dz-text span{{font-size:11px;color:#64748b}}
    #up-feedback{{font-size:12px;margin-top:6px}}
    .s-ok{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:7px 12px;color:#15803d}}
    .s-err{{background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:7px 12px;color:#dc2626}}
    .bv{{display:inline-block;font-size:10px;padding:1px 7px;border-radius:20px;font-weight:600;background:#fef9c3;color:#a16207}}
    .ref-doc-btn{{padding:5px 11px;background:#7c3aed;color:#fff;border:none;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap;display:inline-flex;align-items:center;gap:5px;text-decoration:none}}
    .ref-doc-btn:hover{{background:#6d28d9}}
    .pm-newtab{{background:rgba(255,255,255,.12);border:none;color:#f1f5f9;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600;text-decoration:none;display:inline-flex;align-items:center}}
    .pm-newtab:hover{{background:rgba(255,255,255,.22)}}
    .bn{{display:inline-block;font-size:10px;padding:1px 7px;border-radius:20px;font-weight:600;background:#dcfce7;color:#15803d}}
    .swagger-ui .topbar{{display:none!important}}
    #swagger-ui{{min-height:calc(100vh - 99px)}}
  </style>
</head>
<body>

<div id="topbar">
  <div>
    <h1>⚡ API Documentation Hub</h1>
    <div class="sub">Signed in as <strong>{user['username']}</strong></div>
  </div>
  <div style="display:flex;align-items:center;gap:8px">
    <span class="role-badge">{user.get('role','entity')}</span>
    {upload_btn_html}
    <button class="logout-btn" onclick="fetch('/api/auth/logout',{{method:'POST'}}).then(()=>window.location='/login.html')">Logout</button>
  </div>
</div>

<div id="selector-bar">
  <span class="bar-label">Project</span>
  <select id="dd-project" class="sel" onchange="onProjectChange()">
    <option value="">— select —</option>
  </select>
  <span class="arrow">›</span>
  <span class="bar-label">Document</span>
  <select id="dd-document" class="sel" disabled onchange="onDocumentChange()">
    <option value="">— select —</option>
  </select>
  <span class="arrow">›</span>
  <span class="bar-label">Version</span>
  <select id="dd-version" class="sel" disabled onchange="onVersionChange()">
    <option value="">— select —</option>
  </select>
  <button id="load-btn" disabled onclick="loadSelectedSpec()">Load ↗</button>
  <span id="spec-label"></span>
  <div id="ref-doc-bar" style="display:none;align-items:center;gap:6px;flex-wrap:wrap;margin-left:8px"></div>
  <span class="bar-spacer"></span>
</div>

<!-- Reference Document Preview Modal -->
<div id="refDocModal" onclick="if(event.target===this)closeRefModal()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:400;align-items:center;justify-content:center">
  <div style="background:#1e293b;border-radius:12px;width:min(960px,95vw);height:90vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 25px 60px rgba(0,0,0,.5)">
    <div style="padding:12px 16px;display:flex;align-items:center;gap:10px;border-bottom:1px solid rgba(255,255,255,.1)">
      <span style="font-size:16px">📎</span>
      <h3 id="ref-modal-title" style="color:#f1f5f9;font-size:14px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Reference Document</h3>
      <a id="ref-modal-newtab" href="#" target="_blank" class="pm-newtab">↗ New Tab</a>
      <a id="ref-modal-download" href="#" download style="background:#0ea5e9;border:none;color:#fff;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600;text-decoration:none;display:inline-flex;align-items:center;gap:5px">⬇ Download</a>
      <button onclick="closeRefModal()" style="background:rgba(255,255,255,.1);border:none;color:#f1f5f9;padding:6px 12px;border-radius:6px;cursor:pointer;font-size:12px">✕ Close</button>
    </div>
    <iframe id="ref-modal-iframe" src="about:blank" style="flex:1;border:none;width:100%;background:#fff"></iframe>
  </div>
</div>

<div id="upload-drawer">
  <div class="drawer-inner">
    <div class="drawer-title">⬆ Upload API Contract</div>
    <div class="drawer-fields">
      <div class="d-field" style="max-width:200px">
        <label>Project *</label>
        <select id="up-project">
          <option value="">— select —</option>
        </select>
      </div>
      <div class="d-field">
        <label>Version label</label>
        <input id="up-version" placeholder="e.g. 2.1.0 — blank = auto">
      </div>
      <div class="d-field">
        <label>Notes</label>
        <input id="up-notes" placeholder="e.g. Added payment endpoints">
      </div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:stretch">
      <div class="drop-zone" id="up-dropzone"
           onclick="document.getElementById('up-file').click()"
           ondragover="upDv(event,true)" ondragleave="upDv(event,false)" ondrop="upDrop(event)">
        <div class="dz-icon">⬆</div>
        <div class="dz-text">
          <strong>Drop YAML / JSON / Postman here</strong>
          <span>or click to browse &nbsp;·&nbsp; same filename → new version auto-created</span>
        </div>
      </div>
      <input type="file" id="up-file" accept=".yaml,.yml,.json" multiple style="display:none" onchange="doUpload(this.files)">
    </div>
    <div id="up-feedback"></div>
  </div>
</div>

<div id="swagger-ui"></div>

<script src="{CDN}/swagger-ui-bundle.js"></script>
<script src="{CDN}/swagger-ui-standalone-preset.js"></script>
<script>
const NAV         = {nav_json};
const WRITE_PROJS = {write_proj_json};
const TOKEN       = {json.dumps(token)};
const API_BASE    = {json.dumps(base)};

let ui = null, selectedUrl = null;

window.onload = function() {{
  const allLatest = [];
  NAV.forEach(proj => proj.documents.forEach(doc => {{
    const v = doc.versions[doc.versions.length - 1];
    allLatest.push({{ url: v.url, name: proj.name + ' / ' + doc.base_name }});
  }}));

  ui = SwaggerUIBundle({{
    urls: allLatest,
    'urls.primaryName': allLatest.length ? allLatest[0].name : '',
    dom_id: '#swagger-ui',
    deepLinking: true,
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
    layout: 'StandaloneLayout',
    persistAuthorization: true,
    displayRequestDuration: true,
    filter: true,
    tryItOutEnabled: true,
  }});
  window.ui = ui;
  setTimeout(() => {{ {preauth} }}, 500);

  // Populate view-project dropdown
  const ddP = document.getElementById('dd-project');
  NAV.forEach(proj => {{
    const o = document.createElement('option');
    o.value = proj.id;
    o.textContent = proj.name + (proj.permission === 'write' ? ' ✏' : '');
    ddP.appendChild(o);
  }});
  if (NAV.length === 1) {{ ddP.value = NAV[0].id; onProjectChange(); }}

  // Populate upload-project dropdown
  const upP = document.getElementById('up-project');
  WRITE_PROJS.forEach(p => {{
    const o = document.createElement('option');
    o.value = p.id; o.textContent = p.name;
    upP.appendChild(o);
  }});
  if (WRITE_PROJS.length === 1) upP.value = WRITE_PROJS[0].id;
}};

// ── View selector bar ─────────────────────────────────────────────────────────
function onProjectChange() {{
  const pid = document.getElementById('dd-project').value;
  const ddD = document.getElementById('dd-document');
  const ddV = document.getElementById('dd-version');
  ddD.innerHTML = '<option value="">— select —</option>';
  ddV.innerHTML = '<option value="">— select —</option>';
  ddD.disabled = true; ddV.disabled = true;
  document.getElementById('load-btn').disabled = true;
  document.getElementById('spec-label').textContent = '';
  selectedUrl = null;
  if (!pid) return;
  const proj = NAV.find(p => p.id === pid);
  if (!proj) return;
  proj.documents.forEach(doc => {{
    const o = document.createElement('option');
    o.value = doc.base_name;
    o.textContent = doc.base_name + (doc.versions.length > 1 ? ` (${{doc.versions.length}} versions)` : '');
    ddD.appendChild(o);
  }});
  ddD.disabled = false;
  if (proj.documents.length === 1) {{ ddD.value = proj.documents[0].base_name; onDocumentChange(); }}
}}

function onDocumentChange() {{
  const pid  = document.getElementById('dd-project').value;
  const base = document.getElementById('dd-document').value;
  const ddV  = document.getElementById('dd-version');
  ddV.innerHTML = '<option value="">— select —</option>';
  ddV.disabled = true;
  document.getElementById('load-btn').disabled = true;
  selectedUrl = null;
  updateRefDocBar([]);
  if (!base) return;
  const proj = NAV.find(p => p.id === pid);
  const doc  = proj && proj.documents.find(d => d.base_name === base);
  if (!doc) return;
  doc.versions.forEach(v => {{
    const o = document.createElement('option');
    o.value = v.url;
    o.textContent = v.label + (v === doc.versions[doc.versions.length-1] ? '  ★ latest' : '');
    ddV.appendChild(o);
  }});
  ddV.disabled = false;
  const latest = doc.versions[doc.versions.length - 1];
  if (latest) {{
    ddV.value = latest.url; selectedUrl = latest.url;
    document.getElementById('load-btn').disabled = false;
    document.getElementById('spec-label').textContent = latest.filename;
    updateRefDocBar(latest.refs || []);
  }}
}}

function onVersionChange() {{
  const url = document.getElementById('dd-version').value;
  selectedUrl = url || null;
  document.getElementById('load-btn').disabled = !selectedUrl;
  const opt = document.getElementById('dd-version').selectedOptions[0];
  document.getElementById('spec-label').textContent =
    opt ? opt.textContent.replace('  ★ latest','').trim() : '';
  // Find the version object to get its refs
  const pid  = document.getElementById('dd-project').value;
  const base = document.getElementById('dd-document').value;
  const proj = NAV.find(p => p.id === pid);
  const doc  = proj && proj.documents.find(d => d.base_name === base);
  const ver  = doc && doc.versions.find(v => v.url === url);
  updateRefDocBar(ver ? (ver.refs || []) : []);
}}

function loadSelectedSpec() {{
  if (!selectedUrl || !ui) return;
  ui.specActions.updateUrl(selectedUrl);
  ui.specActions.download(selectedUrl);
  const opt = document.getElementById('dd-version').selectedOptions[0];
  document.getElementById('spec-label').textContent =
    opt ? opt.textContent.replace('  ★ latest','').trim() : '';
}}

// ── Reference Document bar & modal ────────────────────────────────────────────
const PREVIEWABLE = new Set(['pdf','png','jpg','jpeg']);

function updateRefDocBar(refs) {{
  const bar = document.getElementById('ref-doc-bar');
  while (bar.firstChild) bar.removeChild(bar.firstChild);
  if (!refs || !refs.length) {{ bar.style.display = 'none'; return; }}
  const label = document.createElement('span');
  label.style.cssText = 'font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap';
  label.textContent = 'REF DOCS';
  bar.appendChild(label);
  refs.forEach(function(r) {{
    if (PREVIEWABLE.has(r.format)) {{
      const btn = document.createElement('button');
      btn.className = 'ref-doc-btn';
      btn.title = 'Preview: ' + r.filename;
      btn.textContent = '📎 ' + r.filename;
      (function(ref) {{
        btn.addEventListener('click', function() {{
          openRefModal(ref.view_url, ref.filename, ref.download_url);
        }});
      }})(r);
      bar.appendChild(btn);
    }} else {{
      const a = document.createElement('a');
      a.className = 'ref-doc-btn';
      a.href = r.download_url;
      a.target = '_blank';
      a.title = 'Open: ' + r.filename;
      a.textContent = '📎 ' + r.filename + ' ↗';
      bar.appendChild(a);
    }}
  }});
  bar.style.display = 'flex';
  bar.style.alignItems = 'center';
  bar.style.gap = '6px';
}}

function openRefModal(viewUrl, filename, downloadUrl) {{
  document.getElementById('ref-modal-title').textContent = filename;
  document.getElementById('ref-modal-iframe').src = viewUrl;
  document.getElementById('ref-modal-download').href = downloadUrl;
  document.getElementById('ref-modal-download').download = filename;
  document.getElementById('ref-modal-newtab').href = viewUrl;
  const modal = document.getElementById('refDocModal');
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
}}

function closeRefModal() {{
  document.getElementById('refDocModal').style.display = 'none';
  document.getElementById('ref-modal-iframe').src = 'about:blank';
  document.body.style.overflow = '';
}}

document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeRefModal(); }});

// ── Upload drawer ─────────────────────────────────────────────────────────────
function toggleUpload() {{
  const drawer = document.getElementById('upload-drawer');
  const btn    = document.getElementById('upload-toggle-btn');
  const open   = drawer.classList.toggle('open');
  btn.style.background = open ? '#059669' : '#10b981';
  if (open) document.getElementById('up-feedback').innerHTML = '';
}}

function upDv(e, on) {{
  e.preventDefault();
  document.getElementById('up-dropzone').classList.toggle('drag', on);
}}

function upDrop(e) {{
  e.preventDefault();
  document.getElementById('up-dropzone').classList.remove('drag');
  doUpload(e.dataTransfer.files);
}}

async function doUpload(files) {{
  const fileInput = document.getElementById('up-file');
  const pid     = document.getElementById('up-project').value;
  const version = document.getElementById('up-version').value.trim();
  const notes   = document.getElementById('up-notes').value.trim();
  const fb      = document.getElementById('up-feedback');

  // Always reset the file input so the same file can be re-selected after an error
  fileInput.value = '';

  if (!pid) {{
    fb.innerHTML = '<div class="s-err">⚠ Please select a project first, then drop or browse your file again.</div>';
    return;
  }}

  const fd = new FormData();
  Array.from(files).forEach(f => fd.append('files', f));
  if (version) fd.append('version', version);
  if (notes)   fd.append('notes', notes);

  fb.innerHTML = '<div style="color:#059669;font-size:12px;padding:4px 0">⏳ Uploading…</div>';

  try {{
    const r = await fetch(API_BASE + '/api/entity/projects/' + pid + '/specs', {{
      method: 'POST',
      headers: {{ 'Authorization': 'Bearer ' + TOKEN }},
      body: fd
    }});
    const d = await r.json();
    if (!r.ok) throw d.detail || JSON.stringify(d);

    const items = d.uploaded.map(u =>
      '<strong>' + u.base_name + '</strong> ' +
      '<span class="bv">v' + u.version_num + ' · ' + u.version + '</span>' +
      (u.version_num > 1 ? ' <span class="bn">new version</span>' : '')
    ).join(' &nbsp;·&nbsp; ');

    fb.innerHTML = '<div class="s-ok">✓ Uploaded: ' + items + '</div>';
    setTimeout(() => {{ fb.innerHTML = ''; }}, 8000);

    refreshNavAfterUpload(pid);
  }} catch(e) {{
    fb.innerHTML = '<div class="s-err">✗ ' + e + '</div>';
    // input already reset at top of function — user can retry immediately
  }}
}}

// Refresh nav_data + selector bar after upload
async function refreshNavAfterUpload(pid) {{
  try {{
    const r = await fetch(API_BASE + '/api/entity/projects/' + pid + '/documents', {{
      headers: {{ 'Authorization': 'Bearer ' + TOKEN }}
    }});
    if (!r.ok) return;
    const freshDocs = await r.json();
    const projEntry = NAV.find(p => p.id === pid);
    if (projEntry) {{
      projEntry.documents = freshDocs.map(doc => {{
        const entry = {{ base_name: doc.base_name, versions: [] }};
        (doc.versions || []).forEach(v => {{
          const url = API_BASE + '/api/entity/projects/' + pid + '/specs/' + v.filename + (TOKEN ? '?token=' + TOKEN : '');
          entry.versions.push({{ filename: v.filename, version: v.version, version_num: v.version_num, url, label: 'v' + v.version_num + ' — ' + v.version }});
        }});
        return entry;
      }});
    }}
    // If the currently selected project is the same one, refresh its dropdowns
    if (document.getElementById('dd-project').value === pid) onProjectChange();
  }} catch(e) {{ console.warn('nav refresh:', e); }}
}}
</script>
</body>
</html>"""


def _no_specs_page(username):
    return f"""<!DOCTYPE html>
<html><head><title>API Hub</title>
<style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;background:#f8fafc}}
.box{{text-align:center;padding:2rem;background:#fff;border-radius:12px;border:1px solid #e2e8f0;max-width:400px}}
h2{{margin-bottom:8px;color:#0f172a}}p{{color:#64748b;font-size:14px;line-height:1.6}}</style></head>
<body><div class="box">
<h2>👋 Hello, {username}</h2>
<p>No API contracts have been assigned to your account yet.<br>Contact your administrator to get access.</p>
<a href="/login.html" style="display:inline-block;margin-top:1rem;padding:8px 20px;background:#0f172a;color:#fff;border-radius:6px;text-decoration:none;font-size:13px">Back to login</a>
</div></body></html>"""


def _login_redirect():
    return HTMLResponse(
        '<script>window.location="/login.html"</script>',
        status_code=302,
        headers={"Location": "/login.html"},
    )