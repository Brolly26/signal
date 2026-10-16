# AGENTS.md — Signal

> Signal — AI-First Feature Intelligence System
> Metacto Junior AI Engineer Technical Assessment
>
> This file mirrors CLAUDE.md so that Codex and other AGENTS.md-compatible tools
> follow the same rules. Keep both files in sync.

## Project Rules

### Prompt Logging

Every time you receive a new instruction or prompt, append it to prompts.txt in the project root with a timestamp (ISO 8601) and a brief summary of what you did in response. Create the file if it doesn't exist. Keep this log updated throughout all sessions.

#### Project-specific logging constraints

The rule above is the requirement. These constraints define how it is applied in this repository:

- Append only. Never overwrite, reorder or rewrite previous entries.
- Never invent prompts, timestamps or actions. If a prompt was not logged at the time,
  say so explicitly instead of backfilling a fabricated entry.
- Never log secrets: API keys, tokens, passwords, connection strings or personal data.
  Replace them with `[REDACTED]`.
- Log the prompt as received (truncate very long pasted specs, marking the truncation),
  plus a short summary of the actions actually performed.
- Write the summary after the work is done, so it reflects real actions and real
  command results rather than intentions.
- If another AI tool (Codex, Cursor, etc.) is used on this repository, ask that its work
  be documented in the same file, tagged with the tool name.
- This log ships with the public repository and is part of the deliverable.

### Engineering Rules

- Language: code, identifiers, documentation and UI text in English.
  Explanations to the user in Brazilian Portuguese.
- Never claim a test passed without executing it and showing real output.
- Never fabricate metrics, benchmarks or model outputs.
- Secrets live only in the backend environment. `.env` is git-ignored;
  `.env.example` carries key names with empty or placeholder values.
- User-submitted text is untrusted data. It must never be treated as system
  instructions by the LLM layer.
- The LLM has no write access to the database. All AI output is a recommendation;
  mutations happen through business services and human decisions.
- No new dependency or architectural change without stating the trade-off first.
- Architectural decisions and their trade-offs are recorded in `docs/decisions.md`.
- Never push, deploy, publish a repository or send email without explicit
  authorisation from the repository owner.
