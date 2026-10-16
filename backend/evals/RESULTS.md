# Measured evaluation results

Every measured run, kept against the dataset version that produced it. Numbers
are never edited after the fact, and a label is never changed to improve one.

Read [`README.md`](README.md) first: **mock mode measures the pipeline, not
model judgement.** Only the LIVE rows below say anything about the model.

---

## LIVE — dataset v1 — 2026-10-16

Real model, `claude-sonnet-5-5`, effort `medium`.

```
Cases passed: 5/6
Duplicate recommendations (candidate level):
  proposed 2 | correct 2 | wrong 0
  precision 1.00  (n=2)
  recall    0.67  (n=3)
Unverifiable references discarded: 0
Latency: mean 6805 ms | max 8237 ms  (n=6)
Retrieval: agentic on all 6 cases, 2 tool calls each
```

**The one failure — `duplicate-different-words`:**

```
expected DUPLICATE | got RELATED
  | 1 duplicate(s) missed
  | related set differed (expected none, got ['scheduled-report'])
```

This is the "Excel export" versus "scheduled report delivery" pair — the case
the product exists for.

**The label was wrong, not the model.** Our own system prompt reserves
`DUPLICATE` for requests representing essentially the same need, and defines
`RELATED` as a shared underlying problem where the requests "are not
interchangeable and could be delivered separately". An export button and a
scheduled email are not interchangeable, and either could ship without the
other. The v1 label contradicted the definition the model was given.

The model's reasoning on the same pair during the end-to-end journey made the
distinction explicitly, unprompted:

> "The two 'Scheduled report delivery' requests share the weekly Friday
> reporting problem, but they ask for automatic emailing on a schedule. That is
> a separate deliverable from an export, so I marked them related rather than
> duplicate."

**Provenance correction.** That example comes from our own Signal product
specification, not from the assessment brief. Earlier wording of mine implied
the brief supplied it; that was wrong.

---

## LIVE — dataset v2 — 2026-10-16

Same model, same prompt, same configuration. The **only** change is the label
for `duplicate-different-words`, corrected from `DUPLICATE` to `RELATED` with
the justification above. The system prompt was deliberately left untouched.

```
Cases passed: 6/6
Duplicate recommendations (candidate level):
  proposed 2 | correct 2 | wrong 0
  precision 1.00  (n=2)
  recall    1.00  (n=2)
Unverifiable references discarded: 0
Latency: mean 6816 ms | max 8718 ms  (n=6)
Retrieval: agentic on all 6 cases, 2 tool calls each
```

### This is not a model improvement

**The model's answer did not change.** It returned `RELATED` for that case in
both runs. What changed is that the dataset now agrees with the definition the
model was working from.

Stating it precisely, because the difference between these two readings is the
whole point:

- **Not true:** "accuracy went from 5/6 to 6/6"
- **True:** "a labelling error was found by a live run and corrected; the model
  answered identically before and after"

The v1 numbers stay above for exactly this reason. If this project is ever
presented, 5/6 and the reason for it is the more informative result, and the
disagreement between a label and a prompt is a finding worth more than a clean
score.

### Sample limitations, unchanged by the correction

- **Six live cases. Two duplicate proposals.** `n=2` cannot support a precision
  claim. These are regression signals.
- **One person wrote the labels** — and that is precisely how the v1 error got
  in. A real measurement needs several independent labellers and a reported
  agreement rate.
- **No false positives have been observed yet**, which says more about the small
  sample than about the model: the set contains only one case designed to tempt
  a false duplicate (`related-not-duplicate`).
- **Nothing here measures recall of the retrieval step** — whether the request
  that *should* have been found was in the candidate set at all. That is the
  number that would justify adding embeddings, and it is not measured.

---

## MOCK — dataset v2 — 2026-10-16

```
Cases passed: 10/10
precision 1.00 (n=2) | recall 1.00 (n=2)
Latency: mean 4 ms (no network in the path)
```

**These numbers describe the pipeline, not the model.** The model's answers are
scripted here. What passing means: reference checking against the allowlist,
the derived classification and recommendation, the failure codes for a timeout,
a dead provider and unusable output, and the defusing of a forged
`</untrusted_request>` marker in submitted text.
