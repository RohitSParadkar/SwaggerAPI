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

    # build swagger url list for each spec across all allowed projects
    base  = str(request.base_url).rstrip("/")
    urls  = []
    for proj in projects:
        for spec in project_store.list_specs(proj["id"]):
            url = f"{base}/api/entity/projects/{proj['id']}/specs/{spec['filename']}"
            if token:
                url += f"?token={token}"
            urls.append({"url": url, "name": f"{proj['name']} / {spec['stem']}"})

    if not urls:
        return HTMLResponse(_no_specs_page(user["username"]), status_code=200)

    return HTMLResponse(_swagger_page(urls, user, token))


def _swagger_page(urls, user, token):
    preauth = f"ui.preauthorizeApiKey && ui.preauthorizeApiKey('BearerAuth', '{token}');" if token else ""
    role_badge_colors = {"admin": "#6366f1", "entity": "#0ea5e9"}
    badge_color = role_badge_colors.get(user.get("role", "entity"), "#64748b")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>API Hub — {user['username']}</title>
  <link rel="stylesheet" href="{CDN}/swagger-ui.css"/>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
    #topbar{{background:#0f172a;color:#fff;padding:12px 24px;display:flex;align-items:center;gap:12px;justify-content:space-between}}
    #topbar h1{{font-size:15px;font-weight:600;letter-spacing:-.2px}}
    #topbar .sub{{font-size:11px;opacity:.55;margin-top:2px}}
    .badge{{font-size:11px;padding:3px 10px;border-radius:20px;font-weight:500;background:{badge_color};color:#fff}}
    .logout-btn{{background:rgba(255,255,255,.1);border:none;color:#fff;font-size:12px;padding:5px 12px;border-radius:6px;cursor:pointer}}
    .logout-btn:hover{{background:rgba(255,255,255,.2)}}
    .swagger-ui .topbar{{display:none!important}}
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
<div id="swagger-ui"></div>
<script src="{CDN}/swagger-ui-bundle.js"></script>
<script src="{CDN}/swagger-ui-standalone-preset.js"></script>
<script>
window.onload=function(){{
  const ui=SwaggerUIBundle({{
    urls:{json.dumps(urls)},
    "urls.primaryName":"{urls[0]['name'] if urls else ''}",
    dom_id:"#swagger-ui",
    deepLinking:true,
    presets:[SwaggerUIBundle.presets.apis,SwaggerUIStandalonePreset],
    layout:"StandaloneLayout",
    persistAuthorization:true,
    displayRequestDuration:true,
    filter:true,
    tryItOutEnabled:true,
  }});
  window.ui=ui;
  setTimeout(()=>{{ {preauth} }},500);
}};
</script>
</body></html>"""


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
