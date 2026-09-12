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

# Highest IMAP UID present when the channel started. Only mail that arrives
# after startup is ever read or answered — a mailbox with a backlog of
# personal mail must never be auto-replied to.
_baseline_uid: int | None = None


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


def _connect():
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
    return imap


def _set_baseline() -> int:
    """Everything already in the mailbox is left untouched: unread, unanswered."""
    with _connect() as imap:
        imap.select("INBOX", readonly=True)
        status, data = imap.uid("search", None, "ALL")
        uids = data[0].split() if status == "OK" and data[0] else []
        return int(uids[-1]) if uids else 0


def _poll_loop():
    global _baseline_uid
    while True:
        try:
            with _connect() as imap:
                imap.select("INBOX")
                status, data = imap.uid("search", None, f"UID {_baseline_uid + 1}:*")
                if status == "OK" and data[0]:
                    for uid in data[0].split():
                        uid_int = int(uid)
                        if uid_int <= _baseline_uid:
                            continue  # Gmail returns the last UID even when nothing is newer
                        status, fetched = imap.uid("fetch", uid, "(RFC822)")
                        if status == "OK" and fetched and fetched[0]:
                            _handle(fetched[0][1])
                            imap.uid("store", uid, "+FLAGS", "\\Seen")
                        _baseline_uid = uid_int
        except Exception as exc:
            print(f"[mail] poll error: {exc}")
        time.sleep(config.GMAIL_POLL_SECONDS)


def start():
    global _baseline_uid
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
        print("[mail] GMAIL_ADDRESS/GMAIL_APP_PASSWORD not set — email channel disabled.")
        return
    try:
        _baseline_uid = _set_baseline()
    except Exception as exc:
        print(f"[mail] cannot reach mailbox, email channel disabled: {exc}")
        return
    orchestrator.register_sender("email", send_message)
    threading.Thread(target=_poll_loop, daemon=True).start()
    print(f"[mail] polling {config.GMAIL_ADDRESS} every {config.GMAIL_POLL_SECONDS}s "
          f"— ignoring everything up to UID {_baseline_uid}, only new mail is answered")
