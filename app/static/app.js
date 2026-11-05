"use strict";

/* Emotion Responder dashboard. Plain JavaScript, no build step.
   It polls the local API every 1.5 seconds and redraws what changed. */

const EMOTIONS = ["joy", "sadness", "anger", "fear", "disgust", "surprise", "neutral"];

const STATUS_TEXT = {
  stopped: "Stopped",
  starting: "Starting",
  waiting_login: "Waiting for WhatsApp login",
  running: "Running",
  stopping: "Stopping",
  error: "Stopped with an error",
};

const MESSAGE_STATE = {
  thinking: "Writing a reply...",
  dry_run: "Practice: not sent",
  pending: "Waiting for your OK",
  approved: "Sending...",
  sent: "Sent",
  discarded: "Discarded",
  skipped: "Skipped",
  failed: "Failed",
};

const KIND_LABEL = {
  image: "Photo", video: "Video", audio: "Voice note", document: "Document", other: "Attachment",
};

const $ = (selector) => document.querySelector(selector);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, method = "GET", body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) detail = "Please check the values you entered.";
    } catch (_) { /* keep statusText */ }
    throw new Error(detail);
  }
  return response.json();
}

/* ---- shared UI state ---------------------------------------------------- */

let state = null;
let actionNotice = "";
let connectionLost = false;
let settingsDirty = false;
let busy = false;

let stripSig = "";
let feedSig = "";
let demoSig = "";
let feedRendered = false;
const knownIds = new Set();
const approvalNodes = new Map();

const hue = (emotion) => `var(--${EMOTIONS.includes(emotion) ? emotion : "neutral"})`;
const clock = (epoch) =>
  new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const isActive = (status) => ["starting", "waiting_login", "running"].includes(status);

/* ---- header, notice ----------------------------------------------------- */

function renderTop() {
  const engine = state.engine;
  const status = $("#status");
  status.dataset.state = engine.status;
  $("#status-text").textContent = STATUS_TEXT[engine.status] || engine.status;

  const toggle = $("#toggle");
  toggle.disabled = engine.status === "stopping";
  toggle.textContent = isActive(engine.status) ? "Stop bot" : engine.status === "stopping" ? "Stopping..." : "Start bot";
  toggle.classList.toggle("primary", !isActive(engine.status));

  let watching = "Not watching a chat yet";
  if (engine.chat_title) watching = `Watching: ${engine.chat_title}`;
  else if (isActive(engine.status)) watching = "Waiting for a chat to be open";
  $("#watching").textContent = watching;
}

function renderNotice() {
  let text = "";
  if (connectionLost) {
    text = "Can't reach the app. Make sure run.py is still running in your terminal.";
  } else if (actionNotice) {
    text = actionNotice;
  } else if (state) {
    const engine = state.engine;
    if (engine.detail) text = engine.detail;
    else if (engine.status === "error" && engine.last_error) text = engine.last_error;
    else if (!state.llm.ready && !isActive(engine.status)) {
      text = "No Gemini API key found. Add GEMINI_API_KEY to the .env file, then restart the app.";
    }
  }
  const notice = $("#notice");
  notice.hidden = !text;
  notice.textContent = text;
}

/* ---- settings ----------------------------------------------------------- */

function renderSettings() {
  const s = state.settings;
  const form = $("#settings-form");
  for (const radio of form.querySelectorAll('input[name="mode"]')) radio.checked = radio.value === s.mode;
  $("#driver").disabled = state.engine.running;
  if (settingsDirty) return;
  $("#driver").value = s.driver;
  $("#target").value = s.target_chat;
  $("#style").value = s.style_notes;
  $("#poll").value = s.poll_interval;
  $("#delay").value = s.reply_delay;
  $("#nontext").value = s.non_text_policy;
  $("#ack").value = s.ack_text;
  form.elements.self_chat_test.checked = s.self_chat_test;
  form.elements.reply_to_existing.checked = s.reply_to_existing;
}

async function saveSettings(partial) {
  try {
    actionNotice = "";
    state.settings = await api("/api/settings", "POST", partial);
    return true;
  } catch (error) {
    actionNotice = error.message;
    renderNotice();
    return false;
  }
}

