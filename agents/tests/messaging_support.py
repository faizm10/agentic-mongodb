"""Messaging fixture: a small real service that implements the live-check probe contract.

Stands in for Taha's Messaging app until it lands. ``build_messaging_repo`` creates a
git repo with two commits: a working store, then "refactor: route by thread owner",
which files every message under the *sender's* inbox, so probes never arrive. The
service runs as a subprocess from an exported commit, so a live failure is caused by
the code at that commit and deploying a patch genuinely restores delivery.

Run it by hand:

    agents/.venv/bin/python -m agents.tests.messaging_support --mode healthy --port 8002
    agents/.venv/bin/python -m agents.tests.messaging_support --mode bug     --port 8002
    agents/.venv/bin/python -m agents.tests.messaging_support --mode paused  --port 8002
    agents/.venv/bin/python -m agents.tests.messaging_support --mode hang    --port 8002
"""

from __future__ import annotations

import argparse
import io
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from agents import RepairRequest
from agents.investigator import _DiagnosisDraft, _EvidenceDraft
from agents.patcher import _Candidate, _Edit, _PatchDraft

from .support import StubLLM, _git

STORE_PATH = "message-platform/src/messaging/store.py"
BUGGY_LINE = "        inbox = normalize(message.sender)\n"
FIXED_LINE = "        inbox = normalize(message.recipient)\n"

STORE_GOOD = '''"""In-memory message store for the demo messaging service."""

import itertools
import time
from dataclasses import dataclass, field


@dataclass
class Message:
    id: str
    sender: str
    recipient: str
    body: str
    probe_id: str | None = None
    inbox: str = ""
    sent_at: float = field(default_factory=time.time)


def normalize(address: str) -> str:
    return address.strip().lower()


class MessageStore:
    def __init__(self) -> None:
        self._inboxes: dict[str, list[Message]] = {}
        self._ids = itertools.count(1)

    def send(self, sender: str, recipient: str, body: str, probe_id: str | None = None) -> Message:
        message = Message(f"m{next(self._ids)}", sender, recipient, body, probe_id)
        self._deliver(message)
        return message

    def _deliver(self, message: Message) -> None:
''' + FIXED_LINE + '''        message.inbox = inbox
        self._inboxes.setdefault(inbox, []).append(message)

    def inbox(self, name: str) -> list[Message]:
        return list(self._inboxes.get(normalize(name), []))
'''

STORE_BUGGY = STORE_GOOD.replace(FIXED_LINE, BUGGY_LINE)

SERVER = '''"""HTTP API for the demo messaging service, including the OpsRelay probe endpoints."""

import argparse
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from messaging.store import MessageStore

log = logging.getLogger("messaging")
STORE = MessageStore()
DELIVERY_PAUSED = os.environ.get("MESSAGING_DELIVERY_PAUSED") == "1"
PROBE_SENDER = "opsrelay-probe-sender"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self._json(200, {"status": "ok"})
        if url.path.startswith("/probe/inbox/"):
            inbox = unquote(url.path[len("/probe/inbox/"):])
            probe_id = parse_qs(url.query).get("probe_id", [None])[0]
            messages = [m for m in STORE.inbox(inbox) if probe_id is None or m.probe_id == probe_id]
            return self._json(200, {"inbox": inbox, "messages": [
                {"message_id": m.id, "probe_id": m.probe_id, "sent_at": m.sent_at} for m in messages]})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/probe/messages":
            return self._json(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if DELIVERY_PAUSED:
            log.warning("delivery queue paused; probe_id=%s for inbox=%s queued, not delivered",
                        body.get("probe_id"), body.get("inbox"))
            return self._json(202, {"probe_id": body.get("probe_id"), "message_id": None, "status": "queued"})
        message = STORE.send(PROBE_SENDER, body["inbox"], body.get("body", ""), body.get("probe_id"))
        log.info("delivered message_id=%s probe_id=%s to inbox=%s", message.id, message.probe_id, message.inbox)
        return self._json(202, {"probe_id": message.probe_id, "message_id": message.id, "status": "accepted"})

    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--log-file")
    args = parser.parse_args()
    logging.basicConfig(filename=args.log_file, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"listening on {server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
'''

TEST_STORE = '''from messaging.store import MessageStore


def test_message_lands_in_recipient_inbox():
    store = MessageStore()
    store.send("alice", "bob", "hi")
    assert [m.body for m in store.inbox("bob")] == ["hi"]
    assert store.inbox("alice") == []


def test_inbox_lookup_normalises_address():
    store = MessageStore()
    store.send("alice", " Bob ", "hi")
    assert len(store.inbox("BOB")) == 1


def test_unknown_inbox_is_empty():
    assert MessageStore().inbox("nobody") == []
'''

PYPROJECT = '''[project]
name = "messaging"
version = "0.1.0"

[tool.pytest.ini_options]
pythonpath = ["src"]
'''


@dataclass
class MessagingRepo:
    repo: Path
    good_sha: str
    bad_sha: str
    recent_diff: str


def build_messaging_repo(root: Path) -> MessagingRepo:
    repo = root / "opsrelay-messaging"
    files = {
        STORE_PATH: STORE_GOOD,
        "message-platform/src/messaging/__init__.py": "",
        "message-platform/src/messaging/server.py": SERVER,
        "message-platform/tests/test_store.py": TEST_STORE,
        "message-platform/pyproject.toml": PYPROJECT,
    }
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "messaging: store and probe API")
    good = _git(repo, "rev-parse", "HEAD")
    (repo / STORE_PATH).write_text(STORE_BUGGY)
    _git(repo, "commit", "-q", "-am", "refactor: route by thread owner")
    bad = _git(repo, "rev-parse", "HEAD")
    return MessagingRepo(repo, good, bad, _git(repo, "diff", good, bad))


