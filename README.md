# Banno CRM — Omni-channel customer-care agent for small e-commerce shops

An agent team that lives **inside the channels a small shop already uses** —
the website chat widget, Telegram, and the shop owner's own Telegram chat —
instead of in a separate chat window.

The unusual part is the **approval surface**: when the AI drafts a reply that
is too risky to auto-send (a refund request, an angry VIP customer), the draft
is pushed to the shop owner *in Telegram* with Approve / Edit / Reject buttons.
The owner reviews AI work from their phone, in the app they already have open,
and the approved text lands back in the customer's original channel.

## Architecture

```
Website widget ─┐                      ┌─► auto-send (safe intents)
                ├─► Router Agent ──────┤
Telegram    ────┘   (intent/sentiment/ └─► Human-in-the-loop queue
                     priority/VIP)             │
                          │                    ├─► Dashboard (approve/edit/reject)
                          ▼                    └─► Owner's Telegram (inline buttons)
                  Support Agent  ── grounded in commerce data (products,
                          │           stock, orders, FAQs)
                          ▼
                   Sales Agent   ── cross-sell + proactive follow-up drafts
```

The agents run on **Gemini 2.5 Flash via Google Vertex AI** (`app/llm.py`),
authenticated with a GCP service account.

- **Router Agent** (`app/agents/router_agent.py`) — classifies every inbound
  message: sentiment, intent, priority, and whether a human must approve.
  Falls back to accent-insensitive Vietnamese keyword rules when the model is
  unreachable, so the pipeline never hard-fails.
- **Support Agent** (`app/agents/support_agent.py`) — answers grounded in real
  shop data (catalog, stock per colour variant, order status, FAQs). It is
  told explicitly not to invent prices or delivery states.
- **Sales Agent** (`app/agents/sales_agent.py`) — appends a cross-sell line to
  product answers, and drafts proactive follow-ups for unfulfilled orders.
  Proactive messages **always** go through human approval.
- **Orchestrator** (`app/orchestrator.py`) — wires the agents, applies the
  approval gate, dispatches to the right channel, and notifies the owner.

Channel adapters register their sender with the orchestrator at startup, so
adding a channel (email, Zalo, Messenger) means writing one adapter — no
changes to the agent logic.

## Commerce data

`USE_MOCK_COMMERCE=true` (default) reads `backend/data/*.json`, shaped like
Shopify's Admin API. `app/commerce/shopify_provider.py` implements the same
function signatures against the real API — set `USE_MOCK_COMMERCE=false` plus
store domain and token to switch a running demo to a real store with no code
changes.

## Quickstart

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env      # then fill in the keys below
python demo_seed.py       # optional: populate the inbox with sample traffic
python -m uvicorn app.main:app --port 8000
```

- Owner console (mobile-first): http://localhost:8000 — Dashboard / Inbox / Analytics / Settings,
  every number rendered from the live API (`frontend/index.html`; `frontend/test.html` is the
  original Stitch design export kept as a visual reference)
- Demo shop with the chat widget: http://localhost:8000/widget/

### Keys

| Variable | How to get it | Effect if empty |
|---|---|---|
| `VERTEX_PROJECT_ID` + `GOOGLE_APPLICATION_CREDENTIALS` | A GCP project with the Vertex AI API enabled, plus a service-account JSON key (or `gcloud auth application-default login` and leave the credentials path empty) | Agents fall back to rule-based classification and templated replies — the system still runs end to end |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather), ~2 min | Telegram channel disabled, website widget still works |
| `OWNER_TELEGRAM_CHAT_ID` | Message your bot, then open `/api/telegram/recent-chats` | Owner approval console disabled, dashboard approvals still work |

## Demo script (2 minutes)

1. **Website widget** (http://localhost:8000/widget/) — ask
   *"kinh ram aviator con mau den khong"*. The reply comes back with real
   stock per colour plus a cross-sell line. Point at the dashboard: classified
   `product_inquiry / normal`, auto-sent, no human needed.
2. **Telegram, as a customer** — message @your_bot *"don #1001 toi dau roi"*.
   Same pipeline, different channel, one unified inbox.
3. **The escalation** — send *"Toi muon hoan tien don #1003, qua te"*.
   Nothing is sent to the customer. Instead the **owner's Telegram** buzzes
   with the draft and Approve / Edit / Reject buttons.
4. **Approve from the phone** — tap ✏️ Sửa, type a corrected sentence, send.
   It lands in the customer's chat, and the dashboard flips the message from
   `pending_review` to `edited_sent`.
5. **Proactive** — hit "Quét đơn chưa xử lý". The Sales Agent drafts follow-ups
   for unfulfilled orders, queued for the same approval gate.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/channels/website/message` | Widget inbound message |
| `GET /api/channels/website/messages` | Widget polls for replies sent after approval |
| `GET /api/conversations` | Unified inbox |
| `GET /api/pending` | Human-in-the-loop queue |
| `POST /api/pending/{id}/approve` | Approve, optionally with edited text |
| `POST /api/pending/{id}/reject` | Reject a draft |
| `POST /api/conversations/{id}/reply` | Owner types a reply themselves, bypassing the agents |
| `POST /api/proactive/scan` | Run the Sales Agent proactive scan |
| `GET /api/stats` | Per-agent counters, auto-send rate, response time, channel/priority/intent breakdowns |
