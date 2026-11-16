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

`MONGODB_URI`, `MONGODB_DB`, `OPSRELAY_STORAGE=mongodb`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `GITHUB_OWNER`,
`GITHUB_REPO`, `OPSRELAY_CRON_SECRET` (same value as the GitHub secret) and `OPSRELAY_BLOCK_PRIVATE_URLS=1`
(sites may not point at private or internal addresses). Previews use the same Atlas
database. Atlas accepts connections from `0.0.0.0/0`, because Vercel functions have no fixed IPs; the
database password is the protection, so rotate it if it leaks.
