// Shared page behaviour: card buttons, filters, "Pick for us", confirm-twice buttons.
(() => {
  "use strict";

  async function post(url, body) {
    const r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (r.status === 401) { location.href = "/login"; throw new Error("signed out"); }
    if (!r.ok) throw new Error(`Request failed (${r.status})`);
    return r.json();
  }

  function swapCard(card, html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    const fresh = t.content.firstElementChild;
    // The same place can be on screen twice (the pick and the list): update both.
    document.querySelectorAll(`.card[data-id="${card.dataset.id}"]`).forEach((c) => {
      c.replaceWith(c === card ? fresh : fresh.cloneNode(true));
    });
    return fresh;
  }

  // ---- card buttons: Favorite / Want to try / We ate here / Hide
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".act[data-act]");
    if (!btn) return;
    const card = btn.closest(".card");
    const id = Number(card.dataset.id);
    const pressed = btn.dataset.act + (btn.dataset.status || "");
    btn.disabled = true;
    try {
      const data = btn.dataset.act === "visit"
        ? await post("/api/visit", { place_id: id })
        : await post("/api/mark", { place_id: id, status: btn.dataset.status });
      const fresh = swapCard(card, data.html);
      [...fresh.querySelectorAll(".act")]
        .find((b) => b.dataset.act + (b.dataset.status || "") === pressed)?.focus();
    } catch (err) {
      btn.disabled = false;
      if (err.message !== "signed out") alert("That didn't save. Check the connection and try again.");
    }
  });

  // ---- filters: submit as soon as something changes
  const filters = document.getElementById("filters");
  if (filters) {
    let timer;
    filters.addEventListener("change", () => filters.requestSubmit());
    filters.querySelector('input[type="search"]')?.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => filters.requestSubmit(), 600);
    });
    // Leave empty fields out of the address so it stays readable.
    filters.addEventListener("submit", () => {
      filters.querySelectorAll("input, select").forEach((el) => {
        if ((el.type === "radio" || el.type === "checkbox") ? (el.checked && !el.value) : !el.value) {
          el.disabled = true;
        }
      });
    });
    window.addEventListener("pageshow", () => {
      filters.querySelectorAll(":disabled").forEach((el) => { el.disabled = false; });
    });
  }

  // ---- Pick for us
  const spinBtn = document.getElementById("spin");
  const result = document.getElementById("spin-result");
  if (spinBtn && result && filters) {
    const seen = [];
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

    async function spin() {
      spinBtn.disabled = true;
      const params = new URLSearchParams(filters.dataset.query);
      seen.forEach((id) => params.append("not", id));
      try {
        const r = await fetch(`/api/spin?${params}`);
        if (r.status === 401) { location.href = "/login"; return; }
        const data = await r.json();
        if (!data.count) {
          result.innerHTML = '<p class="spin-reel">Nothing matches those filters.</p>';
          result.hidden = false;
          return;
        }
        result.hidden = false;
        if (!calm) {
          const names = [...document.querySelectorAll("#cards .card")].map((c) => c.dataset.name);
          result.innerHTML = '<p class="spin-reel" aria-hidden="true"></p>';
          const reel = result.firstElementChild;
          for (let i = 0; i < 12 && names.length > 1; i++) {
            reel.textContent = names[Math.floor(Math.random() * names.length)];
            await sleep(60 + i * 12);
          }
        }
        seen.push(data.id);
        if (seen.length >= data.count) seen.length = 0;      // been through them all: start over
        result.innerHTML =
          `<div class="spin-head"><h2>Tonight: ${escapeHtml(data.name)}</h2>` +
          `<span class="muted small">picked from ${data.count} place${data.count === 1 ? "" : "s"}</span>` +
          '<button type="button" class="btn small" id="spin-again">Pick again</button></div>' + data.html;
        result.querySelector("#spin-again").addEventListener("click", spin);
        result.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "nearest" });
      } catch {
        result.innerHTML = '<p class="spin-reel">Could not pick just now. Try again.</p>';
        result.hidden = false;
      } finally {
        spinBtn.disabled = false;
      }
    }
    spinBtn.addEventListener("click", spin);
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  window.eatEscape = escapeHtml;

  // ---- slow forms: show that something is happening
  document.querySelectorAll("form.busy-form").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("button[data-busy]");
      if (btn) { btn.textContent = btn.dataset.busy; btn.disabled = true; }
    });
  });

  // ---- "Use this device's location"
  const geoBtn = document.getElementById("geo-btn");
  if (geoBtn) {
    const msg = document.getElementById("geo-msg");
    geoBtn.addEventListener("click", () => {
      if (!navigator.geolocation || !window.isSecureContext) {
        msg.textContent = "The browser only shares location on https or on this computer itself. Type an address instead.";
        return;
      }
      msg.textContent = "Asking the browser…";
      navigator.geolocation.getCurrentPosition((pos) => {
        const form = document.getElementById("geo-form");
        form.lat.value = pos.coords.latitude.toFixed(5);
        form.lon.value = pos.coords.longitude.toFixed(5);
        msg.textContent = "Searching nearby…";
        form.submit();
      }, () => { msg.textContent = "The browser would not share a location. Type an address instead."; },
      { timeout: 15000 });
    });
  }

  // ---- destructive buttons need a second click (same pattern as the D&D Companion)
  const ARM_MS = 5000;
  function disarm(btn) {
    clearTimeout(btn._timer);
    btn.classList.remove("armed");
    btn.textContent = btn._text;
    btn._hint?.remove();
    delete btn.dataset.armed;
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-confirm]");
    if (!btn || btn.dataset.armed) return;
    e.preventDefault();
    btn.dataset.armed = "1";
    btn._text = btn.textContent;
    btn.textContent = "Click again to confirm";
    btn.classList.add("armed");
    const hint = document.createElement("div");
    hint.className = "confirm-hint";
    hint.setAttribute("role", "alert");
    hint.textContent = btn.dataset.confirm;
    document.body.appendChild(hint);
    const r = btn.getBoundingClientRect();
    hint.style.left = `${Math.max(8, Math.min(r.left, innerWidth - hint.offsetWidth - 8)) + scrollX}px`;
    hint.style.top = `${r.bottom + scrollY + 6}px`;
    btn._hint = hint;
    btn._timer = setTimeout(() => disarm(btn), ARM_MS);
  }, true);
})();
