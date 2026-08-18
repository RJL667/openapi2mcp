# CORPUS SURVEY — coverage over the public API universe

**Three independent samples**, each n=250 drawn from the same **2,529** apis.guru
provider/API pairs:

| Run id | Seed | Delivered | Median wall |
|---|---|---|---|
| `2026-08-17_s17n250` | 17 | 245 / 250 — 98.0% | 2.6s |
| `2026-08-17_s23n250` | 23 | 248 / 250 — 99.2% | 3.0s |
| `2026-08-18_s31n250` | 31 | **249 / 250 — 99.6%** | 2.3s |
| **combined (rows)** | — | **742 / 750 — 98.9%** | — |
| **combined (distinct APIs)** | — | **674 / 682 — 98.8%** | — |

Records: `bench/corpus_<run id>.jsonl` (250 rows each, 250 unique names each) ·
summaries: `bench/corpus_<run id>.json`. The three samples overlap on 65 specs
total (17∩23 = 23, 17∩31 = 26, 23∩31 = 22) and **agree on every overlapping
spec — zero disagreements**, which is why the distinct-API figure is quoted
alongside the row figure rather than instead of it (P19).

Sections below that name only seed 17 or only seeds 17+23 are kept as written:
they record what was quotable when that was all the evidence there was.

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

**Third proof of that caveat, found 2026-08-17 (PLAYBOOK P20).** Every Swagger
2.0 spec in this survey — **201 of the 493 delivered, 40.8%** — generated a server
whose base URL was the `http://localhost:8000` placeholder, because `base_url()`
read only OpenAPI 3's `servers` block and 2.0 puts the base in `schemes` + `host`
+ `basePath`. All 201 passed here, and every one of them would have failed at the
first live call with `Name or service not known`. Fixed in the generator; the
delivered counts below are unchanged and still correct **as schema-level
measurements**, which is exactly the point of the caveat: a green row in this
table has never proved that a request leaves the machine.

**Now measured rather than assumed (P23).** `corpus_run.py` records the resolved
base URL of every delivered server and classifies it `usable` / `placeholder` /
`templated` / `unreadable`. Seed 31 is the first run carrying it:

| | seed 31 |
|---|---|
| Delivered | 249 / 250 |
| **Delivered with a usable base** | **242 / 249 — 97.2%** |
| `placeholder` (`http://localhost:8000`) | 7 |
| `templated` / `unreadable` | 0 |

The 7 placeholders are specs that genuinely carry no resolvable host —
`influxdata.com:2.0.0`, `parliament.uk:erskine-may:v1`,
`parliament.uk:lordsvotes:v1`, `gsmtasks.com:2.4.13`,
`presalytics.io:story:0.3.1`, `wheretocredit.com:1.0`, `thetvdb.com:3.0.0` —
six OpenAPI 3.x, one Swagger 2.0. That is the residue after the P20 fix, and it
is the correct output for those documents: the client overrides one env var.
Before the fix the same tally would have read **201 placeholders**, all of them
Swagger 2.0, and no survey number would have shown it.

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

**Superseded by the seed-23 replication below — this section is kept because it
records what was quotable when only one sample existed.** The quotable figure is
now the two-seed combined one, and it is the figure in `OFFERS.md` and the public
README:

- **Quote 742/750 — 98.9% across three seeds**, naming all three run ids, or
  **674/682 = 98.8% per distinct API**. Earlier claims (493/500 two-seed, 245/250
  seed 17 alone) remain true and citable; they are simply the weaker versions.
- **Quote the base tally next to it.** Seed 31: **242 of 249 delivered servers
  point at a real host (97.2%)**. "Delivered" on its own overstates what a buyer
  gets — P23 exists because 201 of the original 493 pointed at localhost and no
  published number said so.
- **Zero disagreements on the 65 overlapping specs**, so the combined figure is
  not inflated by double-counting a spec that passes twice.
- **No projected number is quoted anywhere.** The seed-17 fetch_error fixes were
  never claimed as an improved seed-17 score (an un-run number is not evidence,
  P7/P11); the fix is evidenced by seed 23 recording **zero** of that class in a
  real run.
