# CORPUS SURVEY — coverage over the public API universe

**Run id `2026-08-17_s17n250`** · seed 17 · n=250 drawn from **2,529** apis.guru
provider/API pairs · records: `bench/corpus_2026-08-17_s17n250.jsonl` (250 rows,
250 unique names) · summary: `corpus_2026-08-17_s17n250.json`

---

## What this measures — and what it does NOT

`bench.py` (suite v2, 7 fixed tasks) is the **ratchet**: it answers "did I break
anything?". It cannot answer "what fraction of real third-party APIs does this
digest unattended?". This survey answers that, and only that.

> **SCHEMA-LEVEL ONLY.** No credentials exist for 250 third-party APIs, so
> `smoke_test.py --call` was never run. A pass here means *the spec was fetched,
> parsed, and a well-formed MCP tool surface was generated and survived
> `initialize` + `tools/list` + a per-tool schema check*. It does **not** mean a
> live request to that API succeeds. That distinction is not pedantry: suite v1
> scored 1.00 on a generator whose every live call raised `unknown url type`
> (`BASELINE.md`), and the paid offer's acceptance test is a live demo.

---

## Headline

| Metric | Value |
|---|---|
| Delivered (fetch + generate + schema smoke) | **245 / 250 — 98.0%** |
| Median wall time per spec | **2.6s** (p90 3.4s, max 4.3s) |
| OpenAPI 3.x | 152 attempted, **151 pass** |
| Swagger 2.0 | 95 attempted, **94 pass** |
| Specs > 1 MB | 9 attempted, 8 pass (the one failure is the 26 MB cap) |
| Specs > 100 paths | 14 attempted, **14 pass** |

Corpus shape, for context on what "typical" means: median spec **50 KB / 10
paths**; p90 **525 KB / 74 paths**; max **26 MB / 311 paths**. Top providers in
the sample: azure.com 64, amazonaws.com 29, googleapis.com 27, apisetu.gov.in 17.

124 of the 245 generated servers hit the `--max-tools 8` cap; the rest are small
APIs with fewer operations than the cap (28 produced exactly one tool).

---

## Every failure, with attribution

Five rows did not deliver. Naming the cause of each matters more than the rate,
because three of the five are **not** generator defects.

| # | Spec | Class | Attribution |
|---|---|---|---|
| 1 | `microsoft.com:graph-beta` (26,214,401 B) | `too_large` | **Harness policy.** `MAX_BYTES` is 25 MB. Graph-beta is 26 MB. Not a parse failure — a deliberate cap. |
| 2 | `adyen.com:BalancePlatformReportNotification-v1` | `no_operations` | **Correct behaviour — verified.** A webhook *notification* schema: 0 paths, no operations to expose. There is nothing to generate. |
| 3 | `azure.com:containerservice-containerService:2017-07-01` | `no_operations` | **Correct behaviour — diagnosed after the run.** 3 paths, 5 operations, and Microsoft marks **every one of them `deprecated: true`**. The generator skips deprecated operations by design, so the surface is legitimately empty. |
| 4 | `azure.com:cognitiveservices-LUIS-Runtime:v2.0 preview` | `fetch_error` | **Harness defect — FIXED (P14).** The URL contains a raw space; `urllib` raised `InvalidURL` before any byte was fetched. |
| 5 | `nordigen.com:2.0 (v2)` | `fetch_error` | **Harness defect — FIXED (P14).** Same cause. |

### The two fetch errors were mine, not the specs'

8 of apis.guru's 3,992 spec URLs contain a raw space
(`…/LUIS-Runtime/v2.0 preview/swagger.json`, `…/nordigen.com/2.0 (v2)/…`).
`urllib` refuses those outright with `InvalidURL: URL can't contain control
characters` — which reads exactly like "the spec is unreachable" and would be
**wrong in front of a paying client** whose spec sits at a versioned path with a
space in it.

Both the generator (`openapi2mcp.py`) and the harness now percent-encode path and
query before the request (`%` kept safe, so an already-encoded URL is not
double-encoded). Both specs were re-run individually after the fix:

```
luis:     rc=0  POST /{appId} -> prediction_resolve      smoke rc=0
nordigen: rc=0  GET  /api/v2/institutions/ -> …          smoke rc=0
```

### Why the third `no_operations` is not a defect

The survey recorded `paths=11` for that spec, which is what made it look like a
gap. That figure comes from a cheap regex counter (`spec_version()` counts `"/`
occurrences) and it over-counts; the spec really has **3 paths / 5 operations**.
Every operation carries `deprecated: true` — it is the 2017 Azure ARM
`containerServices` API, superseded by AKS — and `extract_tools()` skips
deprecated operations deliberately. Generating an agent tool surface over an API
Microsoft has marked dead is the wrong output, so the empty result is right.

Worth stating plainly because it cuts the other way in a paid job: if a client's
spec is fully deprecated, the correct answer is "there is nothing here to
expose", not eight tools that will be withdrawn.

### What may honestly be claimed

- **The recorded run is 245/250 (98.0%).** That is the number in the summary
  file and the only one traceable to a complete record set. Quote this.
- Two of the five failures are fixed and individually verified, so a re-run at
  the same seed should score 247/250 — but that run has not happened, and an
  un-run number is not evidence (P7/P11). It is not quoted anywhere public.
- **None of the five is an unexplained generator defect.** One is a size cap
  (26 MB > the 25 MB limit), two are specs with no exposable operations by
  design (a webhook notification schema; a fully-deprecated API), and two were
  URL encoding in my own fetch path, now fixed.

---

## Method

```
python3 corpus_run.py --n 250 --seed 17 --jobs 8
```

Per spec: fetch → `openapi2mcp.py --max-tools 8` → `smoke_test.py`.
DELIVERED = fetch AND generate AND smoke AND tools ≥ 1. The spec is passed to the
generator **as a URL, never as a downloaded copy** — an external `$ref` resolves
relative to the document it came from, so a local temp file silently resolves
nothing.

### Guards this run exists because of

- **P11** — the pool is asserted non-empty before anything runs. An earlier
  version read `block["apis"]` (apis.guru keys it `versions`), sampled zero
  specs, and exited 0 with a summary computed over nothing.
- **P12** — output paths are per `(date, seed, n)` and opened with `"x"`. Two
  concurrent runs previously shared one path: the JSONL interleaved with NUL
  bytes and the surviving summary described a population its own record file
  could not substantiate. Quarantined, not deleted, in `bench/contaminated/`.
- **P13** — the index fetch retries 5× with backoff and caches to
  `bench/apis_guru_index.json`; unreachable index with no cache exits non-zero.
  Also: **always send a User-Agent** — measured the same cycle, identical request
  with UA → 200, without → **403 Forbidden**.
- **P14** — percent-encode every URL before the request (above).

---

## Standing

This is an **inbound** asset. A published coverage number and a failure taxonomy
over the public API universe are useful without any outbound pitch, and they need
no attribution decision to exist. The ratchet (`bench.py`, suite v2, 7/7, score
1.00, median 2.2s — **run 5, 2026-08-17**, the re-run that confirmed the P14
encoding fix held) is unaffected by this file and remains the rollback trigger;
this survey is a coverage measurement, not a score to optimise.
