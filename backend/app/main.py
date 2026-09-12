from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, orchestrator
from .channels import mail, messenger, telegram, website

app = FastAPI(title="Banno CRM")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    db.init_db()
    telegram.start()
    messenger.start()
    mail.start()


app.include_router(website.router)
app.include_router(messenger.router)


@app.get("/api/health")
def health():
    from . import llm

    info = llm.provider_info()
    return {
        "ok": True,
        "llm_live": info["live"],
        "llm": info,
        "telegram_enabled": bool(config.TELEGRAM_BOT_TOKEN),
        "messenger_enabled": bool(config.MESSENGER_PAGE_ACCESS_TOKEN),
        "email_enabled": bool(config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD),
        "email_address": config.GMAIL_ADDRESS,
    }


def _with_customer(conv: dict) -> dict:
    from .commerce.provider import get_provider

    customer = get_provider().get_customer(conv.get("customer_id"))
    return {
        **conv,
        "customer_name": customer["name"] if customer else None,
        "vip": bool(customer and customer.get("vip")),
        "lifetime_value": customer.get("lifetime_value") if customer else None,
    }


@app.get("/api/conversations")
def api_conversations():
    return [_with_customer(c) for c in db.list_conversations()]


@app.get("/api/conversations/{conversation_id}/messages")
def api_conversation_messages(conversation_id: int):
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    return {"conversation": _with_customer(conv), "messages": db.list_messages(conversation_id)}


class ReplyBody(BaseModel):
    text: str


@app.post("/api/conversations/{conversation_id}/reply")
def api_manual_reply(conversation_id: int, body: ReplyBody):
    try:
        return orchestrator.send_manual_reply(conversation_id, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/customers")
def api_customers():
    """Customer-centric view of the inbox: who is waiting, what they asked,
    what the agents drafted, and the order behind it."""
    from .commerce.provider import get_provider

    provider = get_provider()
    pending_by_conv = {p["conversation_id"]: p for p in db.list_pending()}
    out = []

    for conv in db.list_conversations():
        messages = db.list_messages(conv["id"])
        last_in = next((m for m in reversed(messages) if m["direction"] == "in"), None)
        customer = provider.get_customer(conv.get("customer_id"))
        draft = pending_by_conv.get(conv["id"])

        order = None
        if last_in:
            order = provider.find_order_mentioned(last_in["body"])
        if not order and customer:
            order = provider.latest_order_for_customer(customer["id"])

        out.append({
            "conversation_id": conv["id"],
            "name": customer["name"] if customer else (conv["display_name"] or conv["external_id"]),
            "handle": conv["display_name"] or conv["external_id"],
            "channel": conv["channel"],
            "vip": bool(customer and customer.get("vip")),
            "lifetime_value": customer.get("lifetime_value") if customer else None,
            "last_message_at": conv["last_message_at"],
            "intent": conv["last_intent"],
            "priority": conv["last_priority"],
            "sentiment": last_in["sentiment"] if last_in else None,
            "customer_message": last_in["body"] if last_in else None,
            "reasoning": last_in["reasoning"] if last_in else None,
            "draft_id": draft["id"] if draft else None,
            "draft": draft["body"] if draft else None,
            "message_count": len(messages),
            "order": {
                "order_number": order["order_number"],
                "fulfillment_status": order["fulfillment_status"],
                "financial_status": order["financial_status"],
                "total": sum(li["price"] * li["quantity"] for li in order["line_items"]),
                "items": ", ".join(li["title"] for li in order["line_items"]),
            } if order else None,
        })
    return out


@app.get("/api/pending")
def api_pending():
    return db.list_pending()


class ApproveBody(BaseModel):
    edited_text: str | None = None


@app.post("/api/pending/{message_id}/approve")
def api_approve(message_id: int, body: ApproveBody):
    try:
        return orchestrator.approve_pending(message_id, body.edited_text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/pending/{message_id}/reject")
def api_reject(message_id: int):
    try:
        orchestrator.reject_pending(message_id)
        return {"status": "rejected"}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/proactive/scan")
def api_proactive_scan():
    return orchestrator.run_proactive_scan()


@app.get("/api/stats")
def api_stats():
    return db.stats()


@app.get("/api/lessons")
def api_lessons():
    """What the Knowledge Agent has learned from the owner's edits."""
    from .agents import knowledge_agent

    return knowledge_agent.load_lessons(limit=20)


@app.get("/api/telegram/recent-chats")
def api_recent_telegram_chats():
    """Helper for setup: message your bot once, then call this to find the
    chat id to put in OWNER_TELEGRAM_CHAT_ID."""
    return [
        {"chat_id": c["external_id"], "display_name": c["display_name"]}
        for c in db.list_conversations()
        if c["channel"] == "telegram"
    ]


app.mount("/widget", StaticFiles(directory=str(config.BASE_DIR.parent / "widget"), html=True), name="widget")
app.mount("/", StaticFiles(directory=str(config.BASE_DIR.parent / "frontend"), html=True), name="frontend")
