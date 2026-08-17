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

## Benchmark

`bench.py` is a fixed suite scored identically every run, appended to
`bench/history.jsonl` with a date. Suite v2, 7 tasks:

| spec | shape | size | tools | result |
|---|---|---|---|---|
| petstore | JSON, single file | 16 KB | 8 | PASS |
| posthog | JSON, 1,333 paths | 12.7 MB | 8 | PASS |
| stripe | JSON | 7.8 MB | 8 | PASS |
| slack | JSON | 1.2 MB | 8 | PASS |
| twilio | JSON | 1.8 MB | 8 | PASS |
| box | JSON | 1.7 MB | 8 | PASS |
| dependency-track | YAML, multi-file | 10 KB entry | 8 | PASS |

**7/7 delivered, median 2.3s** — suite v2, run 3, 2026-08-17, the current
recorded baseline in `bench/`. Wall time here measures the harness and the
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

## Coverage over the public API universe

`bench.py` answers "did I break anything?". It cannot answer "what fraction of
real third-party APIs does this digest unattended?" — `corpus_run.py` does.

Seeded random sample of **250** of the **2,529** provider/API pairs indexed by
[apis.guru](https://apis.guru), each taken fetch → generate → schema smoke:

| | |
|---|---|
| Delivered | **245 / 250 — 98.0%** |
| OpenAPI 3.x | 151 / 152 |
| Swagger 2.0 | 94 / 95 |
| Median wall | 2.6s per spec |

**Schema-level only.** There are no credentials for 250 third-party APIs, so
`--call` was never run: a pass means the tool surface is well-formed and the
server answers `initialize` + `tools/list`, not that live requests succeed.

All five failures are named and attributed in [`bench/CORPUS.md`](bench/CORPUS.md)
— including the two that were this harness's own URL-encoding bug rather than a
problem with the spec. Run id `2026-08-17_s17n250`; records are in `bench/`.

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
