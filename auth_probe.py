#!/usr/bin/env python3
"""P25 acceptance: does the generated server put the credential WHERE THE SPEC SAYS?

This is a LIVE TRANSPORT test, not a schema check. A local echo server records
the exact request line and headers each generated MCP server sends, so the
assertion is on the bytes that leave the process — the same class of evidence
P20/P23 exist to demand (a schema-green server proved nothing about the base
URL; a schema-green server proves nothing about auth either).

Run: python3 auth_probe.py     # exit 0 = every case placed the credential right
"""
import base64
import http.server
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import urllib.parse

GEN = str(pathlib.Path(__file__).with_name("openapi2mcp.py"))
seen: list = []


class Echo(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        seen.append({
            "path": parts.path,
            "query": dict(urllib.parse.parse_qsl(parts.query)),
            "headers": {k: v for k, v in self.headers.items()},
        })
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def oas3(scheme):
    return {"openapi": "3.0.0", "info": {"title": "probe", "version": "1"},
            "components": {"securitySchemes": {"s": scheme}},
            "security": [{"s": []}],
            "paths": {"/ping": {"get": {"operationId": "ping",
                                        "responses": {"200": {"description": "ok"}}}}}}


def sw2(scheme):
    return {"swagger": "2.0", "info": {"title": "probe", "version": "1"},
            "host": "example.com", "basePath": "/v1", "schemes": ["https"],
            "securityDefinitions": {"s": scheme}, "security": [{"s": []}],
            "paths": {"/ping": {"get": {"operationId": "ping",
                                        "responses": {"200": {"description": "ok"}}}}}}


CASES = [
    # (label, spec, token value, expected headers subset, expected query subset,
    #  headers that must NOT be present)
    ("http bearer (3.x)", oas3({"type": "http", "scheme": "bearer"}), "tok123",
     {"Authorization": "Bearer tok123"}, {}, []),
    ("apiKey header, no prefix (3.x)", oas3({"type": "apiKey", "in": "header", "name": "X-Api-Key"}),
     "tok123", {"X-Api-Key": "tok123"}, {}, ["Authorization"]),
    ("apiKey header named Authorization, no prefix",
     oas3({"type": "apiKey", "in": "header", "name": "Authorization"}), "tok123",
     {"Authorization": "tok123"}, {}, []),
    ("apiKey QUERY param (3.x)", oas3({"type": "apiKey", "in": "query", "name": "api_key"}),
     "tok123", {}, {"api_key": "tok123"}, ["Authorization"]),
    ("HTTP basic (swagger 2.0)", sw2({"type": "basic"}), "user:pass",
     {"Authorization": "Basic " + base64.b64encode(b"user:pass").decode()}, {}, []),
    ("oauth2 (2.0 implicit)", sw2({"type": "oauth2", "flow": "implicit",
                                   "authorizationUrl": "https://example.com/a", "scopes": {}}),
     "tok123", {"Authorization": "Bearer tok123"}, {}, []),
]


def rpc(server_py, env, msgs):
    proc = subprocess.run([sys.executable, str(server_py)],
                          input="\n".join(json.dumps(m) for m in msgs) + "\n",
                          capture_output=True, text=True, timeout=90, env=env)
    if proc.returncode != 0:
        print("   server stderr:", proc.stderr.strip()[:400])
    return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]


def main() -> int:
    import os
    srv = http.server.HTTPServer(("127.0.0.1", 0), Echo)
    base = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="authprobe_"))
    failures = 0
    print(f"echo server on {base}\n")
    print(f"{'case':<44} {'verdict':<6} what actually went over the wire")
    print("-" * 118)

    for i, (label, spec, token, want_h, want_q, forbid_h) in enumerate(CASES):
        specf = tmp / f"spec{i}.json"
        specf.write_text(json.dumps(spec), encoding="utf-8")
        out = tmp / f"build{i}"
        gen = subprocess.run([sys.executable, GEN, "--spec", str(specf), "--name", "probe",
                              "--out", str(out), "--base-url", base, "--max-tools", "4"],
                             capture_output=True, text=True, timeout=120)
        if gen.returncode != 0:
            print(f"{label:<44} FAIL   generate: {(gen.stdout + gen.stderr).strip()[-70:]}")
            failures += 1
            continue

        env = dict(os.environ, PROBE_TOKEN=token)
        server_py = out / "probe_mcp_server.py"
        listed = rpc(server_py, env, [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                                      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        tools = (listed[-1].get("result") or {}).get("tools") if listed else None
        if not tools:
            print(f"{label:<44} FAIL   server did not list tools")
            failures += 1
            continue
        before = len(seen)
        rpc(server_py, env, [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                             {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": tools[0]["name"], "arguments": {}}}])
        if len(seen) == before:
            print(f"{label:<44} FAIL   no request reached the echo server")
            failures += 1
            continue
        got = seen[-1]
        problems = []
        for k, v in want_h.items():
            actual = got["headers"].get(k)
            if actual != v:
                problems.append(f"header {k}={actual!r} want {v!r}")
        for k, v in want_q.items():
            if got["query"].get(k) != v:
                problems.append(f"query {k}={got['query'].get(k)!r} want {v!r}")
        for k in forbid_h:
            if k in got["headers"]:
                problems.append(f"header {k} should be absent, got {got['headers'][k]!r}")
        creds = {k: v for k, v in got["headers"].items()
                 if k.lower() in ("authorization", "cookie") or "key" in k.lower()
                 or "token" in k.lower() or "subscription" in k.lower()}
        wire = f"{got['path']}{'?' + urllib.parse.urlencode(got['query']) if got['query'] else ''} {creds}"
        if problems:
            failures += 1
            print(f"{label:<44} FAIL   {wire}")
            for p in problems:
                print(f"{'':<52}{p}")
        else:
            print(f"{label:<44} PASS   {wire}")

    print("-" * 118)
    print(f"{len(CASES) - failures}/{len(CASES)} cases placed the credential exactly where the spec declares it")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
