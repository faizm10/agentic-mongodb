"""Expected-vs-actual assertions that read well in CI logs.

    [OpsRelay] Rule 1 - no duplicate messages FAILED: retry with the same idempotency key must not insert a copy
      function under test: app.messages.send_message -> app.messages.find_duplicate
      expected: 1
      actual:   2
      context:  {"idempotency_key": "retry-1"}
"""

from __future__ import annotations

import json
from typing import Any


def _fmt(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    return repr(value)


def expect_equal(actual: Any, expected: Any, *, rule: str, what: str, function: str,
                 context: dict[str, Any] | None = None) -> None:
    __tracebackhide__ = True
    if actual == expected:
        return
    lines = [
        f"[OpsRelay] {rule} FAILED: {what}",
        f"  function under test: {function}",
        f"  expected: {_fmt(expected)}",
        f"  actual:   {_fmt(actual)}",
    ]
    if context:
        lines.append(f"  context:  {_fmt(context)}")
    raise AssertionError("\n".join(lines))


def expect_status(response, expected: int, *, what: str) -> Any:
    __tracebackhide__ = True
    body = response.json()
    if response.status_code != expected:
        raise AssertionError(
            f"[OpsRelay] HTTP {response.request.method} {response.request.url.path} FAILED: {what}\n"
            f"  expected status: {expected}\n  actual status:   {response.status_code}\n"
            f"  response body:   {json.dumps(body, sort_keys=True, default=str)[:1500]}"
        )
    return body
