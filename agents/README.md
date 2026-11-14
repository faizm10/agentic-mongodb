# OpsRelay agents

## MongoDB & workflow recovery (Mo)

| File | What it does |
|---|---|
| `mongodb.py` | Atlas connection, `incidents` / `workflow_steps` / `memories` collections, JSON-schema validation, unique dedup index, TTL index, Vector Search index, change stream helper |
| `memory.py` | Voyage embeddings (offline fallback), `find_similar` via `$vectorSearch` scoped per app, `remember_fix`, `seed_memory`, `forget` |
| `orchestrator.py` | Stage machine with leases, fenced writes, PR-intent-before-GitHub, verify-after-send, resume, kill switch, CLI |
| `fakes.py`, `dev_demo.py` | Fake GitHub/LLM so the full flow + crash demo runs before the other modules land |

### Stages (incident.stage = last *verified* stage)
`detected → memory_retrieved → patch_verified → pr_intent_saved → pr_confirmed → memory_saved`

### Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill MONGODB_URI
python -m agents.mongodb            # creates collections, indexes, vector index
python -m pytest agents/tests -q    # runs offline with mongomock
```

### Crash demo (fake GitHub, real Atlas)
```bash
python -m agents.dev_demo reset
OPSRELAY_CRASH_AT=pr_created python -m agents.dev_demo run   # PR created, then worker dies
python -m agents.dev_demo run                                # new worker resumes, adopts same PR
python -m agents.orchestrator status "faizm10/agentic-mongodb#9000001.1"
```

### Interfaces the other modules must provide
See the docstring at the top of `orchestrator.py`. Summary:

- **Hamza** – `monitor.poll_failed_runs() -> [FailedRun]`, `github.find_pr(repo, branch)`, `github.open_draft_pr(repo, branch, base_sha, title, body, patch)` (idempotent on branch; commits `patch["files"]`).
- **Faiz** – `repairer.repair(failed_run, similar) -> {tests_passed, test_output, diff, files, changed_files, summary, root_cause}`.
- Wire the real classes in `_build_real()` in `orchestrator.py`.
