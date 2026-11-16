# OpsRelay: project overview (as of 2026-11-14)

OpsRelay is a hackathon demo of an **AI incident detection and repair system**. Two realistic demo apps run locally. An uptime monitor watches them, and GitHub Actions runs their tests. When something breaks, a Python AI agent investigates it, reproduces the failure, writes and verifies a minimal fix, and opens a **pull request that a human must review and merge**. The agent never merges anything itself. MongoDB Atlas holds the incident state, the repair memory and a reinforcement-learning fix policy.

Repo: `faizm10/opsrelay-agent` (branch `main`).

---

## 1. Components at a glance

| Component | Directory | Stack | Local URL |
| --- | --- | --- | --- |
| **NovaMart** (shopping demo app) | `shopping/` | Next.js 15 + TS + Tailwind, FastAPI, local JSON store | App :3100 · API :8100 |
| **NovaConnect** (messaging demo app) | `message-platform/` | Next.js 15 + TS + Tailwind + Motion, FastAPI + WebSockets, SQLite | App :3200 · API :8200 |
| **Uptime monitor and status dashboard** | `admin-dashboard/` | Python worker + FastAPI, Next.js dashboard, MongoDB or SQLite | Dashboard :3300 · API :8300 |
| **AI repair agents** | `agents/` | Python 3.12, OpenAI (`gpt-4.1` by default), MongoDB Atlas, Voyage AI embeddings (optional), GitHub API | CLI and worker |
| **CI** | `.github/workflows/` | GitHub Actions: `shopping-ci.yml`, `messaging-ci.yml` | — |

`./dev.sh setup && ./dev.sh` starts all six servers. If `MONGODB_URI` is set, it also starts the AI agent worker. `DEV_PORT_OFFSET` shifts the ports and `DEV_AGENT=0` skips the agent.

---

## 2. NovaMart (shopping app)

- Storefront: home page, search, filters, product pages, cart drawer, cart, checkout, order confirmation. 19 products in 6 categories.
- FastAPI backend: products, cart quote, checkout, orders and health endpoints. **Pricing is server-authoritative**, so a backend bug shows up in the UI as well as in the tests.
- Business logic: line totals, percentage promotions, shipping, tax, and an inventory oversell guard.
- **Payments are simulated.** Orders are demo records saved to a local JSON file.
- **Side-effect-free checkout health probe** (`/api/health/checkout`) that runs the real checkout logic.
- 88 pytest tests, plus an OpsRelay reporting plugin that writes JUnit output, a machine-readable report and probe JSON.
- `inject_bug.py` injects and resets reproducible bugs: `quantity` (line total off by one), `discount` (divides by the wrong number), `inventory` / `stock` (allows overselling), `images` (broken image URLs).
- Demo outages: `payment` (a simulated Stripe outage, which is external and has no code fix), `images`, `inventory`.

## 3. NovaConnect (messaging app)

- A real-time messenger: direct messages, group chats, replies, reactions, edits, deletes, pins, search, typing indicators, read receipts, presence, light and dark themes, and a mobile layout.
- REST to send (idempotent, acknowledged), WebSocket push for events. The client sends messages optimistically with an idempotency key and retries up to 3 times with backoff.
- Receipt state machine: sent → delivered → read. 6 seeded demo users, 5 DMs and 3 groups. No authentication: a "switch demo user" control picks your identity.
- **Functional monitoring probe** (`/api/monitor/probe`) that runs real business logic against an isolated in-memory database.
- 70 pytest tests.
- `inject_bug.py` bugs: `duplicate` (inverted idempotency guard), `status` (marks a message delivered when the recipient is offline), `ordering` (sorts by time of day, not the full timestamp).
- Demo outages: `send`, `receive`, `both`.

## 4. Uptime monitor and status dashboard

