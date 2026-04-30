"""
Postman Collection v2.0 / v2.1  →  OpenAPI 3.0.3 converter.

Behaviour matches the kevinswiber/postman2openapi reference tool:
  - operationId  = camelCase of item name  (e.g. "Push API" → "pushApi")
  - description  = item name  (same as summary)
  - x-api-key / auth headers stay as header *parameters*,
    unless the collection has a top-level `auth` block → securitySchemes
  - requestBody  uses `examples` (plural, named) not `example` (singular)
  - response schemas are deeply inferred from saved example bodies
  - response `headers` block carries all non-Content-Type response headers
  - servers[] is derived from the first request's host
  - {{variable}} hosts produce a literal server URL (e.g. "{{base_url}}v1")
  - No $ref hoisting — all schemas inline, exactly like the reference tool
  - openapi: 3.0.3
"""
from __future__ import annotations

import json
import re
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def convert(collection: dict) -> dict:
    info = collection.get("info", {})

    # Build variable lookup map from collection-level variables
    # e.g. {"base_url": "https://middleware.finbox.example.com"}
    col_vars: dict[str, str] = {
        v["key"]: v.get("value", "")
        for v in collection.get("variable", [])
        if v.get("key")
    }

    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title":   info.get("name", "API"),
            "version": "1.0.0",
            "contact": {},
        },
        "servers": [],
        "paths":   {},
        "tags":    [],
    }

    # Collection-level auth → securitySchemes (only if present)
    global_auth = collection.get("auth")
    if global_auth:
        scheme_key = _register_scheme(global_auth, spec)
        if scheme_key:
            spec["security"] = [{scheme_key: []}]

    # Infer server from first request, resolving {{variables}} from collection
    leaves = _flatten(collection.get("item", []))
    first  = next((i for i in leaves if i.get("request")), None)
    if first:
        server = _server_url(first["request"].get("url", ""), col_vars)
        if server:
            spec["servers"] = [{"url": server}]

    # Tags from top-level folder names
    for item in collection.get("item", []):
        if item.get("item"):
            spec["tags"].append({"name": item["name"]})

    # Process operations
    _op_id_seen: set[str] = set()
    _process_items(collection.get("item", []), spec, None, _op_id_seen)

    # Clean empty top-level keys
    if not spec["servers"]:
        del spec["servers"]
    if not spec["tags"]:
        del spec["tags"]
    if not spec.get("components", {}).get("securitySchemes"):
        spec.pop("components", None)

    return spec


# ─────────────────────────────────────────────────────────────────────────────
# Item traversal
# ─────────────────────────────────────────────────────────────────────────────

def _flatten(items: list, folder: str = "") -> list:
    out = []
    for i in items:
        if i.get("item"):
            child = f"{folder}/{i['name']}" if folder else i["name"]
            out += _flatten(i["item"], child)
        else:
            out.append(i)
    return out


def _process_items(items: list, spec: dict, tag: str | None,
                   seen: set) -> None:
    for item in items:
        if item.get("item"):
            _process_items(item["item"], spec, item["name"], seen)
        elif item.get("request"):
            _process_request(item, spec, tag, seen)


# ─────────────────────────────────────────────────────────────────────────────
# Request → path / operation
# ─────────────────────────────────────────────────────────────────────────────