- **None of the seven combined non-deliveries is an unexplained generator
  defect.** One size cap (26 MB > the 25 MB limit), four specs with no exposable
  operations by design (three 0-path documents, one fully-deprecated API), and
  two URL-encoding faults in my own fetch path, since fixed.

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
no attribution decision to exist. The ratchet (`bench.py`, suite **v3**, 8/8,
score 1.00, median 0.8s — **run 15**, the latest of fifteen dated runs, fourteen
at 1.00 and one deliberate red row at 0.875 where the harness, not the subject,
was wrong) is unaffected by this file and
remains the rollback trigger; this survey is a coverage measurement, not a score
to optimise. Read the current ratchet figure from `bench/latest.json`, never
from this paragraph.

---

## Replication — independent sample, seed 23

A single seeded sample measures the sample as much as the generator. Re-run with
a different seed over the same 2,529-API pool; **23 specs overlap**, so the two
samples are all but independent.

| | seed 17 | **seed 23** |
|---|---|---|
| Delivered | 245 / 250 — 98.0% | **248 / 250 — 99.2%** |
| OpenAPI 3.x | 151 / 152 | 141 / 143 |
| Swagger 2.0 | 94 / 95 | **107 / 107** |
| Median wall | 2.6s | 3.0s (p90 4.0s, max 5.5s) |
| Servers at the 8-tool cap | 124 | 130 |
| `fetch_error` | 2 (harness, since fixed) | **0** |

**Combined: 493 / 500 — 98.6%.**

Two things this establishes that one run could not:

1. **The P14 encoding fix held.** Seed 17 recorded two `fetch_error`s that were
   this harness's own unencoded-URL defect. Seed 23 records **zero** — the class
   is gone, not merely absent from a lucky sample.
2. **Both seed-23 failures are the benign class**, verified by reading each spec:
   - `adyen.com:BalancePlatformTransferNotification-v3` — OpenAPI **3.1**,
     `paths: 0`, `webhooks: 2`. It is a webhook *contract*: Adyen calls **you**.
     There is no operation an agent can invoke, so emitting no tools is correct.
     (3.1's `webhooks` block is deliberately not turned into tools — an inbound
     callback is not a callable surface.)
   - `googleapis.com:youtubeAnalytics:v1` — 3,958 bytes, `paths: 0`. A stub
     document carrying `info`/`servers`/`tags` and no operations at all.

No new failure mode appeared in a second 250-spec sample.

Swagger 2.0 at 107/107 on the larger 2.0 share is worth keeping in view: a third
to two-fifths of the live universe is still Swagger 2.0, and it is not a reason
to requote a job.

Records: `bench/corpus_2026-08-17_s23n250.jsonl` / `.json` (run id
`2026-08-17_s23n250`).

---

## Third sample — seed 31, and the first run that measured the base URL

Run id `2026-08-18_s31n250` · 250 rows · **249 delivered (99.6%)** · median 2.3s
(p90 3.2s) · OpenAPI 3.x **159/159** · Swagger 2.0 **90/91** · specs >1 MB 9/9 ·
specs >100 paths 5/5 · median spec 47 KB. Top providers: azure.com 69,
amazonaws.com 27, apisetu.gov.in 23, googleapis.com 22.

This run exists to answer one question the first two could not: **after the P20
fix, do the generated servers point at the customer's API?** They do, for
242 of 249 (97.2%) — including **89 of the 90 delivered Swagger 2.0 specs**, the
population that was 0% usable before the fix. Swagger 2.0 at 90/91 delivered and
89/90 usable is the direct replication of P20 on an unseen sample.

