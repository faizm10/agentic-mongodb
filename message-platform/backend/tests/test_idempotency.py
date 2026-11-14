"""Rule 1 - no duplicate messages: retries with the same idempotency key are idempotent."""

import pytest

from app import messages

from ._assertions import expect_equal, expect_status
from .conftest import as_user

pytestmark = pytest.mark.opsrelay(component="messages", rule="Rule 1 - no duplicate messages")
RULE = "Rule 1 - no duplicate messages"
FN = "app.messages.send_message -> app.messages.find_duplicate"


def _count(conn, cid):
    return conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (cid,)).fetchone()[0]


def test_retry_with_same_key_returns_original_message(conn, clock):
    first, created_first = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana",
                                                 body="Lunch?", now=clock(), idempotency_key="retry-1")
    second, created_second = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana",
                                                   body="Lunch?", now=clock(), idempotency_key="retry-1")
    expect_equal(
        {"stored": _count(conn, "c_ana_ben"), "same_id": second["id"] == first["id"], "replay_created": created_second},
        {"stored": 1, "same_id": True, "replay_created": False},
        rule=RULE, what="retrying with the same idempotency key must not insert a copy", function=FN,
        context={"idempotency_key": "retry-1", "first_created": created_first},
    )


def test_many_retries_store_exactly_one_message(conn, clock):
    ids = {
        messages.send_message(conn, conversation_id="c_trio", sender_id="ben", body="On my way",
                              now=clock(), idempotency_key="flaky-network")[0]["id"]
        for _ in range(5)
    }
    expect_equal((len(ids), _count(conn, "c_trio")), (1, 1), rule=RULE,
                 what="5 retries of one send must store one message", function=FN)
    receipts = conn.execute("SELECT COUNT(*) FROM receipts r JOIN messages m ON m.id = r.message_id "
                            "WHERE m.conversation_id = 'c_trio'").fetchone()[0]
    assert receipts == 2, "one receipt per recipient, not per retry"


def test_distinct_keys_and_missing_keys_create_distinct_messages(conn, clock):
    messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="a", now=clock(), idempotency_key="k1")
    messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="a", now=clock(), idempotency_key="k2")
    messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="b", now=clock())
    messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="b", now=clock())
    assert _count(conn, "c_ana_ben") == 4


def test_keys_are_scoped_to_sender_and_conversation(conn, clock):
    a, _ = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="x", now=clock(), idempotency_key="same")
    b, _ = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ben", body="x", now=clock(), idempotency_key="same")
    c, _ = messages.send_message(conn, conversation_id="c_ana_cy", sender_id="ana", body="x", now=clock(), idempotency_key="same")
    assert len({a["id"], b["id"], c["id"]}) == 3


def test_api_replay_returns_200_with_same_message(client):
    payload = {"conversation_id": "c_alex_sarah", "body": "Ready for the review!", "idempotency_key": "client-op-42"}
    first = client.post("/api/messages", headers=as_user("u_alex"), json=payload)
    second = client.post("/api/messages", headers=as_user("u_alex"), json=payload)
    body1 = expect_status(first, 201, what="first send creates the message")
    body2 = expect_status(second, 200, what="retry must be an idempotent replay, not a new message")
    assert second.headers["Idempotent-Replay"] == "true" and first.headers["Idempotent-Replay"] == "false"
    history = client.get("/api/conversations/c_alex_sarah/messages", headers=as_user("u_sarah")).json()["messages"]
    copies = [m["id"] for m in history if m["body"] == "Ready for the review!"]
    expect_equal(copies, [body1["id"]], rule=RULE, what="recipient must see the message exactly once",
                 function="POST /api/messages", context={"replay_id": body2["id"]})