def _process_request(item: dict, spec: dict, tag: str | None,
                     seen: set) -> None:
    req    = item["request"]
    method = req.get("method", "GET").lower()
    name   = item.get("name", "")

    parsed = _parse_url(req.get("url", ""))
    path   = parsed.get("path", "/")

    # Normalise path params  :id → {id}  and  {{var}} → {var}
    path = re.sub(r":([a-zA-Z_]\w*)", r"{\1}", path)
    path = re.sub(r"\{\{([^}]+)\}\}", r"{\1}", path)
    if not path.startswith("/"):
        path = "/" + path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    spec["paths"].setdefault(path, {})

    # ── If this path+method already exists, MERGE examples only ──────────────
    # This handles collections where multiple named items share the same
    # endpoint (e.g. "Trigger - Bureau", "Trigger - AA", ... all → POST /v1/trigger).
    # We add the new item's request body example and description to the existing
    # operation so Swagger UI shows all examples in its dropdown.
    if method in spec["paths"][path]:
        _merge_into_existing(spec["paths"][path][method], item, req, name)
        return

    # ── New operation ─────────────────────────────────────────────────────────
    op_id = _to_camel(name)
    if op_id in seen:
        i = 2
        while f"{op_id}{i}" in seen:
            i += 1
        op_id = f"{op_id}{i}"
    seen.add(op_id)

    op: dict[str, Any] = {
        "summary":     name,
        "description": _item_description(item, req),
        "operationId": op_id,
    }
    if tag:
        op["tags"] = [tag]

    # ── Parameters (path + query + non-CT headers) ────────────────────────────
    params: list[dict] = []

    url_raw = req.get("url", {})
    url_vars: dict[str, str] = {}
    if isinstance(url_raw, dict):
        for v in url_raw.get("variable", []):
            if v.get("key"):
                url_vars[v["key"]] = v.get("description", "")

    for m in re.finditer(r"\{([^}]+)\}", path):
        pname = m.group(1)
        params.append({
            "name":   pname,
            "in":     "path",
            "schema": {"type": "string", "example": url_vars.get(pname, "")},
        })

    for q in parsed.get("query", []):
        if not q.get("key") or q.get("disabled"):
            continue
        p: dict[str, Any] = {
            "name":   q["key"],
            "in":     "query",
            "schema": {"type": "string"},
        }
        if q.get("value"):
            p["schema"]["example"] = q["value"]
        params.append(p)

    for h in req.get("header", []):
        if not h.get("key") or h.get("disabled"):
            continue
        if h["key"].lower() == "content-type":
            continue
        p = {
            "name":   h["key"],
            "in":     "header",
            "schema": {"type": "string"},
        }
        if h.get("value"):
            p["schema"]["example"] = h["value"]
        params.append(p)

    if params:
        op["parameters"] = params

    # ── Request body ──────────────────────────────────────────────────────────
    responses_list = item.get("response", [])
    first_orig_body = None
    if responses_list:
        orig_req = responses_list[-1].get("originalRequest", {})
        first_orig_body = orig_req.get("body")
    body_obj = first_orig_body or req.get("body")
    if body_obj and method in ("post", "put", "patch", "delete"):
        ct = _content_type(req)
        rb = _build_request_body(body_obj, ct, name)
        if rb:
            op["requestBody"] = rb

    # ── Responses from saved examples ─────────────────────────────────────────
    responses: dict[str, Any] = {}
    for ex in item.get("response", []):
        code        = str(ex.get("code", 200))
        ex_name     = ex.get("name", f"Response {code}")
        raw_body    = ex.get("body", "")
        ex_headers  = ex.get("header", [])

        resp: dict[str, Any] = {"description": ex_name}

        SKIP_RESP_H = {"content-type"}
        resp_headers: dict[str, Any] = {}
        for rh in ex_headers:
            rk = rh.get("key", "")
            if not rk or rk.lower() in SKIP_RESP_H:
                continue
            resp_headers[rk] = {"schema": {"type": "string", "example": rh.get("value", "")}}
        if resp_headers:
            resp["headers"] = resp_headers

        if raw_body:
            resp_ct = next(
                (rh["value"].split(";")[0].strip()
                 for rh in ex_headers
                 if rh.get("key", "").lower() == "content-type"),
                "application/json",
            )
            try:
                parsed_body = json.loads(raw_body)
                schema = _json_schema(parsed_body)
                resp["content"] = {
                    resp_ct: {
                        "schema":   schema,
                        "examples": {ex_name: {"value": parsed_body}},
                    }
                }
            except Exception:
                resp["content"] = {
                    resp_ct: {
                        "schema":   {"type": "string"},
                        "examples": {ex_name: {"value": raw_body}},
                    }
                }

        if code in responses:
            existing = responses[code]
            for ct_key, ct_val in resp.get("content", {}).items():
                existing.setdefault("content", {}).setdefault(
                    ct_key, {"schema": ct_val["schema"], "examples": {}})
                existing["content"][ct_key]["examples"].update(
                    ct_val.get("examples", {}))
        else:
            responses[code] = resp

    if not responses:
        responses["200"] = {"description": ""}
    op["responses"] = responses

    spec["paths"][path][method] = op