**The single non-delivery is correct behaviour, and it exposed a classifier bug
(P24).** `azure.com:visualstudio-Projects:2018-08-01-preview` has 4 operations,
all `deprecated: true`. The generator said so precisely — *"all 4 operations in
this spec are marked `deprecated` … re-run with `--include-deprecated`"* — but
`corpus_run.py`'s fail classifier still matched the generator's OLD wording, so
it filed the row as **`generate_error`**: a class that reads as an unexplained
defect of mine. Two of my own components disagreed about what had happened, and
the published one would have been the wrong one. The classifier now matches the
stable `no tools generated:` prefix and sub-classifies on the stated cause
(`no_operations_all_deprecated`, `_webhook_contract`, `_filtered`). Replayed
against all eight recorded non-deliveries across the three runs: seven keep
their stored class, and this row reclassifies to
`no_operations_all_deprecated` — correct behaviour, correctly named.

So across 750 rows there are **eight non-deliveries and still zero unexplained
generator defects**: 1 size cap, 5 specs with nothing invokable by design
(3 zero-path documents, 2 fully-deprecated APIs), 2 URL-encoding faults in my
own fetch path, fixed under P14 and absent from both later runs.

---

## What the survey changed in the tool

A coverage number whose failures are only *counted* teaches nothing. Every
`no_operations` row in the 500 specs was read by hand, and each had a different
cause that the generator reported with one generic string
(`no operations matched — loosen --include or check the spec`). A client handed
that reads it as "your tool is broken".

`extract_tools` now counts what it saw (paths, operations, deprecated, filtered,
webhooks) and `main` reports the actual reason:

| Real spec from the survey | What it now says |
|---|---|
| `adyen.com:BalancePlatformReportNotification-v1` (0 paths, 1 webhook) | "declares 1 webhook definition(s) and 0 callable operations — it is a notification contract, so there is nothing for an agent to invoke" |
| `adyen.com:BalancePlatformTransferNotification-v3` (0 paths, 2 webhooks) | same, 2 webhooks |
| `googleapis.com:youtubeAnalytics:v1` (0 paths, no webhooks) | "declares 0 operations (no `paths` entry carries a GET/POST/PUT/PATCH/DELETE) — check that this is the API spec and not an index, a schema-only document, or a webhook contract" |
| `azure.com:containerservice-containerService:2017-07-01` (5 ops, **all** `deprecated: true`) | "all 5 operations in this spec are marked `deprecated`, and deprecated operations are skipped by default. Re-run with `--include-deprecated` to expose them anyway." |

`--include-deprecated` is new, and verified on that Azure spec: 5 tools
generated, schema smoke passes. The default (skip deprecated) is unchanged and
still correct — a deprecated operation is not one an agent should be handed by
default.

Ratchet re-run after the change: **suite v2, 7/7, score 1.00, median 2.2s**
(`bench/history.jsonl`, 2026-08-17T22:14:12Z). No regression.

---

## Diagnostics: every `no_operations` row now states its own cause

`no operations matched — loosen --include or check the spec` was the same message
for four structurally different situations, and a client who reads it concludes the
tool is broken. Each of the four now names itself, verified against the real specs
from this survey:

| Situation | What the generator now says | Verified on |
|---|---|---|
| Webhook/notification contract | "declares N webhook definition(s) and 0 callable operations — a notification contract, so there is nothing for an agent to invoke" | `adyen.com:BalancePlatformReportNotification-v1` (1), `adyen.com:BalancePlatformTransferNotification-v3` (2) |
| Every operation deprecated | "all N operations in this spec are marked `deprecated`, and deprecated operations are skipped by default. Re-run with `--include-deprecated`" | `azure.com:containerservice-containerService:2017-07-01` (5/5 deprecated) |
| No method keys under any path | "0 operations (no `paths` entry carries a GET/POST/PUT/PATCH/DELETE) — check that this is the API spec and not an index, a schema-only document, or a webhook contract" | `googleapis.com:youtubeAnalytics:v1` |
| `--include` filtered everything out | "N operations found, none matched --include `<pattern>` (M filtered out, K deprecated)" | — |

`--include-deprecated` was added with it: on the Azure spec it generates the 5
tools and passes smoke, so a deliberately-frozen API is reachable when the client
asks for it rather than reading as an unsupported spec.

This changes no survey number. All four outcomes were correct before; only the
explanation was missing. Ratchet re-run after the change: **suite v2, 7/7, score
1.00, median 2.2s** — no regression.
