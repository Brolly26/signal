# Success metrics

Three metrics, with how each would actually be collected and what would make it
misleading. No business-impact figure is claimed anywhere in this project,
because there is no data from real users to support one.

---

## 1. Triage time

**The question it answers.** How long does it take for an incoming request to
become a decision a product manager has made?

**Why it is reported as three numbers, not one.** Mixing them would let a fast
model hide a slow review queue, or make a reviewer's lunch break look like model
latency. They have different owners and different fixes.

| Interval | Measured as | Owned by |
|---|---|---|
| Queue wait | `ai_analyses.created_at` → `started_at` | the backlog / infrastructure |
| **AI processing time** | `ai_analyses.started_at` → `completed_at` | the model and the prompt |
| **Time to human decision** | `ai_analyses.completed_at` → the first `review_decisions.created_at` for that analysis | the reviewer's workload |

Triage time is the sum. `latency_ms` is also stored per analysis, which is the
same interval measured inside the process — useful for separating model time
from database and scheduling time.

```sql
-- the three intervals, per analysis
SELECT a.id,
       a.started_at   - a.created_at              AS queue_wait,
       a.completed_at - a.started_at              AS ai_processing,
       min(d.created_at) - a.completed_at         AS time_to_decision
  FROM ai_analyses a
  LEFT JOIN review_decisions d ON d.analysis_id = a.id
 WHERE a.status = 'completed'
 GROUP BY a.id;
```

**What would make it misleading.** Comparing it to "the manual process" without
measuring the manual process first. We have not measured it. The honest
comparison is a baseline collected from the team that would adopt this —
timestamps on when a request arrived and when it was decided, for a few weeks,
before Signal is introduced.

**What a good result looks like.** AI processing time in seconds is not an
achievement; it is the floor. The number that matters is whether time to human
decision falls, because that is the one the AI is supposed to help with — a
reviewer with the evidence in front of them should decide faster than one
reading raw feedback.

---

## 2. Duplicate recommendation precision

**The question it answers.** When Signal says two requests are duplicates, how
often is it right?

**Measured as.** On a labelled set, at the **candidate level**, not the request
level:

```
precision = duplicates correctly proposed / all duplicates proposed
```

`backend/evals/run_eval.py` computes this and prints the sample size beside it.
Recall is printed too, but precision is the one to optimise: a false duplicate
sends a product manager to consolidate two things that should ship separately,
which costs more than a duplicate they spot themselves later.

**What would make it misleading.**

- **Quoting a figure from ten cases as production accuracy.** The eval set is a
  regression signal — "did this prompt change make things worse". The report
  prints `(n=…)` on every figure to keep that visible.
- **Measuring mock mode.** In mock mode the model's answer is scripted, so
  precision measures the pipeline, not judgement. The report labels the mode on
  every run.
- **Scoring against labels one person wrote.** Whether two requests are "the
  same need" is a judgement. A real measurement needs several people labelling
  independently and an agreement rate reported alongside the precision.

**In production it would come from the decisions themselves**, not from a
labelled set: a proposed `DUPLICATE` that a reviewer approved is a true
positive, one they rejected is a false positive. That is a continuous
measurement on real traffic, and it is the reason `review_decisions` keeps every
decision with its reason.

---

## 3. Human recommendation acceptance rate

**The question it answers.** Do reviewers trust what Signal proposes?

**Measured as.** Approvals over all decisions about proposed relationships,
from the audit log:

```sql
SELECT count(*) FILTER (WHERE latest.decision = 'APPROVED')::float
       / nullif(count(*), 0) AS acceptance_rate
  FROM (
    SELECT DISTINCT ON (d.analysis_id, d.related_request_id) d.decision
      FROM review_decisions d
     WHERE d.related_request_id IS NOT NULL
     ORDER BY d.analysis_id, d.related_request_id, d.created_at DESC, d.id DESC
  ) AS latest;
```

`DISTINCT ON` collapses the append-only log to the current decision per pair, so
a reviewer changing their mind counts once rather than twice.

**What would make it misleading.** Reading it as a quality score in either
direction.

- A **high** rate can mean the recommendations are good, or that reviewers are
  rubber-stamping. The counterweight is precision measured against labels, and
  the rate of reversals — a decision changed later is a signal the first one was
  not considered.
- A **low** rate can mean the model is wrong, or that it is surfacing genuinely
  arguable relationships, which is useful. Reading the `reason` field is how you
  tell those apart, which is why the field exists.

It also only becomes meaningful with volume. On ten decisions it is noise.

---

## What is instrumented today

Per analysis, in `ai_analyses`: `created_at`, `started_at`, `completed_at`,
`latency_ms`, `model_name`, `retrieval_mode`, `tool_calls_count`,
`fabricated_refs_count`, `error_code`.

That is enough to compute every metric above, plus two operational ones worth
watching: the failure rate by `error_code`, and the share of analyses that fell
back to backend retrieval instead of the model searching for itself.

**No prompt text and no user content is stored in those columns.** The point of
the observability is to answer "is this working", not to build a transcript
archive.

## What is not instrumented, and would need to be

- **Per-request token cost.** The provider returns usage on every call and it is
  not currently recorded. It belongs next to `latency_ms`, and without it the
  cost side of any model comparison is guesswork.
- **Reversal rate** — decisions later changed. The data is in the log; nothing
  reads it yet.
- **Retrieval recall** — how often the request that *should* have been found was
  in the candidate set at all. This is the number that would justify adding
  embeddings, and measuring it needs a labelled set larger than the current one.