function setupSettingsForm() {
  const form = $("#settings-form");

  form.addEventListener("input", (event) => {
    if (event.target.name !== "mode") settingsDirty = true;
    $("#saved").textContent = "";
  });

  for (const radio of form.querySelectorAll('input[name="mode"]')) {
    radio.addEventListener("change", async () => {
      const previous = state.settings.mode;
      if (radio.value === "auto" && previous !== "auto") {
        const ok = confirm("Replies will be sent without asking you first. Continue?");
        if (!ok) { radio.checked = false; renderSettings(); return; }
      }
      if (!(await saveSettings({ mode: radio.value }))) renderSettings();
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = form.elements;
    const partial = {
      target_chat: f.target_chat.value,
      style_notes: f.style_notes.value,
      poll_interval: Number(f.poll_interval.value) || 3,
      reply_delay: Number(f.reply_delay.value) || 0,
      non_text_policy: f.non_text_policy.value,
      ack_text: f.ack_text.value,
      self_chat_test: f.self_chat_test.checked,
      reply_to_existing: f.reply_to_existing.checked,
    };
    if (!f.driver.disabled) partial.driver = f.driver.value;
    if (await saveSettings(partial)) {
      settingsDirty = false;
      $("#saved").textContent = "Saved";
      setTimeout(() => { $("#saved").textContent = ""; }, 2500);
      renderSettings();
      renderNotice();
    }
  });
}

/* ---- mood strip --------------------------------------------------------- */

function heightClass(status) {
  if (status === "sent") return "h-full";
  if (status === "skipped" || status === "failed") return "h-low";
  return "h-mid";
}

function renderMood(messages) {
  const last = [...messages].reverse().find((m) => EMOTIONS.includes(m.emotion));
  document.body.dataset.mood = last ? last.emotion : "neutral";
}

function renderStrip(messages) {
  const recent = messages.slice(-90);
  const sig = JSON.stringify(recent.map((m) => [m.id, m.status, m.emotion]));
  if (sig === stripSig) return;
  stripSig = sig;

  const strip = $("#strip");
  strip.replaceChildren();
  if (!recent.length) {
    strip.append(el("span", { class: "strip-empty", text: "Your mood strip fills in as messages arrive." }));
    $("#strip-caption").textContent = "";
    return;
  }
  for (const m of recent) {
    const label = `${m.emotion || "no emotion"}, ${MESSAGE_STATE[m.status] || m.status}`;
    strip.append(el("span", {
      class: `tick ${heightClass(m.status)}`,
      style: `--hue:${hue(m.emotion)}`,
      title: label,
    }));
  }
  $("#strip-caption").textContent =
    "Each tick is one message, colored by emotion. Tall ticks were sent, medium ones are drafts or waiting for you, short faded ones were skipped or failed.";
}

/* ---- approvals ---------------------------------------------------------- */

async function approve(id, textarea, buttons) {
  buttons.forEach((b) => { b.disabled = true; });
  try {
    actionNotice = "";
    await api(`/api/messages/${id}/approve`, "POST", { text: textarea.value });
  } catch (error) {
    actionNotice = error.message;
    buttons.forEach((b) => { b.disabled = false; });
  }
  renderNotice();
  refresh();
}

async function discard(id, buttons) {
  buttons.forEach((b) => { b.disabled = true; });
  try {
    actionNotice = "";
    await api(`/api/messages/${id}/discard`, "POST");
  } catch (error) {
    actionNotice = error.message;
    buttons.forEach((b) => { b.disabled = false; });
  }
  renderNotice();
  refresh();
}

function buildApproval(m) {
  const textarea = el("textarea", { rows: 3, "aria-label": "Reply text" });
  textarea.value = m.reply_text || "";
  const send = el("button", { class: "btn primary", type: "button", text: "Send reply" });
  const drop = el("button", { class: "btn", type: "button", text: "Discard" });
  const buttons = [send, drop];
  send.addEventListener("click", () => approve(m.id, textarea, buttons));
  drop.addEventListener("click", () => discard(m.id, buttons));
  return el("div", { class: "approval", style: `--hue:${hue(m.emotion)}` },
    el("p", { class: "incoming", text: m.incoming_text || `[${KIND_LABEL[m.kind] || "Message"}]` }),
    el("div", { class: "meta" }, el("span", { class: "tag", text: m.emotion || "neutral" })),
    el("p", { class: "reply-label", text: "Your reply (edit it if you like)" }),
    textarea,
    el("div", { class: "row" }, send, drop),
  );
}

function renderPending(messages) {
  const pending = messages.filter((m) => m.status === "pending");
  const ids = new Set(pending.map((m) => m.id));
  for (const [id, node] of approvalNodes) {
    if (!ids.has(id)) { node.remove(); approvalNodes.delete(id); }
  }
  for (const m of pending) {
    if (!approvalNodes.has(m.id)) {
      const node = buildApproval(m);
      approvalNodes.set(m.id, node);
      $("#pending").append(node);
    }
  }
  $("#pending-zone").hidden = pending.length === 0;
}

/* ---- conversation feed -------------------------------------------------- */

function buildExchange(m, fresh) {
  const isMedia = m.kind !== "text" || !m.incoming_text;
  const incoming = isMedia
    ? el("p", { class: "incoming muted", text: `[${KIND_LABEL[m.kind] || "Message"}]` })
    : el("p", { class: "incoming", text: m.incoming_text });

  const meta = el("div", { class: "meta" },
    m.emotion && el("span", { class: "tag", text: m.emotion }),
    el("span", { text: clock(m.created_at) }),
    m.latency_ms ? el("span", { text: `${(m.latency_ms / 1000).toFixed(1)} s to write` }) : null,
  );

  let stateText = MESSAGE_STATE[m.status] || m.status;
  if (m.status === "pending") stateText += " (see above)";

  return el("article", { class: `xchg${fresh ? " fresh" : ""}`, style: `--hue:${hue(m.emotion)}` },
    incoming,
    meta,
    m.reply_text && el("p", { class: "reply-label", text: "Reply" }),
    m.reply_text && el("p", { class: "reply", text: m.reply_text }),
    el("p", { class: `state${m.status === "failed" ? " bad" : ""}`, text: stateText }),
    m.error && el("p", { class: "error-text", text: m.error }),
  );
}

function renderFeed(messages) {
  const sig = JSON.stringify(messages.map((m) => [m.id, m.status, m.reply_text, m.emotion, m.error]));
  if (sig === feedSig) return;
  feedSig = sig;

  const feed = $("#feed");
  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
  feed.replaceChildren();

  if (!messages.length) {
    const hint = state.settings.driver === "mock"
      ? "Start the bot, then use Try it on the right to send a message as your friend."
      : "Start the bot, open the chat it should watch, then wait for a new message.";
    feed.append(el("p", { class: "empty", text: `No messages yet. ${hint}` }));
    return;
  }
  for (const m of messages) {
    feed.append(buildExchange(m, feedRendered && !knownIds.has(m.id)));
    knownIds.add(m.id);
  }
  if (nearBottom || !feedRendered) feed.scrollTop = feed.scrollHeight;
  feedRendered = true;
}

/* ---- side column -------------------------------------------------------- */

function renderSide() {
  const { stats, llm } = state;
  const counts = stats.emotions || {};
  const top = Math.max(1, ...EMOTIONS.map((e) => counts[e] || 0));
  const mix = $("#mix");
  mix.replaceChildren();
  for (const emotion of EMOTIONS) {
    const n = counts[emotion] || 0;
    mix.append(el("li", { style: `--hue:var(--${emotion})` },
      el("span", { text: emotion }),
      el("span", { class: "bar" }, el("i", { style: `width:${(n / top) * 100}%` })),
      el("span", { class: "n", text: n }),
    ));
  }

  let summary = "Nothing handled yet.";
  if (stats.handled || stats.skipped) {
    summary = `Handled ${stats.handled} ${stats.handled === 1 ? "message" : "messages"}: ` +
      `${stats.sent} sent, ${stats.waiting} waiting, ${stats.skipped} skipped` +
      (stats.failed ? `, ${stats.failed} failed` : "") + ".";
    if (stats.avg_latency_ms) summary += ` Average time to write a reply: ${(stats.avg_latency_ms / 1000).toFixed(1)} s.`;
  }
  $("#summary").textContent = summary;
  $("#ai-label").textContent = `AI: ${llm.label}${llm.ready ? "" : " (no API key found)"}`;
}

/* ---- demo chat ---------------------------------------------------------- */

function renderDemo(log) {
  const sig = JSON.stringify(log.map((m) => m.key));
  if (sig === demoSig) return;
  demoSig = sig;
  const box = $("#demo-log");
  box.replaceChildren();
  if (!log.length) box.append(el("p", { class: "empty", text: "No messages yet. Type below to start." }));
  for (const m of log) {
    const media = m.kind !== "text";
    box.append(el("div", {
      class: `bubble ${m.direction}${media ? " media" : ""}`,
      text: media ? `[${KIND_LABEL[m.kind] || "Attachment"}]` : m.text,
    }));
  }
  box.scrollTop = box.scrollHeight;
}

function setupDemo() {
  $("#demo-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#demo-text");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    try { await api("/api/demo/incoming", "POST", { text, kind: "text" }); }
    catch (error) { actionNotice = error.message; renderNotice(); }
    refresh();
  });
  $("#demo-photo").addEventListener("click", async () => {
    await api("/api/demo/incoming", "POST", { text: "", kind: "image" });
    refresh();
  });
  $("#demo-reset").addEventListener("click", async () => {
    await api("/api/demo/reset", "POST");
    refresh();
  });
}

