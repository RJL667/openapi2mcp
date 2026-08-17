# openapi2mcp

Turn any OpenAPI 3.x spec into a working MCP stdio server — typed tool schemas,
auth wiring, and a smoke test per tool. Standard library only, no dependencies
beyond PyYAML for YAML specs.

```bash
python3 openapi2mcp.py \
  --spec https://api.example.com/openapi.json \
  --name example --out ./build --max-tools 8 --include 'pet|store'

cd build && python3 smoke_test.py          # schema check
         && python3 smoke_test.py --call   # execute against the live API
```

Emits into `--out`:

| file | what it is |
|---|---|
| `<name>_mcp_server.py` | the MCP server (stdio, JSON-RPC 2.0) |
| `smoke_test.py` | `initialize` + `tools/list` + per-tool schema check; `--call` hits the live API |
| `README.md` | client config + acceptance checklist |

## What it handles that a naive generator does not

- **Multi-file specs.** Serious APIs keep one YAML per URL and assemble at build
  time (Dependency-Track, most Spring/JAX-RS projects). The entry document is
  nothing but external `$ref`s. External refs are fetched and spliced —
  relative paths, absolute URLs, and `file.yaml#/fragment`.
- **Relative server URLs.** OpenAPI permits `servers[0].url = "/api/v3"`, meaning
  "same origin as this document". Emitted verbatim, every live call dies with
  `unknown url type`. Resolved against the spec's own origin.
- **Cyclic `$ref`s.** Bounded-depth resolution; specs contain cycles.
- **Huge specs.** PostHog's is 12 MB / 1,333 paths. `--include` is a regex over
  path, tag, `operationId` and summary, so you select the surface you want
  instead of taking the first N alphabetically.
- **Reads before writes.** Tools are ordered GET → POST → PUT → PATCH → DELETE,
  so a capped run gives you a safe read-only surface by default.
- **Deprecated operations are skipped** — an agent should not be handed a
  retired endpoint by default. `--include-deprecated` exposes them anyway.
- **Says why when it emits nothing.** A spec with no callable operations is
  reported as what it actually is — a webhook/notification contract, a
  schema-only document, an all-`deprecated` API, or an over-tight `--include`
  — rather than a generic "no operations matched".

## Benchmark

`bench.py` is a fixed suite scored identically every run, appended to
`bench/history.jsonl` with a date. Suite v3, 8 tasks:

| spec | shape | size | tools | result |
|---|---|---|---|---|
| petstore | JSON, single file | 16 KB | 8 | PASS |
| posthog | JSON, 1,333 paths | 12.7 MB | 8 | PASS |
| stripe | JSON | 7.8 MB | 8 | PASS |
| slack | JSON | 1.2 MB | 8 | PASS |
| twilio | JSON | 1.8 MB | 8 | PASS |
| box | JSON | 1.7 MB | 8 | PASS |
| dependency-track | YAML, multi-file | 10 KB entry | 8 | PASS |
| wikimedia | **Swagger 2.0** (`host`+`basePath`, no `servers`) | 1.2 MB | 8 | PASS |

**8/8 delivered, median 0.8s** — suite v3, run 15, 2026-08-17, the current
recorded baseline in `bench/`. Fifteen dated runs so far (3 on v1, 10 on v2,
2 on v3): fourteen at 1.00 and **one red — run 14, 0.875**, kept deliberately.

v3 = v2's seven tasks plus `wikimedia` (a Swagger 2.0 document) plus an
`EXPECTED_BASE` assertion on the generated base URL, which the schema smoke test
structurally cannot see. Both additions exist because v2 could not fail on them:
every v2 task is an OpenAPI 3.x document with a `servers` block, so ten green v2
runs were never evidence that Swagger 2.0 resolved its base correctly — and it
did not. Run 14 is the assertion firing; run 15 is the fix. A suite that has only
ever recorded 1.00 has not been stressed. Wall time here measures the harness and the
network rather than the generator: `dependency-track` alone spends ~30 sequential
HTTPS fetches resolving external `$ref`s, and its wall time has ranged 24–40s
across runs under identical code. Treat delivered/total as the score and time as
a tripwire only when the code changed. OpenAI's 3 MB YAML spec also generates 8
tools and passes smoke in 5.2s (not in the suite).

Run it:

```bash
python3 bench.py           # full suite
python3 bench.py --quick   # small specs only
```

Scores are not comparable across suite versions — adding a task bumps the
version and starts a new baseline.

## Python,2197,Add OAuth Support over the public API universe

`bench.py` answers "did I break anything?". It cannot answer "what fraction of
real third-party APIs does this digest unattended?" — `corpus_run.py` does. It
runs the same pipeline (fetch → generate → schema smoke) over a seeded random
sample of [apis.guru](https://apis.guru): 2,529 real provider/API pairs, not a
curated list.

| Run | Sample | Delivered | Median wall |
|---|---|---|---|
| seed 17 (`2026-08-17_s17n250`) | 250 | **245 — 98.0%** | 2.6s |
| seed 23 (`2026-08-17_s23n250`, independent; 23 specs overlap) | 250 | **248 — 99.2%** | 3.0s |
| **combined** | **500** | **493 — 98.6%** | — |

Across both samples: OpenAPI 3.x **292 / 295**, Swagger 2.0 **201 / 202**,
477 distinct APIs covered.

> **Schema-level only.** There are no credentials for 500 third-party APIs, so
> `--call` was not run. A pass means the spec was fetched and parsed and the
> generated server survived `initialize` + `tools/list` + a per-tool schema
> check — it does **not** mean live requests to that API succeed.

All **seven** non-deliveries are named and attributed in
[`bench/CORPUS.md`](bench/CORPUS.md), none unexplained:

| Cause | n | What it means |
|---|---|---|
| `fetch_error` — raw spaces in the spec URL | 2 | **A defect in this tooling, not the spec.** `urllib` raised `InvalidURL` before a byte moved; fixed, and seed 23 recorded zero fetch errors. |
| `no_operations` — 0-path documents | 3 | Webhook/notification contracts and a stub: nothing invokable, by design. |
| `no_operations` — every operation `deprecated` | 1 | Correct behaviour; `--include-deprecated` exposes them. |
| `too_large` — 26 MB spec | 1 | Harness cap (25 MB), not a parse failure. |

```bash
python3 corpus_run.py --n 250 --seed 17 --jobs 8
```

## Example: OWASP Dependency-Track

```bash
python3 openapi2mcp.py \
  --spec https://raw.githubusercontent.com/DependencyTrack/dependency-track/main/api/src/main/openapi/openapi.yaml \
  --name deptrack --out ./deptrack --max-tools 8 --include 'project|component|vuln'
```

Yields a read-only security surface an agent can query — `listcomponents`,
`listprojectcomponents`, `listvulnpolicies`, `listvulnkevassertions` — without
being able to mutate the portfolio.

## Licence

MIT.
