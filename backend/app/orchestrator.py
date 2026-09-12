"""Ties Router -> Support -> Sales agents together, applies the
human-in-the-loop gate, and dispatches outbound replies to the right
channel. Channel modules register a `send(external_id, text)` callable here
at import time so this module never has to import them directly (avoids
circular imports between orchestrator <-> channels)."""
from typing import Callable

from . import config, db
from .agents import router_agent, sales_agent, support_agent
from .commerce.provider import get_provider

SENDERS: dict[str, Callable[[str, str], None]] = {}
OWNER_NOTIFIER: Callable[[str, dict], None] | None = None


def register_sender(channel: str, fn: Callable[[str, str], None]):
    SENDERS[channel] = fn


def register_owner_notifier(fn: Callable[[str, dict], None]):
    """Channel adapters that can reach the shop owner (currently Telegram)
    register here, so the owner gets approval requests and auto-reply
    digests wherever they already are instead of having to watch a dashboard."""
    global OWNER_NOTIFIER
    OWNER_NOTIFIER = fn


def _notify_owner(kind: str, payload: dict):
    if not OWNER_NOTIFIER:
        return
    try:
        OWNER_NOTIFIER(kind, payload)
    except Exception as exc:
        print(f"[orchestrator] owner notify failed: {exc}")


def _resolve_customer_id(channel: str, external_id: str) -> str | None:
    """Hackathon-simple identity resolution: demo customers are seeded to
    known channel handles below. A real deployment would look this up from
    a channel-identity table populated on first purchase/contact."""
    mapping = {
        ("telegram", "demo_ha"): "C001",
        ("website", "demo_khoi"): "C002",
    }
    return mapping.get((channel, external_id))


def handle_incoming(channel: str, external_id: str, display_name: str, text: str) -> dict:
    customer_id = _resolve_customer_id(channel, external_id)
    provider = get_provider()
    customer = provider.get_customer(customer_id)

    conv = db.get_or_create_conversation(channel, external_id, display_name, customer_id)

    classification = router_agent.classify(text, customer)
    db.insert_message(
        conv["id"], "in", text,
        intent=classification["intent"], sentiment=classification["sentiment"],
        priority=classification["priority"], reasoning=classification.get("reasoning"),
    )
    db.touch_conversation(conv["id"], classification["intent"], classification["priority"])

    ctx = support_agent.gather_context(text, classification, customer)
    support_reply = support_agent.draft_reply(text, classification, customer, ctx)
    reply = sales_agent.maybe_add_upsell(classification, ctx, support_reply)
    upsell = 1 if reply != support_reply else 0

    requires_review = classification["requires_review"] or classification["intent"] not in config.AUTO_SEND_INTENTS

    if requires_review:
        msg_id = db.insert_message(
            conv["id"], "out", reply,
            agent="support+sales", status="pending_review", reasoning=classification.get("reasoning"),
            upsell=upsell,
        )
        _notify_owner("review_needed", {
            "message_id": msg_id, "channel": channel, "customer_name": display_name,
            "customer_message": text, "draft": reply, "classification": classification,
            "vip": bool(customer and customer.get("vip")),
        })
        return {
            "queued_for_review": True,
            "message_id": msg_id,
            "ack": "Cảm ơn bạn, yêu cầu của bạn cần chủ shop xác nhận thêm — Banno sẽ phản hồi sớm nhất!",
            "classification": classification,
        }

    msg_id = db.insert_message(
        conv["id"], "out", reply,
        agent="support+sales", status="auto_sent", reasoning=classification.get("reasoning"),
        upsell=upsell,
    )
    sender = SENDERS.get(channel)
    if sender:
        sender(external_id, reply)
    if config.NOTIFY_AUTO_REPLIES:
        _notify_owner("auto_replied", {
            "channel": channel, "customer_name": display_name,
            "customer_message": text, "reply": reply, "classification": classification,
        })
    return {
        "queued_for_review": False,
        "message_id": msg_id,
        "reply": reply,
        "classification": classification,
    }


def approve_pending(message_id: int, edited_text: str | None) -> dict:
    msg = db.get_message(message_id)
    if not msg or msg["status"] != "pending_review":
        raise ValueError("Message not pending review")

    conv = db.get_conversation(msg["conversation_id"])
    final_text = edited_text or msg["body"]
    status = "edited_sent" if edited_text and edited_text != msg["body"] else "approved_sent"

    db.update_message(message_id, status=status, original_draft=msg["body"] if edited_text else None, body=final_text)

    sender = SENDERS.get(conv["channel"])
    if sender:
        sender(conv["external_id"], final_text)
    return {"status": status, "sent_text": final_text}


def send_manual_reply(conversation_id: int, text: str) -> dict:
    """Shop owner types a reply themselves from the inbox thread view,
    bypassing the agents entirely."""
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise ValueError("Conversation not found")
    msg_id = db.insert_message(conversation_id, "out", text, agent="human", status="approved_sent")
    sender = SENDERS.get(conv["channel"])
    if sender:
        sender(conv["external_id"], text)
    return {"message_id": msg_id, "sent_text": text}


def reject_pending(message_id: int):
    msg = db.get_message(message_id)
    if not msg or msg["status"] != "pending_review":
        raise ValueError("Message not pending review")
    db.update_message(message_id, status="rejected")


def run_proactive_scan() -> list[dict]:
    drafts = sales_agent.draft_proactive_followups()
    queued = []
    for d in drafts:
        # Route proactive outreach through whichever channel the customer
        # already has an open conversation on; skip if none yet (hackathon
        # scope — no outbound-only channel wiring for cold contacts).
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE customer_id=? ORDER BY last_message_at DESC LIMIT 1",
                (d["customer_id"],),
            ).fetchone()
        if not row:
            continue
        conv = dict(row)
        msg_id = db.insert_message(
            conv["id"], "out", d["draft"],
            agent="sales", status="pending_review", reasoning="proactive: unfulfilled order follow-up",
        )
        _notify_owner("review_needed", {
            "message_id": msg_id, "channel": conv["channel"], "customer_name": conv["display_name"],
            "customer_message": f"(chủ động) đơn {d['order']['order_number']} chưa xử lý",
            "draft": d["draft"],
            "classification": {"intent": "proactive_followup", "priority": "normal", "sentiment": "neutral"},
            "vip": False,
        })
        queued.append({"message_id": msg_id, "conversation_id": conv["id"], "draft": d["draft"]})
    return queued
