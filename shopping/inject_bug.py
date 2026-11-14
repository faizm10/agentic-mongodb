#!/usr/bin/env python3
"""Inject or reset reproducible NovaMart bugs for OpsRelay demos.

Each bug is a single-line change to application source under ``backend/app/``.
Regression tests under ``backend/tests/`` are never touched, so after injection
the existing test suite (and the live checkout health probe) catches the bug.

Usage:
    python inject_bug.py list
    python inject_bug.py status
    python inject_bug.py inject <quantity|discount|inventory|images|stock>
    python inject_bug.py reset [<bug> | all]
    python inject_bug.py verify <bug>     # inject -> run pytest (expect failures) -> reset

Add ``--json`` to any command for machine-readable output.
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
    file: str  # relative to shopping/
    original: str
    buggy: str
    symptom: str
    tests: tuple[str, ...]
    probe_check: str


BUGS: dict[str, Bug] = {
    "quantity": Bug(
        key="quantity",
        title="Incorrect quantity calculation (off-by-one in line totals)",
        file="backend/app/pricing.py",
        original="    return unit_price_cents * quantity\n",
        buggy="    return unit_price_cents * max(quantity - 1, 1)\n",
        symptom="Customers buying 2+ units are under-charged by one unit per line (revenue loss).",
        tests=("tests/test_quantity.py", "tests/test_pricing.py", "tests/test_checkout_api.py"),
        probe_check="quantity_line_totals",
    ),
    "discount": Bug(
        key="discount",
        title="Incorrect discount calculation (percentage divided by 10 instead of 100)",
        file="backend/app/pricing.py",
        original="        discount = subtotal_cents * promotion.value // 100\n",
        buggy="        discount = subtotal_cents * promotion.value // 10\n",
        symptom="Percentage promo codes give 10x the advertised discount; WELCOME10 makes orders free.",
        tests=("tests/test_discounts.py", "tests/test_checkout_api.py"),
        probe_check="promotion_discounts",
    ),
    "inventory": Bug(
        key="inventory",
        title="Broken inventory validation (only blocks fully sold-out items)",
        file="backend/app/inventory.py",
        original="        if requested_qty > available_qty:\n",
        buggy="        if available_qty <= 0:\n",
        symptom="Orders exceeding remaining stock are accepted, overselling low-stock products.",
        tests=("tests/test_inventory.py",),
        probe_check="inventory_oversell_guard",
    ),
    "images": Bug(
        key="images",
        title="Broken product image URLs (wrong CDN path prefix)",
        file="backend/app/catalog.py",
        original='_UNSPLASH = "https://images.unsplash.com/photo-"\n',
        buggy='_UNSPLASH = "https://images.unsplash.com/photos/"\n',
        symptom="Every product and category image points at a CDN path that does not exist, so no image loads.",
        tests=("tests/test_health_and_catalog.py",),
        probe_check="product_images",
    ),
    "stock": Bug(
        key="stock",
        title="Stock levels read the sold count instead of remaining stock",
        file="backend/app/checkout.py",
        original="        return {p.id: p.stock - int(sold.get(p.id, 0)) for p in self.catalog.products}\n",
        buggy="        return {p.id: int(sold.get(p.id, 0)) for p in self.catalog.products}\n",
        symptom="Products show as out of stock (0 left) and cannot be added to the cart.",
        tests=("tests/test_inventory.py", "tests/test_health_and_catalog.py"),
        probe_check="stock_levels",
    ),
}


class InjectError(RuntimeError):
    pass


def _path(bug: Bug) -> Path:
    path = (ROOT / bug.file).resolve()
    if BACKEND / "tests" in path.parents:
        raise InjectError("refusing to modify regression tests")
    return path


def state(bug: Bug) -> str:
    text = _path(bug).read_text(encoding="utf-8")
    has_original, has_buggy = text.count(bug.original), text.count(bug.buggy)
    if has_buggy == 1 and has_original == 0:
        return "injected"
    if has_original == 1 and has_buggy == 0:
        return "clean"
    return "unknown"


def _swap(bug: Bug, old: str, new: str) -> None:
    path = _path(bug)
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise InjectError(
            f"[{bug.key}] expected exactly 1 occurrence of {old.strip()!r} in {bug.file}, found {count}. "
            "The source may have been edited by hand; restore it with `git checkout -- " + bug.file + "`."
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def inject(bug: Bug) -> str:
    current = state(bug)
    if current == "injected":
        return "already injected"
    _swap(bug, bug.original, bug.buggy)
    return "injected"


def reset(bug: Bug) -> str:
    current = state(bug)
    if current == "clean":
        return "already clean"
    _swap(bug, bug.buggy, bug.original)
    return "reset"


def run_pytest(paths: tuple[str, ...] | None = None) -> subprocess.CompletedProcess[str]:
    venv_python = BACKEND / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    python = str(venv_python) if venv_python.exists() else sys.executable
    cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *(paths or ())]
    return subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True)


def _diff_line(bug: Bug) -> str:
    return f"--- {bug.file}\n-{bug.original.rstrip()}\n+{bug.buggy.rstrip()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="describe available bugs")
    sub.add_parser("status", help="show which bugs are injected")
    p_inject = sub.add_parser("inject", help="inject a bug")
    p_inject.add_argument("bug", choices=sorted(BUGS))
    p_reset = sub.add_parser("reset", help="reset one bug or all bugs")
    p_reset.add_argument("bug", nargs="?", default="all", choices=[*sorted(BUGS), "all"])
    p_verify = sub.add_parser("verify", help="inject, prove the tests fail, then reset")
    p_verify.add_argument("bug", choices=sorted(BUGS))
    args = parser.parse_args(argv)

    def emit(payload: dict, text: str) -> None:
        print(json.dumps(payload, indent=2) if args.json else text)

    try:
        if args.command == "list":
            payload = {"bugs": [{**asdict(b), "state": state(b)} for b in BUGS.values()]}
            text = "\n\n".join(
                f"{b.key:<10} {b.title}\n           file:    {b.file}\n           symptom: {b.symptom}\n"
                f"           tests:   {', '.join(b.tests)}\n           probe:   /api/health/checkout -> {b.probe_check}"
                for b in BUGS.values()
            )
            emit(payload, text)
        elif args.command == "status":
            states = {k: state(b) for k, b in BUGS.items()}
            emit({"bugs": states}, "\n".join(f"{k:<10} {v}" for k, v in states.items()))
        elif args.command == "inject":
            bug = BUGS[args.bug]
            result = inject(bug)
            emit(
                {"bug": bug.key, "result": result, "file": bug.file, "original": bug.original.strip(),
                 "buggy": bug.buggy.strip()},
                f"[{bug.key}] {result}: {bug.title}\n{_diff_line(bug)}\nExpected failing tests: {', '.join(bug.tests)}",
            )
        elif args.command == "reset":
            targets = BUGS.values() if args.bug == "all" else [BUGS[args.bug]]
            results = {b.key: reset(b) for b in targets}
            emit({"results": results}, "\n".join(f"[{k}] {v}" for k, v in results.items()))
        elif args.command == "verify":
            bug = BUGS[args.bug]
            if any(state(b) != "clean" for b in BUGS.values()):
                raise InjectError("all bugs must be reset before verify; run `python inject_bug.py reset all`")
            inject(bug)
            try:
                injected_run = run_pytest()
            finally:
                reset(bug)
            clean_run = run_pytest()
            summary = lambda run: (run.stdout.strip().splitlines() or ["<no output>"])[-1]  # noqa: E731
            ok = injected_run.returncode != 0 and clean_run.returncode == 0
            emit(
                {"bug": bug.key, "verified": ok, "with_bug": summary(injected_run), "after_reset": summary(clean_run),
                 "with_bug_exit_code": injected_run.returncode, "after_reset_exit_code": clean_run.returncode},
                f"[{bug.key}] with bug:    {summary(injected_run)}\n"
                f"[{bug.key}] after reset: {summary(clean_run)}\n"
                f"[{bug.key}] {'VERIFIED: tests catch the bug and pass after reset' if ok else 'NOT VERIFIED'}",
            )
            return 0 if ok else 1
    except InjectError as exc:
        emit({"error": str(exc)}, f"error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
