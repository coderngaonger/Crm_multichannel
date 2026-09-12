(function () {
  const API_BASE = window.BANNO_API_BASE || "http://localhost:8000";
  const SESSION_KEY = "banno_session_id";

  function getSessionId() {
    let id = localStorage.getItem(SESSION_KEY);
    if (!id) {
      id = "web_" + Math.random().toString(36).slice(2, 10);
      localStorage.setItem(SESSION_KEY, id);
    }
    return id;
  }

  const sessionId = getSessionId();
  let lastMessageId = 0;
  let pollTimer = null;

  const style = document.createElement("style");
  style.textContent = `
    #banno-bubble { position: fixed; bottom: 20px; right: 20px; width: 56px; height: 56px;
      border-radius: 50%; background: #6d28d9; color: #fff; display: flex; align-items: center;
      justify-content: center; cursor: pointer; box-shadow: 0 4px 14px rgba(0,0,0,.25); z-index: 99999;
      font-size: 24px; }
    #banno-panel { position: fixed; bottom: 88px; right: 20px; width: 320px; max-width: 90vw;
      height: 440px; max-height: 70vh; background: #fff; border-radius: 12px; box-shadow: 0 8px 30px rgba(0,0,0,.2);
      display: none; flex-direction: column; overflow: hidden; z-index: 99999; font-family: system-ui, sans-serif; }
    #banno-panel.open { display: flex; }
    #banno-header { background: #6d28d9; color: #fff; padding: 12px 14px; font-weight: 600; font-size: 14px; }
    #banno-messages { flex: 1; overflow-y: auto; padding: 10px; font-size: 13px; background: #f9fafb; }
    .banno-msg { margin: 6px 0; padding: 8px 10px; border-radius: 10px; max-width: 85%; line-height: 1.4; white-space: pre-wrap; }
    .banno-msg.in { background: #6d28d9; color: #fff; margin-left: auto; }
    .banno-msg.out { background: #eee; color: #111; margin-right: auto; }
    #banno-input-row { display: flex; border-top: 1px solid #eee; }
    #banno-input { flex: 1; border: none; padding: 10px; font-size: 13px; outline: none; }
    #banno-send { border: none; background: #6d28d9; color: #fff; padding: 0 14px; cursor: pointer; }
  `;
  document.head.appendChild(style);

  const bubble = document.createElement("div");
  bubble.id = "banno-bubble";
  bubble.textContent = "💬";
  document.body.appendChild(bubble);

  const panel = document.createElement("div");
  panel.id = "banno-panel";
  panel.innerHTML = `
    <div id="banno-header">Banno Eyewear — Hỗ trợ khách hàng</div>
    <div id="banno-messages"></div>
    <div id="banno-input-row">
      <input id="banno-input" placeholder="Nhập tin nhắn..." />
      <button id="banno-send">Gửi</button>
    </div>
  `;
  document.body.appendChild(panel);

  const messagesEl = panel.querySelector("#banno-messages");
  const inputEl = panel.querySelector("#banno-input");
  const sendBtn = panel.querySelector("#banno-send");

  function appendMessage(direction, body) {
    const div = document.createElement("div");
    div.className = "banno-msg " + direction;
    div.textContent = body;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function renderMessages(list) {
    for (const m of list) {
      if (m.id <= lastMessageId) continue;
      lastMessageId = Math.max(lastMessageId, m.id);
      appendMessage(m.direction, m.body);
    }
  }

  async function loadHistory() {
    try {
      const res = await fetch(`${API_BASE}/api/channels/website/messages?session_id=${sessionId}`);
      const data = await res.json();
      renderMessages(data.messages || []);
    } catch (e) {
      console.warn("Banno widget: history load failed", e);
    }
  }

  async function poll() {
    try {
      const res = await fetch(
        `${API_BASE}/api/channels/website/messages?session_id=${sessionId}&since=${lastMessageId}`
      );
      const data = await res.json();
      renderMessages(data.messages || []);
    } catch (e) {
      /* ignore transient errors */
    }
  }

  async function send() {
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = "";
    appendMessage("in", text);
    try {
      const res = await fetch(`${API_BASE}/api/channels/website/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, text }),
      });
      const data = await res.json();
      if (data.reply) {
        appendMessage("out", data.reply);
      } else if (data.ack) {
        appendMessage("out", data.ack);
      }
      // Refresh from server so lastMessageId stays in sync with stored rows.
      await poll();
    } catch (e) {
      appendMessage("out", "(Lỗi kết nối tới máy chủ demo)");
    }
  }

  bubble.addEventListener("click", () => {
    panel.classList.toggle("open");
    if (panel.classList.contains("open") && !pollTimer) {
      loadHistory();
      pollTimer = setInterval(poll, 3000);
    }
  });
  sendBtn.addEventListener("click", send);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });
})();
