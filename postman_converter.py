"""
Postman Collection v2.0 / v2.1  →  OpenAPI 3.0.3 converter.
"""
from __future__ import annotations
import re
from typing import Any


def convert(collection: dict) -> dict:
    info = collection.get("info", {})
    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title":       info.get("name", "API"),
            "description": _desc(info.get("description", "")),
            "version":     "1.0.0",
        },
        "servers":    [],
        "tags":       [],
        "paths":      {},
        "components": {"schemas": {}, "securitySchemes": {}},
    }

    items = _flatten(collection.get("item", []))
    first = next((i for i in items if i.get("request")), None)
    if first:
        u = _parse_url(first["request"].get("url", ""))
        if u.get("host"):
            spec["servers"] = [{"url": f"{u.get('protocol','https')}://{u['host']}",
                                 "description": "Default server"}]

    if collection.get("auth"):
        _apply_auth_global(collection["auth"], spec)

    # top-level folder names → tags
    tag_names = [i["name"] for i in collection.get("item", []) if i.get("item")]
    spec["tags"] = [{"name": n, "description": ""} for n in tag_names]

    _process_items(collection.get("item", []), spec, None)

    # clean empty components
    for k in list(spec["components"]):
        if not spec["components"][k]:
            del spec["components"][k]
    if not spec.get("components"):
        del spec["components"]
    for k in ["servers", "tags"]:
        if not spec.get(k):
            del spec[k]

    return spec


# ── Recursive item processor ──────────────────────────────────────────────────

def _flatten(items, folder=""):
    out = []
    for i in items:
        if i.get("item"):
            out += _flatten(i["item"], (folder + "/" if folder else "") + i["name"])
        else:
            out.append({**i, "_folder": folder})
    return out


def _process_items(items, spec, tag):
    for item in items:
        if item.get("item"):
            _process_items(item["item"], spec, item["name"])
        elif item.get("request"):
            _process_request(item, spec, tag)


def _process_request(item, spec, tag):
    req    = item["request"]
    method = req.get("method", "GET").lower()
    parsed = _parse_url(req.get("url", ""))
    path   = parsed.get("path", "/")

    # normalise path params
    path = re.sub(r":([a-zA-Z_]\w*)", r"{\1}", path)
    path = re.sub(r"\{\{([^}]+)\}\}", r"{\1}", path)
    if not path.startswith("/"):
        path = "/" + path

    spec["paths"].setdefault(path, {})

    op: dict[str, Any] = {
        "summary":     item.get("name", ""),
        "description": _desc(req.get("description", "")),
        "operationId": _op_id(method, path),
        "tags":        [tag] if tag else [],
        "parameters":  [],
        "responses":   {"200": {"description": "Successful response"}},
    }

    # path params
    for m in re.finditer(r"\{([^}]+)\}", path):
        op["parameters"].append({"name": m.group(1), "in": "path",
                                  "required": True, "schema": {"type": "string"}})
    # query params
    for q in parsed.get("query", []):
        if not q.get("key"):
            continue
        op["parameters"].append({
            "name": q["key"], "in": "query", "required": False,
            "description": q.get("description", ""),
            "schema": {"type": "string", "example": q.get("value") or None},
        })
    # headers (skip Content-Type / Authorization)
    for h in req.get("header", []):
        if not h.get("key") or h["key"] in ("Content-Type", "Authorization"):
            continue
        op["parameters"].append({
            "name": h["key"], "in": "header", "required": False,
            "description": h.get("description", ""),
            "schema": {"type": "string", "example": h.get("value") or None},
        })

    if req.get("auth"):
        _apply_auth_op(req["auth"], op, spec)

    if req.get("body") and method in ("post", "put", "patch"):
        body = _parse_body(req["body"])
        if body:
            op["requestBody"] = body

    # responses from saved examples
    if item.get("response"):
        ex = item["response"][0]
        status = str(ex.get("code", 200))
        resp: dict[str, Any] = {"description": ex.get("name") or ex.get("status", "Response")}
        if ex.get("body"):
            try:
                import json
                resp["content"] = {"application/json": {"example": json.loads(ex["body"])}}
            except Exception:
                pass
        op["responses"][status] = resp
        if status != "200":
            op["responses"].pop("200", None)

    # cleanup
    for k in ["parameters", "description", "tags"]:
        if not op.get(k):
            op.pop(k, None)

    spec["paths"][path][method] = op


# ── URL parsing ───────────────────────────────────────────────────────────────

