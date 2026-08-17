#!/usr/bin/env python3
"""openapi2mcp — turn any OpenAPI 3.x spec into a working MCP stdio server.

This is the fulfilment engine behind OFFERS.md Offer 1 ("Your API, as an MCP
server, 48h, $250"). It exists so the second delivery costs a fraction of the
first: point it at a spec, pick up to N operations, and it emits a single-file
MCP server with typed input schemas, auth wiring, and a smoke test per tool.

Usage:
    python3 openapi2mcp.py --spec https://api.example.com/openapi.json \
        --name example --out ./build --max-tools 8 [--include 'pet|store']

Emits into --out:
    <name>_mcp_server.py   the MCP server (stdio, JSON-RPC 2.0, zero deps)
    smoke_test.py          calls tools/list + tools/call against the server
    README.md              client config + acceptance checklist

No third-party dependencies: stdlib only, so it runs anywhere the client can
run python3.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

JSON = dict[str, Any]

# ---------------------------------------------------------------- spec loading


def _encode_url(uri: str) -> str:
    """Percent-encode an http(s) URL's path and query.

    P14: a spec URL containing a raw space makes urllib raise
    `InvalidURL: URL can't contain control characters` BEFORE a single byte is
    fetched. That reads as "the spec is unreachable" when it means "the URL was
    never encoded". Real: 8 of apis.guru's 3,992 spec URLs carry spaces
    (`.../cognitiveservices-LUIS-Runtime/v2.0 preview/swagger.json`,
    `.../nordigen.com/2.0 (v2)/openapi.json`), and a client whose spec lives at a
    versioned path with a space would hit exactly this in a paid job.

    `%` is in every safe set so an already-encoded URL is not double-encoded.
    """
    parts = urllib.parse.urlsplit(uri)
    return urllib.parse.urlunsplit((
        parts.scheme,
        parts.netloc,
        urllib.parse.quote(parts.path, safe="/%:@&=+$,~()!*'"),
        urllib.parse.quote(parts.query, safe="/%?:@&=+$,~()!*'"),
        parts.fragment,
    ))


def _read_source(uri: str) -> str:
    if re.match(r"^https?://", uri):
        req = urllib.request.Request(_encode_url(uri),
                                     headers={"User-Agent": "openapi2mcp"})
        return urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    return Path(uri).read_text(encoding="utf-8")


def _parse(raw: str) -> JSON:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError:
            sys.exit("spec is not JSON and PyYAML is not installed (pip install pyyaml)")
        return yaml.safe_load(raw)


def load_spec(src: str) -> JSON:
    return _parse(_read_source(src))


# ------------------------------------------------------ multi-file spec support
# Large APIs are rarely one file. Dependency-Track, Box and most Spring/JAX-RS
# projects keep one YAML per URL and assemble at build time, so the entry
# document is nothing but external $refs. Without resolving those the generator
# sees zero operations and a routine job looks impossible.

_DOC_CACHE: dict[str, JSON] = {}


def _join(base: str, rel: str) -> str:
    if re.match(r"^https?://", base):
        return urllib.parse.urljoin(base, rel)
    return str((Path(base).parent / rel).resolve())


def _load_doc(uri: str) -> JSON:
    if uri not in _DOC_CACHE:
        try:
            _DOC_CACHE[uri] = _parse(_read_source(uri))
        except Exception:  # noqa: BLE001 — one missing sibling must not kill the run
            _DOC_CACHE[uri] = {}
    return _DOC_CACHE[uri]


def _fragment(doc: Any, frag: str) -> Any:
    node = doc
    for part in frag.strip("/").split("/"):
        if not part:
            continue
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return {}
        node = node[part]
    return node


def inline_external(node: Any, base: str, depth: int = 0,
                    stack: frozenset[str] = frozenset()) -> Any:
    """Inline $refs that point at OTHER FILES, relative to the file they sit in.

    Internal '#/...' pointers are left alone — deref() handles those.
    """
    if depth > 12:
        return {}
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref and not ref.startswith("#"):
            target, _, frag = ref.partition("#")
            uri = _join(base, target)
            key = f"{uri}#{frag}"
            if key in stack:
                return {}
            doc = _load_doc(uri)
            doc = inline_external(doc, uri, depth + 1, stack | {key})
            resolved = _fragment(doc, frag) if frag else doc
            if isinstance(resolved, (dict, list)) and isinstance(doc, dict):
                resolved = deref(doc, resolved)
            siblings = {k: v for k, v in node.items() if k != "$ref"}
            if isinstance(resolved, dict):
                return {**resolved, **siblings}
            return resolved
        return {k: inline_external(v, base, depth + 1, stack) for k, v in node.items()}
    if isinstance(node, list):
        return [inline_external(v, base, depth + 1, stack) for v in node]
    return node


def deref(spec: JSON, node: Any, depth: int = 0) -> Any:
    """Resolve local $ref pointers. Bounded depth: specs contain cycles."""
    if depth > 8:
        return {}
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            ref = node["$ref"]
            if not ref.startswith("#/"):
                return {}
            target: Any = spec
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or part not in target:
                    return {}
                target = target[part]
            return deref(spec, target, depth + 1)
        return {k: deref(spec, v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [deref(spec, v, depth + 1) for v in node]
    return node


# ------------------------------------------------------------ tool extraction

VERBS = ("get", "post", "put", "patch", "delete")


def tool_name(method: str, path: str, op: JSON) -> str:
    raw = op.get("operationId") or f"{method}_{path}"
    name = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").lower()
    return name[:60] or "op"


def json_schema_for(op: JSON, path_params: list[JSON]) -> tuple[JSON, list[str]]:
    props: JSON = {}
    required: list[str] = []
    for p in list(path_params) + list(op.get("parameters") or []):
        if not isinstance(p, dict) or "name" not in p:
            continue
        schema = p.get("schema") or {"type": "string"}
        entry = {"type": schema.get("type", "string")}
        if p.get("description"):
            entry["description"] = p["description"][:200]
        if schema.get("enum"):
            entry["enum"] = schema["enum"]
        props[p["name"]] = entry
        if p.get("required"):
            required.append(p["name"])
    body = (op.get("requestBody") or {}).get("content") or {}
    js = (body.get("application/json") or {}).get("schema")
    if isinstance(js, dict):
        props["body"] = {
            "type": "object",
            "description": "JSON request body",
            **({"properties": js["properties"]} if isinstance(js.get("properties"), dict) else {}),
        }
        if (op.get("requestBody") or {}).get("required"):
            required.append("body")
    return {"type": "object", "properties": props, "required": required}, required


def extract_tools(spec: JSON, include: str | None, max_tools: int,
                  include_deprecated: bool = False) -> tuple[list[JSON], JSON]:
    """Return (tools, stats).

    `stats` exists so an EMPTY result can be EXPLAINED rather than reported as
    "no operations matched". The corpus survey (bench/CORPUS.md) produced three
    distinct zero-tool causes that all printed that one sentence: a spec whose
    every operation is `deprecated` (azure containerservice 2017-07-01), a
    webhook-notification contract with no callable paths (Adyen ×2), and a
    genuine filter miss. Telling a client "your spec has 5 operations, all
    marked deprecated — re-run with --include-deprecated" is a different
    conversation from "your spec doesn't work here".
    """
    spec = deref(spec, spec)
    tools: list[JSON] = []
    stats: JSON = {"paths": 0, "operations": 0, "deprecated": 0,
                   "filtered_out": 0,
                   "webhooks": len(spec.get("webhooks") or {})}
    pat = re.compile(include, re.I) if include else None
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        stats["paths"] += 1
        shared = [p for p in (item.get("parameters") or []) if isinstance(p, dict)]
        for method in VERBS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            stats["operations"] += 1
            if op.get("deprecated"):
                stats["deprecated"] += 1
                if not include_deprecated:
                    continue
            hay = f"{path} {op.get('operationId','')} {op.get('summary','')} {' '.join(op.get('tags') or [])}"
            if pat and not pat.search(hay):
                stats["filtered_out"] += 1
                continue
            schema, _ = json_schema_for(op, shared)
            desc = (op.get("summary") or op.get("description") or f"{method.upper()} {path}").strip()
            tools.append(
                {
                    "name": tool_name(method, path, op),
                    "description": desc[:300],
                    "inputSchema": schema,
                    "_method": method.upper(),
                    "_path": path,
                }
            )
    # stable, useful ordering: reads first (safest to demo), then writes
    tools.sort(key=lambda t: (VERBS.index(t["_method"].lower()), t["_path"]))
    seen: set[str] = set()
    unique = []
    for t in tools:
        n = t["name"]
        i = 2
        while n in seen:
            n = f"{t['name']}_{i}"
            i += 1
        t["name"] = n
        seen.add(n)
        unique.append(t)
    return unique[:max_tools], stats


def explain_empty(stats: JSON, include: str | None) -> str:
    """Say WHY zero tools came out: one line, actionable, no blame-shifting."""
    if stats["operations"] == 0:
        if stats["webhooks"]:
            return (f"this spec declares {stats['webhooks']} webhook definition(s) "
                    f"and 0 callable operations — it is a notification contract, "
                    f"so there is nothing for an agent to invoke.")
        return ("this spec declares 0 operations (no `paths` entry carries a "
                "GET/POST/PUT/PATCH/DELETE) — check that this is the API spec "
                "and not an index, a schema-only document, or a webhook contract.")
    if stats["deprecated"] == stats["operations"]:
        return (f"all {stats['operations']} operations in this spec are marked "
                f"`deprecated`, and deprecated operations are skipped by default. "
                f"Re-run with --include-deprecated to expose them anyway.")
    if include and stats["filtered_out"]:
        extra = f", {stats['deprecated']} deprecated" if stats["deprecated"] else ""
        return (f"{stats['operations']} operations found, none matched "
                f"--include {include!r} ({stats['filtered_out']} filtered out"
                f"{extra}). Loosen the regex.")
    return (f"{stats['operations']} operations found, none survived filtering "
            f"({stats['deprecated']} deprecated, {stats['filtered_out']} filtered).")


def base_url(spec: JSON, override: str | None, spec_src: str = "") -> str:
    """Resolve the server base URL to something that can actually be requested.

    OpenAPI permits a RELATIVE server url ("/api/v3", "/api/v2") meaning "the
    same origin this document is served from". Emitting it verbatim produced a
    server whose every live call died with

        ValueError: unknown url type: '/api/v3/store/inventory'

    The schema smoke test passed anyway — only `smoke_test.py --call` caught it.
    That is exactly the acceptance test the fixed-price offer is sold on, so
    this is a delivery-blocking bug, not a cosmetic one.
    """
    if override:
        return override.rstrip("/")
    raw = ""
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict) and servers[0].get("url"):
        raw = str(servers[0]["url"]).strip()

    # P20: SWAGGER 2.0 HAS NO `servers`. The base is `schemes` + `host` +
    # `basePath`, three separate top-level keys. Reading only `servers` left
    # every 2.0 delivery pointing at the localhost placeholder — 40.8% of the
    # surveyed corpus — and the schema smoke test passes on all of them.
    if not raw and (spec.get("swagger") or spec.get("host") or spec.get("basePath")):
        host = str(spec.get("host") or "").strip().strip("/")
        base_path = str(spec.get("basePath") or "").strip()
        if base_path and not base_path.startswith("/"):
            base_path = "/" + base_path
        if base_path == "/":
            base_path = ""
        schemes = [s for s in (spec.get("schemes") or []) if isinstance(s, str)]
        scheme = "https" if ("https" in schemes or not schemes) else schemes[0]
        if host:
            raw = f"{scheme}://{host}{base_path}"
        elif base_path:
            # No host: same "resolve against the document's origin" case as a
            # relative OpenAPI 3 server url, handled immediately below.
            raw = base_path

    if raw and not re.match(r"^https?://", raw):
        if re.match(r"^https?://", spec_src):
            parts = urllib.parse.urlsplit(spec_src)
            raw = urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, raw if raw.startswith("/") else "/" + raw, "", "")
            )
        else:
            # No origin to resolve against (local spec file). Fall back to a
            # placeholder that is at least a valid URL, and keep the path so the
            # client only has to override the host.
            raw = "http://localhost:8000" + ("" if raw == "/" else raw)
    return (raw or "http://localhost:8000").rstrip("/")


# ------------------------------------------------------------ code generation

SERVER_TEMPLATE = '''#!/usr/bin/env python3
"""MCP stdio server for {title} — generated by openapi2mcp.

