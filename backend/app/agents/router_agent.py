"""Router Agent: first point of contact for every inbound message on every
channel. Classifies sentiment/intent/priority and decides whether a human
must review the reply before it goes out (human-in-the-loop gate)."""
import re

from .. import llm
from ..text_utils import strip_accents

INTENTS = {"product_inquiry", "order_status", "complaint", "refund_request", "general_faq", "other"}

# Patterns are written accent-free and matched against accent-stripped text.
_COMPLAINT_WORDS = re.compile(
    r"rat te|qua te|te qua|kem chat luong|that vong|buc minh|khong hai long|lua dao|khieu nai|phan nan|do rom"
)
_REFUND_WORDS = re.compile(r"hoan tien|tra hang|refund|doi tra|doi hang")
_FAQ_WORDS = re.compile(
    r"bao hanh|chinh sach|thanh toan|\bcod\b|chuyen khoan|momo|zalopay|phi ship|"
    r"giao hang bao lau|ship bao lau|bao lau thi nhan|dia chi shop|gio mo cua"
)
_ORDER_WORDS = re.compile(r"don hang|#\d{3,6}|giao hang|van chuyen|\bship\b|bao gio giao|tracking|van don")
_PRODUCT_WORDS = re.compile(r"\bgia\b|con hang|con mau|\bmau\b|\bsize\b|\bkinh\b|san pham|bao nhieu|con khong")


def _rule_based_fallback(text: str, customer_vip: bool) -> dict:
    normalized = strip_accents(text)

    if _REFUND_WORDS.search(normalized):
        intent, priority = "refund_request", "high"
    elif _COMPLAINT_WORDS.search(normalized):
        intent, priority = "complaint", "high"
    elif _FAQ_WORDS.search(normalized):
        intent, priority = "general_faq", "low"
    elif _ORDER_WORDS.search(normalized):
        intent, priority = "order_status", "normal"
    elif _PRODUCT_WORDS.search(normalized):
        intent, priority = "product_inquiry", "normal"
    else:
        intent, priority = "general_faq", "low"

    sentiment = "negative" if _COMPLAINT_WORDS.search(normalized) else "neutral"
    requires_review = intent in ("complaint", "refund_request") or (customer_vip and sentiment == "negative")
    if priority == "high" and customer_vip:
        priority = "urgent"
    return {
        "sentiment": sentiment,
        "intent": intent,
        "priority": priority,
        "requires_review": requires_review,
        "reasoning": "rule-based fallback (no LLM key configured)",
    }


def classify(text: str, customer: dict | None, history: str = "") -> dict:
    vip = bool(customer and customer.get("vip"))
    fallback = _rule_based_fallback(text, vip)

    system_prompt = (
        "You are the Router Agent inside BannoCRM, a multi-agent customer-care system for a small "
        "e-commerce eyewear shop. Classify the incoming customer message. "
        f"Customer VIP status: {vip}. "
        "Return JSON with keys: sentiment (positive|neutral|negative|angry), "
        "intent (product_inquiry|order_status|complaint|refund_request|general_faq|other), "
        "priority (low|normal|high|urgent), "
        "requires_review (true if a human should approve the reply before sending - always true for "
        "complaints, refund requests, or negative-sentiment VIP customers), "
        "reasoning (lý do ngắn gọn BẰNG TIẾNG VIỆT, dưới 15 từ). "
        "Nếu có lịch sử hội thoại, hãy dùng nó để hiểu các tin ngắn/nối tiếp "
        "(ví dụ 'thế còn màu bạc?' hay 'vẫn chưa thấy gì' phải hiểu theo chủ đề đang nói dở)."
    )
    user_prompt = (
        f"Lịch sử hội thoại trước đó:\n{history}\n\nTin nhắn MỚI cần phân loại: {text}"
        if history else text
    )
    result = llm.complete_json(system_prompt, user_prompt, fallback)

    if result.get("intent") not in INTENTS:
        result["intent"] = fallback["intent"]
    result.setdefault("sentiment", fallback["sentiment"])
    result.setdefault("priority", fallback["priority"])
    result.setdefault("requires_review", fallback["requires_review"])
    result.setdefault("reasoning", fallback["reasoning"])
    return result
