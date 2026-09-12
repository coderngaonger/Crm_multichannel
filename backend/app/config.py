import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "banno.db"

# Gemini on Google Vertex AI. Auth uses a service-account JSON key when
# GOOGLE_APPLICATION_CREDENTIALS is set, otherwise GCP Application Default
# Credentials (gcloud auth application-default login).
VERTEX_PROJECT_ID = os.getenv("VERTEX_PROJECT_ID", "")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "asia-southeast1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Shop owner's own Telegram chat with the bot — this is where approval
# requests and auto-reply digests are pushed.
OWNER_TELEGRAM_CHAT_ID = os.getenv("OWNER_TELEGRAM_CHAT_ID", "")
NOTIFY_AUTO_REPLIES = os.getenv("NOTIFY_AUTO_REPLIES", "true").lower() != "false"

# Gmail over IMAP/SMTP. Needs an App Password (Google account with 2FA on),
# not the normal account password.
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
GMAIL_POLL_SECONDS = int(os.getenv("GMAIL_POLL_SECONDS", "15"))

# Facebook Messenger (Meta Graph API). Needs a public HTTPS webhook.
MESSENGER_PAGE_ACCESS_TOKEN = os.getenv("MESSENGER_PAGE_ACCESS_TOKEN", "")
MESSENGER_VERIFY_TOKEN = os.getenv("MESSENGER_VERIFY_TOKEN", "banno-verify")
MESSENGER_APP_SECRET = os.getenv("MESSENGER_APP_SECRET", "")

USE_MOCK_COMMERCE = os.getenv("USE_MOCK_COMMERCE", "true").lower() != "false"
SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN", "")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")

ENABLE_UPSELL = os.getenv("ENABLE_UPSELL", "true").lower() != "false"

SHOP_NAME = os.getenv("SHOP_NAME", "Banno Eyewear")

# Intents that are safe to auto-send without human approval.
AUTO_SEND_INTENTS = {"product_inquiry", "order_status", "general_faq"}
