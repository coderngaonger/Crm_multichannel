"""Seeds a realistic multi-channel inbox so the dashboard has content for a
demo/video. Run with the server stopped or running — both share the SQLite file.

    python demo_seed.py
"""
import sys

from app import db, orchestrator

# Windows consoles default to cp1252 and choke on Vietnamese output.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCENARIOS = [
    ("website", "demo_khoi", "Trần Minh Khôi", "kinh ram aviator con mau den khong shop oi"),
    ("website", "web_a1b2c3", "Website visitor", "Shop co bao hanh gong kinh khong a?"),
    ("telegram", "demo_ha", "thuha_vip", "Don hang #1001 cua minh toi dau roi ban oi"),
    ("telegram", "demo_ha", "thuha_vip", "Minh mua lau roi ma van chua nhan duoc, that vong qua"),
    ("website", "web_x9y8z7", "Website visitor", "Toi muon hoan tien don #1003, san pham bi loi"),
]


def main():
    db.init_db()
    for channel, external_id, display_name, text in SCENARIOS:
        result = orchestrator.handle_incoming(channel, external_id, display_name, text)
        status = "CẦN DUYỆT" if result["queued_for_review"] else "tự động gửi"
        print(f"[{channel}] {display_name}: {text[:45]}...")
        print(f"    -> {result['classification']['intent']} / {result['classification']['priority']} / {status}\n")

    print("Xong. Mở http://localhost:8000 để xem dashboard.")


if __name__ == "__main__":
    main()
