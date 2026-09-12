"""Website chat-widget channel adapter. Fully synchronous request/response
for the common case (auto-sent reply comes back in the POST response);
polling via GET /messages picks up replies that were queued for human
approval and sent later from the dashboard."""
from fastapi import APIRouter
from pydantic import BaseModel

from .. import db, orchestrator

router = APIRouter(prefix="/api/channels/website", tags=["website"])


def send_message(session_id: str, text: str):
    # No push channel for the widget — it already reads new rows via
    # GET /messages, and the auto-sent case returns inline from POST /message.
    pass


orchestrator.register_sender("website", send_message)


class IncomingMessage(BaseModel):
    session_id: str
    display_name: str | None = "Website visitor"
    text: str


@router.post("/message")
def post_message(payload: IncomingMessage):
    return orchestrator.handle_incoming("website", payload.session_id, payload.display_name, payload.text)


@router.get("/messages")
def get_messages(session_id: str, since: int = 0):
    conv = db.get_or_create_conversation("website", session_id, "Website visitor", None)
    msgs = db.list_messages_since(conv["id"], since) if since else db.list_messages(conv["id"])
    return {"conversation_id": conv["id"], "messages": msgs}
