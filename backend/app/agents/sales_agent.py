"""Sales Agent: opportunistic cross-sell on reactive replies, plus a
proactive scan that drafts outreach for orders sitting unfulfilled — the
'not just reactive' half of BannoCRM's promise. Proactive drafts always go
through human-in-the-loop review."""
from .. import config, llm
from ..commerce.provider import get_provider
from . import knowledge_agent


def maybe_add_upsell(classification: dict, ctx: dict, reply_text: str) -> str:
    if not config.ENABLE_UPSELL or classification["intent"] != "product_inquiry" or not ctx["products"]:
        return reply_text

    provider = get_provider()
    related = provider.related_products(ctx["products"][0])
    if not related:
        return reply_text

    pick = related[0]
    fallback_line = f"\n\nGợi ý thêm: bạn có muốn xem thêm {pick['title']} ({pick['price']:,.0f} {pick['currency']}) đi kèm không?"

    system_prompt = (
        "Bạn là Sales Agent của BannoCRM. Viết đúng 1 câu tiếng Việt, ngắn, không quá mời chào, "
        "gợi ý khách hàng xem thêm một sản phẩm liên quan (cross-sell), dựa trên sản phẩm khách vừa hỏi."
        + knowledge_agent.as_prompt_block()
    )
    user_prompt = f"Sản phẩm khách hỏi: {ctx['products'][0]['title']}\nSản phẩm gợi ý thêm: {pick['title']} ({pick['price']:,.0f} {pick['currency']})"
    line = llm.complete_text(system_prompt, user_prompt, fallback_line.strip())
    return f"{reply_text}\n\n{line}"


def draft_proactive_followups() -> list[dict]:
    """Scans for unfulfilled orders and drafts a check-in message for each.
    Returns list of {customer_id, order, draft} — caller decides channel/routing."""
    provider = get_provider()
    drafts = []
    for order in provider.unfulfilled_orders_for_proactive_outreach():
        customer = provider.get_customer(order["customer_id"])
        name = customer["name"] if customer else "bạn"
        fallback = (
            f"Chào {name}, đơn {order['order_number']} của bạn tại {config.SHOP_NAME} đang được chuẩn bị. "
            "Shop sẽ cập nhật ngay khi có mã vận đơn, cảm ơn bạn đã kiên nhẫn chờ đợi nhé!"
        )
        system_prompt = (
            f"Bạn là Sales Agent của BannoCRM cho shop '{config.SHOP_NAME}'. Viết tin nhắn chủ động (proactive) "
            "ngắn gọn, thân thiện bằng tiếng Việt, trấn an khách hàng về đơn hàng chưa được xử lý xong, "
            "không hứa hẹn thời gian cụ thể nếu không có dữ liệu."
        )
        user_prompt = f"Khách hàng: {name}. Đơn hàng: {order['order_number']}, trạng thái: {order['fulfillment_status']}."
        draft = llm.complete_text(system_prompt, user_prompt, fallback)
        drafts.append({"customer_id": order["customer_id"], "order": order, "draft": draft})
    return drafts
