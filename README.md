# Signal

**Turn customer feedback into product clarity.**

Signal is an AI-first Feature Intelligence System. Product teams receive feature
requests as unstructured text; Signal reads each one, works out the underlying
customer problem, searches the existing requests for related work, and proposes
relationships with the evidence behind them — for a human to approve or reject.

The operating principle is **AI recommends, humans decide**. The model never
deletes, merges or alters a request.

> **Status: Phase 1 of 7 complete.** Working today: submitting a feature
> request, validation, persistence in PostgreSQL, `POST`/`GET /api/requests`,
> and the Request Board and submission form in Next.js with loading, empty,
> validation-error, success and API-unavailable states.
>
> Not built yet: voting, search, AI analysis, and the review queue. Sections
> marked *(planned)* are not implemented. A new request reports
> `analysis_state: "not_requested"` — no analysis is queued, because the
> orchestrator that would process it does not exist yet.

---

## The problem

A product team with real customers accumulates feature requests faster than
anyone can read them. The same need arrives worded five different ways; the
request a customer writes ("I need an Excel export") is often a solution they
guessed at, not the problem they have ("every Friday I have to send a sales
report to my manager"). Triage becomes repetitive reading, and the loudest
request wins by default.

Signal targets the step with the most repetitive manual work and the highest
error rate: **turning an incoming request into a reviewable recommendation** —
what the customer actually needs, which existing requests relate to it, and why.

## Who benefits

| Role | What they get |
|---|---|
| **Requester** (customer or internal stakeholder) | Submits a request, finds existing ones, shows support, sees status |
| **Product Manager** | A queue of AI-prepared recommendations with evidence, approved or rejected one candidate at a time, with a decision history |

## Where human judgement stays

The model proposes; it has no write access to the database and no ability to
merge or remove anything. Every relationship between requests becomes real only
when a person approves it, and the approval is recorded with its reason and
timestamp. Strategic prioritisation is explicitly out of scope for the model:
Signal surfaces demand signals (support count, related requests, recency), not a
fabricated priority score.

---

## Architecture

```
Next.js 16 (App Router, TypeScript, Tailwind)      :3000
        │  HTTP/JSON, CORS restricted to localhost
        ▼
FastAPI                                            :8000
  api/routes    validation, status codes
  services      business rules
  repositories  SQL
  services/ai   LLM orchestration  ──────►  Anthropic API (claude-sonnet-5-5)
  tools         read-only search tool
        │
        ▼
PostgreSQL (Supabase or local Docker) + full-text search
```

The frontend never holds the Anthropic key or a database credential. Every LLM
call originates in the backend.

Decisions and trade-offs are recorded in [`docs/decisions.md`](docs/decisions.md).

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
ANTHROPIC_API_KEY=       # required only for AI analysis (planned)
LLM_MODEL=claude-sonnet-5-5
```

Both files are git-ignored. `.env.example` contains no secret values.

> Using the Supabase **pooler** (port 6543)? Also set
> `DB_STATEMENT_CACHE_SIZE=0` — pgbouncer in transaction mode is incompatible
> with asyncpg's prepared-statement cache. The direct connection on port 5432
> needs no change.

### 3. Migrations

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt                        # macOS/Linux
cd .. && backend/.venv/Scripts/python scripts/migrate.py
```

`scripts/migrate.py --status` lists what is applied. Each migration is recorded
with a checksum, so an already-applied file cannot be edited silently.

### 4. Backend

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
curl http://127.0.0.1:8000/api/health
```

```json
{"status":"ok","database":"connected","schema_version":"0001_init",
 "ai_provider_configured":false,"environment":"local"}
```

### 5. Frontend

```bash
cd frontend && npm install && npm run dev
```

---

## Environment variables

| Variable | Where | Required | Purpose |
|---|---|---|---|
| `DATABASE_URL` | backend | yes | PostgreSQL connection string |
| `DB_STATEMENT_CACHE_SIZE` | backend | no (default 100) | set to `0` behind the Supabase pooler |
| `ANTHROPIC_API_KEY` | backend | for AI analysis | never exposed to the frontend |
| `LLM_MODEL` | backend | no (default `claude-sonnet-5-5`) | model id |
| `LLM_TIMEOUT_SECONDS` | backend | no (default 45) | per-request timeout |
| `APP_ENV`, `CORS_ORIGINS` | backend | no | environment label, allowed origins |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | yes | backend base URL (public by design) |

---

## Data model

Six tables. The integrity rules are the point, so they live in the database
rather than only in application code.

| Table | Holds | Key guarantees |
|---|---|---|
| `feature_requests` | the requests | length checks, status enum, generated `tsvector` + GIN index |
| `request_votes` | support | `UNIQUE (request_id, voter_key)` |
| `ai_analyses` | one row per analysis run | `completed` must carry a verdict, `failed` must carry an error code, **at most one `pending` per request** (partial unique index) |
| `analysis_related_requests` | one row per suggested candidate, with its evidence | foreign key to a real request — a fabricated id cannot be stored |
| `review_decisions` | append-only human decision log | never updated or deleted |
| `request_links` | human-approved relationships | `CHECK (request_a_id < request_b_id)` + `UNIQUE` pair: no self-links, no reversed duplicates |

Relationships are relational rather than JSON specifically so that every
AI-suggested reference is protected by a foreign key.

---

## AI flow *(planned — Phase 3)*

1. The request is validated and **persisted before** any AI call.
2. A `pending` analysis row is created; the HTTP response returns immediately.
3. A bounded loop (max 3 iterations, max 4 tool calls) offers the model one
   read-only tool, `search_existing_requests`.
4. Tool arguments are validated; results are capped at 8 rows and limited to
   `id`, `title`, truncated `description` and vote count.
5. The ids the tool returned are recorded as an allowlist.
6. The final JSON is constrained with `output_config.format` and validated again
   with Pydantic.
7. Every referenced id is checked against the allowlist. Unverifiable
   references are discarded and counted; if *all* of them are invalid the
   analysis fails as `FABRICATED_REFERENCES` rather than being reported as
   "nothing related".
8. The analysis is persisted for human review.

If the provider is unavailable, times out, refuses, or returns invalid output,
the request stays saved, the analysis is marked `failed` with a specific error
code, and it can be retried. The system never fabricates an AI recommendation.

User-submitted text is treated as untrusted data and never as instructions.

---

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest          # requires a running database
cd frontend && npx tsc --noEmit && npm run build
```

Integration tests run against a real PostgreSQL instance, because most of them
verify constraints and indexes that only Postgres enforces. Each test runs
inside a transaction that is rolled back, so order never matters.

**Latest run — 84 passed.** Schema constraints, vote uniqueness, the
one-pending-analysis index, foreign-key rejection of fabricated references, link
pair normalisation, interruption recovery for both "never started" and "started
and hung", transactional retry, two concurrent retries where only one may win,
request validation (lengths, blank input, unknown fields, control-character
stripping), `POST`/`GET /api/requests`, persistence verified directly in
PostgreSQL, the absence of any queued analysis row, pagination bounds, and the
503 and 500 error paths asserting no credential appears in the response.

The frontend is verified with `npx tsc --noEmit` and `npm run build`, plus a
live end-to-end check of all four board states against a running backend.

An evaluation suite over labelled cases, including adversarial input and prompt
injection, is planned for Phase 5.

## Proposed success metrics

Three metrics, with how each would be collected. No business-impact figure is
claimed without data from real users.

1. **Triage time** — submission to human decision, reported as a composition of
   queue wait, AI processing time (`started_at` → `completed_at`) and time to
   human decision (`completed_at` → first decision). The automated step and the
   human step are reported separately so neither hides the other.
2. **Duplicate recommendation precision** — on a labelled set, the share of
   candidates proposed as `DUPLICATE` that really are duplicates. Reported with
   the sample size, as a regression measurement rather than production accuracy.
3. **Human recommendation acceptance rate** — `APPROVED` over total decisions,
   derived from `review_decisions`. It measures whether reviewers trust the
   recommendations; a low rate is a signal about the model, a high rate alone
   does not prove correctness.

---

## Security and limitations

**In place today.** Credentials only in the backend and only from the
environment; `.env` git-ignored and `.env.example` free of secrets; the health
endpoint returns whether a key is configured, never the key; database
exceptions are logged without their messages, because a connection error can
carry the DSN; input length and enum constraints enforced by the database; CORS
restricted to the configured origin.

**Explicitly not production-ready.**

- **No authentication.** Vote identity is a browser-scoped key — it prevents
  accidental double voting, nothing more. The review queue is an open route with
  a visible notice saying so. Production needs real identity (OIDC/SSO),
  role-based authorisation on every mutating endpoint, rate limiting and audit
  logging tied to real user ids.
- **Background tasks run in the API process.** An analysis does not survive a
  restart; it is recovered as a failure and must be retried. A real job queue is
  the correct next step.
- **Five high-severity npm advisories**, all in the lint toolchain
  (`eslint-config-next` → `fast-glob` → `micromatch` → `braces`). Runtime
  dependencies are only `next`, `react` and `react-dom`. `npm audit fix` does
  not resolve them and `--force` breaks the config. Accepted and documented.
- **Logs are not redacted.** Database errors are logged as an exception type
  without a traceback, because a connection error's message can contain the
  DSN. The handler for genuine bugs does keep the traceback, since without it
  nothing is diagnosable — so a production deployment needs a redacting log
  handler in front of it.
- **No deployment.** The application is intended to run locally.
- Evaluation numbers come from a small labelled set and are regression
  signals, not measured production accuracy.

## Future work

Retrieval with embeddings (pgvector) — justified only if evaluation shows
candidate recall falling below roughly 0.8 or the dataset growing past a few
hundred requests; a durable job queue; the optional clarification question;
theme clustering across requests; and a prioritisation model once impact,
effort and strategic alignment data actually exist.

---

## AI-assisted development

This project was built with AI assistance, as the assessment permits. Every
prompt is logged in [`prompts.txt`](prompts.txt) with a timestamp and a summary
of the actions taken, per the rule in [`CLAUDE.md`](CLAUDE.md) and mirrored in
[`AGENTS.md`](AGENTS.md) for other tools. The log is append-only and contains no
credentials.
