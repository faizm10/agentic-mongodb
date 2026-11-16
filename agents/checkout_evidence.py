"""Evidence for a failed NovaMart checkout probe, gathered before the repair agent runs.

The uptime monitor hands over the failing ``checkout_completes`` check, which names the
synthetic checkout's ``checkout_id`` and ``request_id``. From those this module reads:

* NovaMart's unfinished checkout attempts around the incident (order and confirmation status),
* NovaConnect's confirmation record for each one (looked up by its stable confirmation ID),
* log lines from both services that mention those IDs,
* whether a recent commit touched the file named by the probe's source hint,
* the app's CI status, when known.

It then states where the failure happened and what is known about why. A timeout on its own
says only that the other service did not answer in time, so the cause stays ``unknown``
unless other evidence explains it. ``recommend`` turns this and the repair result into one
action: ``propose_code_fix``, ``operational_step`` or ``ask_for_review``.

Nothing here knows which fault the demo injected; it reads only what the services record.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .recovery import LookupFailed, NovaConnectRecords, NovaMartRecords

CHECK = "checkout_completes"
CONFIRMATION_PREFIX = "cnf-"
LOOKBACK = timedelta(minutes=5)
MAX_ATTEMPTS = 20
MAX_LOG_LINES = 60
REPO = Path(__file__).resolve().parents[1]
LOG_FILES = {
    "NovaMart": ("OPSRELAY_SHOPPING_LOG_FILE", REPO / ".dev" / "logs" / "novamart-api.log"),
    "NovaConnect": ("OPSRELAY_MESSAGING_LOG_FILE", REPO / ".dev" / "logs" / "novaconnect-api.log"),
}
_TIMEOUT_KINDS = {"timeout", "connection_error"}


@dataclass
class CheckoutEvidence:
    probe: dict[str, Any] | None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    log_sources: dict[str, str] = field(default_factory=dict)
    lookup_errors: list[str] = field(default_factory=list)
    hinted_file: str | None = None
    hinted_file_changed_in: list[str] = field(default_factory=list)
    ci_status: str = "unknown"

    # ---- conclusions --------------------------------------------------- #
    @property
    def confirmation_error(self) -> dict[str, Any]:
        return ((self.probe or {}).get("confirmation") or {}).get("error") or {}

    @property
    def dependency_timed_out(self) -> bool:
        return self.confirmation_error.get("kind") in _TIMEOUT_KINDS

    @property
    def orders_saved_but_failed(self) -> list[str]:
        """Checkouts whose order exists although the customer was told checkout failed."""
        found = [a["checkout_id"] for a in self.attempts
                 if a["order_id"] and ((a.get("customer_saw") or {}).get("status_code") or 0) >= 500]
        if self.probe and self.probe.get("order_saved") and (self.probe.get("http_status") or 0) >= 500:
            found.insert(0, self.probe["checkout_id"])
        return found

    @property
    def failure_point(self) -> str:
        p = self.probe
        if not p:
            return "unknown: the failing probe did not report a checkout attempt"
        answer = f"checkout returned {p.get('error_code')} (HTTP {p.get('http_status')})"
        if not p.get("order_saved"):
            return f"before the order was saved: {answer}"
        err = self.confirmation_error
        if err:
            return (f"after NovaMart saved order {p.get('order_id')}: the confirmation request to NovaConnect "
                    f"ended with {err.get('kind')} after {err.get('elapsed_ms')} ms, and {answer}")
        return f"after NovaMart saved order {p.get('order_id')}: {answer}"

    @property
    def cause(self) -> str:
        if self.dependency_timed_out:
            return ("unknown: NovaConnect did not answer the confirmation request in time; a timeout alone "
                    "does not show why")
        return "unknown"

    @property
    def code_change_suspected(self) -> bool:
        return bool(self.hinted_file_changed_in)

    # ---- rendering ----------------------------------------------------- #
    def facts(self) -> list[str]:
        p = self.probe or {}
        lines = [f"Failure point: {self.failure_point}", f"Known cause: {self.cause}"]
        if p:
            lines.append(f"Failed probe: checkout_id={p.get('checkout_id')} request_id={p.get('request_id')} "
                         f"order_saved={p.get('order_saved')} order_id={p.get('order_id')} "
                         f"confirmation={json.dumps(p.get('confirmation'), default=str)}")
        saved = self.orders_saved_but_failed
        if saved:
            lines.append(f"{len(saved)} checkout(s) have a saved order although the response was a 5xx: "
                         f"{', '.join(saved[:8])}")
        if self.hinted_file:
            changed = ", ".join(self.hinted_file_changed_in) or "none of the last commits"
            lines.append(f"Probe source hint {self.hinted_file} was changed in: {changed}")
        lines.append(f"CI status for the app: {self.ci_status}")
        lines += [f"Lookup failed: {e}" for e in self.lookup_errors]
        return lines

    def text(self) -> str:
        """For the model, alongside the probe details and recent diff."""
        parts = ["== Checkout evidence ==", *self.facts(), "", "== Affected checkout attempts =="]
        parts += [json.dumps(a, default=str) for a in self.attempts] or ["(none found)"]
        parts += ["", "== Correlated service logs ==",
                  *[f"{name}: {src}" for name, src in self.log_sources.items()], *self.log_lines]
        return "\n".join(parts)

    def markdown(self) -> list[str]:
        lines = ["", "**Checkout evidence:**", *[f"- {f}" for f in self.facts()]]
        if self.attempts:
            lines.append(f"\n**Affected checkouts ({len(self.attempts)}):**")
            for a in self.attempts[:10]:
                saw = a.get("customer_saw") or {}
                lines.append(f"- `{a['checkout_id']}`: state {a['state']}, order {a['order_id'] or 'none'}, "
                             f"NovaMart confirmation status {a['confirmation_status']}, NovaConnect record "
                             f"{a['novaconnect']}, customer saw HTTP {saw.get('status_code', 'n/a')}")
        return lines


def probe_failure(live: dict[str, Any]) -> dict[str, Any] | None:
    for f in live.get("probe_failures") or []:
        if (f.get("check") or f.get("name")) == CHECK:
            return f
    return None


def collect(live: dict[str, Any], *, novamart: NovaMartRecords | None = None,
            novaconnect: NovaConnectRecords | None = None, changed_in=None,
            ci_status: str | None = None) -> CheckoutEvidence | None:
    """Evidence for a live NovaMart incident whose checkout probe failed, else None.

    ``changed_in(path) -> list[str]`` names recent commits that touched ``path`` (repo-relative).
    """
    failure = probe_failure(live)
    if failure is None:
        return None
    actual = failure.get("actual") if isinstance(failure.get("actual"), dict) else {}
    hint = str(failure.get("source") or "").split("::", 1)[0] or None
    hinted = f"shopping/{hint}" if hint and not hint.startswith("shopping/") else hint
    evidence = CheckoutEvidence(probe=actual or None, hinted_file=hinted,
                                ci_status=ci_status or live.get("ci_status") or "unknown")
    if hinted and changed_in is not None:
        evidence.hinted_file_changed_in = list(changed_in(hinted))

    novamart = novamart or NovaMartRecords()
    novaconnect = novaconnect or NovaConnectRecords()
    started = _when(live.get("started_at")) or datetime.now().astimezone()
    try:
        attempts = novamart.unfinished_checkouts(started - LOOKBACK, datetime.now().astimezone())[:MAX_ATTEMPTS]
    except LookupFailed as exc:
        attempts = []
        evidence.lookup_errors.append(f"NovaMart checkout attempts ({exc.kind}): {exc.message}")
    for a in attempts:
        evidence.attempts.append(_attempt(a, novaconnect, evidence.lookup_errors))

    ids = [i for i in [(evidence.probe or {}).get("request_id"), (evidence.probe or {}).get("checkout_id")] if i]
    for a in evidence.attempts:
        ids += [a["checkout_id"], *a["request_ids"]]
    for name, (env, default) in LOG_FILES.items():
        path = Path(os.getenv(env) or default)
        if not path.is_file():
            evidence.log_sources[name] = f"missing: {path}"
            continue
        evidence.log_sources[name] = str(path)
        evidence.log_lines += [f"[{name}] {ln}" for ln in _matching(path, ids)]
    evidence.log_lines = evidence.log_lines[-MAX_LOG_LINES:]
    return evidence


def recommend(evidence: CheckoutEvidence | None, result: dict[str, Any],
              operational: bool = False) -> dict[str, Any]:
    """One action for the incident record and the PR, from the evidence and the repair result.

    ``operational`` marks results that already ruled out code (service health, a provider outage).
    """
    if evidence is None:
        action = ("propose_code_fix" if result.get("outcome") == "verified"
                  else "operational_step" if operational else "ask_for_review")
        return {"action": action, "failure_point": "see the failing probe checks",
                "cause": result.get("root_cause") or "unknown", "evidence": [], "detail": result.get("reason") or ""}
    base = {"failure_point": evidence.failure_point, "cause": evidence.cause, "evidence": evidence.facts()}
    if result.get("outcome") == "verified":
        return {**base, "action": "propose_code_fix", "cause": result.get("root_cause") or evidence.cause,
                "detail": "The patch reproduces the failure before the change and passes the tests after it. "
                          "A human reviews and merges the draft PR; a fresh checkout then verifies the fix."}
    if evidence.dependency_timed_out and not evidence.code_change_suspected:
        return {**base, "action": "operational_step",
                "detail": "Check NovaConnect's latency and error logs for the confirmation requests listed "
                          "above. No recent NovaMart change explains the failure, so no code fix is proposed."}
    return {**base, "action": "ask_for_review",
            "detail": result.get("reason") or "The evidence does not support one action on its own."}


def recommendation_markdown(rec: dict[str, Any] | None) -> str:
    if not rec:
        return ""
    label = {"propose_code_fix": "Propose a code fix", "operational_step": "Operational step",
             "ask_for_review": "Ask for review"}[rec["action"]]
    return "\n".join(["", f"**Recommendation:** {label}. {rec['detail']}",
                      f"- Failure point: {rec['failure_point']}", f"- Cause: {rec['cause']}"])


def _attempt(a: dict[str, Any], novaconnect: NovaConnectRecords, errors: list[str]) -> dict[str, Any]:
    confirmation_id = a.get("confirmation_id") or f"{CONFIRMATION_PREFIX}{a['checkout_id']}"
    if a.get("order_id"):
        try:
            record = novaconnect.confirmation(confirmation_id)
            found = f"{record['status']}" if record else "absent"
        except LookupFailed as exc:
            found = "lookup_failed"
            errors.append(f"NovaConnect {confirmation_id} ({exc.kind}): {exc.message}")
    else:
        found = "not_checked (no order)"
    response = a.get("response") or {}
    return {"checkout_id": a["checkout_id"], "state": a.get("state"), "order_id": a.get("order_id"),
            "confirmation_id": confirmation_id if a.get("order_id") else None,
            "confirmation_status": (a.get("confirmation") or {}).get("status"),
            "novaconnect": found, "request_ids": list(a.get("request_ids") or []),
            "customer_saw": {"status_code": response.get("status_code"), "error_code": response.get("error_code")}
            if response else None}


def _matching(path: Path, ids: list[str]) -> list[str]:
    keys = tuple(dict.fromkeys(i for i in ids if i))
    if not keys:
        return []
    lines = path.read_text(errors="replace").splitlines()[-5000:]
    return [ln[:500] for ln in lines if any(k in ln for k in keys)][-MAX_LOG_LINES:]


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None
