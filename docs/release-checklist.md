# Publication checklist

Run before making the repository public. **Nothing here has been pushed**; the
repository has no remote configured.

Every command below is one you can run yourself and read the output of. Where a
check was already executed, the measured result is recorded beside it with the
date, so you can re-run it rather than take it on trust.

---

## 1. Secrets — the whole history, not just the working tree

A file deleted in a later commit is still in the history. These scan every blob
in every commit.

```bash
# Was a real .env ever added, in any commit?
git log --all --diff-filter=A --name-only --format="" | sort -u | grep -E "(^|/)\.env($|\.)"

# Anthropic key pattern, across every commit
git grep -I -n -E "sk-ant-[A-Za-z0-9_-]{20}" $(git rev-list --all)

# Any database URL carrying a real password
git grep -I -n -E "postgresql://[^:]+:[^@/]+@" $(git rev-list --all) | grep -v "signal:signal@localhost"

# The Supabase project identifier
git grep -I -n "<your-project-ref>" $(git rev-list --all)
```

**Result on 2026-10-16, 37 commits:**

| Check | Result |
|---|---|
`.env` ever committed | **no** — only `.env.example` |
Anthropic key pattern | **no match anywhere in history** |
Non-local database URL | only the `<password>` / `<project-ref>` placeholder in `.env.example` |
Real Supabase project ref | **never committed**; present only in `backend/.env`, which is git-ignored and untracked |

```bash
git check-ignore -v backend/.env frontend/.env.local   # both ignored
git ls-files --error-unmatch backend/.env              # must error: not tracked
```

> If a secret is ever found in history, rotating the credential comes first.
> Rewriting history with `git filter-repo` does not help once a repository is
> public — assume anything pushed was captured.

## 2. What ships

```bash
git ls-files | wc -l                    # 104 files as of 2026-10-16
git ls-files | grep -cE "node_modules|\.venv|\.next"   # must be 0
du -sh .git                             # repository size is sane
```

- [ ] `README.md` states what is verified and what is not
- [ ] `docs/decisions.md` — 19 ADRs, including the corrections
- [ ] `docs/architecture.md`, `docs/metrics.md`
- [ ] `backend/evals/RESULTS.md` keeps both measured runs, v1 and v2
- [ ] `prompts.txt` — the full AI collaboration log, append-only
- [ ] `CLAUDE.md` and `AGENTS.md` carry the required prompt-logging rule

## 3. Reproducibility for someone who clones it

- [ ] `.env.example` lists every variable with no real value
- [ ] `docker compose up -d --wait db` works from a clean checkout
- [ ] `scripts/migrate.py` applies the schema to an empty database
- [ ] `supabase/seed.sql` is idempotent
- [ ] The README's run instructions match the actual commands
- [ ] The application starts and the board renders **without** an API key —
      analysis reports `not_requested`, nothing hangs

## 4. Quality gates, re-run immediately before publishing

```bash
cd backend && .venv/Scripts/python -m pytest          # 224 passed
cd backend && .venv/Scripts/python evals/run_eval.py  # 10/10 mock
cd frontend && npx tsc --noEmit && npm run build      # exit 0
```

> Run the suite against the **local** database, not the demo one. With
> `DATABASE_URL` pointing at Supabase, inject `LOCAL_DATABASE_URL` into the test
> process instead, as documented in `prompts.txt`.

## 5. Repository settings

- [ ] Name and description agree with the README
- [ ] Public, with no GitHub Actions secrets configured
- [ ] Default branch `main`
- [ ] No GitHub Pages or deployment enabled — this runs locally
- [ ] Commits carry the intended author identity

## 6. Create and push — only on explicit instruction

```bash
gh repo create signal --public --source=. --remote=origin \
   --description "AI-first Feature Intelligence System"
git push -u origin main
```

Confirmed on 2026-10-16: `gh` 2.102.0 is authenticated and the name `signal`
was free. **Not executed.**

## 7. After publishing

- [ ] Clone into a clean directory and follow the README end to end
- [ ] Confirm no `.env` arrived in the clone
- [ ] Rotate the Anthropic key and the Supabase database password if either was
      ever pasted anywhere outside `backend/.env`

---

## Known limitations to state when sharing

Already documented in the README, repeated here so they are not forgotten in
the handover: no authentication; background tasks run in the API process and do
not survive a restart; logs are not redacted; per-request token cost is not
recorded; five dev-only npm advisories in the lint toolchain; the detail page
returns 404 content with a 200 status; and no deployment.