def _merge_into_existing(op: dict, item: dict, req: dict, name: str) -> None:
    """
    Called when path+method already has an operation.
    Merges this item's request body example and saved response examples
    into the existing operation so Swagger UI shows all in its dropdowns.

    Also appends this item's description to the operation description
    (separated by a blank line) so no documentation is lost.
    """
    method = req.get("method", "GET").lower()

    # ── Append description ────────────────────────────────────────────────────
    item_desc = _item_description(item, req)
    if item_desc and item_desc not in (op.get("description") or ""):
        existing_desc = op.get("description") or ""
        sep = "\n\n" if existing_desc else ""
        op["description"] = existing_desc + sep + f"**{name}:** " + item_desc

    # ── Merge request body example ────────────────────────────────────────────
    responses_list = item.get("response", [])
    first_orig_body = None
    if responses_list:
        orig_req = responses_list[-1].get("originalRequest", {})
        first_orig_body = orig_req.get("body")
    body_obj = first_orig_body or req.get("body")

    if body_obj and method in ("post", "put", "patch", "delete"):
        ct = _content_type(req)
        rb = _build_request_body(body_obj, ct, name)
        if rb:
            for ct_key, ct_val in rb.get("content", {}).items():
                new_examples = ct_val.get("examples", {})
                if not new_examples:
                    continue
                # Ensure requestBody and content keys exist
                op.setdefault("requestBody", {"content": {}})
                op["requestBody"].setdefault("content", {})
                op["requestBody"]["content"].setdefault(
                    ct_key,
                    {"schema": ct_val.get("schema", {"type": "object"}), "examples": {}},
                )
                # If the existing content has a singular "example", promote it
                existing_media = op["requestBody"]["content"][ct_key]
                if "example" in existing_media and "examples" not in existing_media:
                    first_name = op.get("summary", "Example")
                    existing_media["examples"] = {first_name: {"value": existing_media.pop("example")}}
                existing_media.setdefault("examples", {})
                existing_media["examples"].update(new_examples)

    # ── Merge saved response examples ─────────────────────────────────────────
    for ex in item.get("response", []):
        code    = str(ex.get("code", 200))
        ex_name = ex.get("name", f"Response {code}")
        raw_body = ex.get("body", "")
        ex_headers = ex.get("header", [])

        if not raw_body:
            continue

        resp_ct = next(
            (rh["value"].split(";")[0].strip()
             for rh in ex_headers
             if rh.get("key", "").lower() == "content-type"),
            "application/json",
        )
        try:
            parsed_body = json.loads(raw_body)
            example_val = parsed_body
        except Exception:
            example_val = raw_body

        op.setdefault("responses", {}).setdefault(
            code, {"description": ex_name, "content": {}})
        op["responses"][code].setdefault("content", {})
        op["responses"][code]["content"].setdefault(
            resp_ct,
            {"schema": {"type": "object"}, "examples": {}},
        )
        op["responses"][code]["content"][resp_ct].setdefault("examples", {})
        op["responses"][code]["content"][resp_ct]["examples"][ex_name] = {
            "value": example_val
        }


def _item_description(item: dict, req: dict) -> str:
    """Return the best available description for an item."""
    d = item.get("request", {}).get("description") or item.get("description") or ""
    if isinstance(d, dict):
        d = d.get("content") or d.get("description") or ""
    return str(d).strip()


def _content_type(req: dict) -> str:
    return next(
        (h["value"].split(";")[0].strip()
         for h in req.get("header", [])
         if h.get("key", "").lower() == "content-type"),
        "application/json",
    )




