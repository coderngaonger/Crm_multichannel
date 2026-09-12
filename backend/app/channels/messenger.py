"""Facebook Messenger channel adapter (Meta Graph API).

Unlike Telegram (which can long-poll), Meta only pushes messages to a public
HTTPS webhook, so this module exposes GET/POST /webhooks/messenger and the
server must be reachable through a tunnel (ngrok/cloudflared) during a demo.

Every inbound message joins the same pipeline as the website widget and
Telegram, so the Router/Support/Sales agents and the human-in-the-loop gate
work here with no extra code.
"""
import hashlib
import hmac

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from .. import config, orchestrator

router = APIRouter(prefix="/webhooks/messenger", tags=["messenger"])

_client = httpx.Client(timeout=15)
_GRAPH = "https://graph.facebook.com/v21.0"
_names: dict[str, str] = {}


def send_message(psid: str, text: str):
    if not config.MESSENGER_PAGE_ACCESS_TOKEN:
        return
    try:
        resp = _client.post(
            f"{_GRAPH}/me/messages",
            params={"access_token": config.MESSENGER_PAGE_ACCESS_TOKEN},
            json={"recipient": {"id": psid}, "messaging_type": "RESPONSE", "message": {"text": text}},
        )
        data = resp.json()
        if "error" in data:
            print(f"[messenger] send rejected: {data['error'].get('message')}")
    except Exception as exc:
        print(f"[messenger] send error: {exc}")


def _display_name(psid: str) -> str:
    """Meta only gives a page-scoped id; the profile call fills in a name
    (works for app testers/admins in dev mode)."""
    if psid in _names:
        return _names[psid]
    name = f"Messenger {psid[-4:]}"
    try:
        resp = _client.get(
            f"{_GRAPH}/{psid}",
            params={"fields": "first_name,last_name", "access_token": config.MESSENGER_PAGE_ACCESS_TOKEN},
        )
        data = resp.json()
        if "first_name" in data:
            name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
    except Exception:
        pass
    _names[psid] = name
    return name


def _signature_ok(raw_body: bytes, header: str | None) -> bool:
    """Anyone can POST to a public webhook — only accept payloads signed with
    the app secret."""
    if not config.MESSENGER_APP_SECRET:
        return True  # not configured (local testing only)
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(config.MESSENGER_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


@router.get("")
def verify(request: Request):
    """Meta calls this once when you save the webhook URL."""
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == config.MESSENGER_VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "verify token mismatch")


@router.post("")
async def receive(request: Request):
    raw = await request.body()
    if not _signature_ok(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "bad signature")

    payload = await request.json()
    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            message = event.get("message") or {}
            text = message.get("text")
            # Ignore echoes of our own outgoing messages, and non-text events.
            if not text or message.get("is_echo"):
                continue
            psid = str(event["sender"]["id"])
            orchestrator.handle_incoming("messenger", psid, _display_name(psid), text)
    # Meta retries unless it gets a fast 200.
    return {"status": "ok"}


def start():
    if not config.MESSENGER_PAGE_ACCESS_TOKEN:
        print("[messenger] MESSENGER_PAGE_ACCESS_TOKEN not set — Messenger channel disabled.")
        return
    orchestrator.register_sender("messenger", send_message)
    print("[messenger] ready — point the Meta webhook at <public-url>/webhooks/messenger")
