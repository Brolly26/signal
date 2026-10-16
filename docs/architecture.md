# Architecture

How Signal is put together, and where the guarantees actually live. The
reasoning behind each choice, with its alternatives and costs, is in
[`decisions.md`](decisions.md).

## The shape of it

```
Next.js 16 (App Router, TypeScript, Tailwind)                      :3000
  Server Components read data; two client components write it
        |
        |  HTTP/JSON. CORS restricted to the configured origin.
        |  The browser holds no database credential and no API key.
        v
FastAPI                                                            :8000
  api/routes      validation, status codes, background scheduling
  services        business rules, units of work
  repositories    SQL
  services/ai     the analysis loop and the provider adapter
  tools           the one read-only tool the model may call
        |                                    |
        v                                    v
PostgreSQL 16 (Supabase or Docker)    Anthropic API
  constraints, full-text search         claude-sonnet-5-5
```

## Where each guarantee lives

This is the part worth reading. Most of what makes the system trustworthy is
not in the application code.

| Guarantee | Enforced by |
|---|---|
| One support per browser identity per request | `UNIQUE (request_id, voter_key)` |
| An AI-suggested relationship must point at a real request | foreign key on `analysis_related_requests.related_request_id` |
| At most one analysis in flight per request | partial unique index `ai_analyses_one_pending_per_request` |
| A completed analysis carries a verdict | `CHECK (status <> 'completed' OR classification IS NOT NULL)` |
| A failed analysis says why | `CHECK (status <> 'failed' OR error_code IS NOT NULL)` |
| No self-links, no reversed duplicate links | `CHECK (request_a_id < request_b_id)` + `UNIQUE` pair |
| The model cannot write to the database | it is given one tool, and that tool only runs `SELECT` |
| The model cannot invent a reference | the allowlist of ids a search actually returned, then the foreign key |
| A decision is never lost | `review_decisions` is append-only; nothing updates or deletes it |

Application-level checks that cannot be expressed as constraints — a candidate
that is not the analysed request, a decision about a relationship the analysis
never proposed — are in the service layer with a test each.

## The analysis flow

```
POST /api/requests
  |
  |-- 1. Pydantic validates. Lengths mirror the database CHECKs; control
  |      characters and terminal escape sequences are stripped whole.
  |-- 2. INSERT feature_requests                      <- committed before any AI call
  |-- 3. INSERT ai_analyses (status='pending')        <- only if a provider is configured
  |-- 4. 201 returned immediately
  |
  '-- background task:
        |
        |-- mark started (conditional on still being pending)
        |
        |-- bounded loop: max 3 iterations, max 4 tool calls, 45s per request
        |     |
        |     |  tools    = [search_existing_requests]  (strict, read-only)
        |     |  format   = the analysis JSON schema
        |     |  effort   = medium
        |     |
        |     |-- stop_reason tool_use  -> validate args, run SELECT, cap at 8 rows,
        |     |                            record returned ids in the ALLOWLIST,
        |     |                            return all tool_results in one message
        |     |-- stop_reason end_turn  -> parse and validate the JSON
        |     |-- refusal / max_tokens / unknown -> specific failure
        |
        |-- if the model never searched: retrieve candidates in the backend using
        |     the need it just extracted, and ask once more. retrieval_mode is
        |     recorded as backend_fallback rather than presented as tool calling.
        |
        |-- validate: Pydantic, then the allowlist, then self-reference.
        |     every candidate invalid -> FABRICATED_REFERENCES (never DISTINCT)
        |-- derive classification and recommendation from the surviving candidates
        '-- persist, conditional on the row still being pending
```

Every exit path writes a terminal state. A request can only be left `pending` by
the process dying, and the startup sweep then fails those as `INTERRUPTED` so
they are visibly retryable.

## Why the model is given so little

The model decides one thing: what to search for, and whether to search again.
It does not decide the headline classification (derived), the recommendation
(a mapping), which requests exist (checked), or what happens next (a human).

That is not timidity about model capability. It is that each of those would be a
place where a plausible-sounding wrong answer could not be caught. The
classification is derived because a model asked for both a verdict and its
evidence can return a verdict its evidence contradicts.

## Request lifecycle

```
open ──(a human approves or dismisses an analysis)──> triaged
     └─ planned / declined: reserved for a product manager's own workflow,
        not set by anything automatic
```

`analysis_state` on a request is derived, not stored: a lateral join to the most
recent `ai_analyses` row, with `not_requested` when there is none. There is no
denormalised copy to drift.

## What runs where

| Concern | Where | Why |
|---|---|---|
| Reading the board | Server Component, uncached fetch | a reload always reflects the database |
| Search and sorting | plain GET form and links, no JavaScript | the URL is the state, so it is shareable and works before hydration |
| Support toggle | client component | needs browser storage for the demo identity |
| Decisions | client component | needs to post and then refresh the server render |
| Everything else | server | no secret and no business rule reaches the browser |

## Known structural limits

- **`BackgroundTasks` runs in the API process.** An analysis does not survive a
  restart; it is recovered as a failure, not resumed. A durable queue is the
  correct next step.
- **One connection is held for the duration of an analysis**, because the search
  tool queries during the model's turns. With a 45-second ceiling and a pool of
  ten that is fine here; a worker with its own pool is the production answer.
- **The review queue issues one query per item** for its candidates, bounded by
  the page size. One grouped query would remove it; at 25 rows it has not earned
  the complexity.
- **No authentication.** See the README's limitations section for what
  production would require.
