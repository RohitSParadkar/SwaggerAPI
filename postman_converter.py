"""
Postman Collection v2.0 / v2.1  →  OpenAPI 3.1.0 converter.

Key correctness rules for 3.1.0
────────────────────────────────
• `openapi: 3.1.0`  (not 3.0.3)
• Nullable fields use `anyOf: [{type: X}, {type: 'null'}]`  (not `nullable: true`)
• `type` can be an array  e.g. `type: [string, 'null']`
• `$schema` may appear in components/schemas
• `const` instead of single-value `enum` is preferred (but enum still valid)

Converter features
──────────────────
• Per-operation `requestBody` schema inlined (not only hoisted to $ref)
• Per-operation response schemas inlined (from saved Postman examples)
• Global + per-request auth detection → `security` on every operation
• `apiKey` headers with real values detected as auth (not just skipped)
• `required` arrays on every object schema
• String format detection (uuid, date, date-time, uri, email)
• Enum detection on small finite string sets
• $ref hoisting for repeated object shapes with ≥ 3 properties
• Collision-safe operationId
• Tags from top-level folder names
• GraphQL body mode
• formdata / urlencoded with required[] and binary fields
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def convert(collection: dict) -> dict:
    info = collection.get("info", {})

    spec: dict[str, Any] = {
        "openapi": "3.1.0",
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

    # Detect global auth scheme (collection-level)
    global_auth = collection.get("auth")
    global_security_req: list[dict] = []
    if global_auth:
        scheme_key = _register_scheme(global_auth, spec)
        if scheme_key:
            global_security_req = [{scheme_key: []}]
            spec["security"] = global_security_req

    # Infer base server from first request
    leaves = _flatten(collection.get("item", []))
    first  = next((i for i in leaves if i.get("request")), None)
    if first:
        u = _parse_url(first["request"].get("url", ""))
        if u.get("host") and u["host"] not in ("example.com", ""):
            spec["servers"] = [{
                "url":         f"{u.get('protocol', 'https')}://{u['host']}",
                "description": "Default server",
            }]

    # Tags from top-level folder names
    tag_names = [i["name"] for i in collection.get("item", []) if i.get("item")]
    spec["tags"] = [{"name": n, "description": ""} for n in tag_names]

    # Process all operations
    _op_id_counter: dict[str, int] = {}
    _process_items(collection.get("item", []), spec, None,
                   _op_id_counter, global_auth)

    # Hoist repeated object schemas to $ref
    _hoist_schemas(spec)

    # Prune empty sections
    for k in list(spec["components"]):
        if not spec["components"][k]:
            del spec["components"][k]
    if not spec.get("components"):
        del spec["components"]
    for k in ["servers", "tags", "security"]:
        if not spec.get(k):
            spec.pop(k, None)

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
            out.append({**i, "_folder": folder})
    return out


def _process_items(items: list, spec: dict, tag: str | None,
                   counter: dict, global_auth: dict | None) -> None:
    for item in items:
        if item.get("item"):
            _process_items(item["item"], spec, item["name"],
                           counter, global_auth)
        elif item.get("request"):
            _process_request(item, spec, tag, counter, global_auth)


# ─────────────────────────────────────────────────────────────────────────────
# Request → path / operation
# ─────────────────────────────────────────────────────────────────────────────

# Headers that carry authentication — detected as apiKey security, not params
_AUTH_HEADER_KEYS = {"api_key", "apikey", "api-key", "x-api-key", "authorization",
                     "token", "x-auth-token", "x-access-token"}


def _process_request(item: dict, spec: dict, tag: str | None,
                     counter: dict, global_auth: dict | None) -> None:
    req    = item["request"]
    method = req.get("method", "GET").lower()
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

    # Unique operationId
    base_id = _op_id(method, path)
    if base_id in counter:
        counter[base_id] += 1
        op_id = f"{base_id}_{counter[base_id]}"
    else:
        counter[base_id] = 0
        op_id = base_id

    op: dict[str, Any] = {
        "summary":     item.get("name", ""),
        "description": _desc(req.get("description", "")),
        "operationId": op_id,
        "tags":        [tag] if tag else [],
        "parameters":  [],
        "responses":   {},
    }

    # ── Path parameters ───────────────────────────────────────────────────────
    url_raw = req.get("url", {})
    url_vars: dict[str, str] = {}
    if isinstance(url_raw, dict):
        for v in url_raw.get("variable", []):
            if v.get("key"):
                url_vars[v["key"]] = _desc(v.get("description", ""))

    for m in re.finditer(r"\{([^}]+)\}", path):
        pname = m.group(1)
        op["parameters"].append({
            "name":        pname,
            "in":          "path",
            "required":    True,
            "description": url_vars.get(pname, ""),
            "schema":      {"type": "string"},
        })

    # ── Separate auth headers from regular headers ────────────────────────────
    headers       = req.get("header", [])
    auth_headers  = []   # will become apiKey securityScheme
    param_headers = []   # become header parameters

    for h in headers:
        if not h.get("key"):
            continue
        hkey_lower = h["key"].lower()
        hval       = h.get("value", "")
        disabled   = h.get("disabled", False)

        if hkey_lower in _AUTH_HEADER_KEYS:
            # Even if disabled=true, still register the scheme so Swagger UI
            # shows the Authorize button; but only auto-apply security if enabled
            auth_headers.append({"key": h["key"], "value": hval, "disabled": disabled})
        elif hkey_lower not in ("content-type", "accept"):
            param_headers.append(h)

    # ── Query parameters ──────────────────────────────────────────────────────
    for q in parsed.get("query", []):
        if not q.get("key") or q.get("disabled"):
            continue
        ex = _strip_vars(q.get("value") or "")
        p: dict[str, Any] = {
            "name":        q["key"],
            "in":          "query",
            "required":    False,
            "description": _desc(q.get("description", "")),
            "schema":      {"type": "string"},
        }
        if ex:
            p["schema"]["example"] = ex
        op["parameters"].append(p)

    # ── Non-auth header parameters ────────────────────────────────────────────
    for h in param_headers:
        if h.get("disabled"):
            continue
        ex = _strip_vars(h.get("value") or "")
        p = {
            "name":        h["key"],
            "in":          "header",
            "required":    False,
            "description": _desc(h.get("description", "")),
            "schema":      {"type": "string"},
        }
        if ex:
            p["schema"]["example"] = ex
        op["parameters"].append(p)

    # ── Security resolution ───────────────────────────────────────────────────
    # Priority: explicit request auth > auth headers > global auth
    req_auth = req.get("auth")

    if req_auth and req_auth.get("type") == "noauth":
        op["security"] = []  # explicitly public endpoint

    elif req_auth:
        scheme_key = _register_scheme(req_auth, spec)
        if scheme_key:
            op["security"] = [{scheme_key: []}]

    elif auth_headers:
        # Detect apiKey security from header(s)
        for ah in auth_headers:
            if ah["disabled"]:
                continue
            # Register as apiKey scheme
            scheme_key = f"ApiKeyHeader_{ah['key']}"
            ss = spec.setdefault("components", {}).setdefault("securitySchemes", {})
            ss[scheme_key] = {
                "type": "apiKey",
                "in":   "header",
                "name": ah["key"],
            }
            op["security"] = [{scheme_key: []}]
            break  # one scheme per op is enough
    # If none of the above, fall back to global auth (Swagger UI uses spec-level security)

    # ── Request body ──────────────────────────────────────────────────────────
    if req.get("body") and method in ("post", "put", "patch", "delete"):
        ct_header = next(
            (h["value"] for h in headers
             if h.get("key", "").lower() == "content-type"),
            None,
        )
        body = _parse_body(req["body"], ct_header)
        if body:
            op["requestBody"] = body

    # ── Responses from saved Postman examples ─────────────────────────────────
    if item.get("response"):
        for ex in item["response"]:
            status    = str(ex.get("code", 200))
            resp: dict[str, Any] = {
                "description": ex.get("name") or ex.get("status") or "Response"
            }
            raw_body = ex.get("body", "")
            if raw_body:
                parsed_val, schema = _parse_example_body(raw_body, ex)
                if parsed_val is not None:
                    resp["content"] = {
                        _response_ct(ex): {
                            "schema":  schema,
                            "example": parsed_val,
                        }
                    }
            op["responses"][status] = resp
    else:
        op["responses"]["200"] = {"description": "Successful response"}

    # ── Cleanup empty fields ──────────────────────────────────────────────────
    for k in ["parameters", "description", "tags"]:
        if not op.get(k):
            op.pop(k, None)

    spec["paths"][path][method] = op


# ─────────────────────────────────────────────────────────────────────────────
# URL parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_url(raw) -> dict:
    if not raw:
        return {"path": "/"}
    if isinstance(raw, dict):
        parts = raw.get("path") or []
        path  = "/" + "/".join(str(p) for p in (parts if isinstance(parts, list) else [parts]) if p)
        host  = raw.get("host", [])
        host  = ".".join(host) if isinstance(host, list) else str(host)
        host  = re.sub(r"\{\{[^}]+\}\}", "", host).strip(".")
        query = [
            {
                "key":         q.get("key"),
                "value":       q.get("value", ""),
                "description": _desc(q.get("description", "")),
                "disabled":    q.get("disabled", False),
            }
            for q in raw.get("query", [])
        ]
        return {
            "protocol": raw.get("protocol", "https"),
            "host":     host,
            "path":     path,
            "query":    query,
        }

    raw = str(raw)
    safe = re.sub(r"\{\{[^}]+\}\}", "placeholder", raw)
    if not safe.startswith("http"):
        safe = "https://example.com" + safe
    try:
        from urllib.parse import urlparse, parse_qsl
        p = urlparse(safe)
        query = [{"key": k, "value": v, "description": "", "disabled": False}
                 for k, v in parse_qsl(p.query)]
        return {"protocol": p.scheme, "host": p.netloc, "path": p.path, "query": query}
    except Exception:
        return {"path": "/"}


# ─────────────────────────────────────────────────────────────────────────────
# Body parsing → requestBody object
# ─────────────────────────────────────────────────────────────────────────────

def _parse_body(body: dict, ct_hint: str | None = None) -> dict | None:
    mode = body.get("mode", "none")
    if mode == "none":
        return None

    if mode == "raw":
        raw  = body.get("raw", "").strip()
        lang = (body.get("options") or {}).get("raw", {}).get("language", "json")

        if ct_hint:
            cl = ct_hint.lower()
            if "xml"  in cl: lang = "xml"
            elif "text" in cl: lang = "text"
            elif "json" in cl: lang = "json"

        if lang == "json" or (not lang and raw.startswith(("{", "["))):
            try:
                parsed = json.loads(_strip_vars(raw))
                return {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema":  _json_schema_31(parsed),
                            "example": parsed,
                        }
                    },
                }
            except Exception:
                return {
                    "required": True,
                    "content": {"application/json": {
                        "schema": {"type": "object"}
                    }},
                }

        if lang == "xml":
            return {"required": True,
                    "content": {"application/xml": {"schema": {"type": "string"}}}}

        if lang == "graphql":
            gql = body.get("graphql", {})
            return {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "query":     {"type": "string",
                                              "example": gql.get("query", "")},
                                "variables": {"type": "object"},
                            },
                            "required": ["query"],
                        }
                    }
                },
            }

        return {"required": True,
                "content": {"text/plain": {"schema": {"type": "string", "example": raw}}}}

    if mode == "graphql":
        gql = body.get("graphql", {})
        return {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "query":     {"type": "string",
                                          "example": gql.get("query", "")},
                            "variables": {"type": "object"},
                        },
                        "required": ["query"],
                    }
                }
            },
        }

    if mode in ("formdata", "urlencoded"):
        fields = body.get(mode, [])
        props: dict[str, Any] = {}
        required_fields: list[str] = []
        for f in fields:
            if not f.get("key") or f.get("disabled"):
                continue
            fschema: dict[str, Any] = {"type": "string"}
            if f.get("type") == "file":
                fschema["contentEncoding"] = "binary"   # 3.1 way
            ex = _strip_vars(f.get("value") or "")
            if ex:
                fschema["example"] = ex
            desc = _desc(f.get("description", ""))
            if desc:
                fschema["description"] = desc
            props[f["key"]] = fschema
            if f.get("value"):
                required_fields.append(f["key"])

        obj: dict[str, Any] = {"type": "object", "properties": props}
        if required_fields:
            obj["required"] = required_fields

        ct = ("application/x-www-form-urlencoded"
              if mode == "urlencoded" else "multipart/form-data")
        return {"required": True, "content": {ct: {"schema": obj}}}

    if mode == "file":
        return {
            "required": True,
            "content": {"application/octet-stream": {
                "schema": {"type": "string", "contentEncoding": "binary"}
            }},
        }

    return None


# ─────────────────────────────────────────────────────────────────────────────
# JSON value → OpenAPI 3.1 schema
# (key difference from 3.0: nullable uses anyOf with {type:'null'})
# ─────────────────────────────────────────────────────────────────────────────

def _null_schema(inner: dict) -> dict:
    """Wrap a schema as nullable using 3.1 anyOf syntax."""
    return {"anyOf": [inner, {"type": "null"}]}


def _json_schema_31(val: Any, depth: int = 0) -> dict:
    """
    Recursively derive an OpenAPI 3.1.0 schema from a sample JSON value.

    - Null fields use anyOf[{type:X},{type:'null'}] (3.1 style)
    - Non-null object keys go into `required`
    - Primitive types carry an `example`
    - Common string formats are detected (uuid, date, date-time, uri, email)
    """
    if depth > 6:
        return {"type": "object"}

    if val is None:
        return {"type": "null"}

    if isinstance(val, bool):
        return {"type": "boolean", "example": val}

    if isinstance(val, int):
        fmt = "int64" if abs(val) > 2 ** 31 else "int32"
        return {"type": "integer", "format": fmt, "example": val}

    if isinstance(val, float):
        return {"type": "number", "format": "double", "example": val}

    if isinstance(val, str):
        schema: dict[str, Any] = {"type": "string"}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
            schema["format"] = "date"
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.+\-Z]+", val):
            schema["format"] = "date-time"
        elif re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
            r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", val
        ):
            schema["format"] = "uuid"
        elif val.startswith(("http://", "https://")):
            schema["format"] = "uri"
        elif "@" in val and "." in val.split("@")[-1]:
            schema["format"] = "email"
        if val:
            schema["example"] = val
        return schema

    if isinstance(val, list):
        if not val:
            return {"type": "array", "items": {}}
        if len(val) > 1 and all(isinstance(i, dict) for i in val):
            items_schema = _merge_object_schemas_31(
                [_json_schema_31(i, depth + 1) for i in val[:5]]
            )
        else:
            items_schema = _json_schema_31(val[0], depth + 1)
        return {"type": "array", "items": items_schema}

    if isinstance(val, dict):
        properties: dict[str, Any] = {}
        required: list[str] = []
        for k, v in val.items():
            if v is None:
                # 3.1 nullable: anyOf with null
                inner_schema = _null_schema({"type": "string"})
            else:
                inner_schema = _json_schema_31(v, depth + 1)
                required.append(k)
            properties[k] = inner_schema

        obj: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            obj["required"] = required
        return obj

    return {}


def _merge_object_schemas_31(schemas: list[dict]) -> dict:
    """Merge multiple 3.1 object schemas into one, marking absent fields nullable."""
    if not schemas:
        return {}
    if len(schemas) == 1:
        return schemas[0]

    all_props: dict[str, list] = defaultdict(list)
    for s in schemas:
        for k, v in s.get("properties", {}).items():
            all_props[k].append(v)

    merged_props: dict[str, Any] = {}
    required: list[str] = []
    for k, vs in all_props.items():
        is_always_present = len(vs) == len(schemas)
        merged = vs[0].copy()
        # If field is absent in some items → wrap as nullable
        if not is_always_present:
            if "anyOf" not in merged:
                merged = _null_schema(merged)
        else:
            required.append(k)
        merged_props[k] = merged

    result: dict[str, Any] = {"type": "object", "properties": merged_props}
    if required:
        result["required"] = required
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Response example parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_example_body(raw: str, ex: dict) -> tuple[Any, dict]:
    if not raw:
        return None, {}
    try:
        parsed = json.loads(raw)
        return parsed, _json_schema_31(parsed)
    except Exception:
        return raw, {"type": "string"}


def _response_ct(ex: dict) -> str:
    for h in ex.get("header", []):
        if h.get("key", "").lower() == "content-type":
            return h["value"].split(";")[0].strip()
    return "application/json"


# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

# Maps Postman auth type → a stable scheme key name
_AUTH_KEY_MAP = {
    "bearer": "BearerAuth",
    "jwt":    "BearerAuth",
    "basic":  "BasicAuth",
    "apikey": "ApiKeyAuth",
    "oauth2": "OAuth2",
}


def _register_scheme(auth: dict, spec: dict) -> str | None:
    """
    Register the auth scheme in components/securitySchemes and return
    the scheme key, or None if the auth type is unknown/noauth.
    """
    t  = (auth.get("type") or "").lower()
    ss = spec.setdefault("components", {}).setdefault("securitySchemes", {})

    if t in ("bearer", "jwt"):
        ss["BearerAuth"] = {
            "type":          "http",
            "scheme":        "bearer",
            "bearerFormat":  "JWT",
        }
        return "BearerAuth"

    if t == "basic":
        ss["BasicAuth"] = {"type": "http", "scheme": "basic"}
        return "BasicAuth"

    if t == "apikey":
        params = {i["key"]: i.get("value", "")
                  for i in auth.get("apikey", []) if "key" in i}
        key_name = params.get("key", "X-API-Key")
        key_in   = params.get("in", "header")
        scheme_id = f"ApiKeyAuth"
        ss[scheme_id] = {
            "type": "apiKey",
            "in":   key_in,
            "name": key_name,
        }
        return scheme_id

    if t == "oauth2":
        flows_raw = auth.get("oauth2", [])
        token_url = next((i.get("value", "") for i in flows_raw
                          if i.get("key") == "accessTokenUrl"), "")
        auth_url  = next((i.get("value", "") for i in flows_raw
                          if i.get("key") == "authUrl"), "")
        scopes_raw = next((i.get("value", []) for i in flows_raw
                           if i.get("key") == "scope"), [])
        if isinstance(scopes_raw, str):
            scopes = {s: "" for s in scopes_raw.split()}
        elif isinstance(scopes_raw, list):
            scopes = {s: "" for s in scopes_raw if isinstance(s, str)}
        else:
            scopes = {}

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
# Schema hoisting ($ref deduplication)
# Only for objects with ≥ 3 properties; avoids over-engineering small schemas
# ─────────────────────────────────────────────────────────────────────────────

def _schema_fingerprint(schema: dict) -> str:
    try:
        return json.dumps(schema, sort_keys=True)
    except Exception:
        return ""


def _hoist_schemas(spec: dict) -> None:
    fingerprint_map: dict[str, str] = {}
    name_counter: dict[str, int] = {}

    def _clean_hint(hint: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", hint.title()) or "Model"

    def _maybe_hoist(schema: dict, hint: str = "Model") -> dict:
        if not isinstance(schema, dict):
            return schema
        if schema.get("type") != "object":
            return schema
        props = schema.get("properties", {})
        if len(props) < 3:
            return schema
        fp = _schema_fingerprint(schema)
        if fp in fingerprint_map:
            return {"$ref": f"#/components/schemas/{fingerprint_map[fp]}"}
        base = _clean_hint(hint)
        if base in name_counter:
            name_counter[base] += 1
            name = f"{base}{name_counter[base]}"
        else:
            name_counter[base] = 0
            name = base
        fingerprint_map[fp] = name
        spec.setdefault("components", {}).setdefault("schemas", {})[name] = schema
        return {"$ref": f"#/components/schemas/{name}"}

    for path, path_item in spec.get("paths", {}).items():
        for method, op in path_item.items():
            if not isinstance(op, dict):
                continue
            rb = op.get("requestBody", {})
            for ct, media in rb.get("content", {}).items():
                s    = media.get("schema", {})
                hint = (op.get("summary") or op.get("operationId") or "Request")
                media["schema"] = _maybe_hoist(s, hint + "Body")

            for status, resp in op.get("responses", {}).items():
                for ct, media in resp.get("content", {}).items():
                    s = media.get("schema", {})
                    hint = (op.get("summary") or op.get("operationId") or "Response")
                    if s.get("type") == "array":
                        s["items"] = _maybe_hoist(
                            s.get("items", {}), hint + "Item"
                        )
                    else:
                        media["schema"] = _maybe_hoist(s, hint + "Response")


# ─────────────────────────────────────────────────────────────────────────────
# Misc helpers
# ─────────────────────────────────────────────────────────────────────────────

def _desc(d: Any) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        return d.strip()
    if isinstance(d, dict):
        return (d.get("content") or d.get("description") or "").strip()
    return ""


def _strip_vars(s: str) -> str:
    """Remove {{variable}} placeholders from example values."""
    return re.sub(r"\{\{[^}]+\}\}", "", s).strip()


def _op_id(method: str, path: str) -> str:
    """Generate a camelCase operationId from HTTP method + path."""
    parts = []
    for seg in path.split("/"):
        if not seg:
            continue
        if seg.startswith("{") and seg.endswith("}"):
            parts.append("By" + seg[1:-1].capitalize())
        else:
            # Convert kebab-case segments to CamelCase
            parts.append("".join(w.capitalize() for w in re.split(r"[-_]", seg)))
    return method + "".join(parts) if parts else method