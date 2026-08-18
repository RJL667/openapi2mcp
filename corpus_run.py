#!/usr/bin/env python3
"""corpus_run.py — survey the generator against the PUBLIC API UNIVERSE.

Why this exists
---------------
`bench.py` is the RATCHET: 7 hand-picked specs, frozen, scored identically every
run. It answers "did I break anything?". It cannot answer "what fraction of real
third-party APIs does this digest unattended?" — and that second number is the
only defensible evidence behind OFFERS.md Offer 1's remaining edge ("specs that
don't digest"). It is also an INBOUND asset: a published coverage number and a
failure taxonomy need no attribution decision to be useful.

apis.guru indexes ~2,500 real provider/API pairs. This walks a seeded random
sample of them end to end: fetch -> generate -> schema smoke.

DELIVERED = fetch AND generate AND smoke AND tools >= 1.

No `--call`: these are third-party APIs and there are no credentials, so this is
SCHEMA-LEVEL ONLY. Any report written from it must say so — a green number that
never exercised live transport is the exact failure BASELINE.md exists to record.

P11: the pool is asserted non-empty before anything runs. A previous version read
`block["apis"]` (the key is `versions`), sampled zero specs, and exited 0 with a
summary computed over nothing.

Usage:
    python3 corpus_run.py [--n 250] [--seed 17] [--jobs 8]

Writes:
    /work/money/bench/corpus_<date>.jsonl   one record per spec attempted
    /work/money/bench/corpus_<date>.json    the summary
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN = HERE / "openapi2mcp.py"
BENCH_DIR = Path("/work/money/bench")
LIST_URL = "https://api.apis.guru/v2/list.json"

MAX_BYTES = 25 * 1024 * 1024
GEN_TIMEOUT = 150
SMOKE_TIMEOUT = 90
MAX_TOOLS = 8
UA = {"User-Agent": "openapi2mcp-corpus-survey"}


def fetch(url: str, cap: int = MAX_BYTES, tries: int = 1) -> bytes:
    """Fetch with optional bounded retry.

    P13: the index fetch is a SINGLE POINT OF FAILURE for the whole survey — a
    transient `[Errno -3] Temporary failure in name resolution` killed two runs
    at step one, before a single spec was attempted. Per-spec fetches keep
    tries=1 on purpose: a dead spec URL is a RESULT (fail_class=fetch_error),
    not an outage, and retrying 250 of them would inflate the run and hide the
    failure taxonomy the survey exists to produce.
    """
    last: Exception | None = None
    for attempt in range(tries):
        try:
            # P14: percent-encode before the request. 8 of the 3,992 index URLs
            # contain a raw space; urllib refuses them outright (InvalidURL),
            # which the survey would otherwise score as fetch_error — a defect
            # of the harness recorded as a property of the spec.
            parts = urllib.parse.urlsplit(url)
            safe = urllib.parse.urlunsplit((
                parts.scheme, parts.netloc,
                urllib.parse.quote(parts.path, safe="/%:@&=+$,~()!*'"),
                urllib.parse.quote(parts.query, safe="/%?:@&=+$,~()!*'"),
                parts.fragment,
            ))
            req = urllib.request.Request(safe, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read(cap + 1)
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt + 1 < tries:
                time.sleep(min(30, 5 * (attempt + 1)))
    raise last  # type: ignore[misc]


def run(cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def spec_version(raw: bytes) -> tuple[str, int]:
    """(version string, path count) without a full parse where possible."""
    txt = raw[:400_000].decode("utf-8", "replace")
    m = re.search(r'["\']?(?:openapi|swagger)["\']?\s*[:=]\s*["\']([0-9.]+)', txt)
    ver = m.group(1) if m else ""
    return ver, txt.count('"/') + txt.count("\n  /")


def run_one(entry: dict) -> dict:
    rec = {"name": entry["name"], "url": entry["url"], "spec_version": "",
           "bytes": 0, "paths": 0, "tools": 0, "generated": False,
           "smoke": False, "base": "", "base_class": "",
           "fail_class": "", "error": "", "seconds": 0.0}
    t0 = time.time()

    try:
        raw = fetch(entry["url"])
    except urllib.error.HTTPError as e:
        rec.update(fail_class="fetch_http_%s" % e.code, error=str(e)[:160],
                   seconds=round(time.time() - t0, 1))
        return rec
    except Exception as e:  # noqa: BLE001 — a failed fetch is a scored outcome
        rec.update(fail_class="fetch_error", error=f"{type(e).__name__}: {e}"[:160],
                   seconds=round(time.time() - t0, 1))
        return rec

    rec["bytes"] = len(raw)
    if len(raw) > MAX_BYTES:
        rec.update(fail_class="too_large", error=f">{MAX_BYTES} bytes",
                   seconds=round(time.time() - t0, 1))
        return rec

    rec["spec_version"], rec["paths"] = spec_version(raw)

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "build"
        # Pass the URL, not a downloaded copy: an external $ref resolves relative
        # to the document it came from (BASELINE.md, deptrack).
        code, log = run([sys.executable, str(GEN), "--spec", entry["url"],
                         "--name", "svy", "--out", str(out),
                         "--max-tools", str(MAX_TOOLS)], GEN_TIMEOUT)
        if code != 0:
            low = log.lower()
            # P24: the generator's no-tool message was REWRITTEN to name its own
            # cause, and this classifier still matched the OLD wording. Seed 31
            # filed `azure.com:visualstudio-Projects` (4 operations, all
            # `deprecated: true`) as `generate_error` — a class that reads as an
            # unexplained defect — when it is the tool behaving exactly as
            # designed. Match the STABLE prefix the generator always emits
            # (`no tools generated:`) and sub-classify on the cause it states.
            if "timeout" in low:
                cls = "generate_timeout"
            elif "pyyaml" in low:
                cls = "yaml_unavailable"
            elif ("no tools generated" in low or "no operations" in low
                  or "0 tools" in low):
                # Order matters: the FILTERED message names a deprecated count
                # inside itself ("… (M filtered out, K deprecated)"), so a
                # `deprecated` test placed first swallows it. Most specific
                # phrase first, always.
                if "none matched --include" in low:
                    cls = "no_operations_filtered"
                elif "webhook" in low:
                    cls = "no_operations_webhook_contract"
                elif "deprecated" in low:
                    cls = "no_operations_all_deprecated"
                else:
                    cls = "no_operations"
            elif "yaml" in low or "json" in low and "decode" in low:
                cls = "parse_error"
            else:
                cls = "generate_error"
            rec.update(fail_class=cls, error=log.strip()[-200:],
                       seconds=round(time.time() - t0, 1))
            return rec

        rec["generated"] = True
        m = re.search(r"generated\s+(\d+)\s+tools", log)
        rec["tools"] = int(m.group(1)) if m else 0

        if rec["tools"] < 1:
            rec.update(fail_class="no_operations", error=log.strip()[-160:],
                       seconds=round(time.time() - t0, 1))
            return rec

        # P20/P23: RECORD THE GENERATED BASE URL. A schema-green server pointed
        # at http://localhost:8000 passes every check below — `initialize`,
        # `tools/list`, per-tool schema — and then fails on the client's first
        # live call. That is exactly what happened to Swagger 2.0 specs
        # (`schemes`+`host`+`basePath`, no `servers`) before P20: 201 of the 493
        # "delivered" specs in the published runs carried a placeholder base and
        # the survey could not see it, because it never looked. `delivered` is
        # therefore reported WITH a base tally, not on its own.
        server_py = out / "svy_mcp_server.py"
        if server_py.exists():
            mb = re.search(r'^BASE = os\.environ\.get\([^,]+,\s*"([^"]*)"',
                           server_py.read_text(encoding="utf-8", errors="replace"),
                           re.M)
            rec["base"] = mb.group(1) if mb else ""
        if not rec["base"]:
            rec["base_class"] = "unreadable"
        elif rec["base"].startswith("http://localhost:8000"):
            rec["base_class"] = "placeholder"
        elif "{" in rec["base"]:
            rec["base_class"] = "templated"
        else:
            rec["base_class"] = "usable"

        smoke = out / "smoke_test.py"
        if not smoke.exists():
            rec.update(fail_class="no_smoke_emitted",
                       seconds=round(time.time() - t0, 1))
            return rec

        code, log = run([sys.executable, str(smoke)], SMOKE_TIMEOUT)
        if code != 0:
            rec.update(fail_class="smoke_fail", error=log.strip()[-200:],
                       seconds=round(time.time() - t0, 1))
            return rec

    rec["smoke"] = True
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=250)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()

    # P13: DNS in this workspace is intermittent — two runs died outright on
    # `Temporary failure in name resolution` while fetching the index, before a
    # single spec was attempted. Retry with backoff, and keep the index on disk
    # so a later run needs no DNS for step one. An unreachable index with no
    # cached copy still exits non-zero rather than surveying nothing (P11).
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    cache = BENCH_DIR / "apis_guru_index.json"
    listing = None
    for attempt in range(1, 6):
        try:
            print(f"fetching apis.guru index (attempt {attempt}/5) …", flush=True)
            raw = fetch(LIST_URL)
            listing = json.loads(raw.decode())
            cache.write_bytes(raw)
            break
        except Exception as e:  # noqa: BLE001 — any transport failure is retryable
            print(f"  index fetch failed: {type(e).__name__}: {e}", flush=True)
            time.sleep(5 * attempt)
    if listing is None:
        if cache.exists():
            age_h = (time.time() - cache.stat().st_mtime) / 3600
            print(f"index unreachable — using cached copy {cache} "
                  f"({age_h:.1f}h old). The sample is drawn from a STALE index; "
                  f"any report must say so.", flush=True)
            listing = json.loads(cache.read_text())
        else:
            sys.exit("apis.guru index unreachable and no cached copy exists — "
                     "refusing to report a survey over nothing (P11).")

    # apis.guru keys the version map as "versions"; accept "apis" too so a schema
    # rename degrades to a smaller pool rather than a silent zero (P11).
    pool: list[dict] = []
    for provider, block in listing.items():
        vers = block.get("versions") or block.get("apis") or {}
        if not vers:
            continue
        pref = block.get("preferred")
        ver = pref if pref in vers else sorted(vers)[-1]
        entry = vers.get(ver) or {}
        url = entry.get("swaggerUrl") or entry.get("swaggerYamlUrl")
        if url:
            pool.append({"name": f"{provider}:{ver}", "url": url})

    if not pool:
        sys.exit("corpus pool is EMPTY — the directory schema changed. "
                 "Refusing to report a survey over zero specs (P11).")

    rng = random.Random(args.seed)
    rng.shuffle(pool)
    sample = pool[: args.n]
    print(f"corpus: {len(pool)} provider/API pairs · sampling {len(sample)} "
          f"(seed={args.seed}, jobs={args.jobs})", flush=True)

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    # P12: the output path is unique per (date, seed, n) AND opened with "x".
    # Two concurrent runs previously wrote the same file: the records interleaved,
    # NUL bytes appeared mid-line, and the surviving summary described a sample
    # the record file could not substantiate. An exclusive create makes the second
    # run die instead of silently corrupting the first one's evidence.
    run_id = f"{stamp}_s{args.seed}n{args.n}"
    jsonl = BENCH_DIR / f"corpus_{run_id}.jsonl"
    summary_path = BENCH_DIR / f"corpus_{run_id}.json"
    try:
        fh = jsonl.open("x", encoding="utf-8")
    except FileExistsError:
        sys.exit(f"{jsonl} already exists — another run of this exact "
                 f"(date, seed, n) is in flight or has completed. Refusing to "
                 f"share an output path (P12). Move it aside or change --seed.")
    recs: list[dict] = []
    done = 0
    with fh, ThreadPoolExecutor(args.jobs) as ex:
        futs = {ex.submit(run_one, e): e for e in sample}
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception as e:  # noqa: BLE001
                # Must carry EVERY field the summary reads, or one harness
                # error takes the whole run down at reporting time (P11).
                r = dict(name=futs[f]["name"], url=futs[f]["url"],
                         spec_version="", bytes=0, paths=0, tools=0,
                         generated=False, smoke=False, base="", base_class="",
                         fail_class="harness_error", error=str(e)[:160],
                         seconds=0.0)
            recs.append(r)
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            done += 1
            if done % 25 == 0:
                ok = sum(1 for x in recs if x["smoke"])
                print(f"  {done}/{len(sample)} · smoke-pass {ok} ({ok/done:.0%})",
                      flush=True)

    def rate(sub: list[dict]) -> str:
        if not sub:
            return "n/a"
        ok = sum(1 for x in sub if x["smoke"])
        return f"{ok}/{len(sub)} = {ok/len(sub):.0%}"

    v3 = [r for r in recs if str(r["spec_version"]).startswith("3")]
    v2 = [r for r in recs if str(r["spec_version"]).startswith("2")]
    classes: dict[str, int] = {}
    for r in recs:
        if not r["smoke"]:
            k = r["fail_class"] or "unknown"
            classes[k] = classes.get(k, 0) + 1

    bases: dict[str, int] = {}
    for r in recs:
        if r["smoke"]:
            bases[r["base_class"] or "unknown"] = bases.get(r["base_class"] or "unknown", 0) + 1
    usable = bases.get("usable", 0)

    summary = {
        "date": stamp, "seed": args.seed, "n": len(recs),
        "corpus_size": len(pool),
        "smoke_pass": sum(1 for r in recs if r["smoke"]),
        "generated": sum(1 for r in recs if r["generated"]),
        "openapi3": {"n": len(v3), "pass": sum(1 for r in v3 if r["smoke"])},
        "swagger2": {"n": len(v2), "pass": sum(1 for r in v2 if r["smoke"])},
        "fail_classes": dict(sorted(classes.items(), key=lambda kv: -kv[1])),
        # P20: delivered AND pointed at a real host. The gap between these two
        # numbers is the part of "coverage" a client would have found at their
        # first live call, not at generation. One key, one derivation — an
        # earlier draft carried two names for the same tally (P19).
        "base_classes": dict(sorted(bases.items(), key=lambda kv: -kv[1])),
        "delivered_with_usable_base": usable,
        "median_seconds": (sorted(r["seconds"] for r in recs)[len(recs)//2]
                           if recs else 0),
        "note": "schema-level only — no credentials, so no --call. Coverage here "
                "means the tool surface is well-formed, not that live requests "
                "succeed.",
    }
    summary_path.write_text(json.dumps(summary, indent=1))

    print("\n=== CORPUS SURVEY", stamp, "===")
    print("sampled        :", len(recs), "of", len(pool))
    print("smoke-pass ALL :", rate(recs))
    print("smoke-pass 3.x :", rate(v3), f"(n={len(v3)})")
    print("smoke-pass 2.0 :", rate(v2), f"(n={len(v2)})")
    print("\nfailure classes (biggest first):")
    for k, v in summary["fail_classes"].items():
        print(f"  {v:>4}  {k}")
    print("\nbase URL of DELIVERED servers (P20 — schema-green is not callable):")
    for k, v in summary["base_classes"].items():
        print(f"  {v:>4}  {k}")
    dl = summary["smoke_pass"]
    if dl:
        print(f"  delivered with a usable base: {usable}/{dl} = {usable/dl:.0%}")
    print("\nworst 3.x failures, one example each:")
    seen: set[str] = set()
    for r in v3:
        if not r["smoke"] and r["fail_class"] not in seen:
            seen.add(r["fail_class"])
            print(f"  [{r['fail_class']}] {r['name']}\n      {r['error'][:150]}")
    print(f"\nwrote {jsonl} and {summary_path}")


if __name__ == "__main__":
    main()