Run:  python3 {module}.py
Env:  {envvar}   bearer token / api key (optional)
      {baseenv}  override base URL (default {base})
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("{baseenv}", "{base}").rstrip("/")
TOKEN = os.environ.get("{envvar}", "")
AUTH_HEADER = os.environ.get("{envvar}_HEADER", "Authorization")
AUTH_PREFIX = os.environ.get("{envvar}_PREFIX", "Bearer ")

# P19: embedded as JSON and parsed at runtime, NOT as a Python literal. A schema
# carrying a boolean/null (enum: [false, true], default: null) dumps as `false`/
# `null`, which are not Python names — the server then dies at import with
# `NameError: name 'false' is not defined` before it can answer a single request.
TOOLS = json.loads({tools_json})

SPECS = {{t["name"]: t for t in TOOLS}}


def _call_api(spec, args):
    path = spec["_path"]
    query = {{}}
    body = args.get("body")
    for key, val in (args or {{}}).items():
        if key == "body":
            continue
        token = "{{" + key + "}}"
        if token in path:
            path = path.replace(token, urllib.parse.quote(str(val), safe=""))
        else:
            query[key] = val
    url = BASE + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    data = None
    headers = {{"Accept": "application/json", "User-Agent": "{module}/1.0"}}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if TOKEN:
        headers[AUTH_HEADER] = AUTH_PREFIX + TOKEN
    req = urllib.request.Request(url, data=data, headers=headers, method=spec["_method"])
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = resp.read().decode("utf-8", "replace")
            return f"HTTP {{resp.status}}\\n{{payload[:20000]}}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:4000]
        return f"HTTP {{exc.code}} {{exc.reason}}\\n{{detail}}"
    except Exception as exc:  # network, DNS, timeout
        return f"ERROR calling {{spec['_method']}} {{url}}: {{exc}}"


