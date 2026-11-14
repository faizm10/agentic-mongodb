import json

import pytest

from opsrelay_dashboard.ci import GitHubActions


def run(status="completed", conclusion="success", sha="abcdef1234"):
    return {"id": 1, "display_title": "fix cart", "status": status, "conclusion": conclusion,
            "head_branch": "main", "head_sha": sha, "created_at": "2026-11-14T10:00:00Z", "html_url": "https://github.com/x"}


def fake(responses):
    calls = []

    def fetch(url, headers):
        calls.append((url, headers))
        return responses.pop(0)
    return fetch, calls


@pytest.mark.parametrize("runs, state", [
    ([run()], "passing"),
    ([run(conclusion="failure"), run()], "failing"),
    ([run(status="in_progress", conclusion=None)], "running"),
    ([], "no_runs"),
])
def test_state_reflects_latest_run(runs, state):
    fetch, _ = fake([(200, json.dumps({"workflow_runs": runs}).encode())])

    result = GitHubActions("faizm10/agentic-mongodb", fetch=fetch).latest("shopping.yml")

    assert result["state"] == state
    if runs:
        assert result["runs"][0]["sha"] == "abcdef1"


def test_results_are_cached_and_token_is_sent():
    fetch, calls = fake([(200, b'{"workflow_runs": []}')])
    now = [0.0]
    gh = GitHubActions("o/r", token="t0k", fetch=fetch, clock=lambda: now[0], ttl_seconds=60)

    gh.latest("ci.yml")
    now[0] = 30
    gh.latest("ci.yml")

    assert len(calls) == 1
    url, headers = calls[0]
    assert url == "https://api.github.com/repos/o/r/actions/workflows/ci.yml/runs?per_page=5"
    assert headers["Authorization"] == "Bearer t0k"


def test_missing_workflow_and_errors_are_reported_not_raised():
    fetch, _ = fake([(404, b"{}"), (403, b"{}")])
    gh = GitHubActions("o/r", fetch=fetch, ttl_seconds=0)

    assert gh.latest("missing.yml")["state"] == "unconfigured"
    assert gh.latest("limited.yml")["error"] == "GitHub API returned HTTP 403"
    assert gh.latest(None)["state"] == "unconfigured"


def test_names_are_validated_before_building_urls():
    with pytest.raises(ValueError):
        GitHubActions("o/r/../../evil")
    fetch, calls = fake([])

    assert GitHubActions("o/r", fetch=fetch).latest("../x.yml?a=")["state"] == "unknown"
    assert calls == []
