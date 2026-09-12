"""Real Shopify Admin REST API backed provider.

Mirrors the function signatures of mock_provider.py so the rest of the app
(agents, orchestrator) never needs to know which backend is active — flip
USE_MOCK_COMMERCE=false and set SHOPIFY_STORE_DOMAIN / SHOPIFY_ACCESS_TOKEN
in .env to switch a running demo over to a real store with no code changes.
"""
import re

import httpx

from .. import config
from . import mock_provider as _mock_fallback  # FAQs/customers stay local for now

ORDER_NUMBER_RE = re.compile(r"#?(\d{3,6})")

_BASE = f"https://{config.SHOPIFY_STORE_DOMAIN}/admin/api/2024-01"
_HEADERS = {"X-Shopify-Access-Token": config.SHOPIFY_ACCESS_TOKEN, "Content-Type": "application/json"}


def _get(path: str, params: dict | None = None):
    resp = httpx.get(f"{_BASE}{path}", headers=_HEADERS, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_customer(customer_id: str | None):
    return _mock_fallback.get_customer(customer_id)


def list_faqs():
    return _mock_fallback.list_faqs()


def search_products(text: str, limit: int = 3):
    data = _get("/products.json", {"title": text, "limit": limit})
    return data.get("products", [])


def find_order_mentioned(text: str):
    m = ORDER_NUMBER_RE.search(text)
    if not m:
        return None
    data = _get("/orders.json", {"name": f"#{m.group(1)}", "status": "any"})
    orders = data.get("orders", [])
    return orders[0] if orders else None


def latest_order_for_customer(customer_id: str | None):
    if not customer_id:
        return None
    data = _get("/orders.json", {"customer_id": customer_id, "limit": 1, "status": "any"})
    orders = data.get("orders", [])
    return orders[0] if orders else None


def related_products(product: dict):
    return []


def unfulfilled_orders_for_proactive_outreach():
    data = _get("/orders.json", {"fulfillment_status": "unfulfilled", "status": "open"})
    return data.get("orders", [])
