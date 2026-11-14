"""Sending, receiving, editing, deleting, reactions, pins, search and invalid input."""

import pytest

from app import messages
from app.errors import ForbiddenMessageActionError, InvalidMessageError

from ._assertions import expect_equal, expect_status
from .conftest import as_user

pytestmark = pytest.mark.opsrelay(component="messages", rule="message lifecycle")


def send(client, user, cid, body, **extra):
    return client.post("/api/messages", headers=as_user(user), json={"conversation_id": cid, "body": body, **extra})


def test_send_and_receive(client):
    sent = expect_status(send(client, "u_alex", "c_alex_jordan", "Here's the icon set 🎁", idempotency_key="k-1"),
                         201, what="message must be created")
    assert sent["sender_id"] == "u_alex" and sent["body"] == "Here's the icon set 🎁"
    assert sent["status"] == "sent" and sent["receipts"][0]["user_id"] == "u_jordan"
    history = client.get("/api/conversations/c_alex_jordan/messages", headers=as_user("u_jordan")).json()
    expect_equal(history["messages"][-1]["id"], sent["id"], rule="send/receive",
                 what="recipient must receive the new message last", function="GET /api/conversations/{id}/messages")
    convs = client.get("/api/conversations", headers=as_user("u_jordan")).json()
    top = convs[0]
    assert top["id"] == "c_alex_jordan" and top["last_message"]["body"] == "Here's the icon set 🎁"


def test_new_message_increments_recipient_unread_count(client):
    before = {c["id"]: c["unread_count"] for c in client.get("/api/conversations", headers=as_user("u_emma")).json()}
    send(client, "u_alex", "c_alex_emma", "Hero image looks great")
    after = {c["id"]: c["unread_count"] for c in client.get("/api/conversations", headers=as_user("u_emma")).json()}
    assert after["c_alex_emma"] == before["c_alex_emma"] + 1


@pytest.mark.parametrize(("body", "code"), [("", "INVALID_MESSAGE"), ("   \n ", "INVALID_MESSAGE"),
                                            ("x" * 4001, "INVALID_MESSAGE")])
def test_invalid_message_bodies(client, body, code):
    response = expect_status(send(client, "u_alex", "c_alex_sarah", body), 422, what="invalid body rejected")
    assert response["error"]["code"] == code


def test_unknown_conversation_and_non_participant(client):
    assert send(client, "u_alex", "c_nope", "hi").json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert send(client, "u_alex", "c_sarah_ryan", "hi").json()["error"]["code"] == "NOT_A_PARTICIPANT"


def test_edit_own_message(client):
    sent = send(client, "u_alex", "c_alex_sarah", "See you at 3").json()
    edited = expect_status(client.patch(f"/api/messages/{sent['id']}", headers=as_user("u_alex"),
                                        json={"body": "See you at 3pm"}), 200, what="edit own message")
    assert edited["body"] == "See you at 3pm" and edited["edited_at"] is not None
    assert edited["created_at"] == sent["created_at"] and edited["id"] == sent["id"]


def test_cannot_edit_or_delete_someone_elses_message(client):
    sent = send(client, "u_sarah", "c_alex_sarah", "Mine").json()
    for response in (
        client.patch(f"/api/messages/{sent['id']}", headers=as_user("u_alex"), json={"body": "hacked"}),
        client.delete(f"/api/messages/{sent['id']}", headers=as_user("u_alex")),
    ):
        assert expect_status(response, 403, what="only the sender may change a message")["error"]["code"] == \
            "FORBIDDEN_MESSAGE_ACTION"


def test_delete_is_soft_and_keeps_timeline_position(client):
    sent = send(client, "u_alex", "c_alex_sarah", "Oops, wrong chat").json()
    send(client, "u_alex", "c_alex_sarah", "Next message")
    deleted = expect_status(client.delete(f"/api/messages/{sent['id']}", headers=as_user("u_alex")), 200,
                            what="delete own message")
    assert deleted["deleted"] is True and deleted["body"] == ""
    history = client.get("/api/conversations/c_alex_sarah/messages", headers=as_user("u_sarah")).json()["messages"]
    assert [m["id"] for m in history][-2] == sent["id"]
    assert client.patch(f"/api/messages/{sent['id']}", headers=as_user("u_alex"),
                        json={"body": "again"}).status_code == 422


def test_reactions_toggle_and_aggregate(client):
    sent = send(client, "u_alex", "c_launch_squad", "We shipped! 🚀").json()
    for user in ("u_sarah", "u_emma"):
        client.post(f"/api/messages/{sent['id']}/reactions", headers=as_user(user), json={"emoji": "🎉"})
    message = client.post(f"/api/messages/{sent['id']}/reactions", headers=as_user("u_ryan"),
                          json={"emoji": "🔥"}).json()
    expect_equal(
        [(r["emoji"], r["count"], r["user_ids"]) for r in message["reactions"]],
        [("🎉", 2, ["u_sarah", "u_emma"]), ("🔥", 1, ["u_ryan"])],
        rule="reactions", what="reactions are grouped per emoji", function="app.messages.toggle_reaction",
    )
    toggled = client.post(f"/api/messages/{sent['id']}/reactions", headers=as_user("u_sarah"), json={"emoji": "🎉"})
    assert [(r["emoji"], r["count"]) for r in toggled.json()["reactions"]] == [("🎉", 1), ("🔥", 1)]


def test_pins(client):
    sent = send(client, "u_alex", "c_design_crit", "Agenda doc: https://docs.example.com/agenda").json()
    pinned = client.post(f"/api/messages/{sent['id']}/pin", headers=as_user("u_jordan")).json()
    assert pinned["pinned"] is True and pinned["pinned_by"] == "u_jordan"
    history = client.get("/api/conversations/c_design_crit/messages", headers=as_user("u_alex")).json()
    assert sent["id"] in [m["id"] for m in history["pinned"]]


def test_search_finds_messages_and_conversations(client):
    body = client.get("/api/search", params={"q": "embargo"}, headers=as_user("u_alex")).json()
    assert [m["conversation_id"] for m in body["messages"]] == ["c_launch_squad"]
    people = client.get("/api/search", params={"q": "taylor"}, headers=as_user("u_alex")).json()
    assert "c_alex_taylor" in [c["id"] for c in people["conversations"]]


def test_unit_validation(conn, clock):
    with pytest.raises(InvalidMessageError):
        messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body=" ", now=clock())
    msg, _ = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="hi", now=clock())
    with pytest.raises(ForbiddenMessageActionError):
        messages.edit_message(conn, msg["id"], "ben", "nope", clock())
    with pytest.raises(InvalidMessageError):
        messages.toggle_reaction(conn, msg["id"], "ben", "", clock())
