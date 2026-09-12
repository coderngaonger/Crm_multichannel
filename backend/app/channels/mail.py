"""Email channel adapter (Gmail over IMAP/SMTP).

Uses a Gmail App Password rather than OAuth: no consent screen, no tunnel,
no app review — it polls the inbox the way the Telegram adapter long-polls,
so it works from a laptop during a demo.

Replies are threaded with In-Reply-To/References so the customer sees one
conversation in their mail client instead of disconnected messages.
"""
import email as email_lib
import imaplib
import smtplib
import threading
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from .. import config, orchestrator

# customer address -> last inbound Message-ID/Subject, used to keep replies
# in the same mail thread. In-memory: after a restart replies open a new thread.
_threads: dict[str, dict] = {}


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def send_message(to_email: str, text: str):
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
        return
    ctx = _threads.get(to_email, {})
    subject = ctx.get("subject") or f"Phản hồi từ {config.SHOP_NAME}"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((config.SHOP_NAME, config.GMAIL_ADDRESS))
    msg["To"] = to_email
    if ctx.get("message_id"):
        msg["In-Reply-To"] = ctx["message_id"]
        msg["References"] = ctx["message_id"]
    msg.set_content(text)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
    except Exception as exc:
        print(f"[mail] send error: {exc}")


def _plain_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            disposition = str(part.get("Content-Disposition") or "")
            if part.get_content_type() == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def _strip_quoted(text: str) -> str:
    """Drop the quoted history so the agents classify what the customer
    actually just wrote, not the whole thread."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or stripped.startswith("--") or (
            stripped.startswith("On ") and stripped.endswith("wrote:")
        ):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _handle(raw: bytes):
    msg = email_lib.message_from_bytes(raw)
    display_name, address = parseaddr(msg.get("From", ""))
    address = address.lower()
    if not address or address == config.GMAIL_ADDRESS.lower():
        return  # ignore our own mail, never loop on ourselves

    body = _strip_quoted(_plain_body(msg))
    subject = _decode(msg.get("Subject"))
    if not body and not subject:
        return

    _threads[address] = {"message_id": msg.get("Message-ID"), "subject": subject}
    text = f"{subject}\n\n{body}".strip() if subject else body
    orchestrator.handle_incoming("email", address, _decode(display_name) or address, text)


def _poll_loop():
    while True:
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
                imap.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
                imap.select("INBOX")
                status, data = imap.search(None, "UNSEEN")
                if status == "OK":
                    for num in data[0].split():
                        status, fetched = imap.fetch(num, "(RFC822)")
                        if status != "OK" or not fetched or not fetched[0]:
                            continue
                        _handle(fetched[0][1])
                        imap.store(num, "+FLAGS", "\\Seen")
        except Exception as exc:
            print(f"[mail] poll error: {exc}")
        time.sleep(config.GMAIL_POLL_SECONDS)


def start():
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
        print("[mail] GMAIL_ADDRESS/GMAIL_APP_PASSWORD not set — email channel disabled.")
        return
    orchestrator.register_sender("email", send_message)
    threading.Thread(target=_poll_loop, daemon=True).start()
    print(f"[mail] polling {config.GMAIL_ADDRESS} every {config.GMAIL_POLL_SECONDS}s")