class Deployment:
    """The Messaging service running from an exported commit, like a deploy would."""

    def __init__(self, repo: Path, sha: str, workdir: Path, paused: bool = False):
        self.dir = workdir / f"deploy-{sha[:7]}-{int(time.monotonic() * 1000)}"
        self.dir.mkdir(parents=True)
        archive = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", sha],
                                 capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(self.dir, filter="data")
        self.sha = sha
        self.paused = paused
        self.log_file = self.dir / "service.log"
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, port: int = 0) -> Deployment:
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"}
        if self.paused:
            env["MESSAGING_DELIVERY_PAUSED"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "messaging.server", "--port", str(port), "--log-file", str(self.log_file)],
            cwd=self.dir / "message-platform", env=env, stdout=subprocess.PIPE, text=True,
        )
        line = self.proc.stdout.readline()
        if not line.startswith("listening on "):
            self.stop()
            raise RuntimeError(f"messaging fixture failed to start: {line!r}")
        self.port = int(line.split()[-1])
        return self

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait(timeout=5)
            self.proc = None

    def deploy_patch(self, diff: str) -> None:
        """Apply a verified patch to the deployed tree and restart on the same port."""
        subprocess.run(["git", "apply", "-"], cwd=self.dir, input=diff, text=True, check=True)
        port = self.port
        self.stop()
        self.start(port)

    def logs(self) -> str:
        return self.log_file.read_text() if self.log_file.exists() else ""


class HangingServer:
    """Accepts TCP connections but never answers: the checker sees a timeout."""

    def __init__(self, port: int = 0):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self._held: list[socket.socket] = []
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _accept(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                self._held.append(self.sock.accept()[0])
            except OSError:
                continue

    def close(self) -> None:
        self._stop.set()
        for conn in self._held:
            conn.close()
        self.sock.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- Stub model responses for the Messaging scenarios ---------------------------


def messaging_request(repo: MessagingRepo, **overrides) -> RepairRequest:
    fields = dict(app="messaging", repo_path=str(repo.repo), commit_sha=repo.bad_sha, failed_test_logs="",
                  recent_diff=repo.recent_diff, source_files={STORE_PATH: STORE_BUGGY})
    fields.update(overrides)
    return RepairRequest(**fields)


def code_bug_draft(**overrides) -> _DiagnosisDraft:
    fields = dict(
        outcome="diagnosed",
        likely_cause="MessageStore._deliver files each message under the sender's inbox instead of the recipient's.",
        explanation=(
            "The probe was accepted (HTTP 202) but never appeared in the opsrelay-probe inbox. The service log shows "
            "it was delivered to inbox=opsrelay-probe-sender, the probe's sender. The latest commit changed "
            "store.py:31 from normalize(message.recipient) to normalize(message.sender)."
        ),
        evidence=[
            _EvidenceDraft(source="live_probe", reference="live_probe", excerpt='"failure_kind": "not_delivered"',
                           supports="Send succeeded but read-back never saw the probe id."),
            _EvidenceDraft(source="service_logs", reference="service_logs", excerpt="to inbox=opsrelay-probe-sender",
                           supports="The message was filed under the sender's inbox."),
            _EvidenceDraft(source="recent_diff", reference="recent_diff", excerpt="+        inbox = normalize(message.sender)",
                           supports="The last commit switched the inbox key from recipient to sender."),
        ],
        suspect_files=[STORE_PATH],
        confidence=0.9,
        failure_class="code_bug",
        next_diagnostic=None,
        review_reason=None,
    )
    fields.update(overrides)
    return _DiagnosisDraft(**fields)


def infrastructure_draft() -> _DiagnosisDraft:
    return code_bug_draft(
        likely_cause="The delivery queue is paused, so accepted messages are queued but never delivered.",
        explanation="Send returned 202 'queued' and the service log says the delivery queue is paused.",
        evidence=[_EvidenceDraft(source="service_logs", reference="service_logs", excerpt="delivery queue paused",
                                 supports="Messages are accepted but held in the queue.")],
        failure_class="infrastructure",
        next_diagnostic="Check the delivery worker: is the queue paused or the consumer down? Resume it, then re-probe.",
    )


def messaging_fix_draft() -> _PatchDraft:
    return _PatchDraft(outcome="patch", review_reason=None, candidates=[_Candidate(
        summary="File messages under the recipient's inbox again.",
        edits=[_Edit(path=STORE_PATH, find=BUGGY_LINE, replace=FIXED_LINE)],
    )])


def messaging_llm(diagnosis: _DiagnosisDraft | None = None) -> StubLLM:
    return StubLLM({"_DiagnosisDraft": diagnosis or code_bug_draft(), "_PatchDraft": messaging_fix_draft()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Messaging fixture service locally.")
    parser.add_argument("--mode", choices=["healthy", "bug", "paused", "hang"], default="healthy")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        if args.mode == "hang":
            server = HangingServer(args.port)
            print(f"FIXTURE messaging (hang) accepting but never answering on {server.url}", flush=True)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                return
            finally:
                server.close()
        repo = build_messaging_repo(Path(tmp))
        sha = repo.good_sha if args.mode in ("healthy", "paused") else repo.bad_sha
        deployment = Deployment(repo.repo, sha, Path(tmp), paused=args.mode == "paused").start(args.port)
        print(f"FIXTURE messaging ({args.mode}, commit {sha[:7]}) on {deployment.url}; logs: {deployment.log_file}", flush=True)
        try:
            deployment.proc.wait()
        except KeyboardInterrupt:
            pass
        finally:
            deployment.stop()


if __name__ == "__main__":
    main()
