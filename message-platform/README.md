# NovaConnect: Real-Time Messaging for OpsRelay

NovaConnect is a polished, real-time messaging app built for the **OpsRelay** AI incident-recovery hackathon. It is a genuine messaging system, not a mock UI:

- **Next.js 15 + TypeScript + Tailwind + Motion** client with direct messages, group chats, replies, reactions, edits, deletes, pins, search, typing indicators, read receipts, presence, light/dark themes and a responsive mobile layout.
- **FastAPI + WebSockets + SQLite** backend with typed models, structured errors and persistent storage.
- **70 Pytest tests** covering business rules and demo outages, with expected-vs-actual failure messages and a machine-readable OpsRelay report.
- A **functional monitoring probe** (service id `messaging`) that runs real business logic against an isolated in-memory database.
- **`inject_bug.py`**: three realistic, reversible, one-line defects for the OpsRelay agent to detect and repair.

> **Demo identity only.** The "Switch demo user" control picks which seeded profile you are. There is no authentication; do not deploy this as-is.

NovaConnect is fully self-contained in `message-platform/`. It does not need MongoDB, the OpsRelay agents or any external service.

| | URL |
| --- | --- |
| App | http://localhost:3200 |
| API | http://localhost:8200 (OpenAPI docs: http://localhost:8200/docs) |
| WebSocket | ws://localhost:8200/ws/{user_id} |

---

## Contents

