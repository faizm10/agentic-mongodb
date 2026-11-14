# OpsRelay

An on-call agent for internal company tools. The status page shows each service's live checks. Clicking a disruption opens the agent's investigation and recovery timeline.

**Demo scenario:** new hires can sign in, but their onboarding is stuck on Pending because a config change pointed GitHub team provisioning at an invalid team (4812 instead of the approved 4821). OpsRelay finds the cause and proposes a fix. After a human approves it, the agent restores the mapping and reconciles each hire against actual membership. It survives a worker crash mid-recovery without granting anything twice.

## Setup

```bash
npm install
cp .env.example .env.local      # optional: fill in Atlas / OpenAI / GitHub
npm run smoke                   # crash/resume end-to-end test (no credentials needed)
npm run dev                     # local mongod (if no MONGODB_URI) + sandbox :4010 + app :3000
```

`npm run dev` seeds the demo on an empty database. You can re-seed at any time with **Reset demo** on the status page.

### With MongoDB Atlas + OpenAI

1. Put `MONGODB_URI` (an `mongodb+srv://…` Atlas URI), `OPENAI_API_KEY`, and optionally `OPENAI_MODEL` / `OPENAI_EMBEDDING_MODEL` in `.env.local`.
2. Create the vector index and prove it with a real `$vectorSearch` query:
   ```bash
   npm run atlas:index
   ```
   The script embeds the past incidents with the configured model and creates the `past_incidents_vector` index on `past_incidents`. It then waits until the index is queryable and runs a test query. It exits non-zero if the query didn't go through Atlas Vector Search.
   The index definition, if you'd rather create it in the Atlas UI (set `numDimensions` to your embedding size: 1536 for `text-embedding-3-small`):
   ```json
   {
     "fields": [
       { "type": "vector", "path": "embedding", "numDimensions": 1536, "similarity": "cosine" },
       { "type": "filter", "path": "embeddingSource" }
     ]
   }
   ```
   If you change embedding models, rerun `npm run atlas:index`. It re-embeds the past incidents and recreates the index when the dimensions differ.
3. `npm run check:integrations` calls each configured integration and prints `verified`, `configured_unverified`, `fallback`, `missing`, or `error`. The status page shows the same panel.

### Optional: GitHub discovery

Set `GITHUB_ORG` and `GITHUB_TOKEN` (read-only `repo:read` / `read:org` is enough). OpsRelay only lists repos (`GET /orgs/{org}/repos`). Repos map to services and checks explicitly in `config/service-catalog.json`, and any repo not in `repoMap` shows **Not monitored**. Membership changes never touch GitHub. They go only to the sandbox.

## Plugins: GitHub, Slack, Jira (`/plugins`)

Each plugin implements `lib/plugins/types.ts` and is registered in `lib/plugins/index.ts`. Credentials are environment variables, kept server-side. Restart `npm run dev` after editing `.env.local`.

| Plugin | Env vars | What it does |
|---|---|---|
| GitHub | `GITHUB_ORG`, `GITHUB_TOKEN` | Read-only repo discovery, mapped to services in `config/service-catalog.json` |
| Slack | `SLACK_WEBHOOK_URL` | Posts opened / proposed or escalated / approved / crashed / resolved to one channel |
| Jira (Atlassian) | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`, optional `JIRA_ISSUE_TYPE` | One issue per incident (label `opsrelay-<incident>`), plus a comment per stage |

**Notifications follow the same crash-safe rules as grants.**
- Each one is recorded in the Atlas `plugin_deliveries` outbox under the key `incident:event:plugin`, and a delivered key is never sent again.
- If a send is interrupted, the Jira plugin searches for the issue by label and for the comment by a `[opsrelay:<key>]` marker before re-sending.
- A Slack webhook can't be read back, so the outbox is its only dedupe.
- A plugin failure is logged, but it never blocks recovery.

`npm run smoke` points Slack and Jira at a local test double (`scripts/fake-plugins.ts`) and checks four things:
- exactly 5 Slack messages
- one Jira issue
- 4 stage comments
- no duplicates after interrupted sends

The smoke test never posts to a real Slack workspace or Jira site.

**Adding a plugin:** implement `Plugin` (`configured`, `check`, and optionally `deliver` / `sendTest`), then add it to the `plugins` array.

## What's real, and what's seeded or simulated

| Piece | Status |
|---|---|
| Recovery checkpoints, leases, worker records | MongoDB (Atlas when `MONGODB_URI` is Atlas; otherwise local `mongod`, labeled) |
| Vector Search over past incidents | Atlas `$vectorSearch` when the index is queryable; otherwise an in-app cosine fallback labeled **UNVERIFIED** |
| Embeddings / explanation | OpenAI when keyed; otherwise hashed embeddings + template text, labeled |
| Team membership API | Local stateful sandbox (`sandbox/server.ts`) in its own database `<MONGODB_DB>_sandbox`, with an append-only mutation log |
| Provisioning errors, onboarding check | Real calls from the onboarding pipeline to the sandbox |
| Provisioning-worker heartbeat | Simulated by the pipeline; the stalled variant stops it |
| Hires, approved mapping, the bad config change, check history, Expense/Helpdesk services, past incidents | Seeded and labeled |

## How recovery works (`lib/recovery.ts`)

- **Decision** (`lib/decision.ts`). Deterministic rules decide; the LLM only explains. If the LLM recommends a different action, the incident goes to human review.
  - Config drift + 404 team-not-found + healthy worker → `restore_mapping`.
  - Stale worker + correct mapping → `restart_worker` (recommend only).
  - Both at once → `human_review`.
  - Use **Reset (alt: stalled worker)** to see the second branch.
- **Approval** pins the team ID and the hire list. `assertGrantAllowed` blocks any grant outside them in application code.
- **Mapping:** record intent → write → read back the config and look up the team in the sandbox → `verified`.
- **Each hire:** read actual membership first.
  - Already a member → complete (`pre_existing_membership`).
  - Otherwise: record `intent` in Atlas (with a request ID) → PUT (sent) → GET (took effect) → `complete`.
- **Resume:** when a fresh worker finds `intent`/`sent` in the checkpoint, it asks the sandbox before acting.
  - Membership is active → `grant_took_effect_before_crash`, and the PUT is not sent again.
  - Membership is absent → it re-applies with the same request ID.
- **Leases:** a fresh worker takes over the incident lease only after the previous worker has exited or its lease has expired.
- **Fault toggle:** arms a one-shot `SIGKILL` right after the sandbox grant commits and before Atlas records it. The web app keeps running because workers are separate OS processes (`lib/supervisor.ts`).
- **Close:** a fresh synthetic hire runs through the normal pipeline. If it passes, provisioning turns green. The incident closes only when that check passes and both original hires are reconciled.
