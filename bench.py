#!/usr/bin/env python3
"""bench.py — THE MEASUREMENT GATE.

A fixed set of tasks, scored the same way every time, recorded with a date.
No improvement spend is authorised until this exists and has a baseline. This
is that file.

The task set is deliberately shaped like the paid work in OFFERS.md Offer 1
("your API as an MCP server, 48h, $250"): take a real, public, third-party
OpenAPI spec — the exact input a client hands over — and produce a working MCP
server whose tools survive a schema smoke test.

Scored per spec:
  fetch      spec downloaded and parsed
  generate   server emitted without raising
  tools      number of tools produced (target = --max-tools)
  smoke      smoke_test.py exits 0 (initialize + tools/list + schema check)
  seconds    wall clock, fetch to smoke

DELIVERED = fetch AND generate AND smoke AND tools >= 1.
SCORE = delivered / total, plus median seconds. Both go in the history file;
regressions on either are a rollback trigger (see IDENTITY.md ratchet).

Usage:
  python3 bench.py                    # run the suite, append to history
  python3 bench.py --quick            # small specs only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN = HERE / "openapi2mcp.py"
ROOT = Path(os.environ.get("OPENAPI2MCP_HOME", HERE))
BENCH_DIR = ROOT / "bench"
HISTORY = BENCH_DIR / "history.jsonl"

# ---------------------------------------------------------------- the task set
# Fixed. Adding or removing a task invalidates comparison with earlier runs —
# if you must change it, bump SUITE_VERSION and start a new baseline.
# v1 (6 self-contained JSON specs) is SUPERSEDED. It could not catch a regression
# in external $ref resolution, because no task exercised it — the DependencyTrack
# job proved that gap the expensive way, by looking impossible when it wasn't.
# v2 adds YAML parsing and a genuinely multi-file spec. Scores are NOT comparable
# across suite versions: v2 starts its own baseline.
SUITE_VERSION = "v2"

TASKS = [
    # (id, spec url, include-regex, small?)
    ("petstore", "https://petstore3.swagger.io/api/v3/openapi.json", "", True),
    ("posthog", "https://app.posthog.com/api/schema/?format=json", "insight|event|query|person", False),
    ("stripe", "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json",
     "customers|charges|invoices", False),
    ("slack", "https://raw.githubusercontent.com/slackapi/slack-api-specs/master/web-api/slack_web_openapi_v2.json",
     "chat|conversations|users", False),
    ("twilio", "https://raw.githubusercontent.com/twilio/twilio-oai/main/spec/json/twilio_api_v2010.json",
     "Messages|Calls|Accounts", False),
    ("box", "https://raw.githubusercontent.com/box/box-openapi/main/openapi.json",
     "files|folders|users", False),
    # --- v2 additions: YAML, and a spec whose operations live in other files ---
    ("deptrack", "https://raw.githubusercontent.com/DependencyTrack/dependency-track/main/api/src/main/openapi/openapi.yaml",
     "project|component|vuln", False),
]

# Specs whose operations live in OTHER files (one YAML per URL, assembled at
# build time — the DependencyTrack/Spring/JAX-RS house style). These MUST be
# handed to the generator as a URL, never as a downloaded copy: an external $ref
# resolves relative to the document it came from, so a local temp file resolves
# nothing and the spec reads as having zero operations.
REMOTE_ONLY = {"deptrack"}

# Requires PyYAML from v2 onward (JSON-only suites did not).
MAX_TOOLS = 8
FETCH_TIMEOUT = 60
STEP_TIMEOUT = 300


def fetch(url: str, dest: Path) -> tuple[bool, str, int]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "openapi2mcp-bench",
                                                   "Accept": "application/json"})
        data = urllib.request.urlopen(req, timeout=FETCH_TIMEOUT).read()
        dest.write_bytes(data)
        return True, "", len(data)
    except Exception as exc:  # noqa: BLE001 — a failed fetch is a scored outcome
        return False, str(exc)[:120], 0


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=STEP_TIMEOUT)
        return p.returncode, (p.stdout + p.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)[:200]


def score_task(tid: str, url: str, include: str, workdir: Path) -> dict:
    rec: dict = {"task": tid, "fetch": False, "generate": False, "smoke": False,
                 "tools": 0, "seconds": 0.0, "bytes": 0, "error": ""}
    t0 = time.time()
    spec = workdir / f"{tid}.json"
    ok, err, nbytes = fetch(url, spec)
    rec["bytes"] = nbytes
    if not ok:
        rec["error"] = f"fetch: {err}"
        rec["seconds"] = round(time.time() - t0, 1)
        return rec
    rec["fetch"] = True

    out = workdir / f"{tid}_build"
    spec_arg = url if tid in REMOTE_ONLY else str(spec)
    cmd = [sys.executable, str(GEN), "--spec", spec_arg, "--name", tid,
           "--out", str(out), "--max-tools", str(MAX_TOOLS)]
    if include:
        cmd += ["--include", include]
    code, log = run(cmd)
    if code != 0:
        rec["error"] = f"generate: {log[-200:]}"
        rec["seconds"] = round(time.time() - t0, 1)
        return rec
    rec["generate"] = True
    # The generator's FIRST line is a header ("generated N tools -> <path>") which
    # also contains " -> ". Counting every arrow line therefore over-reported by
    # exactly 1 (run 1 recorded tools=9 against --max-tools 8). Read the header's
    # own number instead; fall back to counting the indented per-tool lines.
    m = re.search(r"generated\s+(\d+)\s+tools", log)
    if m:
        rec["tools"] = int(m.group(1))
    else:
        rec["tools"] = sum(
            1 for line in log.splitlines() if line.startswith("  ") and " -> " in line
        )

    smoke = out / "smoke_test.py"
    if not smoke.exists():
        rec["error"] = "smoke: no smoke_test.py emitted"
        rec["seconds"] = round(time.time() - t0, 1)
        return rec
    code, log = run([sys.executable, str(smoke)], cwd=out)
    rec["smoke"] = code == 0
    if code != 0:
        rec["error"] = f"smoke: {log[-200:]}"
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="openapi2mcp measurement gate")
    ap.add_argument("--quick", action="store_true", help="small specs only")
    ap.add_argument("--keep", action="store_true", help="keep build dirs")
    args = ap.parse_args()

    tasks = [t for t in TASKS if (t[3] or not args.quick)]
    workdir = Path(tempfile.mkdtemp(prefix="bench_"))
    results = []
    print(f"suite {SUITE_VERSION} · {len(tasks)} tasks · max_tools={MAX_TOOLS}")
    print("-" * 78)
    for tid, url, include, _small in tasks:
        rec = score_task(tid, url, include, workdir)
        results.append(rec)
        delivered = rec["fetch"] and rec["generate"] and rec["smoke"] and rec["tools"] >= 1
        mark = "PASS" if delivered else "FAIL"
        print(f"{mark:>4} {tid:<10} tools={rec['tools']:<2} {rec['seconds']:>6.1f}s "
              f"{rec['bytes']//1024:>6}KB {rec['error'][:70]}")

    delivered = [r for r in results
                 if r["fetch"] and r["generate"] and r["smoke"] and r["tools"] >= 1]
    score = round(len(delivered) / len(results), 3) if results else 0.0
    med = round(statistics.median([r["seconds"] for r in results]), 1) if results else 0.0

    run_rec = {
        "date": date.today().isoformat(),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "suite": SUITE_VERSION,
        "quick": args.quick,
        "n": len(results),
        "delivered": len(delivered),
        "score": score,
        "median_seconds": med,
        "results": results,
    }
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(run_rec) + "\n")
    (BENCH_DIR / "latest.json").write_text(json.dumps(run_rec, indent=2), encoding="utf-8")

    print("-" * 78)
    print(f"SCORE {score}  ({len(delivered)}/{len(results)} delivered)  median {med}s")
    print(f"appended -> {HISTORY}")

    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
