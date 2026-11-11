// Ask page: stream the model's answer, then show the cards of the places it named.
(() => {
  "use strict";
  const form = document.getElementById("ask-form");
  const box = document.getElementById("question");
  const go = document.getElementById("ask-go");
  const panel = document.getElementById("answer");
  const status = document.getElementById("answer-status");
  const out = document.getElementById("answer-text");
  const cards = document.getElementById("answer-cards");
  const esc = window.eatEscape;

  document.querySelectorAll(".chip-btn").forEach((b) => b.addEventListener("click", () => {
    box.value = b.textContent;
    form.requestSubmit();
  }));
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  function render(text, byNumber) {
    // Escape first, then turn **bold** and [n] into markup.
    return esc(text)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\[(\d+)\]/g, (m, n) => (byNumber[n] ? "" : m));
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = box.value.trim();
    if (!question) return;
    go.disabled = true;
    panel.hidden = false;
    out.textContent = "";
    cards.textContent = "";
    status.textContent = "Thinking… the first answer after a while can take half a minute.";
    let text = "";
    let byNumber = {};
    try {
      const r = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }),
      });
      if (r.status === 401) { location.href = "/login"; return; }
      if (!r.ok) throw new Error(`Request failed (${r.status})`);
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          const msg = JSON.parse(line);
          if (msg.type === "candidates") {
            byNumber = Object.fromEntries(msg.items.map((c) => [String(c.n), c]));
          } else if (msg.type === "text") {
            text += msg.piece;
            status.textContent = "";
            out.innerHTML = render(text, byNumber);
          } else if (msg.type === "error") {
            status.textContent = msg.message;
          }
        }
      }
      // Cards for every place the answer named, in the order it named them.
      const ids = [];
      for (const m of text.matchAll(/\[(\d+)\]/g)) {
        const c = byNumber[m[1]];
        if (c && !ids.includes(c.id)) ids.push(c.id);
      }
      for (const id of ids.slice(0, 5)) {
        const res = await fetch(`/api/card/${id}`);
        if (res.ok) cards.insertAdjacentHTML("beforeend", (await res.json()).html);
      }
    } catch {
      status.textContent = "Something went wrong getting an answer. Try again.";
    } finally {
      go.disabled = false;
    }
  });
})();
