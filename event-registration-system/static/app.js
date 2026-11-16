const $ = (s, r = document) => r.querySelector(s);
const api = async (url, opts = {}) => {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  return { status: res.status, data: await res.json().catch(() => ({})) };
};
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = d => { const t = new Date(d.replace(" ", "T")); return isNaN(t) ? d : t.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); };

let events = [], currentEvent = null;

// ---------- events ----------
async function loadEvents() {
  events = (await api("/api/events")).data;
  const grid = $("#event-grid");
  grid.innerHTML = events.map(e => {
    const pct = Math.min(100, Math.round(e.registered / e.capacity * 100));
    const state = e.remaining <= 0 ? "full" : e.remaining <= 2 ? "low" : "";
    const label = e.overbooked ? `OVERBOOKED by ${-e.remaining}` : e.remaining <= 0 ? "Full"
      : e.remaining <= 2 ? `Only ${e.remaining} left!` : `${e.remaining} places left`;
    return `<article class="card ev">
      <h3>${esc(e.title)}</h3>
      <div class="meta">📅 ${esc(fmtDate(e.date))} · 📍 ${esc(e.venue)}</div>
      <div class="bar-track"><div class="bar-fill ${state}" style="width:${pct}%"></div></div>
      <div class="count"><span>${e.registered} / ${e.capacity} registered</span><span class="badge ${state}">${label}</span></div>
      <div class="row">
        <button class="btn" data-reg="${e.id}" ${e.remaining <= 0 ? "disabled" : ""}>Register</button>
        <button class="btn ghost" data-att="${e.id}">Attendees</button>
      </div></article>`;
  }).join("") || `<p class="muted">No events yet.</p>`;
  const keep = $("#lab-event").value;
  $("#lab-event").innerHTML = events.map(e => `<option value="${e.id}">${esc(e.title)} (cap ${e.capacity})</option>`).join("");
  if (keep) $("#lab-event").value = keep;
}

$("#event-grid").addEventListener("click", async e => {
  const reg = e.target.closest("[data-reg]"), att = e.target.closest("[data-att]");
  if (reg) {
    currentEvent = events.find(x => x.id == reg.dataset.reg);
    $("#reg-title").textContent = "Register: " + currentEvent.title;
    $("#reg-msg").textContent = ""; $("#reg-msg").className = "msg";
    $("#dlg-register").showModal();
  }
  if (att) {
    const ev = events.find(x => x.id == att.dataset.att);
    const rows = (await api(`/api/events/${ev.id}/registrations`)).data;
    $("#att-title").textContent = `${ev.title} – ${rows.length} attendee(s)`;
    $("#att-list").innerHTML = rows.map(r => `<div class="att"><b>${esc(r.name)}</b> · ${esc(r.email)}</div>`).join("") || `<p class="muted">Nobody yet.</p>`;
    $("#dlg-attendees").showModal();
  }
});

document.addEventListener("click", e => { if (e.target.matches("[data-close]")) e.target.closest("dialog").close(); });

// ---------- register ----------
$("#reg-form").addEventListener("submit", async ev => {
  ev.preventDefault();
  const btn = $("#reg-submit"); btn.disabled = true;
  const { data } = await api(`/api/events/${currentEvent.id}/register`, {
    method: "POST", body: { name: $("#reg-name").value, email: $("#reg-email").value } });
  const m = $("#reg-msg");
  m.className = "msg " + (data.ok ? "ok" : "bad");
  m.textContent = data.ok ? `✅ ${data.message} Ticket: ${data.ticket}` : "⚠️ " + data.message;
  btn.disabled = false;
  if (data.ok) { $("#lookup-email").value = $("#reg-email").value; $("#reg-form").reset(); }
  loadEvents();
});

// ---------- create event ----------
$("#btn-new-event").onclick = () => { $("#ev-msg").textContent = ""; $("#dlg-event").showModal(); };
$("#event-form").addEventListener("submit", async ev => {
  ev.preventDefault();
  const { data } = await api("/api/events", { method: "POST", body: {
    title: $("#ev-title").value, venue: $("#ev-venue").value,
    date: $("#ev-date").value.replace("T", " "), capacity: $("#ev-cap").value } });
  if (data.ok) { $("#dlg-event").close(); $("#event-form").reset(); loadEvents(); }
  else { $("#ev-msg").className = "msg bad"; $("#ev-msg").textContent = data.message; }
});

