# API Hub v2 — Centralized Swagger UI (Python / FastAPI)

A production-ready API documentation hub with:
- **Project-based organisation** — group API contracts by project
- **Role-based access** — admin vs entity users
- **Entity login** — username + password (bcrypt hashed)
- **Admin panel** — create users, projects, upload specs, assign access
- **Entity dashboard** — view assigned projects, upload new contracts
- **Postman → OpenAPI conversion** — auto-converts Postman collections on upload
- **Unified Swagger UI** — one `/docs` URL, entity sees only their assigned specs

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env — set JWT_SECRET and ADMIN credentials

# 3. Start
uvicorn main:app --reload --port 8000
```

---

## URLs

| URL | Description |
|-----|-------------|
| `http://localhost:8000/login.html` | Login page (all users) |
| `http://localhost:8000/admin.html` | Admin dashboard |
| `http://localhost:8000/entity.html` | Entity dashboard |
| `http://localhost:8000/docs` | Swagger UI (entity-scoped) |
| `http://localhost:8000/api-docs` | FastAPI interactive API docs |

---
## Workflow

### As Admin:
1. Open `/admin.html` and log in
2. **Projects** tab → Create projects (e.g. "Payments Platform", "Orders Service")
3. **Users** tab → Create entity accounts, assign projects
4. **API Contracts** tab → Upload YAML/JSON/Postman specs per project

### As Entity:
1. Open `/login.html` and log in with credentials given by admin
2. See assigned projects → click a project to view its specs
3. Upload new YAML/JSON/Postman spec files to allowed projects
4. Click **Open Swagger UI** to view all assigned API contracts in one UI

---

## File structure

```
swagger-hub-v2/
├── main.py                   # FastAPI app entry point
├── config.py                 # Env config, paths
├── user_store.py             # User CRUD + bcrypt auth
├── project_store.py          # Project + spec file management
├── postman_converter.py      # Postman → OpenAPI 3.0 converter
├── requirements.txt
├── .env.example
├── middleware/
│   └── auth.py               # JWT dependency
├── routers/
│   ├── auth_router.py        # POST /api/auth/login|logout
│   ├── admin_router.py       # /api/admin/* (admin only)
│   ├── entity_router.py      # /api/entity/* (entity + admin)
│   └── swagger_router.py     # GET /docs (unified Swagger UI)
├── templates/
│   ├── login.html            # Login page
│   ├── admin.html            # Admin dashboard
│   └── entity.html           # Entity dashboard
└── data/                     # Created automatically at runtime
    ├── users/
    │   └── users.json        # User accounts (passwords bcrypt hashed)
    └── projects/
        └── <project-id>/
            ├── meta.json
            └── specs/        # Uploaded YAML/JSON spec files
```

---

## API Reference

### Auth
```
POST /api/auth/login        { username, password }  → { access_token, user }
POST /api/auth/logout
```

### Admin
```
GET    /api/admin/users
POST   /api/admin/users                   { username, password, role, projects }
PATCH  /api/admin/users/{id}              { password?, projects? }
DELETE /api/admin/users/{id}

GET    /api/admin/projects
POST   /api/admin/projects                { name, description }
PATCH  /api/admin/projects/{id}
DELETE /api/admin/projects/{id}

POST   /api/admin/projects/{id}/specs     multipart files
DELETE /api/admin/projects/{id}/specs/{filename}

PUT    /api/admin/users/{id}/projects     { project_ids }
```

### Entity
```
GET  /api/entity/projects
GET  /api/entity/projects/{id}/specs
GET  /api/entity/projects/{id}/specs/{filename}
POST /api/entity/projects/{id}/specs      multipart files (upload)
```

---

## Security Notes

- Passwords are stored as bcrypt hashes — never plain text
- Set a strong `JWT_SECRET` (min 32 chars) in `.env`
- The `/api/admin/*` routes require `role: admin` in the JWT
- Protect the server with HTTPS in production
- The `data/` directory is in `.gitignore` — don't commit user data
