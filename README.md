# Signal

**Turn customer feedback into product clarity.**

Signal is an AI-first Feature Intelligence System. Product teams receive feature
requests as unstructured text; Signal reads each one, works out the underlying
customer problem, searches the existing requests with a read-only tool, proposes
relationships with the evidence behind each one, and waits for a human to
approve or reject them.

The operating principle is **AI recommends, humans decide**. The model has no
write access to anything. It cannot delete, merge or alter a request, and every
reference it returns is checked against data the backend retrieved itself.

> **Status.** The required workflow is complete and tested: submit, browse,
> search, support, analyse with a real LLM integration, review, and persist.
>
> **One thing is not verified and is not claimed to be:** the authenticated
> smoke test against the Anthropic API has not been run, because no API key was
> configured during development. The SDK surface was verified by introspection
> (see [Verification status](#verification-status)); whether the server accepts
> this exact `output_config.format` schema for the configured model is unproven
> until that test runs. Everything else below was executed.

---

## The problem

A team with real customers accumulates feature requests faster than anyone can
read them. The same need arrives worded five different ways. The request a
customer writes is usually a solution they guessed at — "I need an Excel export"
— not the problem they have — "every Friday I rebuild a sales report by hand and
email it to my manager". Triage becomes repetitive reading, and the loudest
request wins by default.

Signal targets the step with the most repetitive manual work and the highest
error rate: **turning an incoming request into a reviewable recommendation** —
what the customer actually needs, which existing requests relate to it, and the
evidence for each claim.

## Who benefits

| Role | What they get |
|---|---|
| **Requester** (customer or internal stakeholder) | Submit a request, find existing ones, show support, see status |
| **Product Manager** | A queue of AI-prepared recommendations with evidence, approved or rejected one candidate at a time, with an audit trail |

## Where human judgement stays

The model decides one thing: what to search for, and whether to search again.
It does not decide the headline classification (the backend derives it from the
validated evidence), the recommendation (a mapping), which requests exist
(checked against an allowlist), or what happens next (a person).

Prioritisation is deliberately out of scope for the model. Signal surfaces
demand signals — support count, related requests, recency — and says plainly
that support count is **not** a priority. Inventing a priority score from data
we do not have would be the easiest way to make this look impressive and be
useless.

---

## Architecture

```
Next.js 16 (App Router, TypeScript, Tailwind)      :3000
        │  HTTP/JSON, CORS restricted to the configured origin
        ▼
FastAPI                                            :8000
  api/routes    validation, status codes
  services      business rules
  repositories  SQL
  services/ai   the analysis loop and the provider adapter
  tools         the one read-only tool the model may call
        │                             │
        ▼                             ▼
PostgreSQL 16 (Supabase or Docker)   Anthropic API
  constraints + full-text search     claude-sonnet-5-5
```

The browser never holds the API key or a database credential. Every LLM call
originates in the backend.

Detail in [`docs/architecture.md`](docs/architecture.md), including a table of
where each guarantee is actually enforced. Decisions and trade-offs — including
the ones that changed, and two claims that were wrong and were corrected — are
in [`docs/decisions.md`](docs/decisions.md).

## Stack

| Layer | Choice | Version |
|---|---|---|
| Frontend | Next.js, React, Tailwind CSS, TypeScript | 16.4.2 / 19.3.0 / 4 / 5 |
| Backend | FastAPI, Pydantic, asyncpg, uvicorn | 0.143.2 / 2.14.1 / 0.32.0 / 0.54.0 |
| Database | PostgreSQL | 16 |
| AI | `anthropic` SDK, Claude Sonnet 5.5 | 1.15.0 |
| Tests | pytest, pytest-asyncio | 9.1.1 / 1.4.0 |

---

## Running it locally

**Requirements:** Python 3.12+, Node 20.9+, Docker (or a Supabase project).

### 1. Database

```bash
docker compose up -d --wait db
```

Or point `DATABASE_URL` at a Supabase project — the migrations are plain SQL and
run unchanged against either.

### 2. Environment

```bash
cp .env.example backend/.env
cp .env.example frontend/.env.local    # keep only the NEXT_PUBLIC_* line
```

Fill in `backend/.env`:

```
DATABASE_URL=postgresql://signal:signal@localhost:5432/signal
ANTHROPIC_API_KEY=       # required for AI analysis; everything else works without it
LLM_MODEL=claude-sonnet-5-5
```

Both files are git-ignored and `.env.example` contains no secret values.

> Using the Supabase **pooler** (port 6543)? Also set
> `DB_STATEMENT_CACHE_SIZE=0` — pgbouncer in transaction mode is incompatible
> with asyncpg's prepared-statement cache. The direct connection on port 5432
> needs no change.

### 3. Install and migrate

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux
cd .. && backend/.venv/Scripts/python scripts/migrate.py
```

`scripts/migrate.py --status` lists what is applied. Each migration is recorded
with a checksum, so an already-applied file cannot be edited silently.

### 4. Demo data (optional)

```bash
docker compose exec -T db psql -U signal -d signal < supabase/seed.sql
```

Eight fictional requests, clearly labelled `demo:*`, including two genuine
duplicates, two that are related but not duplicates, one independent and one
deliberately vague. Re-running it is safe.

**The seed contains no AI analyses.** Shipping a hand-written model answer and
displaying it as though a model produced it is precisely what this project
avoids. Analyses in a demo come from running the application with a real key.

### 5. Run

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev
```

Open **http://localhost:3000**.

---

## Reproducing the demonstration

With the seed applied and a real API key configured:

1. **Board** — eight requests, newest first. Switch to **Most supported**;
   search `report` and watch it match "Scheduled report delivery" by stem.
2. **Submit** — *New request*. Try a title of three characters to see per-field
   validation from the API, then submit something real.
3. **Analysis** — the request page shows `Analysing…`, then the underlying
   need, the theme, each proposed relationship **with the evidence for it**, and
   a footer with the model, the retrieval mode, the number of searches and the
   latency.
4. **The interesting case** — submit "Export sales data to Excel" with a
   description about rebuilding a weekly report by hand. The seeded
   "Scheduled report delivery" shares almost no vocabulary with it; if the
   analysis connects them, it did so by searching for the *need*, not the words.
5. **Review** — *Decision inbox*. Approve the duplicate, reject the related one,
   add a reason. Both original requests remain, intact and separate; the request
   page now shows an **Approved relationship**.
6. **Audit** — the decision history lists both decisions with reviewer and
   reason. Change one and watch the history gain an entry rather than lose one.
7. **Failure** — stop the backend and reload: the board explains the API is
   unreachable instead of breaking. Submitted requests survive an AI failure;
   a failed analysis shows a specific reason and a **Run analysis** button.

The expected classification for each seeded request is written down in
`supabase/seed.sql`, before the demo, so a run can be checked against a
prediction rather than rationalised afterwards.

---

## Environment variables

| Variable | Where | Required | Purpose |
|---|---|---|---|
| `DATABASE_URL` | backend | yes | PostgreSQL connection string |
| `DB_STATEMENT_CACHE_SIZE` | backend | no (100) | set to `0` behind the Supabase pooler |
| `ANTHROPIC_API_KEY` | backend | for analysis | never exposed to the frontend |
| `LLM_MODEL` | backend | no (`claude-sonnet-5-5`) | model id |
| `LLM_TIMEOUT_SECONDS` | backend | no (45) | per-request timeout |
| `LLM_MAX_ITERATIONS` / `LLM_MAX_TOOL_CALLS` | backend | no (3 / 4) | the loop budget |
| `LLM_EFFORT` | backend | no (`medium`) | set explicitly; model defaults differ |
| `STALE_ANALYSIS_*_SECONDS` | backend | no (180 / 60) | interruption recovery thresholds |
| `APP_ENV`, `CORS_ORIGINS` | backend | no | environment label, allowed origins |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | yes | backend base URL (public by design) |

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | liveness, database connectivity, applied schema version |
| `POST` | `/api/requests` | submit a request (`201`) |
| `GET` | `/api/requests` | list, with `?q=`, `?sort=recent\|top`, `?limit=`, `?offset=` |
| `GET` | `/api/requests/{id}` | one request with its latest analysis and approved links |
| `POST` | `/api/requests/{id}/analyses` | run the analysis again (`202`) |
| `POST` | `/api/requests/{id}/votes` | support (`201` new, `200` already) |
| `DELETE` | `/api/requests/{id}/votes` | withdraw support (`200`, idempotent) |
| `GET` | `/api/inbox` | analyses awaiting a human decision |
| `GET` | `/api/analyses/{id}` | one analysis with its candidates and their decisions |
| `POST` | `/api/analyses/{id}/decisions` | approve, reject or dismiss (`201`) |
| `GET` | `/api/decisions` | the decision audit log, newest first |

Vote endpoints take the browser-scoped identity in an `X-Voter-Key` header and
return the authoritative count, so a client whose local state has drifted
corrects itself from the response.

Every failure uses one shape:

```json
{"error": {"code": "validation_error", "message": "Some fields need attention.",
           "fields": {"title": "String should have at least 5 characters"}}}
```

`fields` appears only on validation failures. Internal and database errors never
carry a driver message.

---

## Data model

Six tables. The integrity rules are the point, so they live in the database
rather than only in application code.

| Table | Holds | Key guarantees |
|---|---|---|
| `feature_requests` | the requests | length checks, status enum, generated `tsvector` + GIN index |
| `request_votes` | support | `UNIQUE (request_id, voter_key)` |
| `ai_analyses` | one row per analysis run | `completed` must carry a verdict, `failed` must carry an error code, **at most one `pending` per request** |
| `analysis_related_requests` | one row per suggested candidate, with its evidence | foreign key to a real request — a fabricated id cannot be stored |
| `review_decisions` | append-only human decision log | never updated or deleted |
| `request_links` | human-approved relationships | `CHECK (request_a_id < request_b_id)` + `UNIQUE` pair: no self-links, no reversed duplicates |

Relationships are relational rather than JSON specifically so that every
AI-suggested reference is protected by a foreign key.

---

## The AI flow

1. The request is validated and **persisted before** any AI call.
2. A `pending` analysis row is created — **only if a provider is configured.**
   With no key, nothing is queued and the request reports `not_requested`,
   because a pending row nothing can process would be a lie told by the
   database.
3. A bounded loop (max 3 iterations, 4 tool calls, 45s per request) offers the
   model one read-only tool, `search_existing_requests`.
4. Tool arguments are validated; results are capped at 8 rows and limited to
   `id`, `title`, truncated `description` and vote count. The analysed request
   is always excluded.
5. The ids the tool returned become an **allowlist**.
6. The final JSON is constrained with `output_config.format` and validated again
   with Pydantic.
7. Every referenced id is checked against the allowlist and against the analysed
   request's own id. Unverifiable references are discarded and counted; if
   **all** of them are invalid the analysis fails as `FABRICATED_REFERENCES`
   rather than being reported as "nothing related".
8. The classification and recommendation are **derived** from the surviving
   candidates, never taken from the model.
9. The analysis is persisted for human review.

**Forced tool choice is not available** on current models — `tool_choice: {"type":
"tool"}` returns HTTP 400 — so the model cannot be made to search. When it
answers without searching, the backend retrieves candidates using the need the
model just extracted and asks once more; `retrieval_mode` records `agentic` or
`backend_fallback`, so the two paths can be compared rather than argued about.

If the provider is unavailable, times out, refuses, or returns invalid output,
the request stays saved, the analysis is marked `failed` with a specific error
code, and it can be retried. The system never fabricates an AI recommendation.

**User-submitted text is untrusted data.** It is delimited, declared untrusted
in the system prompt, and a forged closing marker inside it is defused. That is
a mitigation; the guarantees are that the only tool is read-only, the model
cannot write, references are checked, and a human approves everything.

---

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest        # needs a running database
cd backend && .venv/Scripts/python evals/run_eval.py
cd frontend && npx tsc --noEmit && npm run build
```

**221 tests pass.** Integration tests run against real PostgreSQL, because most
of them verify constraints and indexes only Postgres enforces. Each runs inside
a transaction that is rolled back, so order never matters and nothing is left
behind.

What they cover: schema constraints and every documented error code · vote
uniqueness and idempotent support · search by title and description, stemming,
partial words, hostile input · ordering · request validation including stripped
control characters · the analysis loop with a scripted model — timeout, provider
error, refusal, truncation, malformed JSON, schema violations, invented
references, budget exhaustion, injection defence · interruption recovery for
both "never started" and "started and hung", transactional retry, and two
concurrent retries where only one may win · the review rules, including that a
rejection cannot remove a link approved from the other side · the audit log
staying append-only.

The evaluation suite (`backend/evals/`) scores ten labelled cases, including
adversarial ones, and reports duplicate precision with its sample size. **Read
[`backend/evals/README.md`](backend/evals/README.md) before quoting any number
from it:** mock mode measures the pipeline, not model judgement, and the report
labels the mode on every run.

The frontend has no component test framework, so the two client components are
covered by reasoning and manual verification, not automated tests. Stated here
rather than left to be discovered.

### Verification status

| Verified by execution | Not verified |
|---|---|
| 221 tests, the eval suite, `tsc`, `next build` | the authenticated Anthropic smoke test |
| migrations against Postgres 16, the seed applied twice | the live provider accepting this `output_config.format` schema |
| every board, analysis and review state rendered against a running backend | any latency or accuracy figure from a real model |
| SDK surface by introspection: `output_config`, `strict`, `messages.parse`, `AsyncAnthropic`, `betas`/`fallbacks` only on the beta namespace | — |

---

## Proposed success metrics

Three metrics, with collection method and failure modes in
[`docs/metrics.md`](docs/metrics.md). No business-impact figure is claimed.

1. **Triage time** — submission to human decision, reported as three separate
   intervals: queue wait, **AI processing time** (`started_at` → `completed_at`)
   and **time to human decision** (`completed_at` → first decision). Kept apart
   so a fast model cannot hide a slow review queue.
2. **Duplicate recommendation precision** — on a labelled set, at the candidate
   level, reported with its sample size. In production it would come from the
   decisions themselves: an approved `DUPLICATE` is a true positive.
3. **Human recommendation acceptance rate** — approvals over decisions, from the
   audit log, collapsed to the current decision per pair. Misleading in both
   directions on its own; `docs/metrics.md` says why.

---

## Security and limitations

**In place.** Credentials only in the backend and only from the environment;
`.env` git-ignored and `.env.example` free of values; health reports whether a
key is configured, never the key; database errors logged as a type with no
traceback, because a connection error can carry the DSN; input length and enum
constraints enforced by the database; CORS restricted; the LLM has one
read-only tool and no write path; every model reference checked against
retrieved data; search terms always bound parameters, with `strpos` instead of
an interpolated `ILIKE` pattern; fixed `ORDER BY` clauses chosen by a validated
enum.

**Explicitly not production-ready.**

- **No authentication.** Vote identity is a browser-scoped key — it prevents
  accidental double voting, nothing more. The review queue is an open route with
  a visible notice saying so. Production needs real identity (OIDC/SSO),
  role-based authorisation on every mutating endpoint, rate limiting, abuse
  detection and audit logging tied to real user ids.
- **Background tasks run in the API process.** An analysis does not survive a
  restart; it is recovered as `INTERRUPTED` and must be retried. A durable queue
  is the correct next step.
- **Logs are not redacted.** Database errors are logged without a traceback for
  that reason, but the handler for genuine bugs keeps the traceback, since
  without it nothing is diagnosable. Production needs a redacting log handler.
- **No per-request token cost is recorded**, so the cost side of any model
  comparison is currently guesswork.
- **Five high-severity npm advisories**, all in the lint toolchain
  (`eslint-config-next` → `fast-glob` → `micromatch` → `braces`). Runtime
  dependencies are only `next`, `react` and `react-dom`. `npm audit fix` does
  not resolve them and `--force` breaks the config. Accepted and documented.
- **The request detail page returns 404 content with a 200 status** when the id
  does not exist, because the static shell is sent before the streamed boundary
  resolves. The API's own 404 is correct. Reasoning in ADR-018.
- **No deployment.** The application is intended to run locally.

## Future work

Retrieval with embeddings (pgvector) — justified only once retrieval recall is
measured and falling, not before; a durable job queue; the optional
clarification question, deliberately deferred until the required flow was
complete and tested (ADR-013); theme clustering across requests; token cost
instrumentation; a frontend component test framework; and a prioritisation model
once impact, effort and strategic alignment data actually exist.

---

## AI-assisted development

This project was built with AI assistance, as the assessment permits. Every
prompt is logged in [`prompts.txt`](prompts.txt) with a timestamp and a summary
of the actions taken, per the rule in [`CLAUDE.md`](CLAUDE.md), mirrored in
[`AGENTS.md`](AGENTS.md) for other tools. The log is append-only, contains no
credentials, and records what was *not* done as well as what was — including
the pending smoke test, and the bugs that review passes found in work from
earlier phases.
