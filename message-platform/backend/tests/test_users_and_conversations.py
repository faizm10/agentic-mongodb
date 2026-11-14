"""User retrieval and conversation creation/listing."""

import pytest

from app import conversations
from app.errors import InvalidConversationError, NotAParticipantError, UserNotFoundError

from ._assertions import expect_equal, expect_status
from .conftest import as_user

pytestmark = pytest.mark.opsrelay(component="conversations", rule="users and conversations")


def test_list_demo_users(client):
    body = expect_status(client.get("/api/users"), 200, what="users must be listable")
    names = [u["name"] for u in body]
    assert names == sorted(names)
    assert {"Alex Morgan", "Sarah Chen", "Jordan Lee", "Emma Wilson", "Ryan Patel", "Taylor Brooks"} == set(names)
    assert all(set(u) >= {"id", "name", "handle", "title", "avatar_hue"} for u in body)


def test_missing_and_unknown_demo_user_are_structured_errors(client):
    missing = expect_status(client.get("/api/conversations"), 401, what="requests need a demo user")
    assert missing["error"]["code"] == "MISSING_DEMO_USER"
    unknown = expect_status(client.get("/api/conversations", headers=as_user("u_ghost")), 404, what="unknown user")
    assert unknown["error"] == {
        "code": "USER_NOT_FOUND", "component": "users", "message": "User 'u_ghost' does not exist.",
        "details": {"user_id": "u_ghost"},
    }


def test_conversation_list_is_scoped_sorted_and_counts_unread(client):
    convs = expect_status(client.get("/api/conversations", headers=as_user("u_alex")), 200, what="list conversations")
    ids = [c["id"] for c in convs]
    assert "c_sarah_ryan" not in ids, "Alex must not see a DM he is not part of"
    assert len(ids) == 7
    updated = [c["updated_at"] for c in convs]
    assert updated == sorted(updated, reverse=True), "most recent conversation first"
    unread = {c["id"]: c["unread_count"] for c in convs}
    expect_equal(
        {k: unread[k] for k in ("c_alex_sarah", "c_launch_squad", "c_alex_jordan", "c_alex_emma")},
        {"c_alex_sarah": 2, "c_launch_squad": 3, "c_alex_jordan": 1, "c_alex_emma": 0},
        rule="unread counts", what="seeded unread counts", function="app.conversations.unread_count",
    )
    dm = next(c for c in convs if c["id"] == "c_alex_sarah")
    assert dm["title"] == "Sarah Chen" and dm["kind"] == "direct"


def test_create_direct_conversation_is_unique_per_pair(client):
    created = client.post("/api/conversations", headers=as_user("u_jordan"),
                          json={"kind": "direct", "participant_ids": ["u_ryan"]})
    body = expect_status(created, 201, what="new DM must be created")
    assert sorted(body["participant_ids"]) == ["u_jordan", "u_ryan"]
    again = client.post("/api/conversations", headers=as_user("u_ryan"),
                        json={"kind": "direct", "participant_ids": ["u_jordan"]})
    assert expect_status(again, 200, what="existing DM is returned")["id"] == body["id"]


def test_create_group_conversation(client):
    body = expect_status(
        client.post("/api/conversations", headers=as_user("u_emma"),
                    json={"kind": "group", "participant_ids": ["u_ryan", "u_taylor"], "title": "  Press Room  "}),
        201, what="group must be created",
    )
    assert body["title"] == "Press Room" and body["kind"] == "group"
    assert body["participant_ids"][0] == "u_emma" and len(body["participant_ids"]) == 3


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"kind": "direct", "participant_ids": ["u_ryan", "u_emma"]}, "INVALID_CONVERSATION"),
        ({"kind": "group", "participant_ids": ["u_ryan"], "title": "Duo"}, "INVALID_CONVERSATION"),
        ({"kind": "group", "participant_ids": ["u_ryan", "u_emma"], "title": "   "}, "INVALID_CONVERSATION"),
        ({"kind": "direct", "participant_ids": ["u_nobody"]}, "USER_NOT_FOUND"),
        ({"kind": "channel", "participant_ids": ["u_ryan"]}, "VALIDATION_FAILED"),
    ],
)
def test_invalid_conversations_are_rejected(client, payload, code):
    response = client.post("/api/conversations", headers=as_user("u_alex"), json=payload)
    assert response.status_code in (404, 422)
    assert response.json()["error"]["code"] == code


def test_unit_rules(conn, clock):
    with pytest.raises(InvalidConversationError):
        conversations.create_conversation(conn, creator_id="ana", kind="group", participant_ids_in=["ben"],
                                          title="x", now=clock())
    with pytest.raises(UserNotFoundError):
        conversations.create_conversation(conn, creator_id="zed", kind="direct", participant_ids_in=["ben"],
                                          title=None, now=clock())
    with pytest.raises(NotAParticipantError):
        conversations.ensure_participant(conn, "c_ana_ben", "cy")


def test_non_participant_cannot_open_conversation(client):
    body = expect_status(client.get("/api/conversations/c_sarah_ryan", headers=as_user("u_alex")), 403,
                         what="non-participants are rejected")
    assert body["error"]["code"] == "NOT_A_PARTICIPANT"


def test_mute_setting_persists(client):
    expect_status(client.patch("/api/conversations/c_launch_squad/settings", headers=as_user("u_alex"),
                               json={"muted": True}), 200, what="mute")
    convs = client.get("/api/conversations", headers=as_user("u_alex")).json()
    assert next(c for c in convs if c["id"] == "c_launch_squad")["muted"] is True
