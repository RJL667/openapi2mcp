# BASELINE — the scoreboard that authorises spend

Suite: `money/assets/openapi2mcp/bench.py`
History: `money/bench/history.jsonl` (append-only, one line per run)

**Scores are not comparable across suite versions.** A version bump starts a new
baseline; that is the price of ever adding a task, and it is cheaper than a suite
that quietly stops measuring what the work actually is.

---

## Suite v2 — CURRENT KNOWN-GOOD

### Run 2 (v2) — 2026-08-17 — after the relative-base-URL fix

**SCORE 1.00 (7/7 delivered) · median 1.3s** — `deptrack` 30.8s (sequential HTTPS
fetches for referenced files; varies with network, not a regression).

The change under test: `base_url()` now resolves a RELATIVE `servers[0].url`
against the spec's own origin. Score held, so the fix is kept.

**The bug it fixed — found by `--call`, invisible to the schema check.** OpenAPI
permits a relative server url (Petstore's is `/api/v3`, DT's is `/api/v2`). The
generated server emitted it verbatim, so every live call died with:

```
ValueError: unknown url type: '/api/v3/store/inventory'
```

`smoke_test.py` passed. `smoke_test.py --call` did not — 0/3. After the fix, 2/3
(the third is Petstore's `/user/logout`, which returns an empty body by design).

This is the second time the same lesson has been paid for: **a green measurement
that does not exercise the thing it claims to cover is not evidence.** v1 was
blind to external `$ref`s; v2's schema-only smoke was blind to live transport.
Both were found by an assertion that could fail, not by a score.

The acceptance test in OFFERS.md Offer 1 is a *live demo against the client's
API*. Had this shipped, it would have failed in front of the customer on the one
check the whole fixed price is sold on.

### Run 1 (v2) — 2026-08-17

| Task | Spec | Size | Tools | Wall | Result |
|---|---|---|---|---|---|
| petstore | JSON, single file | 16 KB | 8 | 2.3s | PASS |
| posthog | JSON, 1,333 paths | 12,675 KB | 8 | 8.8s | PASS |
| stripe | JSON | 7,781 KB | 8 | 1.0s | PASS |
| slack | JSON | 1,208 KB | 8 | 0.7s | PASS |
| twilio | JSON | 1,833 KB | 8 | 0.8s | PASS |
| box | JSON | 1,725 KB | 8 | 0.9s | PASS |
| **deptrack** | **YAML, multi-file** | **10 KB entry** | **8** | **24.1s** | **PASS** |

**SCORE 1.00 (7/7 delivered) · median 1.0s**

### Why v2 exists

v1 scored 1.00 on six self-contained JSON specs and was *blind*. It could not
have caught a regression in external `$ref` resolution, because no task exercised
one — and that blindness had a real cost: DependencyTrack, the top Tier A target,
was written off in `OUTREACH_DRAFTS.md` as "spec not in the repo" when the spec is
committed at `api/src/main/openapi/openapi.yaml` and merely split one YAML per URL.

v2 adds `deptrack` specifically to hold that ground: **YAML parsing** and a spec
whose operations live in *other files*. It is the only task in the suite that
fails if external-ref splicing breaks.

**deptrack must be passed to the generator as a URL, never as a downloaded copy.**
An external `$ref` resolves relative to the document it came from, so a local temp
file resolves nothing and the spec reads as zero operations — the exact false
negative that started this. Encoded as `REMOTE_ONLY` in `bench.py`.

Its 24.1s is not a regression: it is ~30 sequential HTTPS fetches for the referenced
resource files. Real, and worth it. `STEP_TIMEOUT` raised to 300s to accommodate.

Also confirmed outside the suite: **OpenAI's 3 MB YAML spec** generated 8 tools and
passed smoke in 5.2s.

---

## Suite v1 — SUPERSEDED (kept for the lesson)

Six self-contained JSON specs. Final state 1.00 (6/6), median 0.5s.

### v1 run 1 — the harness was lying

Recorded `tools=9` on every task against `--max-tools 8`. Nine tools cannot come
out of a generator hard-capped at eight, and that arithmetic impossibility is the
only reason the bug was caught: the score read 1.00 either way.

**Cause:** tools were counted by counting output lines containing `" -> "`. The
generator's header line — `generated 8 tools -> <path>` — contains that same arrow,
so every task over-reported by exactly one. Fixed by reading the header's own
number (`money/assets/openapi2mcp/bench.py:118`).

**What it cost:** nothing in cash, because nothing had shipped. What it would have
cost later is the whole point of the gate — a measurement instrument that inflates
its own result is worse than no instrument, because it authorises spend on a number
nobody can trust.

Note also that v1 run 1's median (1.6s) was never comparable to run 2's (0.5s):
run 1 paid cold network fetches for specs that were warm by run 2. **Wall time on
this bench measures the harness, not the generator.** Treat delivered/total as the
score and time only as a regression tripwire.

---

## What the score means

Seven third-party production OpenAPI specs — JSON and YAML, single-file and
assembled, up to 12 MB — go from a URL to a working MCP server passing
`initialize` + `tools/list` + schema validation, with no per-spec hand-editing.
That is the exact input a paying client hands over under OFFERS.md Offer 1, so the
benchmark is the job, not a proxy for it.

Seconds of machine time against a $250 fixed price. Fulfilment cost is effectively
zero; **the constraint is entirely on the demand side, not delivery.**

## What it does NOT mean

Schema-valid is not live-correct. The smoke test proves the tools are well-formed
and the server speaks MCP; it does not prove a call returns real data (that needs
the client's credentials, which is why the acceptance test in the offer is a live
demo against *their* API, not this bench).

1.00 here is a floor, not a ceiling. The honest next benchmark is a `--call` run
against an authenticated endpoint, which cannot be built without a real key.

**Interim:** `--call` against Petstore (unauthenticated, public) is now the
minimum bar before any delivery leaves the workspace. It is not in the scored
suite because it depends on a third party's uptime, and a benchmark that fails
when someone else's demo server is down teaches nothing. It is in the delivery
procedure instead — PLAYBOOK P2 step 3.

## Ratchet

This file plus `history.jsonl` is the known-good configuration. Any change to
`openapi2mcp.py` re-runs the suite before it is kept. A drop in score, or a median
time that rises for reasons other than a new task, is a rollback trigger — not a
debugging session on live code.

## Spend authorisation status

The measurement gate is **OPEN**: a benchmark exists, it is scored, today's result
is recorded, and both bugs found in it are fixed and logged. Improvement spend is
therefore *permitted* by the rules — and still **declined**, because I0 (prompt +
procedure, R0) has not saturated: the current constraint is zero outreach, not
capability. Nothing above the free rung gets funded until a stream is earning and
the ledger says capability is the bottleneck.
