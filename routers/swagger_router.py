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

    # resolve allowed projects
    if user.get("role") == "admin":
        projects = project_store.list_projects()
    else:
        uid  = user.get("sub")
        u    = user_store.get_user_by_id(uid) if uid else None
        pids = (u or {}).get("projects", [])
        projects = [p for p in project_store.list_projects() if p["id"] in pids]

    # Build structured data: projects → documents → versions
    base = str(request.base_url).rstrip("/")
    nav_data = []
    for proj in projects:
        docs = project_store.list_documents(proj["id"])
        if not docs:
            continue
        proj_entry = {"id": proj["id"], "name": proj["name"], "documents": []}
        for doc in docs:
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
                })
            proj_entry["documents"].append(doc_entry)
        nav_data.append(proj_entry)

    if not nav_data:
        return HTMLResponse(_no_specs_page(user["username"]), status_code=200)

    return HTMLResponse(_swagger_page(nav_data, user, token))


def _swagger_page(nav_data, user, token):
    preauth = (f"ui.preauthorizeApiKey && ui.preauthorizeApiKey('BearerAuth', '{token}');"
               if token else "")
    role_badge_colors = {"admin": "#6366f1", "entity": "#0ea5e9"}
    badge_color = role_badge_colors.get(user.get("role", "entity"), "#64748b")

    nav_json = json.dumps(nav_data)

    # Build the initial urls list (all latest versions) for Swagger boot
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
    #topbar{{background:#0f172a;color:#fff;padding:0 24px;height:54px;display:flex;align-items:center;gap:12px;justify-content:space-between;position:sticky;top:0;z-index:100}}
    #topbar h1{{font-size:15px;font-weight:600;letter-spacing:-.2px}}
    #topbar .sub{{font-size:11px;opacity:.55;margin-top:2px}}
    .badge{{font-size:11px;padding:3px 10px;border-radius:20px;font-weight:500;background:{badge_color};color:#fff}}
    .logout-btn{{background:rgba(255,255,255,.1);border:none;color:#fff;font-size:12px;padding:5px 12px;border-radius:6px;cursor:pointer}}
    .logout-btn:hover{{background:rgba(255,255,255,.2)}}

    /* ── Selector bar ── */
    #selector-bar{{background:#fff;border-bottom:1px solid #e2e8f0;padding:10px 24px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;position:sticky;top:54px;z-index:99;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
    #selector-bar label{{font-size:11px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}}
    .sel{{padding:7px 10px;border:1px solid #d1d5db;border-radius:7px;font-size:13px;color:#0f172a;background:#fff;outline:none;min-width:170px;cursor:pointer;transition:border-color .15s}}
    .sel:focus{{border-color:#6366f1;box-shadow:0 0 0 3px rgba(99,102,241,.1)}}
    .sel:disabled{{background:#f8fafc;color:#94a3b8;cursor:default}}
    .arrow{{font-size:12px;color:#94a3b8;user-select:none}}
    #load-btn{{padding:7px 16px;background:#6366f1;color:#fff;border:none;border-radius:7px;font-size:13px;font-weight:500;cursor:pointer;transition:background .15s;white-space:nowrap}}
    #load-btn:hover{{background:#4f46e5}}
    #load-btn:disabled{{background:#94a3b8;cursor:default}}
    #spec-label{{font-size:12px;color:#64748b;margin-left:4px}}

    .swagger-ui .topbar{{display:none!important}}
    #swagger-ui{{min-height:calc(100vh - 108px)}}
  </style>
</head>
<body>

<div id="topbar">
  <div>
    <h1>API Documentation Hub</h1>
    <div class="sub">Signed in as <strong>{user['username']}</strong></div>
  </div>
  <div style="display:flex;align-items:center;gap:10px">
    <span class="badge">{user.get('role','entity')}</span>
    <button class="logout-btn" onclick="fetch('/api/auth/logout',{{method:'POST'}}).then(()=>window.location='/login.html')">Logout</button>
  </div>
</div>

<div id="selector-bar">
  <label>Project</label>
  <select id="dd-project" class="sel" onchange="onProjectChange()">
    <option value="">— select project —</option>
  </select>

  <span class="arrow">›</span>
  <label>Document</label>
  <select id="dd-document" class="sel" disabled onchange="onDocumentChange()">
    <option value="">— select document —</option>
  </select>

  <span class="arrow">›</span>
  <label>Version</label>
  <select id="dd-version" class="sel" disabled onchange="onVersionChange()">
    <option value="">— select version —</option>
  </select>

  <button id="load-btn" disabled onclick="loadSelectedSpec()">Load ↗</button>
  <span id="spec-label"></span>
</div>

<div id="swagger-ui"></div>

<script src="{CDN}/swagger-ui-bundle.js"></script>
<script src="{CDN}/swagger-ui-standalone-preset.js"></script>
<script>
const NAV  = {nav_json};
const ALL_LATEST = {json.dumps(all_latest)};

let ui = null;
let selectedUrl = null;

// ── Bootstrap Swagger with all-latest urls ──────────────────────────────────
window.onload = function() {{
  ui = SwaggerUIBundle({{
    urls: ALL_LATEST,
    "urls.primaryName": ALL_LATEST.length ? ALL_LATEST[0].name : '',
    dom_id: "#swagger-ui",
    deepLinking: true,
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
    layout: "StandaloneLayout",
    persistAuthorization: true,
    displayRequestDuration: true,
    filter: true,
    tryItOutEnabled: true,
  }});
  window.ui = ui;
  setTimeout(() => {{ {preauth} }}, 500);

  // populate project dropdown
  const ddP = document.getElementById('dd-project');
  NAV.forEach(proj => {{
    const o = document.createElement('option');
    o.value = proj.id; o.textContent = proj.name;
    ddP.appendChild(o);
  }});

  // auto-select first project if only one
  if (NAV.length === 1) {{
    ddP.value = NAV[0].id;
    onProjectChange();
  }}
}};

// ── Cascading dropdown logic ────────────────────────────────────────────────
function onProjectChange() {{
  const pid  = document.getElementById('dd-project').value;
  const ddD  = document.getElementById('dd-document');
  const ddV  = document.getElementById('dd-version');
  ddD.innerHTML = '<option value="">— select document —</option>';
  ddV.innerHTML = '<option value="">— select version —</option>';
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

  // auto-select first doc if only one
  if (proj.documents.length === 1) {{
    ddD.value = proj.documents[0].base_name;
    onDocumentChange();
  }}
}}

function onDocumentChange() {{
  const pid    = document.getElementById('dd-project').value;
  const base   = document.getElementById('dd-document').value;
  const ddV    = document.getElementById('dd-version');
  ddV.innerHTML = '<option value="">— select version —</option>';
  ddV.disabled = true;
  document.getElementById('load-btn').disabled = true;
  selectedUrl = null;
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

  // auto-select latest version
  const latest = doc.versions[doc.versions.length - 1];
  if (latest) {{
    ddV.value   = latest.url;
    selectedUrl = latest.url;
    document.getElementById('load-btn').disabled = false;
    document.getElementById('spec-label').textContent = latest.filename;
  }}
}}

function onVersionChange() {{
  const url = document.getElementById('dd-version').value;
  selectedUrl = url || null;
  document.getElementById('load-btn').disabled = !selectedUrl;
  if (url) {{
    const ddV = document.getElementById('dd-version');
    const opt = ddV.options[ddV.selectedIndex];
    document.getElementById('spec-label').textContent = '';
  }}
}}

function loadSelectedSpec() {{
  if (!selectedUrl || !ui) return;
  const ddP = document.getElementById('dd-project');
  const ddD = document.getElementById('dd-document');
  const ddV = document.getElementById('dd-version');
  const projName = ddP.options[ddP.selectedIndex]?.textContent || '';
  const docName  = document.getElementById('dd-document').value;
  const verOpt   = ddV.options[ddV.selectedIndex];
  const label    = `${{projName}} / ${{docName}}`;

  ui.specActions.updateUrl(selectedUrl);
  ui.specActions.download(selectedUrl);
  document.getElementById('spec-label').textContent =
    verOpt ? verOpt.textContent.replace('  ★ latest','').trim() : '';
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