# ─────────────────────────────────────────────────────────────────────────────
# Request body builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_request_body(body: dict, ct: str, op_name: str) -> dict | None:
    mode = body.get("mode", "none")
    if mode == "none":
        return None

    if mode == "raw":
        raw  = body.get("raw", "").strip()
        lang = (body.get("options") or {}).get("raw", {}).get("language", "")

        if ct == "application/json" or lang == "json" or (not lang and raw.startswith(("{", "["))):
            try:
                parsed = json.loads(raw)
                return {
                    "content": {
                        "application/json": {
                            "schema":   _json_schema(parsed),
                            "examples": {op_name: {"value": parsed}},
                        }
                    }
                }
            except Exception:
                return {
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"}
                        }
                    }
                }
        if lang == "xml" or "xml" in ct:
            return {"content": {ct or "application/xml": {"schema": {"type": "string"}}}}

        return {"content": {ct or "text/plain": {"schema": {"type": "string", "example": raw}}}}

    if mode == "graphql":
        gql = body.get("graphql", {})
        return {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "query":     {"type": "string", "example": gql.get("query", "")},
                            "variables": {"type": "object"},
                        },
                    }
                }
            }
        }

    if mode in ("formdata", "urlencoded"):
        fields = body.get(mode, [])
        props: dict[str, Any] = {}
        for f in fields:
            if not f.get("key") or f.get("disabled"):
                continue
            fs: dict[str, Any] = {"type": "string"}
            if f.get("type") == "file":
                fs["format"] = "binary"
            if f.get("value"):
                fs["example"] = f["value"]
            props[f["key"]] = fs
        ct_out = ("application/x-www-form-urlencoded"
                  if mode == "urlencoded" else "multipart/form-data")
        return {"content": {ct_out: {"schema": {"type": "object", "properties": props}}}}

    if mode == "file":
        return {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}

    return None


# ─────────────────────────────────────────────────────────────────────────────
# JSON value → OpenAPI 3.0.3 schema  (matches reference tool behaviour)
# ─────────────────────────────────────────────────────────────────────────────

def _json_schema(val: Any, depth: int = 0) -> dict:
    """
    Recursively infer a schema from a sample JSON value.
    Matches postman2openapi reference output:
    - numbers always get type "number" (not integer)
    - booleans get type "boolean"
    - arrays carry items schema AND an example on the array itself
    - objects carry properties (recursive) but no required[]
    """
    if depth > 8:
        return {}

    if val is None:
        return {"type": "string", "example": None}

    if isinstance(val, bool):
        return {"type": "boolean", "example": val}

    if isinstance(val, (int, float)):
        return {"type": "number", "example": val}

    if isinstance(val, str):
        return {"type": "string", "example": val}

    if isinstance(val, list):
        schema: dict[str, Any] = {"type": "array"}
        if val:
            schema["items"] = _json_schema(val[0], depth + 1)
            schema["example"] = val
        else:
            schema["items"] = {}
        return schema

    if isinstance(val, dict):
        props: dict[str, Any] = {}
        for k, v in val.items():
            props[k] = _json_schema(v, depth + 1)
        return {"type": "object", "properties": props}

    return {}


# ─────────────────────────────────────────────────────────────────────────────
# URL parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_url(raw) -> dict:
    if not raw:
        return {"path": "/"}

    if isinstance(raw, dict):
        parts = raw.get("path") or []
        path  = "/" + "/".join(
            str(p) for p in (parts if isinstance(parts, list) else [parts]) if p
        )
        query = [
            {"key": q.get("key"), "value": q.get("value", ""),
             "disabled": q.get("disabled", False)}
            for q in raw.get("query", [])
        ]
        return {"path": path, "query": query}

    raw = str(raw)
    # Strip protocol + host from string URL
    safe = re.sub(r"\{\{[^}]+\}\}", "VARIABLE", raw)
    if not safe.startswith("http"):
        safe = "https://x.com/" + safe.lstrip("/")
    try:
        from urllib.parse import urlparse, parse_qsl
        p = urlparse(safe)
        query = [{"key": k, "value": v, "disabled": False}
                 for k, v in parse_qsl(p.query)]
        return {"path": p.path or "/", "query": query}
    except Exception:
        return {"path": "/"}


