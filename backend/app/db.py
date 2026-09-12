import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    external_id TEXT NOT NULL,
    display_name TEXT,
    customer_id TEXT,
    created_at TEXT NOT NULL,
    last_message_at TEXT NOT NULL,
    last_intent TEXT,
    last_priority TEXT,
    UNIQUE(channel, external_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    direction TEXT NOT NULL, -- 'in' | 'out'
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    agent TEXT, -- router/support/sales/human
    intent TEXT,
    sentiment TEXT,
    priority TEXT,
    status TEXT, -- for 'out': pending_review | auto_sent | approved_sent | edited_sent | rejected
    original_draft TEXT,
    reasoning TEXT,
    upsell INTEGER DEFAULT 0,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def get_or_create_conversation(channel: str, external_id: str, display_name: str, customer_id: str | None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE channel=? AND external_id=?",
            (channel, external_id),
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            "INSERT INTO conversations (channel, external_id, display_name, customer_id, created_at, last_message_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (channel, external_id, display_name, customer_id, now_iso(), now_iso()),
        )
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)


def touch_conversation(conversation_id: int, intent: str | None, priority: str | None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE conversations SET last_message_at=?, last_intent=?, last_priority=? WHERE id=?",
            (now_iso(), intent, priority, conversation_id),
        )


def insert_message(conversation_id: int, direction: str, body: str, **kwargs) -> int:
    fields = ["conversation_id", "direction", "body", "created_at"]
    values = [conversation_id, direction, body, now_iso()]
    for key in ("agent", "intent", "sentiment", "priority", "status", "original_draft", "reasoning", "upsell"):
        if key in kwargs:
            fields.append(key)
            values.append(kwargs[key])
    placeholders = ",".join(["?"] * len(values))
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO messages ({','.join(fields)}) VALUES ({placeholders})", values
        )
        return cur.lastrowid


