# Deploying the OpsRelay dashboard on Vercel

Two Vercel projects (team `faizm10s-projects`), both reading the same MongoDB Atlas database as local dev:

| Project | Folder | URL |
| --- | --- | --- |
| `opsrelay-dashboard` | `admin-dashboard/web` (Next.js, Vercel Web Analytics) | https://opsrelay-dashboard.vercel.app |
| `opsrelay-api` | `admin-dashboard/backend` (FastAPI, entrypoint `main.py`) | https://opsrelay-api.vercel.app |

The browser only talks to the dashboard. It proxies `/api/*` to `OPSRELAY_UPTIME_API_URL`, so the
session cookie stays first-party. Better Stack webhooks go to the dashboard URL as well
(`https://opsrelay-dashboard.vercel.app/api/v1/webhooks/betterstack/<id>`).

**Monitoring checks don't run on Vercel.** Functions only run while they handle a request, so the
checks and the repair worker keep running wherever they run today (`./dev.sh`, or
`python -m uptime worker` plus the agent worker) against the same `MONGODB_URI`.

## Environment

- `opsrelay-api` (production): `MONGODB_URI`, `MONGODB_DB`, `OPSRELAY_STORAGE=mongodb`, `OPENAI_API_KEY`,
  `OPENAI_MODEL`, `GITHUB_OWNER`, `GITHUB_REPO`. `OPSRELAY_ENV` is unset, so demo controls are off.
- `opsrelay-dashboard` (production): `OPSRELAY_UPTIME_API_URL=https://opsrelay-api.vercel.app`. It is read at
  build time by the rewrite, so redeploy the dashboard after changing it.
- Atlas accepts connections from `0.0.0.0/0`, because Vercel functions have no fixed IPs. The database
  password is the protection, so rotate it if it leaks.

## Redeploy

```bash
cd admin-dashboard/backend && vercel deploy --prod   # API
cd admin-dashboard/web && vercel deploy --prod       # dashboard
```

The first account to sign up claims the existing NovaMart/NovaConnect demo workspace (local and
deployed share the database), so sign up with your own account first.
