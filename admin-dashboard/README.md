# OpsRelay admin dashboard

A read-only status page for the demo apps. It shows:

- **Uptime.** The dashboard polls each app's health endpoint and shows up/down status
  and how long it has held, uptime over the last 1h and 24h, p50/p95 latency, and a
  strip of the last 60 checks you can hover for details.
- **CI.** The latest GitHub Actions runs for each app's workflow. It only reads from
  GitHub, and caches results for 60s.
- **Repairs.** Results from the diagnosis and repair pipeline (`agents/`): outcome,
  cause, evidence, the patch, before and after test output, and every candidate tried.

It uses only the Python standard library, with no build step, and binds to 127.0.0.1 by default.

## Run it

```bash
cd admin-dashboard
python3 -m opsrelay_dashboard            # real health URLs from dashboard.toml
python3 -m opsrelay_dashboard --demo     # simulated health endpoints; Messaging flaps
# open http://127.0.0.1:8090
```

To fill the Repairs table with real pipeline runs (using a stub model, no OpenAI key needed):

```bash
agents/.venv/bin/python -m agents.demo --out admin-dashboard/.data/repairs   # from the repo root
```

Set `GITHUB_TOKEN` (read-only scope is enough) to raise the GitHub API rate limit.
If your Python cannot verify TLS certificates (some uv builds on macOS), install `certifi`
or run with `agents/.venv/bin/python`, which includes it.

## Configure

`dashboard.toml` holds `health_url`, `check_interval_seconds` and `workflow` for each app,
plus the GitHub repo and data paths. The URLs and workflow files are placeholders until
the Shopping and Messaging apps and their CI workflows land.

| Data | Source today | Swap later |
|---|---|---|
| Uptime history | `.data/uptime.jsonl`, rolling 24h | Mo's store |
| Repairs | `.data/repairs/*.json` (`RepairResult.model_dump_json()`) | a MongoDB-backed `RepairStore` with the same `list()` / `get()` |
| CI | GitHub REST API, read only | Hamza's monitor |

## API

| Route | Returns |
|---|---|
| `GET /api/status` | each app's uptime summary plus CI state, recent repairs, and totals |
| `GET /api/repairs/<id>` | one repair with diagnosis, evidence, patch, test runs, candidates |

Only GET requests are served. The page has a strict `Content-Security-Policy` (`default-src 'self'`).
All data, including patches and model output, is inserted with `textContent`, never as HTML.

## Tests

```bash
agents/.venv/bin/python -m pytest admin-dashboard -q
```
