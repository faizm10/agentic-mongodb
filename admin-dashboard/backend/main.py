"""Vercel entrypoint: the dashboard API on its own. Checks run elsewhere (python -m uptime worker)
and write to the same MongoDB, so set OPSRELAY_STORAGE=mongodb and MONGODB_URI on the project."""

from uptime.api import create_app

app = create_app()
