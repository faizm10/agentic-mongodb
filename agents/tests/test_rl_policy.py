"""RL fix policy: mock data, learning from rewards, credit assignment, orchestrator hooks."""
import random

import mongomock
import pytest

from agents import mock_incidents as mk
from agents import mongodb as m
from agents import rl_policy as rl


@pytest.fixture
def db():
    db = mongomock.MongoClient(tz_aware=True)["opsrelay_rl_test"]
    m.ensure_setup(db, vector_index=False)
    rl.ensure_setup(db, vector_index=False)
    return db


def test_mock_data_is_realistic_and_deterministic(db):
    docs = mk.generate(100, seed=42)
    strip = lambda ds: [{k: v for k, v in d.items() if k != "occurred_at"} for d in ds]  # noqa: E731
    assert len(docs) == 100 and strip(docs) == strip(mk.generate(100, seed=42))
    assert {d["app"] for d in docs} == {"shopping", "messaging"}
    assert {d["source"] for d in docs} == {"ci", "live"}
    assert len({d["ground_truth"]["bug"] for d in docs}) == len(mk.BUG_CLASSES)
    for d in docs:  # every incident's correct fix is a playbook valid for its app
        assert d["ground_truth"]["action"] in rl.actions_for(d["app"])
    assert mk.store(100, db=db) == 100 and rl.mock_incidents(db).count_documents({}) == 100
    assert m.incidents(db).count_documents({}) == 0  # never pollutes the real incidents


def test_policy_learns_from_rewards(db):
    mk.store(100, db=db)
    before = rl.evaluate(db, use_experiences=False)["accuracy"]
    out = rl.train(db, verbose=False)
    after = rl.evaluate(db)["accuracy"]
    assert before < 0.35 and after >= 0.8, (before, after)
    assert out["first_try_rate_last_20"] > out["first_try_rate_first_20"]
    # successes became memories (flagged mock), every attempt became an experience
    assert m.memories(db).count_documents({"mock": True}) > 50
    assert rl.experiences(db).count_documents({"source": "mock"}) >= 200  # 2 epochs x 100 incidents


def test_failed_action_is_avoided_next_time(db):
    run = {"app": "shopping", "failed_tests": ["tests/test_discounts.py::test_welcome10"],
           "failed_logs": "AssertionError: discount_cents 900 != 90 for WELCOME10"}
    for _ in range(3):
        rl.record(run, "revert_last_commit", 0.0, source="mock", db=db)
        rl.record(run, "fix_discount_divisor", 1.0, source="mock", db=db)
    rec = rl.recommend(run, db=db, explore=False, rng=random.Random(0))
    assert rec.action == "fix_discount_divisor" and rec.confident
    ranks = {r["action"]: r["expected_success"] for r in rec.ranking}
    assert ranks["revert_last_commit"] < 0.5 < ranks["fix_discount_divisor"]
    # on a retry the tried action is excluded
    assert rl.recommend(run, db=db, exclude={"fix_discount_divisor"}, rng=random.Random(0)).action \
        != "fix_discount_divisor"


def test_no_experience_means_no_confident_hint(db):
    rec = rl.recommend({"app": "messaging", "failed_logs": "boom"}, db=db, explore=False)
    assert not rec.confident and rec.evidence == 0


def test_action_for_patch_recognises_real_fixes():
    assert rl.action_for_patch(rl.PLAYBOOKS["fix_discount_divisor"].diff) == "fix_discount_divisor"
    assert rl.action_for_patch(rl.PLAYBOOKS["fix_idempotency_guard"].diff) == "fix_idempotency_guard"
    assert rl.action_for_patch("-x = 1\n+x = 2\n") is None
    # removed lines must not count: the buggy line is on the '-' side
    assert rl.action_for_patch("-    return unit_price_cents * quantity\n+    return 0\n") is None


def test_learn_from_history_and_human_reward(db):
    m.incidents(db).insert_many([
        {"_id": "i1", "app": "shopping", "status": m.STATUS_RESOLVED, "failed_logs": "WELCOME10",
         "patch": {"diff": rl.PLAYBOOKS["fix_discount_divisor"].diff}},
        {"_id": "i2", "app": "messaging", "status": m.STATUS_NEEDS_REVIEW, "failed_logs": "dup",
         "verification": {"tests_passed": False}, "patch": {"diff": rl.PLAYBOOKS["fix_idempotency_guard"].diff}},
        {"_id": "i3", "app": "shopping", "status": m.STATUS_OPEN, "patch": {"diff": ""}},  # undecided
    ])
    assert rl.learn_from_history(db) == 2 and rl.learn_from_history(db) == 2  # idempotent
    assert rl.experiences(db).count_documents({}) == 2
    assert rl.experiences(db).find_one({"_id": "exp:i2"})["reward"] == 0.0
    assert rl.set_reward("exp:i2", 1.0, "PR merged", db=db)
    assert rl.experiences(db).find_one({"_id": "exp:i2"})["reward"] == 1.0


def test_clear_mock_keeps_real_experiences(db):
    mk.store(20, db=db)
    rl.train(db, verbose=False)
    rl.record({"app": "shopping", "failed_logs": "x"}, "revert_last_commit", 1.0, source="ci",
              exp_id="exp:real", db=db)
    rl.clear_mock(db)
    assert rl.mock_incidents(db).count_documents({}) == 0
    assert m.memories(db).count_documents({"mock": True}) == 0
    assert [d["_id"] for d in rl.experiences(db).find()] == ["exp:real"]
