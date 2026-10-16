# Evaluation suite

```bash
cd backend
.venv/Scripts/python evals/run_eval.py          # mock mode, free
.venv/Scripts/python evals/run_eval.py --live   # real model, costs money
```

Exit code 0 means every case matched its label.

## What the two modes measure, and what they do not

**Mock mode** replays a scripted model answer for each case. It measures the
**pipeline**:

- reference checking — a cited id that no search returned is discarded and
  counted, and a case where *every* reference is invented fails as
  `FABRICATED_REFERENCES` instead of being reported as "nothing related"
- the derived classification and recommendation
- the failure codes for a timeout, a provider error and unusable output
- prompt framing — the injection case asserts that a closing
  `</untrusted_request>` marker inside submitted text was defused

It measures **nothing** about model judgement. A precision of 1.00 in mock mode
means the pipeline handled the scripted answer correctly, not that a model
would produce it. The report says so on every run.

**Live mode** sends each case to the configured model and scores its answers
against the labels. The set is small on purpose, so the result is a **regression
signal** — "did this prompt change make things worse" — not a measurement of
production accuracy. Every figure prints its sample size next to it for that
reason.

Live mode skips the cases marked `mock_only`, which script a specific provider
failure or a specific invalid response: those outcomes cannot be requested from
a real model on demand.

## The cases

| Case | What it checks |
|---|---|
| `duplicate-obvious` | the same request, almost the same words |
| `duplicate-different-words` | the same underlying need with no shared vocabulary — "Excel export" against "scheduled report delivery". The case the product exists for |
| `related-not-duplicate` | a shared problem area that must **not** be called a duplicate. A false duplicate costs a product manager more than a missed one |
| `distinct` | nothing relates; an empty answer must be acceptable |
| `vague` | too little detail to compare, so `NEEDS_CLARIFICATION` |
| `adversarial-prompt-injection` | submitted text that tries to issue instructions, including a forged closing marker |
| `adversarial-invented-reference` | every cited id invented, so the run must fail rather than present an unverifiable answer |
| `adversarial-invalid-output` | the response is prose, not the required JSON |
| `adversarial-provider-down` | the provider is unreachable |
| `adversarial-timeout` | the provider does not answer in time |

The data is fictional and written for this assessment.

## How it runs

Each case seeds its own fixtures, runs the real orchestrator against a real
PostgreSQL instance, and is then rolled back. Nothing is left behind and no case
depends on what another case or the demo data put in the database.

## What is deliberately absent

- **No accuracy claim.** Ten cases cannot support one.
- **No comparison between `agentic` and `backend_fallback` retrieval yet.** That
  comparison is the promise made in ADR-004 and it needs live runs to mean
  anything: in mock mode both paths replay the same scripted answer, so
  comparing them would measure nothing. `ai_analyses.retrieval_mode` already
  records which path produced every analysis, so the data for it is being
  collected.
- **No latency figure from live runs yet**, because the authenticated smoke test
  has not been run. Mock-mode latency is the pipeline's own overhead with no
  network in it, which is why it reads in single-digit milliseconds.
