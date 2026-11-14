# OpsRelay AI diagnosis and repair

Takes a failed CI run for a demo app and returns either a **verified** minimal patch
or a **needs_review** result with a reason.

```
investigator.py  logs + diff + source + prior incidents -> grounded Diagnosis
patcher.py       Diagnosis -> minimal unified diff, scope-checked
verifier.py      failed commit in a temp copy: reproduce -> apply -> rerun suite
pipeline.py      repair_ci_failure(): the one call the orchestrator makes
```

No GitHub or MongoDB writes happen here. The orchestrator stores
`result.model_dump()`, and the PR module opens a PR from a verified `result.patch`.

## Setup

```bash
uv venv --python 3.12 agents/.venv            # or: python3.12 -m venv agents/.venv
uv pip install --python agents/.venv/bin/python -r agents/requirements.txt
export OPENAI_API_KEY=...                     # optional for tests; required for live use
export OPENAI_MODEL=gpt-4.1-mini              # optional, this is the default
```

## Tests

```bash
agents/.venv/bin/python -m pytest agents/tests -q
```

Replay the Shopping discount regression end to end, printing the failing-before and
passing-after pytest output and the patch:

```bash
agents/.venv/bin/python -m pytest agents/tests/test_shopping_repair.py::test_shopping_discount_failure_reproduced_then_fixed -s -q
```

These tests use a stub model. `test_live_openai.py` runs the same regression
against the real model and is skipped unless `OPENAI_API_KEY` is set.

## Interface for the orchestrator

```python
from agents import PriorIncident, RepairRequest, repair_ci_failure

result = repair_ci_failure(RepairRequest(
    app="shopping",                        # key in agents/apps.toml
    repo_path="/srv/opsrelay/checkouts/demo",  # local clone containing commit_sha (read only)
    commit_sha="3f2c9e1",
    failed_test_logs=job_log_text,         # raw GitHub Actions job log
    recent_diff=diff_text,                 # e.g. `git diff <parent> <sha>`
    source_files={"shopping/src/shopping/cart.py": cart_source_at_sha},
    similar_incidents=[PriorIncident(id="inc-42", summary="...", root_cause="...")],
))

result.outcome          # "verified" | "needs_review"
result.reason           # why, in one line
result.diagnosis        # likely_cause, explanation, evidence[], suspect_files, failing_tests
result.patch            # unified diff against commit_sha (git apply -p1)
result.changed_files
result.baseline         # failing tests on the original code (TestRun)
result.reproduce_after_patch
result.patched_suite    # full app suite after the patch
```

`repair_ci_failure(request, llm=...)` accepts any object with
`generate(schema, system, user)` in place of the default OpenAI model.
Model and API errors are raised so the orchestrator can apply its own retry policy.

## Safety rules

- **Evidence is grounded.** Each excerpt must appear verbatim in the input it cites.
  At least one must come from the current run, because similar incidents are hints,
  not proof. Suspect files must be supplied and sit inside the app's source dirs.
  If any of these checks fails, the result is `needs_review`.
- **Untrusted input stays data.** CI logs, diffs, source and incidents reach the
  model inside delimited blocks. Attempts to close a block early are neutralised.
- **Patches stay in scope.** A patch may only make in-place edits to existing files
  under `source_dirs`, with at most 3 files and 60 changed lines. Tests, CI config,
  dependency or build manifests, secrets, hidden files, new or deleted files,
  renames, mode changes and binary patches are all rejected. The verifier checks
  scope again before applying anything.
- **Verification is isolated.** The verifier exports the commit with `git archive`
  into a temp directory, so the developer's checkout is never modified. It runs
  pytest with a scrubbed environment (no API keys, `MONGODB_URI` or `GITHUB_TOKEN`),
  using fixed arguments from `apps.toml` plus validated test ids, under a timeout
  that kills the whole process group.
- **Not a container.** Tests can still reach the network, and the app's
  dependencies must be installed in the verifier's Python environment.

## App configuration

`apps.toml` sets each app's root, editable `source_dirs`, `test_dir`, fixed
`pytest_args` and timeout. The Shopping and Messaging entries are placeholders until
the demo apps land in `shopping/` and `message-platform/`.
