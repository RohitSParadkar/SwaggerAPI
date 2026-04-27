"""
API Hub v2 — Centralized Swagger UI
Start:
    pip install -r requirements.txt
    cp .env.example .env
    uvicorn main:app --reload --port 8000
"""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

import user_store
from config import PORT
from routers.auth_router    import router as auth_router
from routers.admin_router   import router as admin_router
from routers.entity_router  import router as entity_router
from routers.swagger_router import router as swagger_router

app = FastAPI(
    title="API Hub",
    description="Centralized API documentation with project-based access control.",
    version="2.0.0",
    docs_url="/api-docs",
    redoc_url="/api-redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Order matters — admin router must come before entity router so
# /api/admin/* routes are matched first.
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(entity_router)
app.include_router(swagger_router)

TEMPLATES = Path(__file__).parent / "templates"


# ── Static HTML pages ─────────────────────────────────────────────────────────

@app.get("/login.html", response_class=HTMLResponse, include_in_schema=False)
def login_page():
    return (TEMPLATES / "login.html").read_text(encoding="utf-8")

@app.get("/admin.html", response_class=HTMLResponse, include_in_schema=False)
def admin_page():
    return (TEMPLATES / "admin.html").read_text(encoding="utf-8")

@app.get("/entity.html", response_class=HTMLResponse, include_in_schema=False)
def entity_page():
    return (TEMPLATES / "entity.html").read_text(encoding="utf-8")

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/login.html")


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    user_store.bootstrap_admin()
    print(f"\n{'='*55}")
    print(f"  API Hub v2 started on http://localhost:{PORT}")
    print(f"  Login page : http://localhost:{PORT}/login.html")
    print(f"  Admin panel: http://localhost:{PORT}/admin.html")
    print(f"  Swagger UI : http://localhost:{PORT}/docs")
    print(f"  REST API   : http://localhost:{PORT}/api-docs")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)