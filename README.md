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
- **Auth read from the spec, not assumed.** The credential goes where the spec's
  `securitySchemes` / `securityDefinitions` says it goes — header or **query
  parameter**, under the declared name, with the right prefix (`Bearer `,
  `Basic ` with base64, or none at all). APIs that require **two** credentials
  (an api key plus a client id) get both wired. Measured over 200 delivered
  specs from the coverage survey: a hard-coded `Authorization: Bearer` — what a
  naive generator emits — is correct for only **82 of the 159 that declare a
  scheme (52%)**; 7 of them put the key in a query parameter, which no header
  override can reach.
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
python3 bench.py            # full suite
python3 bench.py --quick    # small specs only
```

### `auth_probe.py` — the check the suite structurally cannot do

The smoke test asserts what a server *says about itself*; it never makes the
server send a request, so it cannot see where a credential lands. `auth_probe.py`
stands up a local echo server, points generated servers at it and asserts the
bytes on the wire — http bearer, apiKey in a header, apiKey in a header *named*
`Authorization`, apiKey in a **query parameter**, HTTP basic, and oauth2:

```bash
python3 auth_probe.py       # 6/6 place the credential where the spec declares it
```

It earned its keep immediately: the first run caught the generated server
emitting `Authorization: Bearertok123` — a missing trailing space that every
schema check accepts and every real API rejects with 401.

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
| seed 23 (`2026-08-17_s23n250`) | 250 | **248 — 99.2%** | 3.0s |
| seed 31 (`2026-08-18_s31n250`) | 250 | **249 — 99.6%** | 2.3s |
| **combined (rows)** | **750** | **742 — 98.9%** | — |
| **combined (distinct APIs)** | **682** | **674 — 98.8%** | — |

Across the three samples: OpenAPI 3.x **451 / 454**, Swagger 2.0 **291 / 293**,
**682 distinct APIs** covered. The samples are independent draws from the same
pool and overlap on 65 specs (17∩23 = 23, 17∩31 = 26, 23∩31 = 22); every
overlapping spec got the same outcome in both runs. The row figure and the
distinct-API figure are both quoted because they answer different questions.

The per-spec records are in this repo — `bench/corpus_<run id>.jsonl`, 250 rows
each — so every number in this section can be re-derived without trusting the
summary.

> **Schema-level only.** There are no credentials for 750 third-party APIs, so
> `--call` was not run. A pass means the spec was fetched and parsed and the
> generated server survived `initialize` + `tools/list` + a per-tool schema
> check — it does **not** mean live requests to that API succeed.

That caveat is not decorative. A schema-green server can still be pointed at the
wrong host, and for a while every one of them was: `base_url()` read only
OpenAPI 3's `servers` block, and Swagger 2.0 puts its base in `schemes` +
`host` + `basePath`, so **201 of the first 493 deliveries (40.8%)** carried the
`http://localhost:8000` placeholder and would have died at the client's first
live call. Every check in the survey passed on all 201.

So the survey now measures it. `corpus_run.py` records the resolved base URL of
every delivered server and classifies it `usable` / `placeholder` / `templated` /
`unreadable`. Seed 31 is the first run carrying it:

| Seed 31 | |
|---|---|
| Delivered | 249 / 250 |
| **Delivered with a usable base** | **242 / 249 — 97.2%** |
| `placeholder` (`http://localhost:8000`) | 7 |
| `templated` / `unreadable` | 0 |

Swagger 2.0 specifically: **89 of the 90 delivered** now resolve a real host,
against 0% before the fix. The 7 placeholders are specs that declare no host at
all — the placeholder is the correct output there, and it is overridable with
`--base-url` or the generated `<NAME>_BASE_URL` env var.

All **eight** non-deliveries across the 750 rows are named and attributed in
[`bench/CORPUS.md`](bench/CORPUS.md), none unexplained:

| Cause | n | What it means |
|---|---|---|
| `fetch_error` — raw spaces in the spec URL | 2 | **A defect in this tooling, not the spec.** `urllib` raised `InvalidURL` before a byte moved; fixed, and seeds 23 and 31 recorded zero fetch errors. |
| `no_operations` — 0-path documents | 3 | Webhook/notification contracts and a stub: nothing invokable, by design. |
| `no_operations` — every operation `deprecated` | 2 | Correct behaviour; `--include-deprecated` exposes them. |
| `too_large` — 26 MB spec | 1 | Harness cap (25 MB), not a parse failure. |

Seed 31's single non-delivery is the second fully-deprecated API, and it arrived
filed as `generate_error` — a class that reads as an unexplained defect. The
generator's no-tool message had been rewritten to name its own cause and the
survey's classifier still matched the old wording. The classifier now keys on the
stable `no tools generated:` prefix and sub-classifies on the stated cause; the
reclassification was replayed against all eight recorded non-deliveries, and the
other seven keep their stored class.

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