def update_message(message_id: int, **kwargs):
    if not kwargs:
        return
    sets = ",".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [message_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE messages SET {sets} WHERE id=?", values)


def get_message(message_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return dict(row) if row else None


def get_conversation(conversation_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        return dict(row) if row else None


def list_conversations():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.*,
              (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id
                 AND m.direction='out' AND m.status='pending_review') AS pending_count,
              (SELECT m2.body FROM messages m2 WHERE m2.conversation_id=c.id
                 ORDER BY m2.id DESC LIMIT 1) AS last_body,
              (SELECT m3.status FROM messages m3 WHERE m3.conversation_id=c.id
                 AND m3.direction='out' ORDER BY m3.id DESC LIMIT 1) AS last_out_status,
              (SELECT COUNT(*) FROM messages m4 WHERE m4.conversation_id=c.id) AS message_count
            FROM conversations c ORDER BY c.last_message_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def list_messages(conversation_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC", (conversation_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def recent_messages(conversation_id: int, limit: int = 6):
    """The last few turns the customer actually saw — drafts that were never
    approved are left out so the agents never 'remember' saying something
    that was rejected."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT direction, body FROM messages WHERE conversation_id=? "
            "AND (direction='in' OR status IN ('auto_sent','approved_sent','edited_sent')) "
            "ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def list_messages_since(conversation_id: int, since_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? AND id>? ORDER BY id ASC",
            (conversation_id, since_id),
        ).fetchall()
        return [dict(r) for r in rows]


def list_pending():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT m.*, c.channel AS conv_channel, c.external_id AS conv_external_id,
                   c.display_name AS conv_display_name,
                   (SELECT mi.body FROM messages mi WHERE mi.conversation_id = m.conversation_id
                      AND mi.direction='in' AND mi.id < m.id ORDER BY mi.id DESC LIMIT 1) AS customer_message
            FROM messages m JOIN conversations c ON c.id = m.conversation_id
            WHERE m.direction='out' AND m.status='pending_review' ORDER BY m.created_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


SENT_STATUSES = ("auto_sent", "approved_sent", "edited_sent")


def _avg_response_seconds(conn) -> float | None:
    """Time between a customer message and the reply that actually went out."""
    rows = conn.execute(
        "SELECT conversation_id, direction, status, created_at FROM messages ORDER BY conversation_id, id"
    ).fetchall()
    deltas = []
    last_inbound: dict[int, datetime] = {}
    for r in rows:
        ts = datetime.fromisoformat(r["created_at"])
        if r["direction"] == "in":
            last_inbound[r["conversation_id"]] = ts
        elif r["status"] in SENT_STATUSES:
            started = last_inbound.pop(r["conversation_id"], None)
            if started:
                deltas.append((ts - started).total_seconds())
    return round(sum(deltas) / len(deltas), 1) if deltas else None


def _hourly_volume(conn, hours: int = 8):
    """Inbound volume per hour, split by how the reply was handled, for the
    AI-vs-team stacked bar chart. Buckets are local time, and empty hours are
    kept so the chart keeps a steady shape."""
    rows = conn.execute(
        "SELECT conversation_id, direction, status, created_at FROM messages ORDER BY id"
    ).fetchall()

    now = datetime.now().astimezone()
    buckets: dict[str, dict] = {}
    for offset in range(hours - 1, -1, -1):
        slot = now - timedelta(hours=offset)
        buckets[slot.strftime("%Y-%m-%d %H")] = {"hour": slot.strftime("%H:00"), "ai": 0, "team": 0}

    for r in rows:
        if r["direction"] != "out" or r["status"] not in SENT_STATUSES:
            continue
        key = datetime.fromisoformat(r["created_at"]).astimezone().strftime("%Y-%m-%d %H")
        if key in buckets:
            buckets[key]["ai" if r["status"] == "auto_sent" else "team"] += 1

    return list(buckets.values())


def stats():
    with get_conn() as conn:
        by_channel = [
            dict(r)
            for r in conn.execute(
                """
                SELECT c.channel,
                       COUNT(DISTINCT c.id) AS conversations,
                       COUNT(m.id) AS messages
                FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
                GROUP BY c.channel ORDER BY conversations DESC
                """
            ).fetchall()
        ]
        by_priority = [
            dict(r)
            for r in conn.execute(
                "SELECT priority, COUNT(*) AS n FROM messages "
                "WHERE direction='in' AND priority IS NOT NULL GROUP BY priority"
            ).fetchall()
        ]
        by_intent = [
            dict(r)
            for r in conn.execute(
                "SELECT intent, COUNT(*) AS n FROM messages "
                "WHERE direction='in' AND intent IS NOT NULL GROUP BY intent ORDER BY n DESC"
            ).fetchall()
        ]
        status_counts = {
            r["status"]: r["n"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM messages WHERE direction='out' GROUP BY status"
            ).fetchall()
        }
        by_sentiment = {
            r["sentiment"]: r["n"]
            for r in conn.execute(
                "SELECT sentiment, COUNT(*) AS n FROM messages "
                "WHERE direction='in' AND sentiment IS NOT NULL GROUP BY sentiment"
            ).fetchall()
        }
        inbound = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE direction='in'").fetchone()["n"]
        conversations = conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
        upsells = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE upsell=1").fetchone()["n"]
        proactive = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE agent='sales'"
        ).fetchone()["n"]

        avg_response = _avg_response_seconds(conn)
        auto = status_counts.get("auto_sent", 0)
        reviewed = status_counts.get("approved_sent", 0) + status_counts.get("edited_sent", 0)
        edited = status_counts.get("edited_sent", 0)
        pending = status_counts.get("pending_review", 0)
        rejected = status_counts.get("rejected", 0)
        handled = auto + reviewed

        return {
            "totals": {
                "conversations": conversations,
                "inbound": inbound,
                "auto_sent": auto,
                "reviewed_sent": reviewed,
                "edited_sent": edited,
                "pending": pending,
                "rejected": rejected,
            },
            "auto_rate": round(auto / handled * 100) if handled else 0,
            "avg_response_seconds": avg_response,
            "handled": handled,
            "by_sentiment": by_sentiment,
            # Share of customer messages that arrived without anger/negativity —
            # a measurable stand-in for satisfaction (we cannot survey customers).
            "positive_rate": (
                round(
                    sum(n for s, n in by_sentiment.items() if s in ("positive", "neutral"))
                    / sum(by_sentiment.values()) * 100
                )
                if by_sentiment else 0
            ),
            "by_hour": _hourly_volume(conn),
            "by_channel": by_channel,
            "by_priority": by_priority,
            "by_intent": by_intent,
            "agents": [
                {
                    "key": "router",
                    "name": "Router Agent",
                    "role": "Phân loại & định tuyến",
                    "icon": "alt_route",
                    "badge": f"{inbound} tin",
                    "metric_label": "Cần người duyệt",
                    "metric_value": f"{round((pending + reviewed + rejected) / inbound * 100) if inbound else 0}%",
                },
                {
                    "key": "support",
                    "name": "Support Agent",
                    "role": "Tra cứu đơn & tồn kho",
                    "icon": "support_agent",
                    "badge": f"{handled} trả lời",
                    "metric_label": "Phản hồi trung bình",
                    "metric_value": f"{avg_response}s" if avg_response else "—",
                },
                {
                    "key": "sales",
                    "name": "Sales Agent",
                    "role": "Cross-sell & chủ động",
                    "icon": "trending_up",
                    "badge": f"{upsells} cross-sell",
                    "metric_label": "Tin nhắn chủ động",
                    "metric_value": str(proactive),
                },
            ],
        }