- A **worker probes both apps every 30 seconds** with three checks per service: website, availability (health endpoint) and functional (the business probe). Status is **Operational / Degraded / Down / Unknown**. An HTTP 200 alone does not count as healthy.
- Incident lifecycle: open → update → escalate → resolve, with one open incident per service. A lease ensures that only one worker runs at a time.
- Uptime is calculated on read: coverage, timeline bars and latency series. The charts are hand-built SVG.
- Storage: MongoDB Atlas (`uptime_*` collections) when `MONGODB_URI` is set, otherwise local SQLite.
- Each incident gets a **hand-off evidence document** (`opsrelay.uptime_incident.v1`) that the agents pick up.
- Dashboard pages: overview (`/`), service detail (`/services/[id]`), incident detail (`/incidents/[id]`).
- Dashboard features:
  - **Demo outage controls**: flip a flag file that the apps read on each request. No restart is needed.
  - **"Bad deploy" controls**: commit a real one-line bug from the `inject_bug.py` catalog on top of `origin/main` and push it, like a bad deploy. The probe catches it and the agent fixes it. Restoring pushes a revert if the fix PR was not merged. These commits are built with git plumbing in a temporary index, so your working tree is never touched.
  - **Agent activity panel**: live progress of the AI agent on an active outage, linked to the incident.
  - **Agent dependency health**: read-only checks of MongoDB, OpenAI, GitHub and Voyage.
  - **AI learning panel**: shows the RL fix policy's training runs and stats.
  - **Dashboard assistant chat** (OpenAI): answers questions like "what's broken?" in plain, non-technical language using a status snapshot. It identifies third-party outages (e.g. Stripe) as having no code fix.
  - **Fix confirmation**: a "fixed & merged into main" button, then a check on whether the merged fix actually brought the service back.
  - Acknowledge an incident, "check now", and incident links.
- 82 deterministic tests (fake clock, mock transport, both storage backends).

## 5. AI repair agent (`agents/`)

### Inputs (two incident sources)
1. **CI failures**: `FailureMonitor` polls GitHub Actions for failed, completed runs on the base branch and ignores the agent's own `agent/` branches. It fetches the failed job logs and commit context.
2. **Live incidents**: `intake.py` reads new uptime incidents from MongoDB and records them as `source="live"` incidents, pinned to the deployed commit. If a CI incident is already open for the same app and commit, the live one is skipped, so one bug never produces two PRs.

