#!/usr/bin/env python3
"""Inject or reset reproducible NovaConnect bugs for OpsRelay demos.

Each bug is a one-line change to real business logic under ``backend/app/``.
Regression tests under ``backend/tests/`` are never modified, so the existing
suite, the CI job and the live functional probe all detect the defect.

Usage:
    python3 inject_bug.py list
    python3 inject_bug.py status
    python3 inject_bug.py inject <duplicate|status|ordering|send|receive|both>
    python3 inject_bug.py reset [<bug> | all]
    python3 inject_bug.py verify <bug>      # inject -> pytest must fail -> reset -> pytest must pass

Only one bug can be injected at a time. Add ``--json`` for machine-readable output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"


@dataclass(frozen=True)
class Bug:
    key: str
    title: str
    file: str  # relative to message-platform/
    original: str
    buggy: str
    symptom: str
    failing_tests: tuple[str, ...]
    probe_check: str


BUGS: dict[str, Bug] = {
    "duplicate": Bug(
        key="duplicate",
        title="Bug A - duplicate message delivery (idempotency guard inverted)",
        file="backend/app/messages.py",
        original="    if not idempotency_key:\n        return None\n",
        buggy="    if idempotency_key:\n        return None\n",
        symptom="A client retry with the same idempotency key stores and broadcasts the message twice.",
        failing_tests=(
            "tests/test_idempotency.py::test_retry_with_same_key_returns_original_message",
            "tests/test_idempotency.py::test_many_retries_store_exactly_one_message",
            "tests/test_idempotency.py::test_api_replay_returns_200_with_same_message",
            "tests/test_persistence_realtime_monitoring.py::test_messages_survive_a_backend_restart",
            "tests/test_persistence_realtime_monitoring.py::test_functional_probe_is_healthy_on_clean_code",
            "tests/test_persistence_realtime_monitoring.py::test_probe_cli_exit_code",
        ),
        probe_check="idempotent_retry",
    ),
    "status": Bug(
        key="status",
        title="Bug B - incorrect delivery status (any online user confirms delivery)",
        file="backend/app/delivery.py",
        original="    return recipient_id in online_user_ids\n",
        buggy="    return bool(online_user_ids)\n",
        symptom="Messages show 'Delivered' while the recipient is offline, because the sender's own connection counts.",
        failing_tests=(
            "tests/test_delivery_status.py::test_delivery_requires_the_recipient_to_be_connected[ben-online1-False]",
            "tests/test_delivery_status.py::test_delivery_requires_the_recipient_to_be_connected[ben-online2-False]",
            "tests/test_delivery_status.py::test_sender_online_does_not_deliver_to_offline_recipient",
            "tests/test_delivery_status.py::test_group_status_is_least_advanced_recipient",
            "tests/test_delivery_status.py::test_api_status_lifecycle_with_websockets",
            "tests/test_persistence_realtime_monitoring.py::test_functional_probe_is_healthy_on_clean_code",
            "tests/test_persistence_realtime_monitoring.py::test_probe_cli_exit_code",
        ),
        probe_check="delivery_status",
    ),
    "ordering": Bug(
        key="ordering",
        title="Bug C - incorrect message ordering (sorted by time of day, ignoring the date)",
        file="backend/app/messages.py",
        original='    return sorted(messages, key=lambda m: (m["created_at"], m["seq"]))\n',
        buggy='    return sorted(messages, key=lambda m: (m["created_at"][11:], m["seq"]))\n',
        symptom="Conversations spanning several days are shown out of order (yesterday evening after this morning).",
        failing_tests=(
            "tests/test_delivery_status.py::test_fetching_history_delivers_pending_messages",
            "tests/test_ordering_and_isolation.py::test_history_is_oldest_first_across_midnight",
            "tests/test_ordering_and_isolation.py::test_sort_timeline_orders_shuffled_multi_day_input",
            "tests/test_ordering_and_isolation.py::test_seeded_history_spanning_days_is_chronological",
            "tests/test_persistence_realtime_monitoring.py::test_functional_probe_is_healthy_on_clean_code",
            "tests/test_persistence_realtime_monitoring.py::test_probe_cli_exit_code",
        ),
        probe_check="message_ordering",
    ),
    "send": Bug(
        key="send",
        title="Sending fails (sender permission check passes arguments in the wrong order)",
        file="backend/app/messages.py",
        original="    ensure_participant(conn, conversation_id, sender_id)\n",
        buggy="    ensure_participant(conn, sender_id, conversation_id)\n",
        symptom="Every send is rejected with CONVERSATION_NOT_FOUND; nobody can send messages.",
        failing_tests=(
            "tests/test_delivery_status.py::test_group_status_is_least_advanced_recipient",
            "tests/test_delivery_status.py::test_invalid_transitions_are_rejected_with_details",
            "tests/test_delivery_status.py::test_new_message_starts_as_sent",
            "tests/test_delivery_status.py::test_online_recipient_is_delivered_then_read",
            "tests/test_delivery_status.py::test_sender_online_does_not_deliver_to_offline_recipient",
            "tests/test_idempotency.py::test_distinct_keys_and_missing_keys_create_distinct_messages",
            "tests/test_idempotency.py::test_keys_are_scoped_to_sender_and_conversation",
            "tests/test_idempotency.py::test_many_retries_store_exactly_one_message",
            "tests/test_idempotency.py::test_retry_with_same_key_returns_original_message",
            "tests/test_messages.py::test_unit_validation",
            "tests/test_ordering_and_isolation.py::test_history_is_oldest_first_across_midnight",
            "tests/test_ordering_and_isolation.py::test_identical_timestamps_fall_back_to_insertion_order",
            "tests/test_ordering_and_isolation.py::test_messages_never_leak_between_conversations",
            "tests/test_ordering_and_isolation.py::test_non_participants_cannot_read_or_react",
            "tests/test_ordering_and_isolation.py::test_pagination_returns_older_pages_in_order",
            "tests/test_ordering_and_isolation.py::test_reply_must_reference_same_conversation",
            "tests/test_persistence_realtime_monitoring.py::test_messages_survive_a_backend_restart",
            "tests/test_persistence_realtime_monitoring.py::test_probe_cli_exit_code",
        ),
        probe_check="send_and_receive",
    ),
    "receive": Bug(
        key="receive",
        title="Receiving fails (history query filters by the viewer instead of the conversation)",
        file="backend/app/messages.py",
        original="    params: list[Any] = [conversation_id]\n",
        buggy="    params: list[Any] = [viewer_id]\n",
        symptom="Messages are stored but conversation history comes back empty, so nobody sees new messages.",
        failing_tests=(
            "tests/test_delivery_status.py::test_api_status_lifecycle_with_websockets",
            "tests/test_delivery_status.py::test_fetching_history_delivers_pending_messages",
            "tests/test_demo_outages.py::test_demo_outage_and_restore[both]",
            "tests/test_demo_outages.py::test_demo_outage_and_restore[receive]",
            "tests/test_demo_outages.py::test_demo_outage_and_restore[send]",
            "tests/test_idempotency.py::test_api_replay_returns_200_with_same_message",
            "tests/test_messages.py::test_delete_is_soft_and_keeps_timeline_position",
            "tests/test_messages.py::test_send_and_receive",
            "tests/test_ordering_and_isolation.py::test_history_is_oldest_first_across_midnight",
            "tests/test_ordering_and_isolation.py::test_identical_timestamps_fall_back_to_insertion_order",
            "tests/test_ordering_and_isolation.py::test_messages_never_leak_between_conversations",
            "tests/test_ordering_and_isolation.py::test_pagination_returns_older_pages_in_order",
            "tests/test_ordering_and_isolation.py::test_seeded_history_spanning_days_is_chronological",
            "tests/test_persistence_realtime_monitoring.py::test_functional_probe_is_healthy_on_clean_code",
            "tests/test_persistence_realtime_monitoring.py::test_messages_survive_a_backend_restart",
            "tests/test_persistence_realtime_monitoring.py::test_probe_cli_exit_code",
        ),
        probe_check="send_and_receive",
    ),
    "both": Bug(
        key="both",
        title="Sending and receiving fail (participant lookup binds its parameters in the wrong order)",
        file="backend/app/conversations.py",
        original='        "SELECT 1 FROM participants WHERE conversation_id = ? AND user_id = ?", (conversation_id, user_id)\n',
        buggy='        "SELECT 1 FROM participants WHERE conversation_id = ? AND user_id = ?", (user_id, conversation_id)\n',
        symptom="Every user is treated as a non-participant: sends and history reads all fail with 403.",
        failing_tests=(
            "tests/test_delivery_status.py::test_group_status_is_least_advanced_recipient",
            "tests/test_delivery_status.py::test_invalid_transitions_are_rejected_with_details",
            "tests/test_delivery_status.py::test_new_message_starts_as_sent",
            "tests/test_delivery_status.py::test_online_recipient_is_delivered_then_read",
            "tests/test_delivery_status.py::test_sender_online_does_not_deliver_to_offline_recipient",
            "tests/test_idempotency.py::test_distinct_keys_and_missing_keys_create_distinct_messages",
            "tests/test_idempotency.py::test_keys_are_scoped_to_sender_and_conversation",
            "tests/test_idempotency.py::test_many_retries_store_exactly_one_message",
            "tests/test_idempotency.py::test_retry_with_same_key_returns_original_message",
            "tests/test_messages.py::test_unit_validation",
            "tests/test_ordering_and_isolation.py::test_history_is_oldest_first_across_midnight",
            "tests/test_ordering_and_isolation.py::test_identical_timestamps_fall_back_to_insertion_order",
            "tests/test_ordering_and_isolation.py::test_messages_never_leak_between_conversations",
            "tests/test_ordering_and_isolation.py::test_non_participants_cannot_read_or_react",
            "tests/test_ordering_and_isolation.py::test_pagination_returns_older_pages_in_order",
            "tests/test_ordering_and_isolation.py::test_reply_must_reference_same_conversation",
            "tests/test_persistence_realtime_monitoring.py::test_messages_survive_a_backend_restart",
            "tests/test_persistence_realtime_monitoring.py::test_probe_cli_exit_code",
        ),
        probe_check="send_and_receive",
    ),
}


class InjectError(RuntimeError):
    pass


def _path(bug: Bug) -> Path:
    path = (ROOT / bug.file).resolve()
    if BACKEND / "tests" in path.parents or BACKEND / "app" not in path.parents:
        raise InjectError(f"refusing to modify {bug.file}: only backend/app/ may be changed")
    return path


def state(bug: Bug) -> str:
    text = _path(bug).read_text(encoding="utf-8")
    original, buggy = text.count(bug.original), text.count(bug.buggy)
    if original == 1 and buggy == 0:
        return "clean"
    if buggy == 1 and original == 0:
        return "injected"
    return "unknown"


def _swap(bug: Bug, old: str, new: str) -> None:
    path = _path(bug)
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise InjectError(
            f"[{bug.key}] expected exactly 1 occurrence of {old.strip()!r} in {bug.file}, found {count}. "
            f"The file was edited by hand; restore it with `git checkout -- message-platform/{bug.file}`."
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def inject(bug: Bug) -> str:
    current = state(bug)
    if current == "injected":
        return "already injected"
    if current == "unknown":
        raise InjectError(f"[{bug.key}] source is in an unknown state; run `reset {bug.key}` or restore from git")
    others = [b.key for b in BUGS.values() if b.key != bug.key and state(b) == "injected"]
    if others:
        raise InjectError(f"only one bug at a time: reset {', '.join(others)} first (`python3 inject_bug.py reset all`)")
    _swap(bug, bug.original, bug.buggy)
    return "injected"


def reset(bug: Bug) -> str:
    current = state(bug)
    if current == "clean":
        return "already clean"
    if current == "unknown":
        raise InjectError(f"[{bug.key}] source is in an unknown state; restore it with git")
    _swap(bug, bug.buggy, bug.original)
    return "reset"


def run_pytest() -> subprocess.CompletedProcess[str]:
    venv = BACKEND / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    python = str(venv) if venv.exists() else sys.executable
    return subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=BACKEND,
                          capture_output=True, text=True)


def failed_tests(output: str) -> list[str]:
    return [line.split(" ", 1)[1].split(" - ")[0] for line in output.splitlines() if line.startswith("FAILED ")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("status")
    p = sub.add_parser("inject")
    p.add_argument("bug", choices=sorted(BUGS))
    p = sub.add_parser("reset")
    p.add_argument("bug", nargs="?", default="all", choices=[*sorted(BUGS), "all"])
    p = sub.add_parser("verify")
    p.add_argument("bug", choices=sorted(BUGS))
    args = parser.parse_args(argv)

    def emit(payload: dict, text: str) -> None:
        print(json.dumps(payload, indent=2) if args.json else text)

    try:
        if args.command == "list":
            emit({"bugs": [{**asdict(b), "state": state(b)} for b in BUGS.values()]}, "\n\n".join(
                f"{b.key:<10} {b.title}\n           file:    {b.file}\n           change:  "
                f"{b.original.strip()!r} -> {b.buggy.strip()!r}\n           symptom: {b.symptom}\n"
                f"           probe:   POST /api/monitor/probe -> {b.probe_check}\n           tests:\n"
                + "\n".join(f"             - {t}" for t in b.failing_tests)
                for b in BUGS.values()))
        elif args.command == "status":
            states = {k: state(b) for k, b in BUGS.items()}
            emit({"bugs": states}, "\n".join(f"{k:<10} {v}" for k, v in states.items()))
        elif args.command == "inject":
            bug = BUGS[args.bug]
            result = inject(bug)
            emit({"bug": bug.key, "result": result, "file": bug.file},
                 f"[{bug.key}] {result}: {bug.title}\n--- {bug.file}\n-{bug.original.rstrip()}\n+{bug.buggy.rstrip()}\n"
                 f"Expected failing tests: {len(bug.failing_tests)} (see `python3 inject_bug.py list`)")
        elif args.command == "reset":
            targets = BUGS.values() if args.bug == "all" else [BUGS[args.bug]]
            results = {b.key: reset(b) for b in targets}
            emit({"results": results}, "\n".join(f"[{k}] {v}" for k, v in results.items()))
        elif args.command == "verify":
            bug = BUGS[args.bug]
            if any(state(b) != "clean" for b in BUGS.values()):
                raise InjectError("reset all bugs before verify (`python3 inject_bug.py reset all`)")
            inject(bug)
            try:
                broken = run_pytest()
            finally:
                reset(bug)
            clean = run_pytest()
            failed = failed_tests(broken.stdout)
            last = lambda r: (r.stdout.strip().splitlines() or ["<no output>"])[-1]  # noqa: E731
            ok = broken.returncode != 0 and clean.returncode == 0 and sorted(failed) == sorted(bug.failing_tests)
            emit({"bug": bug.key, "verified": ok, "failed_tests": failed, "expected_failing_tests": list(bug.failing_tests),
                  "with_bug": last(broken), "after_reset": last(clean)},
                 f"[{bug.key}] with bug:    {last(broken)}\n" + "\n".join(f"    FAILED {t}" for t in failed) +
                 f"\n[{bug.key}] after reset: {last(clean)}\n[{bug.key}] "
                 + ("VERIFIED: exactly the documented tests fail, and all pass after reset" if ok else
                    "NOT VERIFIED: failing tests differ from the documented list"))
            return 0 if ok else 1
    except InjectError as exc:
        emit({"error": str(exc)}, f"error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
