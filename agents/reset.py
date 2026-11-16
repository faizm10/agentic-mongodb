"""Start the demo fresh: clear incidents in Atlas, local demo records and old agent branches.

    python -m agents.reset            # asks before deleting anything
    python -m agents.reset --yes      # no prompt
    python -m agents.reset --learning # also clear fix memories and RL experiences

Service configuration (``uptime_services``) and the apps' catalog and messages are kept.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from pathlib import Path

from agents import mongodb as m
from agents import rl_policy

ROOT = Path(__file__).resolve().parents[1]
INCIDENT_COLLECTIONS = [m.INCIDENTS, m.WORKFLOW_STEPS, m.LIVE_INCIDENTS, m.CHECKOUT_RECOVERIES,
                        "uptime_incidents", "uptime_checks", "uptime_status", "uptime_worker"]
LEARNING_COLLECTIONS = [m.MEMORIES, rl_policy.RL_EXPERIENCES, rl_policy.TRAINING_RUNS]
LOCAL_FILES = ["shopping/backend/data/novamart-store.json", "shopping/backend/data/opsrelay-outage.json",
               "message-platform/backend/data/opsrelay-outage.json", ".dev/recovery-fault.json",
               ".dev/recovery-fault.json.fired"]
NOVACONNECT_DB = ROOT / "message-platform/backend/data/novaconnect.db"


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def agent_branches() -> list[str]:
    """Remote ``agent/*`` branches without an open PR."""
    remote = [line.split("refs/heads/", 1)[1] for line in _git("ls-remote", "origin", "refs/heads/agent/*").splitlines()]
    out = subprocess.run(["gh", "pr", "list", "--state", "open", "--json", "headRefName"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    open_heads = {pr["headRefName"] for pr in json.loads(out or "[]")}
    return [b for b in remote if b not in open_heads]


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m agents.reset")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--learning", action="store_true", help="also clear fix memories and RL experiences")
    a = p.parse_args()

    db = m.get_db()
    collections = INCIDENT_COLLECTIONS + (LEARNING_COLLECTIONS if a.learning else [])
    counts = {c: db[c].count_documents({}) for c in collections}
    files = [ROOT / f for f in LOCAL_FILES if (ROOT / f).exists()]
    branches = agent_branches()

    print(f"Atlas database {db.name}:")
    print("\n".join(f"  {c}: {n} document(s)" for c, n in counts.items()))
    print("Local files:", *(f"  {f.relative_to(ROOT)}" for f in files) or ["  none"], sep="\n")
    print(f"NovaConnect confirmations in {NOVACONNECT_DB.relative_to(ROOT)}, and the logs in .dev/logs")
    print("Remote branches:", *(f"  {b}" for b in branches) or ["  none"], sep="\n")
    if not a.yes and input("Delete all of the above? [y/N] ").strip().lower() != "y":
        print("Nothing deleted.")
        return

    for c in collections:
        db[c].delete_many({})
    for f in files:
        f.unlink()
    if NOVACONNECT_DB.exists():
        with sqlite3.connect(NOVACONNECT_DB) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'confirmations'").fetchone():
                conn.execute("DELETE FROM confirmations")
    for log in (ROOT / ".dev/logs").glob("*.log"):
        log.write_text("")
    if branches:
        _git("push", "origin", "--delete", *branches)
    print("Reset done. Restart ./dev.sh.")


if __name__ == "__main__":
    main()
