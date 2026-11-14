"""Live model check. Skipped unless OPENAI_API_KEY is set."""

import os

import pytest

from agents import OpenAIStructuredLLM, repair_ci_failure

from .support import CART_PATH

pytestmark = pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")


def test_live_model_repairs_shopping_discount(shopping):
    result = repair_ci_failure(shopping.request(), llm=OpenAIStructuredLLM())

    assert result.diagnosis is not None
    print(result.model_dump_json(indent=2))
    assert result.outcome == "verified", result.reason
    assert result.changed_files == [CART_PATH]