def _server_url(raw, col_vars: dict[str, str] | None = None) -> str:
    """
    Extract server base URL from a Postman URL object.

    If the host contains {{variable}} references, they are resolved using
    col_vars (the collection-level variable block) so Swagger UI shows the
    real URL instead of an unresolvable template placeholder.

    e.g.  host=["{{base_url}}"], col_vars={"base_url": "https://api.example.com"}
          → "https://api.example.com"
    """
    col_vars = col_vars or {}

    def _resolve(s: str) -> str:
        """Replace {{key}} with its value from col_vars."""
        return re.sub(
            r"\{\{([^}]+)\}\}",
            lambda m: col_vars.get(m.group(1), m.group(0)),
            s,
        )

    if not raw:
        return ""

    if isinstance(raw, dict):
        raw_str = raw.get("raw", "")

        # First try: resolve the raw URL string and extract the base
        if raw_str:
            resolved = _resolve(raw_str)
            # Strip path after the host to get just the base URL
            m = re.match(r"(https?://[^/?#]+)", resolved)
            if m:
                return m.group(1)

        # Fallback: build from host + protocol parts
        host  = raw.get("host", [])
        proto = raw.get("protocol", "https")
        host_str = ".".join(str(h) for h in host) if isinstance(host, list) else str(host)
        host_str = _resolve(host_str)
        # If it already contains a full URL (e.g. resolved to https://...), extract host
        m = re.match(r"https?://([^/?#]+)", host_str)
        if m:
            proto_in = "https" if host_str.startswith("https") else "http"
            return f"{proto_in}://{m.group(1)}"
        if host_str and "{{" not in host_str:
            return f"{proto}://{host_str}"
        # Unresolved variable — return as-is so Swagger UI still has something
        if host_str:
            return host_str
        return ""

    raw = str(raw)
    resolved = _resolve(raw)
    m = re.match(r"(https?://[^/?#]+)", resolved)
    return m.group(1) if m else ""


# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers (only used when collection has top-level auth block)
# ─────────────────────────────────────────────────────────────────────────────

def _register_scheme(auth: dict, spec: dict) -> str | None:
    t  = (auth.get("type") or "").lower()
    ss = spec.setdefault("components", {}).setdefault("securitySchemes", {})

    if t in ("bearer", "jwt"):
        ss["BearerAuth"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        return "BearerAuth"

    if t == "basic":
        ss["BasicAuth"] = {"type": "http", "scheme": "basic"}
        return "BasicAuth"

    if t == "apikey":
        params   = {i["key"]: i.get("value", "") for i in auth.get("apikey", []) if "key" in i}
        key_name = params.get("key", "X-API-Key")
        key_in   = params.get("in", "header")
        ss["ApiKeyAuth"] = {"type": "apiKey", "in": key_in, "name": key_name}
        return "ApiKeyAuth"

    if t == "oauth2":
        flows_raw  = auth.get("oauth2", [])
        token_url  = next((i.get("value", "") for i in flows_raw if i.get("key") == "accessTokenUrl"), "")
        auth_url   = next((i.get("value", "") for i in flows_raw if i.get("key") == "authUrl"), "")
        scopes_raw = next((i.get("value", []) for i in flows_raw if i.get("key") == "scope"), [])
        scopes     = ({s: "" for s in scopes_raw.split()} if isinstance(scopes_raw, str)
                      else {s: "" for s in scopes_raw if isinstance(s, str)})
        ss["OAuth2"] = {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": auth_url,
                    "tokenUrl":         token_url,
                    "scopes":           scopes,
                }
            },
        }
        return "OAuth2"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_camel(name: str) -> str:
    """
    Convert item name to camelCase operationId.
    "Push API" → "pushApi",  "Fetch Decision API" → "fetchDecisionApi"
    Matches reference tool exactly.
    """
    words = re.split(r"[\s\-_/]+", name.strip())
    if not words:
        return "operation"
    result = words[0].lower()
    for w in words[1:]:
        if w:
            result += w[0].upper() + w[1:].lower() if len(w) > 1 else w.upper()
    # Strip non-alphanumeric
    result = re.sub(r"[^a-zA-Z0-9]", "", result)
    return result or "operation"