"""Compliance Agent: the last check before a draft can reach a customer.

Support and Sales optimise for a helpful answer; this agent optimises for not
promising something the shop cannot honour. Anything it flags is forced
through human approval, no matter how routine the intent looked.
"""
from datetime import date, datetime

from .. import llm
from ..text_utils import strip_accents

REFUND_WINDOW_DAYS = 7
WARRANTY_MONTHS = 12

_REFUND_PROMISE = ("hoan tien", "hoan lai tien", "tra lai tien", "refund")


def _days_since_delivery(order: dict | None) -> int | None:
    if not order or order.get("fulfillment_status") != "delivered":
        return None
    delivered = order.get("estimated_delivery")
    if not delivered:
        return None
    try:
        return (date.today() - datetime.fromisoformat(delivered).date()).days
    except ValueError:
        return None


def _deterministic_violations(draft: str, ctx: dict) -> list[str]:
    """Checks that must not depend on a model being reachable."""
    violations = []
    normalized = strip_accents(draft)

    days = _days_since_delivery(ctx.get("order"))
    if days is not None and days > REFUND_WINDOW_DAYS and any(p in normalized for p in _REFUND_PROMISE):
        violations.append(
            f"Nháp nhắc tới hoàn tiền nhưng đơn đã giao {days} ngày, "
            f"quá hạn đổi trả {REFUND_WINDOW_DAYS} ngày"
        )
    return violations


def review(draft: str, classification: dict, ctx: dict) -> dict:
    """Returns {ok, violations, reviewed_by_llm}. `ok=False` forces approval."""
    violations = _deterministic_violations(draft, ctx)

    policy = "\n".join(f"- {f['question']}: {f['answer']}" for f in ctx.get("faqs", []))
    order = ctx.get("order")
    order_line = (
        f"Đơn {order['order_number']}: {order['fulfillment_status']} / {order['financial_status']}, "
        f"dự kiến giao {order.get('estimated_delivery')}"
        if order else "(không có đơn liên quan)"
    )
    stock_lines = "\n".join(
        f"- {p['title']}: " + ", ".join(f"{v['color']}={v['inventory_quantity']}" for v in p["variants"])
        for p in ctx.get("products", [])
    ) or "(không tra cứu sản phẩm)"

    system_prompt = (
        "Bạn là Compliance Agent của một shop kính mắt. Nhiệm vụ DUY NHẤT: kiểm tra bản nháp trả lời "
        "có hứa hẹn điều gì trái chính sách hoặc sai dữ liệu không. Bạn KHÔNG viết lại câu trả lời.\n"
        "Gắn cờ khi bản nháp: hứa hoàn tiền/đổi trả ngoài thời hạn chính sách; cam kết mốc thời gian "
        "giao hàng mà dữ liệu không có; khẳng định còn hàng khi tồn kho bằng 0; nêu sai giá; "
        "hứa giảm giá/voucher không có trong dữ liệu; hoặc tự nhận lỗi thay cho shop khi chưa có bằng chứng.\n"
        'Trả JSON: {"ok": true/false, "violations": ["lý do ngắn bằng tiếng Việt"]}. '
        "Nếu bản nháp an toàn thì ok=true và violations rỗng. Đừng bắt lỗi vặt về văn phong."
    )
    user_prompt = (
        f"CHÍNH SÁCH SHOP:\n{policy}\n\n"
        f"ĐƠN HÀNG LIÊN QUAN:\n{order_line}\n\n"
        f"TỒN KHO LIÊN QUAN:\n{stock_lines}\n\n"
        f"Ý ĐỊNH KHÁCH: {classification.get('intent')}\n\n"
        f"BẢN NHÁP CẦN KIỂM TRA:\n{draft}"
    )

    verdict = llm.complete_json(system_prompt, user_prompt, {"ok": True, "violations": []})
    llm_violations = [v for v in verdict.get("violations", []) if isinstance(v, str)]
    if not verdict.get("ok", True):
        violations.extend(llm_violations)

    return {"ok": not violations, "violations": violations}
