"""Run the full workflow against real Atlas using fake GitHub/LLM - including the crash demo.

    python -m agents.dev_demo reset                       # wipe demo incident + seed memories
    OPSRELAY_CRASH_AT=pr_created python -m agents.dev_demo run   # dies right after PR is created
    python -m agents.dev_demo run                         # fresh worker resumes, finds same PR
    python -m agents.orchestrator status "faizm10/agentic-mongodb#9000001.1"

Once Hamza's and Faiz's modules exist, use `python -m agents.orchestrator worker` instead.
"""
import sys
from pathlib import Path

from agents import memory
from agents import mongodb as m
from agents import orchestrator as o
from agents.fakes import SAMPLE_RUN, FakeGitHub, FakeRepairer

FAKE_GH_STATE = Path(__file__).with_name(".fake_github.json")

SEEDS = [
    ("shopping",
     "FAILED shopping/tests/test_cart.py::test_discount_applied AssertionError "
     "assert 100.0 == 90.0 Cart.total()",
     "Cart.total() ignored the discount; multiply subtotal by (1 - discount) before rounding",
     "discount not applied in total"),
    ("shopping", "FAILED shopping/tests/test_inventory.py::test_reserve KeyError: 'sku'",
     "Inventory.reserve looked up items by name instead of sku", "wrong dict key"),
    ("messaging", "FAILED message-platform/tests/test_send.py::test_unread_count assert 3 == 2",
     "Unread counter incremented twice on send; increment only on receive", "double increment"),
]


def reset() -> None:
    m.ensure_setup()
    iid = o.incident_id_for(SAMPLE_RUN)
    m.incidents().delete_one({"_id": iid})
    m.workflow_steps().delete_many({"incident_id": iid})
    m.memories().delete_many({"source_incident_id": iid})
    FAKE_GH_STATE.unlink(missing_ok=True)
    for app, logs, summary, cause in SEEDS:
        memory.seed_memory(app, logs, summary, root_cause=cause)
    print(f"reset done; seeded {len(SEEDS)} memories")


def run() -> None:
    iid, created = o.ingest_failed_run(SAMPLE_RUN)
    print(f"{'new' if created else 'existing'} incident {iid}")
    orch = o.Orchestrator(FakeGitHub(FAKE_GH_STATE), FakeRepairer(), lease_seconds=5)
    # Short lease so the restarted worker can take over a few seconds after the crash.
    import time
    for _ in range(10):
        if iid in orch.resumable_ids():
            break
        print("waiting for crashed worker's lease to expire…")
        time.sleep(1)
    orch.run_incident(iid)


if __name__ == "__main__":
    {"reset": reset, "run": run}[sys.argv[1] if len(sys.argv) > 1 else "run"]()