def _public(tool):
    return {{k: v for k, v in tool.items() if not k.startswith("_")}}


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {{
            "jsonrpc": "2.0",
            "id": mid,
            "result": {{
                "protocolVersion": "2024-11-05",
                "capabilities": {{"tools": {{}}}},
                "serverInfo": {{"name": "{module}", "version": "1.0.0"}},
            }},
        }}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "tools/list":
        return {{"jsonrpc": "2.0", "id": mid, "result": {{"tools": [_public(t) for t in TOOLS]}}}}
    if method == "tools/call":
        params = msg.get("params") or {{}}
        spec = SPECS.get(params.get("name"))
        if spec is None:
            return {{"jsonrpc": "2.0", "id": mid,
                    "error": {{"code": -32602, "message": f"unknown tool {{params.get('name')}}"}}}}
        text = _call_api(spec, params.get("arguments") or {{}})
        is_err = text.startswith("ERROR") or text.startswith("HTTP 4") or text.startswith("HTTP 5")
        return {{"jsonrpc": "2.0", "id": mid,
                "result": {{"content": [{{"type": "text", "text": text}}], "isError": is_err}}}}
    if mid is None:
        return None
    return {{"jsonrpc": "2.0", "id": mid, "error": {{"code": -32601, "message": f"unknown method {{method}}"}}}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
'''

SMOKE_TEMPLATE = '''#!/usr/bin/env python3
"""Acceptance harness: every tool must list and every tool must execute.

    python3 smoke_test.py [--call]   # --call actually hits the live API
"""
import json
import subprocess
import sys
from pathlib import Path

SERVER = str(Path(__file__).with_name("{module}.py"))


def rpc(messages):
    proc = subprocess.run(
        [sys.executable, SERVER],
        input="\\n".join(json.dumps(m) for m in messages) + "\\n",
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        print(proc.stderr[:2000])
    return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]


def main():
    live = "--call" in sys.argv
    out = rpc([{{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {{}}}},
               {{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}}])
    if not out or "tools" not in (out[-1].get("result") or {{}}):
        print("FAIL: the server did not answer tools/list. "
              "Its stderr is above; the acceptance test has NOT passed.")
        sys.exit(1)
    tools = out[-1]["result"]["tools"]
    print(f"initialize OK · {{len(tools)}} tools exposed")
    for t in tools:
        req = t["inputSchema"].get("required") or []
        print(f"  - {{t['name']}}  required={{req or '-'}}")
    if not live:
        print("\\nSchema check passed. Re-run with --call to execute against the live API.")
        return
    msgs = [{{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {{}}}}]
    for i, t in enumerate(tools):
        if t["inputSchema"].get("required"):
            continue  # needs real arguments — demo these by hand
        msgs.append({{"jsonrpc": "2.0", "id": 100 + i, "method": "tools/call",
                     "params": {{"name": t["name"], "arguments": {{}}}}}})
    res = rpc(msgs)
    ok = sum(1 for r in res if r.get("result", {{}}).get("content") and not r["result"].get("isError"))
    print(f"\\nlive calls: {{ok}}/{{len(msgs)-1}} returned non-error")


if __name__ == "__main__":
    main()
'''

README_TEMPLATE = """# {title} — MCP server

Generated by `openapi2mcp` from `{spec_src}`. Stdlib only, no dependencies.

## Run

```bash
export {envvar}="<your token>"        # optional
export {baseenv}="{base}"             # optional override
python3 {module}.py                   # speaks MCP over stdio
```

## Client config (Claude Desktop / Cursor / any MCP client)

```json
{{
  "mcpServers": {{
    "{name}": {{
      "command": "python3",
      "args": ["{abs_server}"],
      "env": {{ "{envvar}": "<your token>" }}
    }}
  }}
}}
```

## Tools exposed ({n})

{tool_table}

## Acceptance checklist

- [ ] `python3 smoke_test.py` — server initializes and lists all {n} tools with typed schemas
- [ ] `python3 smoke_test.py --call` — no-argument tools execute against the live API
- [ ] each argument-taking tool demonstrated live, one call each
- [ ] auth verified with a real token
- [ ] client config above pasted into the customer's own MCP client and tools appear

Delivery is not complete until every box is ticked in front of the customer.
"""


def tools_literal(tools: list[JSON]) -> str:
    """Render the tool list as a Python expression that evaluates to the JSON.

    P19: `json.dumps(...)` inlined directly into the template is NOT valid Python
    whenever a schema contains `true`, `false` or `null` — which any spec with a
    boolean enum or a null default produces. Emit the JSON as a raw triple-quoted
    string parsed by `json.loads` at import; fall back to `repr` in the (only
    theoretical) case that the document itself contains a triple quote.
    """
    doc = json.dumps(tools, indent=4)
    if '"""' in doc:
        return repr(doc)
    return 'r"""\n' + doc + '\n"""'


def generate(spec: JSON, name: str, out: Path, tools: list[JSON], base: str, spec_src: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    module = f"{re.sub(r'[^a-z0-9_]+', '_', name.lower())}_mcp_server"
    envvar = f"{re.sub(r'[^A-Z0-9]+', '_', name.upper())}_TOKEN"
    baseenv = f"{re.sub(r'[^A-Z0-9]+', '_', name.upper())}_BASE_URL"
    title = (spec.get("info") or {}).get("title") or name

    server_path = out / f"{module}.py"
    server_path.write_text(
        SERVER_TEMPLATE.format(
            title=title, module=module, envvar=envvar, baseenv=baseenv, base=base,
            tools_json=tools_literal(tools),
        ),
        encoding="utf-8",
    )
    (out / "smoke_test.py").write_text(SMOKE_TEMPLATE.format(module=module), encoding="utf-8")

    rows = "\n".join(
        f"| `{t['name']}` | {t['_method']} `{t['_path']}` | {t['description'][:80]} |" for t in tools
    )
    table = "| tool | endpoint | what it does |\n|---|---|---|\n" + rows
    (out / "README.md").write_text(
        README_TEMPLATE.format(
            title=title, spec_src=spec_src, envvar=envvar, baseenv=baseenv, base=base,
            module=module, name=name, abs_server=str(server_path.resolve()),
            n=len(tools), tool_table=table,
        ),
        encoding="utf-8",
    )
    return server_path


def main() -> None:
    ap = argparse.ArgumentParser(description="OpenAPI 3.x -> MCP stdio server")
    ap.add_argument("--spec", required=True, help="URL or path to openapi.json/yaml")
    ap.add_argument("--name", required=True, help="short slug, e.g. 'stripe'")
    ap.add_argument("--out", default="./build")
    ap.add_argument("--max-tools", type=int, default=8)
    ap.add_argument("--include", default=None, help="regex filter on path/tag/operationId")
    ap.add_argument("--include-deprecated", action="store_true",
                    help="expose operations marked deprecated (skipped by default)")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--ref-base", default=None,
                    help="resolve external $refs against this URL/path (default: --spec)")
    args = ap.parse_args()

    spec = inline_external(load_spec(args.spec), args.ref_base or args.spec)
    tools, stats = extract_tools(spec, args.include, args.max_tools,
                                 args.include_deprecated)
    if not tools:
        sys.exit("no tools generated: " + explain_empty(stats, args.include))
    base = base_url(spec, args.base_url, args.spec)
    server = generate(spec, args.name, Path(args.out), tools, base, args.spec)

    print(f"generated {len(tools)} tools -> {server}")
    for t in tools:
        print(f"  {t['_method']:6} {t['_path']:40} -> {t['name']}")
    print(f"\nnext: cd {args.out} && python3 smoke_test.py")


if __name__ == "__main__":
    main()
