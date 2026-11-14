"""Structured assertion helpers.

Failures read like:

    [OpsRelay] pricing.line_total FAILED: line total must equal unit price x quantity
      function under test: app.pricing.line_total
      expected: 10500 cents ($105.00)
      actual:   7000 cents ($70.00)
      delta:    -3500 cents (-$35.00)
      context:  {"quantity": 3, "unit_price_cents": 3500}
"""

from __future__ import annotations

import json
from typing import Any


def _fmt(value: Any, money: bool) -> str:
    if money and isinstance(value, int) and not isinstance(value, bool):
        return f"{value} cents (${value / 100:,.2f})"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return repr(value)


def expect_equal(
    actual: Any,
    expected: Any,
    *,
    check: str,
    rule: str,
    function: str,
    context: dict[str, Any] | None = None,
    money: bool = False,
) -> None:
    __tracebackhide__ = True
    if actual == expected:
        return
    lines = [
        f"[OpsRelay] {check} FAILED: {rule}",
        f"  function under test: {function}",
        f"  expected: {_fmt(expected, money)}",
        f"  actual:   {_fmt(actual, money)}",
    ]
    if money and isinstance(actual, int) and isinstance(expected, int):
        delta = actual - expected
        sign = "-" if delta < 0 else "+"
        lines.append(f"  delta:    {delta} cents ({sign}${abs(delta) / 100:,.2f})")
    if context:
        lines.append(f"  context:  {json.dumps(context, sort_keys=True, default=str)}")
    raise AssertionError("\n".join(lines))


def expect_status(response, expected_status: int, *, check: str, rule: str) -> dict[str, Any]:
    """Assert an HTTP status and include the structured API error body on failure."""
    __tracebackhide__ = True
    body = response.json()
    if response.status_code != expected_status:
        raise AssertionError(
            "\n".join(
                [
                    f"[OpsRelay] {check} FAILED: {rule}",
                    f"  endpoint: {response.request.method} {response.request.url.path}",
                    f"  expected status: {expected_status}",
                    f"  actual status:   {response.status_code}",
                    f"  response body:   {json.dumps(body, sort_keys=True, default=str)[:1500]}",
                ]
            )
        )
    return body
