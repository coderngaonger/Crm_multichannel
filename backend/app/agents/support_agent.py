"""Support Agent: answers product / order / FAQ questions using live
commerce context (product catalog + order lookup), so replies are grounded
in real shop data instead of the model guessing."""
from .. import config, llm
from ..commerce.provider import get_provider
from . import knowledge_agent


def _format_product(p: dict) -> str:
    variants = ", ".join(
        f"{v['color']} (còn {v['inventory_quantity']})" if v["inventory_quantity"] > 0 else f"{v['color']} (hết hàng)"
        for v in p["variants"]
    )
    return f"- {p['title']} — {p['price']:,.0f} {p['currency']}. Màu: {variants}"


def _format_order(o: dict) -> str:
    items = "; ".join(li["title"] for li in o["line_items"])
    tracking = f", mã vận đơn {o['tracking_number']}" if o.get("tracking_number") else ""
    return (
        f"Đơn {o['order_number']}: {items}. Trạng thái thanh toán: {o['financial_status']}, "
        f"trạng thái giao hàng: {o['fulfillment_status']}{tracking}. "
        f"Dự kiến giao: {o.get('estimated_delivery', 'đang cập nhật')}."
    )


def gather_context(text: str, classification: dict, customer: dict | None, history: str = "") -> dict:
    provider = get_provider()
    ctx = {"products": [], "order": None, "faqs": provider.list_faqs()}
    # Follow-ups like "thế còn màu bạc?" carry no searchable keyword on their
    # own — the subject lives in the previous turns.
    searchable = f"{history}\n{text}" if history else text

    if classification["intent"] == "product_inquiry":
        ctx["products"] = provider.search_products(searchable)
    elif classification["intent"] in ("order_status", "refund_request"):
        order = provider.find_order_mentioned(searchable)
        if not order and customer:
            order = provider.latest_order_for_customer(customer.get("id"))
        ctx["order"] = order

    return ctx


def draft_reply(text: str, classification: dict, customer: dict | None, ctx: dict, history: str = "") -> str:
    customer_name = customer["name"] if customer else "bạn"

    context_lines = []
    if ctx["products"]:
        context_lines.append("Sản phẩm liên quan:\n" + "\n".join(_format_product(p) for p in ctx["products"]))
    if ctx["order"]:
        context_lines.append("Đơn hàng liên quan:\n" + _format_order(ctx["order"]))
    if classification["intent"] == "general_faq":
        context_lines.append(
            "FAQ:\n" + "\n".join(f"- {f['question']}: {f['answer']}" for f in ctx["faqs"])
        )
    context_block = "\n\n".join(context_lines) if context_lines else "(không có dữ liệu liên quan trong hệ thống)"

    # Fallback used when no LLM key is configured yet, or the call fails.
    if ctx["order"]:
        fallback = f"Chào {customer_name}, {_format_order(ctx['order'])}"
    elif ctx["products"]:
        fallback = f"Chào {customer_name}, đây là thông tin bạn hỏi:\n" + "\n".join(
            _format_product(p) for p in ctx["products"]
        )
    else:
        fallback = f"Chào {customer_name}, cảm ơn bạn đã liên hệ {config.SHOP_NAME}. Đội ngũ sẽ phản hồi sớm."

    system_prompt = (
        f"Bạn là Support Agent của BannoCRM cho shop kính mắt '{config.SHOP_NAME}'. "
        "Trả lời khách hàng bằng tiếng Việt, ngắn gọn (2-4 câu), lịch sự, thân thiện, đúng trọng tâm câu hỏi. "
        "CHỈ dùng thông tin có trong phần 'Dữ liệu hệ thống' bên dưới, không bịa số liệu, giá, hay tình trạng đơn hàng. "
        "Nếu không có dữ liệu phù hợp, xin lỗi và hẹn nhân viên hỗ trợ thêm. "
        "Nếu có lịch sử hội thoại, hãy trả lời tiếp mạch đang nói, không chào hỏi lại từ đầu "
        "và không lặp lại thông tin vừa nói ở lượt trước."
        + knowledge_agent.as_prompt_block()
    )
    user_prompt = (
        (f"Lịch sử hội thoại trước đó:\n{history}\n\n" if history else "")
        + f"Tin nhắn MỚI của khách ({classification['intent']}): {text}\n\n"
        + f"Dữ liệu hệ thống:\n{context_block}"
    )
    return llm.complete_text(system_prompt, user_prompt, fallback)
