# Working in this repo

Instructions for coding agents (Claude Code, Codex). Keep changes small and the output short.

## Code
- Write the least code that does the job. Reuse what exists (helpers, clients, components) before adding new ones.
- No speculative abstractions, config options, fallbacks or "just in case" branches.
- Match the surrounding style. Comments only where the why is not obvious; no docstrings restating the name.
- Edit in place. Don't rewrite whole files or reformat code you didn't change.

## Tests
- Don't add tests by default. Add one only for new behaviour that can break silently (data integrity,
  idempotency, money, crash recovery) or when asked.
- One focused test per behaviour. Extend an existing test file before creating a new one.
- No tests for trivial getters, wiring, UI layout or copy.
- Run only the affected suite while iterating; run the full suites once at the end.

## Tokens
- Read only the files and line ranges you need; search before reading.
- Don't re-read files you just wrote. Don't paste large outputs; use `tail`, `grep` or `-q`.
- Keep reports brief: what changed, what was run and its result, anything left to do.

## Layout
- `shopping/` NovaMart (FastAPI :8100, Next.js :3100) · `message-platform/` NovaConnect (:8200, :3200)
- `admin-dashboard/` OpsRelay uptime API (:8300) and dashboard (:3300) · `agents/` repair agent, recovery worker
- Architecture: `docs/system-architecture.md`. Start everything with `./dev.sh`; clear demo state with `./dev.sh reset`.
- Python suites: `<app>/backend/.venv/bin/python -m pytest -q`; agents: `agents/.venv/bin/python -m pytest agents/tests -q`.
  Dashboard types: `cd admin-dashboard/web && npx tsc --noEmit`.