// ---------- my registrations / cancel ----------
async function loadMine() {
  const email = $("#lookup-email").value.trim(); if (!email) return;
  const { data } = await api("/api/registrations?email=" + encodeURIComponent(email));
  $("#my-list").innerHTML = (Array.isArray(data) ? data : []).map(r => `
    <div class="card my"><div><b>${esc(r.title)}</b><br><span class="muted">${esc(fmtDate(r.date))} · ${esc(r.venue)} · Ticket ${esc(r.ticket)}</span></div>
    <button class="btn ghost" data-cancel="${r.id}" data-email="${esc(r.email)}">Cancel</button></div>`).join("")
    || `<p class="muted">No registrations found for that email.</p>`;
}
$("#lookup-form").addEventListener("submit", e => { e.preventDefault(); loadMine(); });
$("#my-list").addEventListener("click", async e => {
  const b = e.target.closest("[data-cancel]"); if (!b) return;
  if (!confirm("Cancel this registration?")) return;
  await api(`/api/registrations/${b.dataset.cancel}/cancel`, { method: "POST", body: { email: b.dataset.email } });
  loadMine(); loadEvents();
});

// ---------- Race Lab ----------
async function runLab(safe) {
  const btns = document.querySelectorAll("#lab-mode button"); btns.forEach(b => b.disabled = true);
  const { data: r, status } = await api("/api/simulate", { method: "POST", body: {
    event_id: $("#lab-event").value, users: +$("#lab-users").value, safe, same_user: $("#lab-same").checked } });
  btns.forEach(b => b.disabled = false);
  if (status !== 200) { $("#lab-result").innerHTML = `<div class="result bad">${esc(r.message || "Error")}</div>`; return; }
  const e = r.event, bad = r.problem_detected;
  $("#lab-result").innerHTML = `<div class="result ${bad ? "bad" : "good"}">
    <b class="big">${bad ? "❌ Race condition detected" : "✅ Data stayed consistent"}</b> — ${esc(r.mode)}
    <div class="stats">
      <span>Capacity: <b>${e.capacity}</b></span>
      <span>Final registered: <b>${e.registered}</b></span>
      <span>Accepted: <b>${r.accepted}</b></span>
      <span>Rejected (full): <b>${r.rejected_full}</b></span>
      <span>Rejected (duplicate): <b>${r.rejected_duplicate}</b></span>
      <span>Duplicate emails stored: <b>${r.duplicate_emails}</b></span>
      <span>Time: <b>${r.elapsed_ms} ms</b></span>
    </div>
    ${e.overbooked ? `<p>Overbooked by <b>${-e.remaining}</b> seat(s): several users read “seats available” before anyone wrote.</p>` : ""}</div>`;
  $("#lab-users-out").innerHTML = r.results.map(x =>
    `<span class="chip ${x.ok ? "ok" : x.code === "duplicate" ? "dup" : "no"}">${x.user.replace("User-", "U")}: ${x.ok ? "✔ got seat" : x.code}</span>`).join("");
  const log = (await api("/api/log")).data;
  $("#lab-log").innerHTML = log.map(l => `<div class="${l.action}">${l.time} [${esc(l.thread)}] ${l.action} – ${esc(l.detail)}</div>`).join("");
  loadEvents();
}
$("#lab-mode").addEventListener("click", e => { const b = e.target.closest("button"); if (b) runLab(b.dataset.safe === "true"); });
$("#lab-clear").onclick = async () => {
  await api(`/api/events/${$("#lab-event").value}/clear-simulated`, { method: "POST" });
  $("#lab-result").innerHTML = ""; $("#lab-log").innerHTML = ""; $("#lab-users-out").innerHTML = ""; loadEvents();
};

loadEvents();
setInterval(() => { if (!document.querySelector("dialog[open]")) loadEvents(); }, 8000);
