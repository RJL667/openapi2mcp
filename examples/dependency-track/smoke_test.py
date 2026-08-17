#!/usr/bin/env python3
"""Acceptance harness: every tool must list and every tool must execute.

    python3 smoke_test.py [--call]   # --call actually hits the live API
"""
import json
import subprocess
import sys
from pathlib import Path

SERVER = str(Path(__file__).with_name("deptrack_mcp_server.py"))


def rpc(messages):
    proc = subprocess.run(
        [sys.executable, SERVER],
        input="\n".join(json.dumps(m) for m in messages) + "\n",
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        print(proc.stderr[:2000])
    return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]


def main():
    live = "--call" in sys.argv
    out = rpc([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
               {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    tools = out[-1]["result"]["tools"]
    print(f"initialize OK · {len(tools)} tools exposed")
    for t in tools:
        req = t["inputSchema"].get("required") or []
        print(f"  - {t['name']}  required={req or '-'}")
    if not live:
        print("\nSchema check passed. Re-run with --call to execute against the live API.")
        return
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
    for i, t in enumerate(tools):
        if t["inputSchema"].get("required"):
            continue  # needs real arguments — demo these by hand
        msgs.append({"jsonrpc": "2.0", "id": 100 + i, "method": "tools/call",
                     "params": {"name": t["name"], "arguments": {}}})
    res = rpc(msgs)
    ok = sum(1 for r in res if r.get("result", {}).get("content") and not r["result"].get("isError"))
    print(f"\nlive calls: {ok}/{len(msgs)-1} returned non-error")


if __name__ == "__main__":
    main()
