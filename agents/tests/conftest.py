import pytest

from .support import build_shopping_regression


@pytest.fixture(scope="session")
def shopping(tmp_path_factory):
    return build_shopping_regression(tmp_path_factory.mktemp("shopping"))
