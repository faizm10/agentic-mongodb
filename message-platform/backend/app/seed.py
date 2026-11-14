"""Deterministic demo data: six people, five direct chats and three groups.

Content and IDs are fixed; timestamps are relative to the moment of seeding so
the demo always looks fresh. Seeding only runs on an empty database.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import conversations, messages
from .db import iso

USERS = [
    ("u_alex", "Alex Morgan", "alex", "Product Designer", "Designing calm software. Coffee, typography, trail runs.", "San Francisco", 252),
    ("u_sarah", "Sarah Chen", "sarah", "Engineering Lead", "Building NovaConnect's realtime core. Ask me about WebSockets.", "Seattle", 340),
    ("u_jordan", "Jordan Lee", "jordan", "Frontend Engineer", "Pixels, motion and accessibility.", "Austin", 160),
    ("u_emma", "Emma Wilson", "emma", "Marketing Director", "Telling the NovaConnect story.", "New York", 18),
    ("u_ryan", "Ryan Patel", "ryan", "Data Scientist", "Latency charts and long hikes.", "Denver", 200),
    ("u_taylor", "Taylor Brooks", "taylor", "Customer Success", "Here to help our customers love NovaConnect.", "Chicago", 40),
]

# (minutes ago, sender, body, options). Options: react={emoji: [users]}, pin=user, reply=<index in list>
Thread = list[tuple[int, str, str, dict]]

CONVERSATIONS: list[dict] = [
    {
        "id": "c_alex_sarah", "kind": "direct", "members": ["alex", "sarah"], "unread": {"alex": 2},
        "messages": [
            (2990, "sarah", "Morning! Did you get a chance to look at the onboarding flow v3?", {}),
            (2984, "alex", "Just opened it ☕ The new progress indicator is so much clearer.", {}),
            (2983, "alex", "One thing: the skip button on step 2 feels a bit hidden. Could we try it as a text link under the main button?", {}),
            (2975, "sarah", "Good call. I'll ask Jordan to mock up both versions.", {"react": {"👍": ["alex"]}}),
            (1460, "sarah", "Sharing the final copy deck here so it's easy to find: https://docs.novaconnect.app/onboarding-copy", {"pin": "alex"}),
            (1455, "alex", "Perfect, pinning it 📌", {"react": {"❤️": ["sarah"]}}),
            (1454, "alex", "Also, are we still on for our 1:1 tomorrow at 10?", {}),
            (1440, "sarah", "Yes! I'll bring the Q4 roadmap draft.", {"reply": 6}),
            (46, "sarah", "Heads up: the launch review moved to 3pm today.", {}),
            (45, "sarah", "Could you present the new messaging UI? 15 minutes should be plenty.", {}),
        ],
    },
    {
        "id": "c_alex_jordan", "kind": "direct", "members": ["alex", "jordan"], "unread": {"alex": 1},
        "messages": [
            (1620, "jordan", "Pushed the new message bubble component to the design branch 🎨", {}),
            (1612, "alex", "Looks great. The corner radius on grouped bubbles is chef's kiss.", {"react": {"😄": ["jordan"]}}),
            (1610, "jordan", "Ha, took four iterations. Dark mode contrast passes AA everywhere now too.", {}),
            (1600, "alex", "Let's demo it at the launch review.", {}),
            (180, "jordan", "Do you have the latest icon set? Mine is missing the new reaction glyphs.", {}),
        ],
    },
    {
        "id": "c_alex_emma", "kind": "direct", "members": ["alex", "emma"], "unread": {},
        "messages": [
            (4350, "emma", "The beta waitlist just passed 12,000 signups 🎉", {"react": {"🎉": ["alex"]}}),
            (4342, "alex", "Incredible! What drove the spike?", {}),
            (4338, "emma", "The product video. 48% of signups came straight from it.", {}),
            (4330, "alex", "We should do a follow-up that shows off group chats.", {}),
            (1300, "emma", "Drafting the launch email now. Want to review the hero image?", {}),
            (1290, "alex", "Send it over whenever, I'm free after 4.", {}),
        ],
    },
    {
        "id": "c_alex_taylor", "kind": "direct", "members": ["alex", "taylor"], "unread": {},
        "messages": [
            (125, "taylor", "Quick one: a customer asked whether edited messages are labelled. They are, right?", {}),
            (121, "alex", "Yep, edited messages show a subtle 'edited' tag next to the timestamp.", {}),
            (119, "taylor", "Perfect, thanks! Closing the ticket ✅", {"react": {"🙏": ["alex"]}}),
        ],
    },
    {
        "id": "c_sarah_ryan", "kind": "direct", "members": ["sarah", "ryan"], "unread": {"sarah": 1},
        "messages": [
            (1500, "ryan", "Latency dashboards are live. p95 message delivery is 84ms.", {}),
            (1495, "sarah", "That's a big improvement from last sprint!", {}),
            (1490, "ryan", "WebSocket fan-out did most of the work.", {"react": {"🔥": ["sarah"]}}),
            (20, "ryan", "Want me to add a delivery-status breakdown to the dashboard?", {}),
        ],
    },
    {
        "id": "c_launch_squad", "kind": "group", "title": "Launch Squad", "emoji": "🚀",
        "topic": "NovaConnect 2.0 launch · Thursday",
        "members": ["sarah", "alex", "jordan", "emma", "ryan"], "unread": {"alex": 3, "sarah": 1},
        "messages": [
            (2880, "sarah", "Welcome to the launch squad 👋 This is our home base until Thursday.", {"react": {"👋": ["alex", "emma"]}}),
            (2860, "emma", "The press kit is 80% done. I need final screenshots from design.", {}),
            (2850, "jordan", "Screenshots coming tonight, in both dark and light themes.", {}),
            (2700, "ryan", "Load test passed: 50k concurrent connections, zero dropped messages.", {"react": {"🔥": ["alex", "sarah", "emma"], "🎉": ["jordan"]}}),
            (1400, "alex", "Amazing work everyone. Here's the launch checklist: https://docs.novaconnect.app/launch-checklist", {"pin": "sarah"}),
            (1390, "sarah", "Go/no-go meeting is Wednesday at 2pm.", {}),
            (95, "emma", "The embargo lifts Thursday 9am PT. Please don't share anything publicly before then 🙏", {}),
            (88, "ryan", "Noted. The status page is ready too: https://status.novaconnect.app", {}),
            (12, "jordan", "Final screenshots are in the shared drive!", {"react": {"🙌": ["sarah"]}}),
        ],
    },
    {
        "id": "c_design_crit", "kind": "group", "title": "Design Crit", "emoji": "✏️",
        "topic": "Weekly critique · Fridays at 11",
        "members": ["jordan", "alex", "taylor"], "unread": {},
        "messages": [
            (3000, "jordan", "Agenda for Friday: composer redesign and empty states.", {}),
            (2990, "taylor", "Can I bring two customer quotes about the composer? Super relevant.", {}),
            (2985, "alex", "Please do! Real feedback beats our opinions.", {"react": {"💯": ["jordan"]}}),
            (1350, "jordan", "Empty-state illustrations v2: https://figma.com/file/novaconnect-empty-states", {}),
            (1340, "taylor", "The coffee cup one is adorable ☕", {"react": {"😍": ["alex", "jordan"]}}),
        ],
    },
    {
        "id": "c_weekend_hikers", "kind": "group", "title": "Weekend Hikers", "emoji": "🏔️",
        "topic": "Saturday trail plans",
        "members": ["emma", "sarah", "taylor", "ryan", "alex"], "unread": {},
        "messages": [
            (4400, "emma", "Who's in for Mount Tam on Saturday?", {}),
            (4390, "ryan", "In! I'll bring snacks.", {}),
            (4385, "taylor", "Count me in 🥾", {}),
            (4370, "sarah", "Same! Meet at the trailhead at 8?", {}),
            (4360, "alex", "I'm in too. Carpool from the city?", {}),
            (4350, "emma", "Perfect, I'll drive. 4 seats left.", {"react": {"🚗": ["alex", "sarah"]}}),
        ],
    },
]


def _uid(short: str) -> str:
    return f"u_{short}"


def seed_if_empty(conn: sqlite3.Connection, now: datetime) -> bool:
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return False
    with conn:
        conn.executemany(
            "INSERT INTO users(id, name, handle, title, bio, location, avatar_hue) VALUES (?,?,?,?,?,?,?)", USERS
        )

    for spec in CONVERSATIONS:
        members = [_uid(m) for m in spec["members"]]
        first_at = now - timedelta(minutes=spec["messages"][0][0] + 60)
        cid, _ = conversations.create_conversation(
            conn,
            creator_id=members[0],
            kind=spec["kind"],
            participant_ids_in=members,
            title=spec.get("title"),
            topic=spec.get("topic", ""),
            emoji=spec.get("emoji", ""),
            now=first_at,
            conversation_id=spec["id"],
        )
        ids: list[str] = []
        for minutes_ago, sender, body, opts in spec["messages"]:
            at = now - timedelta(minutes=minutes_ago)
            reply_to = ids[opts["reply"]] if "reply" in opts else None
            msg, _ = messages.send_message(
                conn, conversation_id=cid, sender_id=_uid(sender), body=body, now=at, reply_to_id=reply_to,
                idempotency_key=f"seed-{cid}-{len(ids)}",
            )
            ids.append(msg["id"])
            for emoji, users in opts.get("react", {}).items():
                for u in users:
                    messages.toggle_reaction(conn, msg["id"], _uid(u), emoji, at + timedelta(minutes=1))
            if "pin" in opts:
                messages.toggle_pin(conn, msg["id"], _uid(opts["pin"]), at + timedelta(minutes=1))

        # Receipts: everything is read except the newest N messages for users listed in "unread".
        read_at = iso(now - timedelta(minutes=1))
        with conn:
            for member in members:
                unread = spec["unread"].get(member.removeprefix("u_"), 0)
                rows = conn.execute(
                    "SELECT r.message_id FROM receipts r JOIN messages m ON m.id = r.message_id "
                    "WHERE m.conversation_id = ? AND r.user_id = ? ORDER BY m.seq",
                    (cid, member),
                ).fetchall()
                cutoff = len(rows) - unread
                for i, row in enumerate(rows):
                    if i < cutoff:
                        conn.execute(
                            "UPDATE receipts SET status='read', delivered_at=?, read_at=? WHERE message_id=? AND user_id=?",
                            (read_at, read_at, row["message_id"], member),
                        )
                    else:
                        conn.execute(
                            "UPDATE receipts SET status='delivered', delivered_at=? WHERE message_id=? AND user_id=?",
                            (read_at, row["message_id"], member),
                        )
    return True