def _parse_url(raw) -> dict:
    if not raw:
        return {"path": "/"}
    if isinstance(raw, dict):
        parts = raw.get("path") or []
        path  = "/" + "/".join(parts if isinstance(parts, list) else [parts])
        host  = raw.get("host", [])
        host  = ".".join(host) if isinstance(host, list) else str(host)
        query = [{"key": q.get("key"), "value": q.get("value"),
                  "description": q.get("description", "")}
                 for q in raw.get("query", [])]
        return {"protocol": raw.get("protocol", "https"), "host": host,
                "path": path, "query": query}
    raw = str(raw)
    # swap {{var}} so URL parser doesn't choke
    raw_safe = re.sub(r"\{\{[^}]+\}\}", "placeholder", raw)
    if not raw_safe.startswith("http"):
        raw_safe = "https://example.com" + raw_safe
    try:
        from urllib.parse import urlparse, parse_qs, urlencode
        from urllib.parse import parse_qsl
        p = urlparse(raw_safe)
        query = [{"key": k, "value": v} for k, v in parse_qsl(p.query)]
        return {"protocol": p.scheme, "host": p.netloc, "path": p.path, "query": query}
    except Exception:
        return {"path": "/"}


# ── Body parser ───────────────────────────────────────────────────────────────

def _parse_body(body: dict):
    mode = body.get("mode", "none")
    if mode == "none":
        return None
    if mode == "raw":
        raw  = body.get("raw", "")
        lang = (body.get("options") or {}).get("raw", {}).get("language", "json")
        if lang == "json" or raw.strip().startswith(("{", "[")):
            try:
                import json
                parsed = json.loads(raw)
                return {"required": True,
                        "content": {"application/json": {
                            "schema":  _json_schema(parsed),
                            "example": parsed}}}
            except Exception:
                return {"required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}}}
        if lang == "xml":
            return {"required": True, "content": {"application/xml": {"schema": {"type": "string"}}}}
        return {"required": True, "content": {"text/plain": {"schema": {"type": "string", "example": raw}}}}

    if mode in ("formdata", "urlencoded"):
        fields = body.get(mode, [])
        props  = {f["key"]: {"type": "string", "description": f.get("description", "")}
                  for f in fields if f.get("key")}
        ct = "application/x-www-form-urlencoded" if mode == "urlencoded" else "multipart/form-data"
        return {"required": True, "content": {ct: {"schema": {"type": "object", "properties": props}}}}

    if mode == "file":
        return {"required": True,
                "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}
    return None


def _json_schema(val, depth=0) -> dict:
    if depth > 4:
        return {"type": "object"}
    if val is None:
        return {"type": "string", "nullable": True}
    if isinstance(val, list):
        return {"type": "array",
                "items": _json_schema(val[0], depth + 1) if val else {"type": "object"}}
    if isinstance(val, dict):
        return {"type": "object",
                "properties": {k: _json_schema(v, depth + 1) for k, v in val.items()}}
    if isinstance(val, bool):
        return {"type": "boolean", "example": val}
    if isinstance(val, int):
        return {"type": "integer", "example": val}
    if isinstance(val, float):
        return {"type": "number", "example": val}
    return {"type": "string", "example": val}


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _apply_auth_global(auth: dict, spec: dict):
    _register_scheme(auth, spec)


def _apply_auth_op(auth: dict, op: dict, spec: dict):
    t = auth.get("type", "")
    _register_scheme(auth, spec)
    mapping = {"bearer": "BearerAuth", "jwt": "BearerAuth", "basic": "BasicAuth",
               "apikey": "ApiKeyAuth", "oauth2": "OAuth2"}
    key = mapping.get(t)
    if key:
        op["security"] = [{key: []}]
    elif t == "noauth":
        op["security"] = []


def _register_scheme(auth: dict, spec: dict):
    t = auth.get("type", "")
    ss = spec.setdefault("components", {}).setdefault("securitySchemes", {})
    if t in ("bearer", "jwt"):
        ss["BearerAuth"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    elif t == "basic":
        ss["BasicAuth"] = {"type": "http", "scheme": "basic"}
    elif t == "apikey":
        params = {i["key"]: i["value"] for i in auth.get("apikey", []) if "key" in i}
        ss["ApiKeyAuth"] = {"type": "apiKey", "in": params.get("in", "header"),
                            "name": params.get("key", "X-API-Key")}
    elif t == "oauth2":
        ss["OAuth2"] = {"type": "oauth2",
                        "flows": {"authorizationCode": {
                            "authorizationUrl": "", "tokenUrl": "", "scopes": {}}}}


# ── Misc ──────────────────────────────────────────────────────────────────────

def _desc(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        return d
    return d.get("content", "") if isinstance(d, dict) else ""


def _op_id(method: str, path: str) -> str:
    parts = [p.replace("{", "").replace("}", "By").rstrip("By")
             for p in path.split("/") if p]
    return method + "".join(p.capitalize() for p in parts)
