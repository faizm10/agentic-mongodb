import os
from pathlib import Path

import pytest

# Fixture apps (stand-in layouts), set before any test module loads its config.
os.environ["OPSRELAY_APPS_CONFIG"] = str(Path(__file__).with_name("apps.toml"))
# Tests use the local embedding: a VOYAGE_API_KEY in .env would make real, rate-limited Voyage calls.
os.environ["VOYAGE_API_KEY"] = ""

from .support import build_shopping_regression


@pytest.fixture(scope="session")
def shopping(tmp_path_factory):
    return build_shopping_regression(tmp_path_factory.mktemp("shopping"))


@pytest.fixture(scope="session")
def messaging(tmp_path_factory):
    from .messaging_support import build_messaging_repo

    return build_messaging_repo(tmp_path_factory.mktemp("messaging"))


@pytest.fixture
def deploy(messaging, tmp_path):
    """Start the Messaging fixture at a commit; every deployment is stopped after the test."""
    from .messaging_support import Deployment

    started = []

    def _deploy(sha: str, paused: bool = False):
        deployment = Deployment(messaging.repo, sha, tmp_path, paused=paused).start()
        started.append(deployment)
        return deployment

    yield _deploy
    for deployment in started:
        deployment.stop()
