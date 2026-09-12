"""Knowledge Agent: turns the shop owner's corrections into standing rules.

Every time the owner edits a draft before approving it, that edit carries
information the system did not have — a tone the shop uses, a policy detail
missing from the FAQ, a fact about how they do business. Without this agent
the correction is applied once and thrown away; with it, the same correction
never has to be made twice.
"""
import json
import threading

from .. import config, llm

_LESSONS_PATH = config.DATA_DIR / "learned.json"
_lock = threading.Lock()
MAX_LESSONS = 30


def load_lessons(limit: int = 6) -> list[dict]:
    try:
        with open(_LESSONS_PATH, encoding="utf-8") as f:
            return json.load(f)[-limit:]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _append(lesson: dict):
    with _lock:
        try:
            with open(_LESSONS_PATH, encoding="utf-8") as f:
                lessons = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            lessons = []
        lessons.append(lesson)
        with open(_LESSONS_PATH, "w", encoding="utf-8") as f:
            json.dump(lessons[-MAX_LESSONS:], f, ensure_ascii=False, indent=2)


def as_prompt_block(limit: int = 6) -> str:
    lessons = load_lessons(limit)
    if not lessons:
        return ""
    lines = "\n".join(f"- {l['lesson']}" for l in lessons)
    return f"\nNhững điều chủ shop đã dặn (rút ra từ các lần chủ shop sửa bản nháp trước đây):\n{lines}\n"


def learn_from_edit(original: str, final: str, customer_message: str | None = None):
    """Compares the draft with what the owner actually sent and stores the
    difference as a rule. Runs in a background thread — approving must stay
    instant for the owner."""
    if not original or original.strip() == final.strip():
        return

    system_prompt = (
        "Bạn là Knowledge Agent của hệ thống CSKH. Chủ shop vừa SỬA bản nháp do AI viết trước khi gửi. "
        "Hãy rút ra MỘT bài học ngắn, tổng quát, áp dụng được cho các lần trả lời sau — "
        "không phải mô tả lần sửa này.\n"
        "Ví dụ tốt: 'Xưng hô là shop/mình, không dùng chúng tôi'. "
        "'Với yêu cầu hoàn tiền, luôn hẹn kiểm tra trong 24h thay vì hứa hoàn ngay'.\n"
        "Nếu chủ shop chỉ sửa lỗi chính tả hoặc thay đổi vụn vặt, trả về lesson rỗng.\n"
        'Trả JSON: {"lesson": "câu ngắn bằng tiếng Việt, dưới 25 từ", "type": "style|policy|fact"}'
    )
    user_prompt = (
        (f"Khách hỏi: {customer_message}\n\n" if customer_message else "")
        + f"BẢN NHÁP CỦA AI:\n{original}\n\nBẢN CHỦ SHOP THỰC SỰ GỬI:\n{final}"
    )

    result = llm.complete_json(system_prompt, user_prompt, {"lesson": "", "type": "style"})
    lesson = (result.get("lesson") or "").strip()
    if lesson:
        _append({"lesson": lesson, "type": result.get("type", "style")})
        print(f"[knowledge] đã học: {lesson}")


def learn_async(original: str, final: str, customer_message: str | None = None):
    threading.Thread(
        target=learn_from_edit, args=(original, final, customer_message), daemon=True
    ).start()