/* ---- main loop ---------------------------------------------------------- */

async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const [newState, messages] = await Promise.all([api("/api/state"), api("/api/messages?limit=150")]);
    state = newState;
    connectionLost = false;
    renderTop();
    renderSettings();
    renderMood(messages);
    renderStrip(messages);
    renderPending(messages);
    renderFeed(messages);
    renderSide();

    const showDemo = state.settings.driver === "mock";
    $("#demo").hidden = !showDemo;
    if (showDemo) renderDemo(await api("/api/demo/chat"));
  } catch (error) {
    connectionLost = true;
  } finally {
    renderNotice();
    busy = false;
  }
}

function setup() {
  setupSettingsForm();
  setupDemo();

  $("#toggle").addEventListener("click", async () => {
    try {
      actionNotice = "";
      await api(isActive(state.engine.status) ? "/api/stop" : "/api/start", "POST");
    } catch (error) {
      actionNotice = error.message;
    }
    renderNotice();
    refresh();
  });

  $("#clear").addEventListener("click", async () => {
    if (!confirm("Clear the message history shown here? Chats themselves are not touched.")) return;
    await api("/api/clear", "POST");
    feedSig = stripSig = "";
    knownIds.clear();
    feedRendered = false;
    refresh();
  });

  refresh();
  setInterval(refresh, 1500);
}

setup();
