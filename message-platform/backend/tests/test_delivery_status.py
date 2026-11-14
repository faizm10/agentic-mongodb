"""Rule 2 - correct delivery status: sent -> delivered -> read, only when conditions are met."""

import pytest

from app import delivery, messages, status
from app.errors import InvalidStatusTransitionError

from ._assertions import expect_equal
from .conftest import as_user

pytestmark = pytest.mark.opsrelay(component="delivery", rule="Rule 2 - correct delivery status")
RULE = "Rule 2 - correct delivery status"


def _receipt(conn, message_id, user_id):
    return conn.execute("SELECT status FROM receipts WHERE message_id=? AND user_id=?", (message_id, user_id)).fetchone()[0]


@pytest.mark.parametrize(
    ("recipient", "online", "expected"),
    [("ben", set(), False), ("ben", {"ana"}, False), ("ben", {"ana", "cy"}, False), ("ben", {"ben"}, True),
     ("ben", {"ana", "ben"}, True)],
)
def test_delivery_requires_the_recipient_to_be_connected(recipient, online, expected):
    expect_equal(delivery.is_delivery_confirmed(recipient, online), expected, rule=RULE,
                 what="delivery is confirmed only by the recipient's own connection",
                 function="app.delivery.is_delivery_confirmed",
                 context={"recipient": recipient, "online_user_ids": sorted(online)})


def test_new_message_starts_as_sent(conn, clock):
    msg, _ = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="hi", now=clock())
    assert msg["status"] == "sent" and [r["status"] for r in msg["receipts"]] == ["sent"]


def test_sender_online_does_not_deliver_to_offline_recipient(conn, clock):
    msg, _ = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="hi", now=clock())
    delivered = delivery.record_delivery(conn, msg["id"], ["ben"], {"ana"}, clock())
    expect_equal(
        {"delivered_to": delivered, "ben": _receipt(conn, msg["id"], "ben")},
        {"delivered_to": [], "ben": "sent"},
        rule=RULE, what="a message must stay 'sent' while the recipient is offline",
        function="app.delivery.record_delivery -> app.delivery.is_delivery_confirmed",
        context={"online_user_ids": ["ana"]},
    )


def test_online_recipient_is_delivered_then_read(conn, clock):
    msg, _ = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="hi", now=clock())
    assert delivery.record_delivery(conn, msg["id"], ["ben"], {"ana", "ben"}, clock()) == ["ben"]
    assert _receipt(conn, msg["id"], "ben") == "delivered"
    changes = delivery.mark_conversation_read(conn, "c_ana_ben", "ben", clock())
    assert [c["status"] for c in changes] == ["read"]
    full = messages.get_message(conn, msg["id"])
    assert full["status"] == "read" and full["receipts"][0]["read_at"] is not None


def test_group_status_is_least_advanced_recipient(conn, clock):
    msg, _ = messages.send_message(conn, conversation_id="c_trio", sender_id="ana", body="team", now=clock())
    delivery.record_delivery(conn, msg["id"], ["ben", "cy"], {"ben"}, clock())
    expect_equal(messages.get_message(conn, msg["id"])["status"], "sent", rule=RULE,
                 what="group message is only 'delivered' when every recipient has it",
                 function="app.status.aggregate_status", context={"online_user_ids": ["ben"]})
    delivery.deliver_pending(conn, "cy", clock())
    assert messages.get_message(conn, msg["id"])["status"] == "delivered"


@pytest.mark.parametrize(("current", "target", "ok"), [
    ("sent", "delivered", True), ("delivered", "read", True), ("sent", "read", False),
    ("read", "delivered", False), ("delivered", "sent", False), ("read", "read", False),
])
def test_state_machine(current, target, ok):
    assert status.can_transition(current, target) is ok


def test_invalid_transitions_are_rejected_with_details(conn, clock):
    msg, _ = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="hi", now=clock())
    with pytest.raises(InvalidStatusTransitionError) as info:
        delivery.set_receipt_status(conn, msg["id"], "ben", "read", clock())
    assert info.value.details["current"] == "sent" and info.value.details["allowed"] == ["delivered"]


def test_api_status_lifecycle_with_websockets(client):
    """Sender online, recipient offline -> 'sent'. Recipient connects -> 'delivered'. Reads -> 'read'."""
    with client.websocket_connect("/ws/u_alex") as alex_ws:
        assert alex_ws.receive_json()["type"] == "hello"
        sent = client.post("/api/messages", headers=as_user("u_alex"),
                           json={"conversation_id": "c_alex_jordan", "body": "Status check"}).json()
        expect_equal(sent["status"], "sent", rule=RULE, what="recipient offline: message must not be delivered",
                     function="POST /api/messages -> app.delivery.record_delivery",
                     context={"online": ["u_alex"], "recipient": "u_jordan"})
        with client.websocket_connect("/ws/u_jordan") as jordan_ws:
            assert jordan_ws.receive_json()["type"] == "hello"
            event = alex_ws.receive_json()
            while event["type"] != "receipts.updated":
                event = alex_ws.receive_json()
            assert event["changes"][0]["message_id"] == sent["id"] and event["changes"][0]["status"] == "delivered"
        read = client.post("/api/conversations/c_alex_jordan/read", headers=as_user("u_jordan")).json()
        assert read["unread_count"] == 0 and read["marked_read"] >= 1
    history = client.get("/api/conversations/c_alex_jordan/messages", headers=as_user("u_alex")).json()["messages"]
    assert next(m for m in history if m["id"] == sent["id"])["status"] == "read"


def test_fetching_history_delivers_pending_messages(client):
    sent = client.post("/api/messages", headers=as_user("u_alex"),
                       json={"conversation_id": "c_alex_emma", "body": "ping"}).json()
    assert sent["status"] == "sent"
    client.get("/api/conversations/c_alex_emma/messages", headers=as_user("u_emma"))
    history = client.get("/api/conversations/c_alex_emma/messages", headers=as_user("u_alex")).json()["messages"]
    assert history[-1]["status"] == "delivered"