1. [Architecture](#architecture)
2. [Setup](#setup)
3. [Development commands](#development-commands)
4. [Environment variables](#environment-variables)
5. [Business rules](#business-rules)
6. [API reference](#api-reference)
7. [WebSocket protocol](#websocket-protocol)
8. [Tests](#tests)
9. [Monitoring for OpsRelay](#monitoring-for-opsrelay)
10. [Bug injection](#bug-injection)
11. [Example failure output](#example-failure-output)
12. [Messaging CI (GitHub Actions)](#messaging-ci-github-actions)
13. [Hackathon demo script](#hackathon-demo-script)
14. [Known limitations](#known-limitations)

---

## Architecture

```
message-platform/
├── backend/                         FastAPI, port 8200
│   ├── app/
│   │   ├── main.py                  routes, WebSocket endpoint, error handling, JSON logs, request IDs
│   │   ├── db.py                    SQLite connection + schema (persistence, Rule 4)
│   │   ├── users.py                 demo users / X-Demo-User identity
│   │   ├── conversations.py         DMs, groups, participants, unread counts (isolation, Rule 5)
│   │   ├── messages.py              send / history / edit / delete / reactions / pins / search
│   │   │                              find_duplicate  -> Rule 1 (Bug A site)
│   │   │                              sort_timeline   -> Rule 3 (Bug C site)
│   │   ├── delivery.py              when receipts become delivered / read
│   │   │                              is_delivery_confirmed -> Rule 2 (Bug B site)
│   │   ├── status.py                receipt state machine: sent -> delivered -> read
│   │   ├── realtime.py              WebSocket connection registry and fan-out
│   │   ├── monitor.py               functional probe (HTTP + CLI)
│   │   ├── seed.py                  6 demo users, 5 DMs, 3 groups (deterministic content)
│   │   ├── schemas.py               typed request/response models
│   │   └── errors.py                structured error types
│   ├── tests/                       Pytest suite + OpsRelay reporting plugin
│   ├── requirements.txt
│   └── pytest.ini
├── frontend/                        Next.js, port 3200
│   └── src/
│       ├── app/                     layout (fonts, theme bootstrap), single-page app
│       ├── components/              Messenger, Sidebar, ChatView, Composer, DetailsPanel, Dialogs, primitives
│       └── lib/                     API client, reducer store, WebSocket hook, preferences, formatting
├── ci/messaging-ci.yml              GitHub Actions template (not active; see below)
├── inject_bug.py                    bug injection / reset tool
└── Makefile
```

**Data flow.** The browser sends REST calls to Next.js, which proxies `/api/*` and `/health` to FastAPI, so no CORS setup is needed. The browser also opens a WebSocket directly to `ws://<host>:8200/ws/<user_id>` for push events. Sending goes over REST, which gives an idempotent and acknowledged write. The server then pushes `message.created` to every participant's open sockets.

**Reliability on the client:**
- Optimistic messages carry an idempotency key.
- Failed sends retry up to 3 times with exponential backoff, reusing the same key.
- The WebSocket reconnects with backoff (1s → 15s) and sends a heartbeat every 20s.
- While disconnected, the client polls over REST every 5s, then resyncs after reconnecting.
- A banner shows the connection state.

**Server-authoritative order.** The client renders history in exactly the order the API returns it and never re-sorts. A server ordering bug is therefore visible in the UI.

**Data model** (SQLite, `backend/data/novaconnect.db`): `users`, `conversations`, `participants` (with `muted`), `messages` (with `seq`, `idempotency_key`, `reply_to_id`, `edited_at`, `deleted_at`, `pinned_at`), `receipts` (one per message and recipient, holding the status), and `reactions`. Message and conversation IDs are stable strings (`m_<uuid>`, `c_<id>`). All timestamps are server-generated UTC in a fixed-width ISO format.

---

## Setup

Prerequisites: **Python 3.11+** (tested on 3.14) and **Node.js 18.18+** (tested on 20.19).

```bash
cd message-platform

# Backend
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt

# Frontend
cd frontend && npm ci && cd ..
```

Run in two terminals:

```bash
# Terminal 1: API + WebSockets on :8200. Creates and seeds the SQLite DB on first start.
cd message-platform/backend
.venv/bin/uvicorn app.main:app --reload --port 8200
```

```bash
# Terminal 2: app on :3200
cd message-platform/frontend
npm run dev            # or: npm run build && npm run start
```

Open **http://localhost:3200**, pick a demo profile, and you're in.

**Two-user demo:** open a second browser window (an incognito one works too) at `http://localhost:3200/?as=u_sarah`. Identity is per tab, so one window can be Alex and the other Sarah. Deep links work too: `?as=u_alex&c=c_launch_squad` opens a conversation directly.

Demo user IDs: `u_alex`, `u_sarah`, `u_jordan`, `u_emma`, `u_ryan`, `u_taylor`.

---

## Development commands

Run from `message-platform/`. Each command has a `make` shortcut.

| Task | Command | Make |
| --- | --- | --- |
| Backend (hot reload) | `cd backend && .venv/bin/uvicorn app.main:app --reload --port 8200` | `make backend` |
| Frontend (dev) | `cd frontend && npm run dev` | `make frontend` |
| **Python tests** | `cd backend && .venv/bin/python -m pytest` | `make test` |
| Functional probe (CLI) | `cd backend && .venv/bin/python -m app.monitor` | `make probe` |
| TypeScript check | `cd frontend && npm run typecheck` | `make typecheck` |
| Production build | `cd frontend && npm run build` | `make build` |
| Bug status / reset | `python3 inject_bug.py status` / `reset all` | `make bugs` / `make reset-bugs` |
| Reset demo data | delete `backend/data/novaconnect.db*`, then restart the backend | `make reset-data` |

---

## Environment variables

Nothing sensitive is needed. `.env` files are git-ignored; see `frontend/.env.example`.

| Variable | Default | Used by | Purpose |
| --- | --- | --- | --- |
| `NOVACONNECT_DB_PATH` | `backend/data/novaconnect.db` | backend | SQLite file location |
| `NOVACONNECT_CORS_ORIGINS` | `http://localhost:3200,http://127.0.0.1:3200` | backend | Origins allowed to call the API directly |
| `NOVACONNECT_LOG_LEVEL` | `INFO` | backend | Level for JSON log lines |
| `BACKEND_URL` | `http://127.0.0.1:8200` | frontend (server) | Target of the `/api` proxy |
| `NEXT_PUBLIC_WS_URL` | `ws://<page host>:8200` | frontend (browser) | WebSocket base URL |

---

## Business rules

Each rule is a small, readable Python function. They are the targets of the controlled bugs.

| Rule | Implementation | Guarantee |
| --- | --- | --- |
| **1. No duplicate messages** | `messages.find_duplicate` called from `send_message` | A retry with the same `(conversation, sender, idempotency_key)` returns the original message (HTTP 200, `Idempotent-Replay: true`) instead of inserting a copy |
| **2. Correct delivery status** | `status.ensure_transition`, `delivery.is_delivery_confirmed` | Receipts only move `sent → delivered → read`. *Delivered* requires the **recipient's own** connection to have received the push, or the recipient to have fetched history. *Read* requires the recipient to mark the conversation read. |
| **3. Chronological order** | `messages.sort_timeline` | History is oldest → newest by full timestamp, with `seq` as tie-breaker |
| **4. Persistence** | `db.py` (SQLite, WAL) | Messages, receipts, reactions and pins survive restarts; seeding never runs twice |
| **5. Conversation isolation** | `conversations.ensure_participant`, the history query, reply validation | History contains only the requested conversation, non-participants get 403, and replies cannot reference another conversation |

The sender sees the least-advanced receipt status: a group message shows *Delivered* only once every member has it, and *Read* once everyone has read it.

---

## API reference

All `/api` calls except `users`, `presence` and `monitor` require the header `X-Demo-User: <user id>`.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | **Availability** only (`service: messaging`, `check: availability`) |
| `GET`/`POST` | `/api/monitor/probe` | **Functional correctness** probe: `200` healthy, `503` unhealthy |
| `GET` | `/api/users` | Demo users |
| `GET` | `/api/presence` | IDs of users with an open WebSocket |
| `GET` | `/api/conversations` | Your conversations: participants, last message, unread count, muted; newest first |
| `GET` | `/api/conversations/{id}` | One conversation |
| `POST` | `/api/conversations` | `{kind: "direct", participant_ids: [id]}` or `{kind: "group", participant_ids: [...], title, emoji?}`. `201` created; `200` if the DM already exists |
| `PATCH` | `/api/conversations/{id}/settings` | `{muted: bool}` |
| `GET` | `/api/conversations/{id}/messages?limit=&before_seq=` | History (oldest → newest) plus pinned messages and `has_more`. Also delivers your pending receipts. |
| `POST` | `/api/conversations/{id}/read` | Mark everything read; returns `marked_read` and `unread_count` |
| `POST` | `/api/messages` | `{conversation_id, body, idempotency_key?, reply_to_id?}`. `201` new, or `200` + `Idempotent-Replay: true` for a retry |
| `PATCH` | `/api/messages/{id}` | Edit your own message: `{body}` |
| `DELETE` | `/api/messages/{id}` | Soft-delete your own message; it keeps its timeline position |
| `POST` | `/api/messages/{id}/reactions` | Toggle a reaction: `{emoji}` |
| `POST` | `/api/messages/{id}/pin` | Toggle pin |
| `GET` | `/api/search?q=` | Matching conversations and messages, limited to your conversations |

```bash
curl -s -X POST http://localhost:8200/api/messages \
  -H 'Content-Type: application/json' -H 'X-Demo-User: u_alex' \
  -d '{"conversation_id":"c_alex_sarah","body":"Hello from curl","idempotency_key":"demo-op-1"}'
```

**Errors** always look like this, with a stable `code`:

```json
{ "error": { "code": "NOT_A_PARTICIPANT", "component": "conversations",
             "message": "User 'u_alex' is not a participant of conversation 'c_sarah_ryan'.",
             "details": { "conversation_id": "c_sarah_ryan", "user_id": "u_alex" } },
  "request_id": "8f3c2a1b9d0e4f57" }
```

Codes: `MISSING_DEMO_USER`, `USER_NOT_FOUND`, `CONVERSATION_NOT_FOUND`, `NOT_A_PARTICIPANT`, `INVALID_CONVERSATION`, `MESSAGE_NOT_FOUND`, `INVALID_MESSAGE`, `FORBIDDEN_MESSAGE_ACTION`, `INVALID_STATUS_TRANSITION`, `VALIDATION_FAILED`. Every response carries `X-Request-ID`, and the backend writes one JSON log line per request, error and created message.

---

## WebSocket protocol

Connect to `ws://localhost:8200/ws/{user_id}`. An unknown user is closed with code `4404`.

**Server → client**

| `type` | Payload | When |
| --- | --- | --- |
| `hello` | `user_id`, `online_user_ids` | Right after connecting |
| `presence` | `user_id`, `online` | Another user's first tab opens or last tab closes |
| `message.created` | `message` | A new message in one of your conversations |
| `message.upserted` | `message` | Edit, delete, reaction, pin, or a status change of a message |
| `receipts.updated` | `changes[]` (`message_id`, `user_id`, `status`, `at`) | Your messages were delivered or read |
| `typing` | `conversation_id`, `user_id`, `is_typing` | A participant is typing |
| `conversation.upserted` | `conversation` | You were added to a new conversation |
| `conversation.read` | `conversation_id` | You read it in another tab |
| `pong` | `ts` | Reply to `ping` |

**Client → server:** `{"type":"typing","conversation_id":"…","is_typing":true}` and `{"type":"ping","ts":123}`.

When a user connects, every message still `sent` to them becomes `delivered`, and the senders receive `receipts.updated`.

---

## Tests

```bash
cd message-platform/backend
.venv/bin/python -m pytest          # 70 tests, exit code != 0 on any failure
```

Tests use isolated databases (in-memory or a temp file) and a deterministic fake clock. They never touch your demo data and need no network.

| File | Covers |
| --- | --- |
| `test_users_and_conversations.py` | user retrieval, missing/unknown user errors, DM uniqueness, group creation and validation, scoped listing, unread counts, mute |
| `test_messages.py` | send/receive, unread increments, invalid bodies, non-participants, edit, forbidden edit/delete, soft delete, reactions, pins, search |
| `test_idempotency.py` | **Rule 1**: retries, 5× retries, distinct keys, key scoping, API replay semantics |
| `test_delivery_status.py` | **Rule 2**: delivery condition, sent/delivered/read lifecycle, group aggregation, state machine, invalid transitions, live WebSocket lifecycle, delivery on history fetch |
| `test_ordering_and_isolation.py` | **Rule 3**: midnight-crossing order, shuffled multi-day input, ties, seeded history, pagination. **Rule 5**: leak checks, cross-conversation replies, non-participant reads, scoped search |
| `test_persistence_realtime_monitoring.py` | **Rule 4**: restart persistence and idempotency after restart; two-user live fan-out, typing, ping; health; probe health, side effects, structured failures, CLI exit code |

Every run writes `backend/test-results/opsrelay-report.json`, which groups failures by component and business rule.

---

## Monitoring for OpsRelay

Service ID: **`messaging`**. Availability and correctness are deliberately separate. The UI can load while sending is broken.

| Signal | Endpoint | Meaning |
| --- | --- | --- |
| Availability | `GET /health` → `200 {"service":"messaging","check":"availability","status":"ok",…}` | The process is up and the DB answers |
| Functional correctness | `GET` or `POST /api/monitor/probe` → `200` healthy / `503` unhealthy | Messaging business rules actually work |
| CI / CLI | `python -m app.monitor [--init-db]` → exit code `0` or `1` | Same probe, outside the server |

The probe runs these checks: `database_reachable`, `send_and_receive`, `idempotent_retry`, `delivery_status`, `message_ordering` and `conversation_isolation`.

**Safety:** checks run against a private `:memory:` SQLite database with dedicated probe users. The persistent database is only opened **read-only**, and the probe verifies it wrote nothing (`side_effects.persistent_messages_written == 0`). No real user receives anything.

Every check result has this shape:

```json
{ "service": "messaging", "check": "delivery_status", "component": "delivery", "status": "fail",
  "timestamp": "2026-11-14T17:25:42.169Z",
  "failure_reason": "Rule 2 violated: message marked delivered although the recipient is offline.",
  "details": { "expected": {"delivered_to": [], "recipient_status": "sent"},
               "actual":   {"delivered_to": ["probe_ben"], "recipient_status": "delivered"},
               "rule": "Rule 2 - correct delivery status",
               "source": "backend/app/delivery.py::is_delivery_confirmed", "online_users": ["probe_ana"] },
  "duration_ms": 0.41 }
```

The top level of the report adds `service`, `probe`, `status`, `timestamp`, `summary`, `failed_checks` and `side_effects`. The in-app **Settings → OpsRelay diagnostics** panel can run the probe and display the results.

---

## Bug injection

```bash
cd message-platform
python3 inject_bug.py list                 # files, exact change, symptom, failing tests
python3 inject_bug.py status
python3 inject_bug.py inject duplicate     # or: status | ordering
python3 inject_bug.py reset all
python3 inject_bug.py verify ordering      # inject -> pytest must fail exactly as documented -> reset -> pytest must pass
python3 inject_bug.py --json status        # machine-readable
```

**Guarantees:**
- Only one bug can be injected at a time.
- Each injection changes exactly one line in `backend/app/`, and the tool refuses to touch `backend/tests/`.
- Injecting or resetting twice is a no-op.
- If a file was hand-edited and the expected line is missing, the tool exits with code 2 instead of corrupting the file.
- `git diff message-platform/backend/app` shows exactly what changed.
- With uvicorn `--reload`, the bug goes live immediately: the app misbehaves and the probe returns 503.

| Bug | File | Change | Real-app symptom |
| --- | --- | --- | --- |
| **A, `duplicate`** | `backend/app/messages.py` (`find_duplicate`) | `if not idempotency_key:` → `if idempotency_key:` | A retried send is stored and broadcast twice. Turn on *Settings → Simulate a flaky network* to see doubles in the UI. |
| **B, `status`** | `backend/app/delivery.py` (`is_delivery_confirmed`) | `return recipient_id in online_user_ids` → `return bool(online_user_ids)` | Messages show **Delivered** while the recipient is offline, because the sender's own socket counts |
| **C, `ordering`** | `backend/app/messages.py` (`sort_timeline`) | sort key `m["created_at"]` → `m["created_at"][11:]` | History is sorted by time of day, ignoring the date: yesterday's evening messages appear after this morning's, with repeated date separators |

### Tests that fail for each bug (verified)

**A: duplicate** (6 tests fail; 60 pass)
- `test_idempotency.py::test_retry_with_same_key_returns_original_message`
- `test_idempotency.py::test_many_retries_store_exactly_one_message`
- `test_idempotency.py::test_api_replay_returns_200_with_same_message`
- `test_persistence_realtime_monitoring.py::test_messages_survive_a_backend_restart` (its idempotency-after-restart assertion)
- `test_persistence_realtime_monitoring.py::test_functional_probe_is_healthy_on_clean_code`
- `test_persistence_realtime_monitoring.py::test_probe_cli_exit_code`
- Probe: `idempotent_retry` fails

**B: status** (7 tests fail; 59 pass)
- `test_delivery_status.py::test_delivery_requires_the_recipient_to_be_connected[ben-online1-False]`
- `test_delivery_status.py::test_delivery_requires_the_recipient_to_be_connected[ben-online2-False]`
- `test_delivery_status.py::test_sender_online_does_not_deliver_to_offline_recipient`
- `test_delivery_status.py::test_group_status_is_least_advanced_recipient`
- `test_delivery_status.py::test_api_status_lifecycle_with_websockets`
- `test_persistence_realtime_monitoring.py::test_functional_probe_is_healthy_on_clean_code`
- `test_persistence_realtime_monitoring.py::test_probe_cli_exit_code`
- Probe: `delivery_status` fails

**C: ordering** (6 tests fail; 60 pass)
- `test_ordering_and_isolation.py::test_history_is_oldest_first_across_midnight`
- `test_ordering_and_isolation.py::test_sort_timeline_orders_shuffled_multi_day_input`
- `test_ordering_and_isolation.py::test_seeded_history_spanning_days_is_chronological`
- `test_delivery_status.py::test_fetching_history_delivers_pending_messages` (it reads the newest message as `history[-1]`)
- `test_persistence_realtime_monitoring.py::test_functional_probe_is_healthy_on_clean_code`
- `test_persistence_realtime_monitoring.py::test_probe_cli_exit_code`
- Probe: `message_ordering` fails

---

## Example failure output

Pytest with Bug C injected (real output):

```
E       AssertionError: [OpsRelay] Rule 3 - chronological ordering FAILED: history must be returned oldest -> newest
E         function under test: app.messages.sort_timeline
E         expected: ["late night", "almost midnight", "just after midnight", "next morning"]
E         actual:   ["just after midnight", "next morning", "late night", "almost midnight"]
E         context:  {"created_at": ["2026-05-02T00:05:00.000000Z", "2026-05-02T09:00:00.000000Z", "2026-05-01T22:50:00.000000Z", "2026-05-01T23:59:00.000000Z"]}

============ OpsRelay failure summary (service: messaging) ============
[messages | Rule 3 - chronological ordering] tests/test_ordering_and_isolation.py::test_history_is_oldest_first_across_midnight :: AssertionError: ...
Machine-readable report: backend/test-results/opsrelay-report.json
```

Live symptoms observed against the running app during verification:

```
duplicate: retry responses: 201/201, same id: False, copies stored: 2 (expected 1)
status:    Jordan offline, Alex online -> message status: 'delivered' (expected 'sent')
ordering:  chronological: False; day sequence in returned order: ['2026-11-12', '2026-11-14', '2026-11-13', '2026-11-14']
probe:     HTTP 503 unhealthy (5/6 checks passed)
```

---

## Messaging CI (GitHub Actions)

Developer 1 owns `.github/workflows/`, so this app ships an **inactive template** at `message-platform/ci/messaging-ci.yml`. Nothing at the repository root has been modified.

To enable it, copy the template to `.github/workflows/messaging-ci.yml`:

```bash
mkdir -p .github/workflows && cp message-platform/ci/messaging-ci.yml .github/workflows/messaging-ci.yml
```

- **Workflow name:** `Messaging CI`. **Jobs:** `Messaging CI / backend tests + functional probe` and `Messaging CI / frontend typecheck + build`. Both are distinct from Shopping CI.
- **Triggers:** pushes and PRs touching `message-platform/**`.
- **Exact test command:**

  ```bash
  cd message-platform/backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m pytest
  ```

- **Probe step:** `NOVACONNECT_DB_PATH=$RUNNER_TEMP/novaconnect-ci.db .venv/bin/python -m app.monitor --init-db` exits 1 when unhealthy.
- **Artifact `messaging-test-results`:** `opsrelay-report.json`, `junit.xml` and `messaging-probe.json`.

**For the OpsRelay agents:**
- **Developer 1 (workflow monitoring):** a failed `Messaging CI` run maps to service `messaging`. Download the artifact above.
- **Developer 2 (repair):** `opsrelay-report.json` lists each failing test with its business rule. Assertion messages name the `function under test`, and probe failures name a `source` such as `backend/app/messages.py::sort_timeline`. Every bug is a single line in that function, and a repair is verified by `python -m pytest` exiting 0.
- **Developer 3 (persistence):** persist the probe's per-check objects as-is. They already carry `service`, `check`, `status`, `timestamp`, `failure_reason` and `details`.

---

## Hackathon demo script

1. Start the backend and frontend. Open **window A** at `http://localhost:3200/?as=u_alex` and **window B** at `http://localhost:3200/?as=u_sarah`, side by side.
2. In A, open *Sarah Chen* and type. B shows *typing…* in the header and the sidebar. Send the message: it appears instantly in B, and A's receipt goes **Delivered → Read** when B views it.
3. In B, hover a message to react ❤️ and to reply. Both appear live in A. Edit a message with ↑, delete one (with the confirmation dialog), and pin one (it appears in the details panel).
4. Create a group from the ✚ button, toggle dark mode, and resize to mobile width.
5. Show health: `curl -s localhost:8200/health` returns `ok`, and `curl -s -XPOST localhost:8200/api/monitor/probe | jq .status` returns `"healthy"`.
6. **Break it:** `python3 inject_bug.py inject ordering`. Reload window A: Alex and Sarah's history is out of order, with date separators jumping between days. The page still loads and `/health` still says `ok`, but the probe returns **503** with `message_ordering` failing and `source: backend/app/messages.py::sort_timeline`.
7. Commit on a throwaway branch and push (or run `make test`): **Messaging CI** goes red with structured messages. Let OpsRelay detect, diagnose and open a repair PR.
8. `python3 inject_bug.py reset all`: 66/66 green, probe healthy.

Bug A in the UI: enable **Settings → OpsRelay diagnostics → Simulate a flaky network**, inject `duplicate`, and send. Every message appears twice for both users.
Bug B in the UI: close Sarah's window, inject `status`, and send from Alex. The receipt reads **Delivered** although Sarah is offline.

---

## Known limitations

- **No authentication.** The demo user switcher trusts the `X-Demo-User` header and the user ID in the WebSocket URL.
- **Single process.** WebSocket presence and fan-out live in memory. Multiple backend workers would need a shared broker (e.g. Redis pub/sub).
- **SQLite** is shared by one process, which is fine for a demo but not for horizontal scaling.
- **No attachments or uploads.** The details panel shows pinned messages and shared links rather than media.
- **No message-history paging in the UI.** The API supports `before_seq`, but the client loads the newest 200 messages.
- **Presence is per connection.** A user counts as online while any tab has a socket open.
- **Seeded timestamps** are relative to the first start, so delete the DB (`make reset-data`) for a fresh-looking demo.