### Orchestrator (crash-safe, MongoDB-backed)
- Stage machine with leases. It resumes from the last completed stage after a crash. MongoDB collections: `incidents`, `workflow_steps`, `memories`, plus the RL collections.
- Stages: memory lookup → RL recommendation → diagnose and repair → save PR intent (before sending, so a crash can't open duplicate PRs) → open the PR → save the fix to memory → track the review.
- Review tracking: a merged PR marks the incident `resolved`. A closed PR marks it `rejected` and switches off that fix memory.
- CLI: `python -m agents.orchestrator setup | worker | intake | resume | reviews | list`.

### How it reaches a precise fix
1. **Reproduce first.** It reruns the failing tests twice at the failed commit, inside a `git archive` temp copy, before any model call. Flaky or non-reproducing failures go to `needs_review` at no model cost.
2. **Grounded diagnosis.** OpenAI receives the logs, parsed traceback frames, line-numbered source and the recent diff. **Every piece of evidence must be quoted word for word from those inputs.** A misquoted diagnosis gets one retry and is otherwise rejected.
3. **Multiple candidates.** It asks for up to 3 minimal fixes and applies each to a fresh copy. A candidate must pass the failing tests on every rerun **and** the full app suite. The smallest passing patch wins.
4. **Retry with feedback.** If none pass, a second round sends the failed patches and their real test output back to the model.
5. **Scope guard.** Patches may only touch the app's source directories. Changes to tests, CI, dependency manifests or secrets are always rejected.
6. **Regression tests for live bugs.** If the existing tests miss a live bug, the agent writes a new `tests/test_opsrelay_*.py`. It is accepted only if it fails on the deployed code with an assertion error on every rerun, and the rest of the suite still passes. The new test ships in the PR next to the fix.
7. **Live routing.** If a service is down, timing out or returning errors, there is no AI call and no code change: a person gets a checklist. Only "up, but a business check fails" goes to code repair.
8. **Anything uncertain becomes `needs_review`**, with the reason and the next diagnostic to run.

### Memory and learning
- **Incident memory**: past fixes are embedded with Voyage AI, or a local hash fallback, and retrieved with **Atlas Vector Search** (local cosine fallback) to find similar earlier incidents.
- **RL fix policy** (`rl_policy.py`): a **contextual multi-armed bandit using Thompson sampling**.
  - State: the embedded failure.
  - Actions: fix playbooks. Targeted ones are `fix_quantity_multiplier`, `fix_discount_divisor`, `fix_stock_comparison`, `fix_idempotency_guard`, `fix_recipient_delivery` and `fix_full_timestamp_sort`. Generic ones are `revert_last_commit`, `add_defensive_guard`, `retry_flaky_tests` and `increase_timeout`.
  - Reward: 1 if the tests pass, updated later to 1 if a human merges the PR or 0 if they close it.
  - Method: similarity-weighted Beta beliefs over the nearest past experiences, retrieved with Atlas Vector Search.
  - Use: the policy's pick is passed to the LLM as a **hint**. No model is retrained.
  - CLI: `seed | train | evaluate | recommend | stats | clear`, with mock incidents for training.

### PR publication
- `PullRequestPublisher` creates or reuses a deterministic `agent/fix-...` branch, commit and PR idempotently, then reads it back from GitHub to verify it. The PR body contains the evidence, the test results and the investigation.
- `GitHubAdapter` in the orchestrator connects the orchestrator to the publisher.
- Real agent PRs have been merged, e.g. #23 and #27.

### Live check CLI
- `python -m agents live-check messaging` runs a one-shot health check plus a send-and-read-back delivery probe with a unique probe ID.
- Failure kinds: `connection_timeout`, `connection_refused`, `connection_error`, `http_error`, `invalid_response`, `not_delivered`.
- `confirm_recovery()` only reports recovery after a **fresh** probe is confirmed delivered.

---

## 6. End-to-end demo flow

1. Run `./dev.sh`. Six servers and the agent worker start.
2. On the dashboard, trigger a **bad deploy**, e.g. the NovaMart quantity bug. A real commit lands on `main`.
3. Within 30 seconds the monitor marks NovaMart **Degraded** because the checkout probe fails, and opens an incident. CI also fails on the push.
4. The agent picks up the incident, reproduces the failure, diagnoses it with grounded evidence, verifies a minimal patch and opens a PR. The dashboard shows its progress live.
5. A human reviews and merges the PR on GitHub, then clicks "fixed & merged". The dashboard confirms that the service recovered. The RL policy records the reward.
6. Contrast: a **Stripe outage** demo is classified as third-party. The agent does not attempt a code fix, and the assistant explains why.

---

## 7. Known limitations and open questions

- **No authentication anywhere.** NovaConnect trusts a demo-user header, and the dashboard's mutation endpoints are open. Localhost only.
- Single-host, single-worker monitoring. A network partition looks like "down", which is why "down" incidents are never sent to code repair automatically.
- NovaConnect runs as a single process with in-memory WebSocket fan-out and SQLite. It does not scale horizontally.
- Uptime is computed on read, with no rollups.
- Hand-off from the monitor to the agents is pull-based through MongoDB. The monitor does not push.
- The real Atlas behaviour of the dashboard backend (e.g. TTL timing) is only tested with `mongomock`.
- The agent works on a narrow, known bug catalog. The RL playbooks are mostly tailored to the injected bugs, with a few generic moves.
- The architecture doc at `docs/system-architecture.md` is **partly out of date**. It still describes the messaging app and the dashboard as placeholders and the PR adapter as missing.

## 8. Possible next steps (for discussion)

- Generalise beyond the injected bug catalog: more realistic and varied failures, and multi-file fixes.
- Auth and roles for the dashboard. Approval workflows inside the dashboard rather than only on GitHub.
- Deploy integration: automatic redeploy after merge, then automatic `confirm_recovery`.
- Push-based hand-off (change streams or a queue) instead of polling.
- Better RL: a richer state, generic playbooks, and offline evaluation on real incident history.
- Multi-region probes, uptime rollups, and alerting (Slack or email).
- Use the CI diagnostic artifacts (JUnit, probe JSON) in diagnosis, not only the job logs.
- Pitch and positioning: who the user is, how this compares with existing AIOps tools, and safety guarantees such as human-in-the-loop, scope guards and verified-only PRs.
