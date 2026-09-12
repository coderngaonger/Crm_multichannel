"""Telegram adapter with two distinct roles:

1. Customer channel — customers chat with the bot, messages flow into the
   same unified pipeline as the website widget.
2. Shop-owner console — the owner's own chat with the bot becomes the
   approval surface: flagged replies arrive as a card with inline
   Approve / Edit / Reject buttons, so the owner reviews AI drafts from
   their phone without opening any dashboard.

Uses long polling, so no public HTTPS webhook is needed for a live demo.
"""
import threading
import time

import httpx

from .. import config, orchestrator

_client = httpx.Client(timeout=35)
_offset = 0
_api_base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

# owner_chat_id -> message_id awaiting a replacement text typed by the owner
_awaiting_edit: dict[str, int] = {}

PRIORITY_ICON = {"urgent": "🔴", "high": "🟠", "normal": "🔵", "low": "⚪"}


def _api(method: str, payload: dict):
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    try:
        resp = _client.post(f"{_api_base}/{method}", json=payload)
        data = resp.json()
        if not data.get("ok"):
            print(f"[telegram] {method} rejected: {data.get('description')}")
        return data
    except Exception as exc:
        print(f"[telegram] {method} error: {exc}")
        return None


def send_message(chat_id: str, text: str):
    _api("sendMessage", {"chat_id": chat_id, "text": text})


def notify_owner(kind: str, payload: dict):
    owner = config.OWNER_TELEGRAM_CHAT_ID
    if not owner:
        return

    cls = payload.get("classification", {})
    icon = PRIORITY_ICON.get(cls.get("priority"), "🔵")

    if kind == "review_needed":
        vip = " ⭐VIP" if payload.get("vip") else ""
        text = (
            f"🚩 CẦN DUYỆT {icon} {cls.get('priority', '')}\n"
            f"Kênh: {payload['channel']} · Khách: {payload['customer_name']}{vip}\n"
            f"Phân loại: {cls.get('intent', '')} / {cls.get('sentiment', '')}\n\n"
            f"💬 Khách: {payload['customer_message']}\n\n"
            f"🤖 Bản nháp Banno:\n{payload['draft']}"
        )
        mid = payload["message_id"]
        _api("sendMessage", {
            "chat_id": owner,
            "text": text,
            "reply_markup": {"inline_keyboard": [[
                {"text": "✅ Duyệt & gửi", "callback_data": f"approve:{mid}"},
                {"text": "✏️ Sửa", "callback_data": f"edit:{mid}"},
                {"text": "❌ Từ chối", "callback_data": f"reject:{mid}"},
            ]]},
        })
    elif kind == "auto_replied":
        text = (
            f"✅ Banno đã tự trả lời ({payload['channel']} · {payload['customer_name']})\n"
            f"Phân loại: {cls.get('intent', '')} {icon}\n\n"
            f"💬 Khách: {payload['customer_message']}\n"
            f"🤖 Đã gửi: {payload['reply'][:300]}"
        )
        _api("sendMessage", {"chat_id": owner, "text": text})


def _handle_owner_callback(callback: dict):
    data = callback.get("data", "")
    owner_chat = str(callback["message"]["chat"]["id"])
    callback_id = callback["id"]
    action, _, raw_id = data.partition(":")

    try:
        message_id = int(raw_id)
    except ValueError:
        return

    if action == "approve":
        try:
            result = orchestrator.approve_pending(message_id, None)
            _api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Đã duyệt và gửi cho khách"})
            _api("sendMessage", {"chat_id": owner_chat, "text": f"✅ Đã gửi cho khách:\n{result['sent_text']}"})
        except ValueError as exc:
            _api("answerCallbackQuery", {"callback_query_id": callback_id, "text": str(exc)})
    elif action == "reject":
        try:
            orchestrator.reject_pending(message_id)
            _api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Đã từ chối bản nháp"})
            _api("sendMessage", {"chat_id": owner_chat, "text": "❌ Đã từ chối bản nháp, không gửi gì cho khách."})
        except ValueError as exc:
            _api("answerCallbackQuery", {"callback_query_id": callback_id, "text": str(exc)})
    elif action == "edit":
        _awaiting_edit[owner_chat] = message_id
        _api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Gửi nội dung thay thế"})
        _api("sendMessage", {
            "chat_id": owner_chat,
            "text": "✏️ Nhập nội dung thay thế cho bản nháp này rồi gửi — Banno sẽ gửi đúng nội dung bạn viết.",
        })


def _handle_owner_message(owner_chat: str, text: str):
    message_id = _awaiting_edit.pop(owner_chat, None)
    if message_id is None:
        _api("sendMessage", {
            "chat_id": owner_chat,
            "text": "Đây là kênh điều khiển Banno CRM. Bạn sẽ nhận cảnh báo cần duyệt tại đây.",
        })
        return
    try:
        result = orchestrator.approve_pending(message_id, text)
        _api("sendMessage", {"chat_id": owner_chat, "text": f"✅ Đã gửi bản đã sửa:\n{result['sent_text']}"})
    except ValueError as exc:
        _api("sendMessage", {"chat_id": owner_chat, "text": f"Không gửi được: {exc}"})


def _poll_loop():
    global _offset
    while True:
        try:
            resp = _client.get(f"{_api_base}/getUpdates", params={"timeout": 30, "offset": _offset})
            resp.raise_for_status()
            for update in resp.json().get("result", []):
                _offset = update["update_id"] + 1

                if "callback_query" in update:
                    _handle_owner_callback(update["callback_query"])
                    continue

                message = update.get("message")
                if not message or "text" not in message:
                    continue

                chat_id = str(message["chat"]["id"])
                if config.OWNER_TELEGRAM_CHAT_ID and chat_id == config.OWNER_TELEGRAM_CHAT_ID:
                    _handle_owner_message(chat_id, message["text"])
                    continue

                sender = message.get("from", {})
                display_name = sender.get("username") or sender.get("first_name") or "Telegram user"
                orchestrator.handle_incoming("telegram", chat_id, display_name, message["text"])
        except Exception as exc:
            print(f"[telegram] poll error: {exc}")
            time.sleep(3)


def start():
    if not config.TELEGRAM_BOT_TOKEN:
        print("[telegram] TELEGRAM_BOT_TOKEN not set - Telegram channel disabled. "
              "Get one from @BotFather and add it to backend/.env to enable.")
        return
    orchestrator.register_sender("telegram", send_message)
    orchestrator.register_owner_notifier(notify_owner)
    threading.Thread(target=_poll_loop, daemon=True).start()
    print("[telegram] polling started")
    if not config.OWNER_TELEGRAM_CHAT_ID:
        print("[telegram] OWNER_TELEGRAM_CHAT_ID not set - owner approval console disabled. "
              "Message your bot, then check GET /api/telegram/recent-chats to find your chat id.")
