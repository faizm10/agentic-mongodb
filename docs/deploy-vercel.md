# Deploying the OpsRelay dashboard on Vercel

One Vercel project, `opsrelay-dashboard` (team `faizm10s-projects`, root directory `admin-dashboard`), at
https://opsrelay-dashboard.vercel.app. It uses [Vercel Services](https://vercel.com/docs/services) (beta)
to deploy two services together, configured in `admin-dashboard/vercel.json`:

| Service | Folder | Serves |
| --- | --- | --- |
| `api` | `admin-dashboard/backend` (FastAPI, `main:app`) | `/api/v1/*` |
| `web` | `admin-dashboard/web` (Next.js, Vercel Web Analytics) | everything else |

Both ship in every deployment, so previews and rollbacks always pair a dashboard with its own API. The
session cookie stays first-party. Better Stack webhooks go to
`https://opsrelay-dashboard.vercel.app/api/v1/webhooks/betterstack/<id>`. Locally, Next.js still proxies
`/api/*` to `OPSRELAY_UPTIME_API_URL` (default `http://127.0.0.1:8300`).

Production deploys from `main` through the GitHub connection; each PR gets a preview.

## Scheduled checks

Vercel functions only run per request (and Hobby cron runs once a day), so the `uptime-checks` GitHub
Actions workflow calls `POST /api/v1/cron/check` every 5 minutes with the `OPSRELAY_CRON_SECRET`
repository secret. The repair agent worker still runs wherever you run it (`./dev.sh`).

## Environment (production and preview)

Set `MONGODB_URI`, `MONGODB_DB`, `OPSRELAY_STORAGE=mongodb`, `OPENAI_API_KEY`, `OPENAI_MODEL`,
`GITHUB_OWNER`, `GITHUB_REPO`, `OPSRELAY_CRON_SECRET` (the production value matches the GitHub
secret) and `OPSRELAY_BLOCK_PRIVATE_URLS=1` (sites may not point at private or internal addresses).

Use separate Atlas credentials and databases for the Vercel production and preview targets:

| Vercel target | `MONGODB_DB` | Atlas credential |
| --- | --- | --- |
| Production | `opsrelay` (existing data) | Read/write access to `opsrelay` only |
| Preview | `opsrelay_preview` | Read/write access to `opsrelay_preview` only |

Set these as target-specific variables in Vercel, including distinct `MONGODB_URI` values. The API
refuses to start if a preview uses a database without the `_preview` suffix or production uses a
preview database. Existing preview data in `opsrelay` is production data; do not copy it into the
new preview database. Seed preview with test accounts and connectors instead. Keep the agent worker
on the production Atlas credential and `MONGODB_DB=opsrelay` so its incidents remain visible to the
production dashboard.

Enable Atlas backups for production and perform a restore into a separate test database before
launch. Keep preview credentials out of the production worker and GitHub Actions secrets. Review
the Atlas IP access list as part of this change; use restricted egress addresses where available
instead of relying on a broad `0.0.0.0/0` entry.
