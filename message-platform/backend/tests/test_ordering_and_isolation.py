"""Rule 3 (chronological ordering) and Rule 5 (conversation isolation)."""

import random
from datetime import datetime, timedelta, timezone

import pytest

from app import messages
from app.errors import InvalidMessageError, NotAParticipantError

from ._assertions import expect_equal
from .conftest import as_user

ORDER = pytest.mark.opsrelay(component="messages", rule="Rule 3 - chronological ordering")
ISOLATION = pytest.mark.opsrelay(component="conversations", rule="Rule 5 - conversation isolation")
DAY = datetime(2026, 5, 1, tzinfo=timezone.utc)


@ORDER
def test_history_is_oldest_first_across_midnight(conn):
    stamps = [DAY.replace(hour=22, minute=50), DAY.replace(hour=23, minute=59),
              DAY + timedelta(days=1, minutes=5), DAY + timedelta(days=1, hours=9)]
    bodies = ["late night", "almost midnight", "just after midnight", "next morning"]
    for body, at in zip(bodies, stamps):
        messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body=body, now=at)
    history = messages.list_history(conn, "c_ana_ben", "ben")
    expect_equal([m["body"] for m in history], bodies, rule="Rule 3 - chronological ordering",
                 what="history must be returned oldest -> newest", function="app.messages.sort_timeline",
                 context={"created_at": [m["created_at"] for m in history]})


@ORDER
def test_sort_timeline_orders_shuffled_multi_day_input():
    base = DAY.replace(hour=20)
    items = [{"id": f"m{i}", "seq": i, "created_at": (base + timedelta(hours=7 * i)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
             for i in range(8)]
    shuffled = items[:]
    random.Random(7).shuffle(shuffled)
    expect_equal([m["id"] for m in messages.sort_timeline(shuffled)], [m["id"] for m in items],
                 rule="Rule 3 - chronological ordering", what="sort_timeline must sort by full timestamp",
                 function="app.messages.sort_timeline")


@ORDER
def test_identical_timestamps_fall_back_to_insertion_order(conn):
    for body in ("first", "second", "third"):
        messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body=body, now=DAY)
    assert [m["body"] for m in messages.list_history(conn, "c_ana_ben", "ana")] == ["first", "second", "third"]


@ORDER
def test_seeded_history_spanning_days_is_chronological(client):
    history = client.get("/api/conversations/c_alex_sarah/messages", headers=as_user("u_alex")).json()["messages"]
    created = [m["created_at"] for m in history]
    expect_equal(created, sorted(created), rule="Rule 3 - chronological ordering",
                 what="API history must be chronological", function="GET /api/conversations/{id}/messages")
    assert len({c[:10] for c in created}) >= 3, "fixture spans several days"


@ORDER
def test_pagination_returns_older_pages_in_order(conn, clock):
    for i in range(10):
        messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body=f"m{i}", now=clock())
    newest = messages.list_history(conn, "c_ana_ben", "ana", limit=4)
    assert [m["body"] for m in newest] == ["m6", "m7", "m8", "m9"]
    older = messages.list_history(conn, "c_ana_ben", "ana", limit=4, before_seq=newest[0]["seq"])
    assert [m["body"] for m in older] == ["m2", "m3", "m4", "m5"]


@ISOLATION
def test_messages_never_leak_between_conversations(conn, clock):
    messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="for ben", now=clock())
    messages.send_message(conn, conversation_id="c_ana_cy", sender_id="ana", body="for cy", now=clock())
    expect_equal([m["body"] for m in messages.list_history(conn, "c_ana_cy", "cy")], ["for cy"],
                 rule="Rule 5 - conversation isolation", what="history contains only its own conversation",
                 function="app.messages.list_history")
    assert all(m["conversation_id"] == "c_ana_ben" for m in messages.list_history(conn, "c_ana_ben", "ben"))


@ISOLATION
def test_reply_must_reference_same_conversation(conn, clock):
    original, _ = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="q", now=clock())
    with pytest.raises(InvalidMessageError):
        messages.send_message(conn, conversation_id="c_ana_cy", sender_id="ana", body="a", now=clock(),
                              reply_to_id=original["id"])
    reply, _ = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ben", body="a", now=clock(),
                                     reply_to_id=original["id"])
    assert reply["reply_to"]["id"] == original["id"] and reply["reply_to"]["body"] == "q"


@ISOLATION
def test_non_participants_cannot_read_or_react(conn, clock):
    msg, _ = messages.send_message(conn, conversation_id="c_ana_ben", sender_id="ana", body="private", now=clock())
    with pytest.raises(NotAParticipantError):
        messages.list_history(conn, "c_ana_ben", "cy")
    with pytest.raises(NotAParticipantError):
        messages.toggle_reaction(conn, msg["id"], "cy", "👀", clock())


@ISOLATION
def test_search_is_scoped_to_the_viewers_conversations(client):
    alex = client.get("/api/search", params={"q": "latency"}, headers=as_user("u_alex")).json()
    sarah = client.get("/api/search", params={"q": "latency"}, headers=as_user("u_sarah")).json()
    assert alex["messages"] == [] and [m["conversation_id"] for m in sarah["messages"]] == ["c_sarah_ryan"]
