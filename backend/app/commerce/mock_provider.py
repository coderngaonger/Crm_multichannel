import json
import re

from .. import config
from ..text_utils import strip_accents

with open(config.DATA_DIR / "products.json", encoding="utf-8") as f:
    _PRODUCTS = json.load(f)
with open(config.DATA_DIR / "orders.json", encoding="utf-8") as f:
    _ORDERS = json.load(f)
with open(config.DATA_DIR / "customers.json", encoding="utf-8") as f:
    _CUSTOMERS = json.load(f)
with open(config.DATA_DIR / "faqs.json", encoding="utf-8") as f:
    _FAQS = json.load(f)

_PRODUCTS_BY_ID = {p["id"]: p for p in _PRODUCTS}
_ORDERS_BY_NUMBER = {o["order_number"].lstrip("#"): o for o in _ORDERS}
_ORDERS_BY_NUMBER.update({o["order_number"]: o for o in _ORDERS})
_CUSTOMERS_BY_ID = {c["id"]: c for c in _CUSTOMERS}

ORDER_NUMBER_RE = re.compile(r"#?(\d{3,6})")


def get_customer(customer_id: str | None):
    if not customer_id:
        return None
    return _CUSTOMERS_BY_ID.get(customer_id)


def list_faqs():
    return _FAQS


def search_products(text: str, limit: int = 3):
    tokens = re.findall(r"\w+", strip_accents(text))
    scored = []
    for p in _PRODUCTS:
        haystack = strip_accents(" ".join([p["title"], p["category"], " ".join(p["tags"])]))
        score = sum(1 for tok in tokens if len(tok) > 2 and tok in haystack)
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:limit]]


def find_order_mentioned(text: str):
    m = ORDER_NUMBER_RE.search(text)
    if not m:
        return None
    return _ORDERS_BY_NUMBER.get(m.group(1)) or _ORDERS_BY_NUMBER.get("#" + m.group(1))


def latest_order_for_customer(customer_id: str | None):
    if not customer_id:
        return None
    orders = [o for o in _ORDERS if o["customer_id"] == customer_id]
    if not orders:
        return None
    return sorted(orders, key=lambda o: o["created_at"], reverse=True)[0]


def related_products(product: dict):
    return [_PRODUCTS_BY_ID[pid] for pid in product.get("related_product_ids", []) if pid in _PRODUCTS_BY_ID]


def unfulfilled_orders_for_proactive_outreach():
    """Orders sitting unfulfilled — used to demo the Sales Agent's proactive
    (not purely reactive) behaviour: nudging the shop owner to follow up."""
    return [o for o in _ORDERS if o["fulfillment_status"] == "unfulfilled"]
