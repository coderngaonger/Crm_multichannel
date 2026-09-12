from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, orchestrator
from .channels import messenger, telegram, website

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
