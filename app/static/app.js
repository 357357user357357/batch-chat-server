"use strict";

// ---------------------------------------------------------------
// Batch Chat — plain Vanilla JS client (no framework, no build step)
// ---------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);

const state = {
  token: localStorage.getItem("bc_token") || null,
  conversations: [],
  currentConversationId: null,
  defaultModels: [],
  selectedModels: JSON.parse(localStorage.getItem("bc_models") || "null"),
  // "live" = one model, standard tier; "flex" = one model via the cheaper
  // Flex tier (":flex" appended on send); "batch" = all selected models in parallel
  chatMode: ["live", "flex", "batch"].includes(localStorage.getItem("bc_chat_mode"))
    ? localStorage.getItem("bc_chat_mode")
    : "live",
  liveModel: localStorage.getItem("bc_live_model") || null,
  sending: false,
  batches: [],
  batchRefreshTimer: null,
};

const els = {
  loginView: $("#login-view"),
  appView: $("#app-view"),
  loginForm: $("#login-form"),
  loginPassword: $("#login-password"),
  loginAccount: $("#login-account"),
  loginRegisterToggle: $("#login-register-toggle"),
  registerForm: $("#register-form"),
  registerLogin: $("#register-login"),
  registerPassword: $("#register-password"),
  registerBack: $("#register-back"),
  registerError: $("#register-error"),
  loginError: $("#login-error"),
  conversationList: $("#conversation-list"),
  newChatBtn: $("#new-chat-btn"),
  logoutBtn: $("#logout-btn"),
  serverStatus: $("#server-status"),
  chatTitle: $("#chat-title"),
  messages: $("#messages"),
  chatForm: $("#chat-form"),
  chatInput: $("#chat-input"),
  sendBtn: $("#send-btn"),
  webSearchToggle: $("#web-search-toggle"),
  modelPickerBtn: $("#model-picker-btn"),
  modelDropdownHint: $("#model-dropdown-hint"),
  modeLiveBtn: $("#mode-live-btn"),
  modeFlexBtn: $("#mode-flex-btn"),
  modeBatchBtn: $("#mode-batch-btn"),
  modelDropdown: $("#model-dropdown"),
  modelCheckboxes: $("#model-checkboxes"),
  customModelInput: $("#custom-model-input"),
  addModelBtn: $("#add-model-btn"),
  importBtn: $("#import-btn"),
  importModal: $("#import-modal"),
  menuBtn: $("#menu-btn"),
  menuPopover: $("#menu-popover"),
  importClose: $("#import-close"),
  importTextarea: $("#import-textarea"),
  importStatus: $("#import-status"),
  importSubmit: $("#import-submit"),
  batchBtn: $("#batch-btn"),
  batchBadge: $("#batch-badge"),
  cacheBtn: $("#cache-btn"),
  syncBtn: $("#sync-btn"),
  reasoningSelect: $("#reasoning-select"),
  cachePings: $("#cache-pings"),
  batchModal: $("#batch-modal"),
  batchClose: $("#batch-close"),
  batchModel: $("#batch-model"),
  batchSystem: $("#batch-system"),
  batchJsonl: $("#batch-jsonl"),
  batchStatus: $("#batch-status"),
  batchSubmit: $("#batch-submit"),
  batchJobs: $("#batch-jobs"),
  settingsBtn: $("#settings-btn"),
  settingsModal: $("#settings-modal"),
  settingsClose: $("#settings-close"),
  settingsOpenrouterKey: $("#settings-openrouter-key"),
  settingsOpenrouterHint: $("#settings-openrouter-hint"),
  settingsTavilyKey: $("#settings-tavily-key"),
  settingsTavilyHint: $("#settings-tavily-hint"),
  settingsCacheDuration: $("#settings-cache-duration"),
  settingsKeepalive: $("#settings-keepalive"),
  settingsGoogleProject: $("#settings-google-project"),
  settingsGoogleLocation: $("#settings-google-location"),
  settingsGoogleJson: $("#settings-google-json"),
  settingsGoogleHint: $("#settings-google-hint"),
  settingsAwsKey: $("#settings-aws-key"),
  settingsAwsKeyHint: $("#settings-aws-key-hint"),
  settingsAwsSecret: $("#settings-aws-secret"),
  settingsAwsSecretHint: $("#settings-aws-secret-hint"),
  settingsAwsRegion: $("#settings-aws-region"),
  settingsStatus: $("#settings-status"),
  settingsSubmit: $("#settings-submit"),
  settingsBackupStatus: $("#settings-backup-status"),
  accountStatus: $("#account-status"),
  accountReveal: $("#account-reveal"),
  accountCopy: $("#account-copy"),
  accountRotate: $("#account-rotate"),
  accountLabel: $("#account-label"),
  accountClientPassword: $("#account-client-password"),
  accountAdminPassword: $("#account-admin-password"),
  accountCreate: $("#account-create"),
  accountList: $("#account-list"),
  accountCode: $("#account-code"),
  settingsBackupDownload: $("#settings-backup-download"),
  settingsBackupRestoreBtn: $("#settings-backup-restore-btn"),
  settingsBackupFile: $("#settings-backup-file"),
};

// ---------------------------------------------------------------
// API helper
// ---------------------------------------------------------------
async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";

  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    logout();
    throw new Error("Session expired. Please log in again.");
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      detail = data.detail || detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

// ---------------------------------------------------------------
// Auth
// ---------------------------------------------------------------
els.loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  els.loginError.classList.add("hidden");
  try {
    const body = { password: els.loginPassword.value };
    const loginName = els.loginAccount.value.trim();
    if (loginName) body.login = loginName;
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.token = data.token;
    localStorage.setItem("bc_token", data.token);
    showApp();
  } catch (err) {
    els.loginError.textContent = err.message;
    els.loginError.classList.remove("hidden");
  }
});

// Client self-registration (Login + mandatory password).
els.registerBack.addEventListener("click", () => {
  els.registerForm.classList.add("hidden");
  els.loginForm.classList.remove("hidden");
  els.registerError.classList.add("hidden");
});
els.loginRegisterToggle.addEventListener("click", () => {
  els.loginForm.classList.add("hidden");
  els.registerForm.classList.remove("hidden");
  els.loginError.classList.add("hidden");
});
els.registerForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  els.registerError.classList.add("hidden");
  try {
    const data = await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        login: els.registerLogin.value.trim(),
        password: els.registerPassword.value,
      }),
    });
    state.token = data.token;
    localStorage.setItem("bc_token", data.token);
    showApp();
  } catch (err) {
    els.registerError.textContent = err.message;
    els.registerError.classList.remove("hidden");
  }
});

function logout() {
  state.token = null;
  localStorage.removeItem("bc_token");
  localStorage.removeItem("bc_models");
  location.reload();
}

els.logoutBtn.addEventListener("click", () => {
  api("/api/auth/logout", { method: "POST" }).catch(() => {});
  logout();
});

// ---------------------------------------------------------------
// App boot
// ---------------------------------------------------------------
function showApp() {
  els.loginView.classList.add("hidden");
  els.appView.classList.remove("hidden");
  loadModels();
  loadConversations();
  checkHealth();
  loadBatches().catch(() => {});
  // Keep the 🧾 JSONL badge fresh even with no jobs in flight.
  if (!state.batchBadgeTimer) {
    state.batchBadgeTimer = setInterval(() => loadBatches().catch(() => {}), 60000);
  }
}

function showLogin() {
  els.appView.classList.add("hidden");
  els.loginView.classList.remove("hidden");
}

async function checkHealth() {
  try {
    const data = await api("/api/health");
    els.serverStatus.classList.add("ok");
    els.serverStatus.classList.remove("down");
    const configured = [
      data.openrouter_configured && "OpenRouter",
      data.vertex_configured && "Vertex AI",
      data.bedrock_configured && "Bedrock",
      data.tavily_configured && "Tavily",
    ].filter(Boolean);
    els.serverStatus.title = configured.length
      ? `Server OK · configured: ${configured.join(", ")}`
      : "Server OK · no provider keys set — calls will fail";
  } catch {
    els.serverStatus.classList.remove("ok");
    els.serverStatus.classList.add("down");
  }
}

if (state.token) {
  api("/api/auth/me")
    .then(() => showApp())
    .catch(() => showLogin());
} else {
  showLogin();
}

// ---------------------------------------------------------------
// Models
// ---------------------------------------------------------------
async function loadModels() {
  try {
    const data = await api("/api/chat/models");
    state.defaultModels = data.default_models || [];
    state.modelPricing = data.pricing || {};
    const batchChatDefault = () => {
      // Batch chat default: Fable 5.1 (sync id — the :batch variant of the
      // default belongs to the async ⚡ JSONL batch modal).
      const b = (data.default_batch_model || "").replace(/:batch$/, "");
      return b && state.defaultModels.includes(b) ? b : state.defaultModels[0];
    };

    // One-time cleanup: the default model list was trimmed — drop saved
    // selections that no longer exist (custom models added afterwards stay).
    if (!localStorage.getItem("bc_models_pruned_v4")) {
      const known = new Set(state.defaultModels);
      state.selectedModels = (state.selectedModels || []).filter((m) => known.has(m));
      if (!state.selectedModels.length) state.selectedModels = [batchChatDefault()];
      if (!state.liveModel || !known.has(state.liveModel) || state.liveModel.endsWith(":batch")) {
        state.liveModel = data.default_live_model || state.defaultModels.find((m) => !m.includes(":batch")) || state.defaultModels[0] || null;
      }
      localStorage.setItem("bc_models_pruned_v4", "1");
      saveModels();
      saveChatMode();
    }

    if (!state.selectedModels || !state.selectedModels.length) {
      state.selectedModels = [batchChatDefault()];
      saveModels();
    }
    // Default live model: DeepSeek v4 flash (latest).
    if (!state.liveModel) {
      state.liveModel = data.default_live_model
        || state.defaultModels.find((m) => !m.includes(":batch"))
        || state.defaultModels[0]
        || null;
      saveChatMode();
    }
    applyChatMode();
  } catch { /* ignore */ }
}

function saveModels() {
  localStorage.setItem("bc_models", JSON.stringify(state.selectedModels));
}

// ---------------------------------------------------------------
// Live / Flex / Batch chat mode (mirrors the Android app's tabs;
// Flex = live chat via the cheaper Flex processing tier)
// ---------------------------------------------------------------
function saveChatMode() {
  localStorage.setItem("bc_chat_mode", state.chatMode);
  localStorage.setItem("bc_live_model", state.liveModel || "");
}

function applyChatMode() {
  els.modeLiveBtn.classList.toggle("active", state.chatMode === "live");
  els.modeFlexBtn.classList.toggle("active", state.chatMode === "flex");
  els.modeBatchBtn.classList.toggle("active", state.chatMode === "batch");
  els.modelDropdownHint.textContent =
    state.chatMode === "live"
      ? "Answer with this model (Live):"
      : state.chatMode === "flex"
        ? "Answer with this model via the cheaper Flex tier:"
        : "Send each message to these models in parallel:";
  els.modelPickerBtn.textContent = state.chatMode === "batch" ? "Models" : "Model";
  renderModelCheckboxes();
}

els.modeLiveBtn.addEventListener("click", () => {
  state.chatMode = "live";
  saveChatMode();
  applyChatMode();
});

els.modeFlexBtn.addEventListener("click", () => {
  state.chatMode = "flex";
  saveChatMode();
  applyChatMode();
});

els.modeBatchBtn.addEventListener("click", () => {
  state.chatMode = "batch";
  saveChatMode();
  applyChatMode();
});

function renderModelCheckboxes() {
  els.modelCheckboxes.innerHTML = "";
  state.defaultModels.forEach((model) => {
    const label = document.createElement("label");
    label.className = "model-check";
    const cb = document.createElement("input");
    if (state.chatMode !== "batch") {
      // Live & Flex chat: single choice, like the phone app's chat tab.
      // ":batch" ids are async-only — they can't answer a live request.
      if (model.endsWith(":batch")) return;
      cb.type = "radio";
      cb.name = "live-model";
      cb.checked = model === state.liveModel;
      cb.addEventListener("change", () => {
        state.liveModel = model;
        saveChatMode();
        renderModelCheckboxes();
      });
    } else {
      // Batch chat: any number of models in parallel.
      cb.type = "checkbox";
      cb.checked = (state.selectedModels || []).includes(model);
      cb.addEventListener("change", () => toggleModel(model, cb.checked));
    }
    label.append(cb, model);
    const price = modePrice(model);
    if (price) {
      const span = document.createElement("span");
      span.className = "model-price";
      span.title = `USD per 1M tokens (prompt / completion) — ${state.chatMode} tier`;
      span.textContent = `· $${fmtPrice(price.prompt)} / $${fmtPrice(price.completion)} /1M`;
      label.append(span);
    }
    els.modelCheckboxes.appendChild(label);
  });
}

// Mode-aware pricing: the server returns {live, flex, batch} per model —
// Flex and Batch run at a 50% discount vs the standard tier. Falls back to
// the legacy flat shape when the server predates per-mode pricing.
function modePrice(model) {
  const p = state.modelPricing && state.modelPricing[model];
  if (!p) return null;
  if (p.live || p.flex || p.batch) return p[state.chatMode] || p.live || null;
  return p;
}

// Per-1M-token price formatting (same style as the phone app).
function fmtPrice(price) {
  if (price == null) return "—";
  const per1m = price * 1_000_000;
  if (per1m >= 100) return String(Math.round(per1m));
  if (per1m >= 0.1) return per1m.toFixed(2);
  return per1m > 0 ? `${Math.round(per1m * 100)}¢` : "0";
}

function toggleModel(model, checked) {
  if (checked) {
    if (!state.selectedModels.includes(model)) state.selectedModels.push(model);
  } else {
    state.selectedModels = state.selectedModels.filter((m) => m !== model);
  }
  saveModels();
}

els.modelPickerBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  els.modelDropdown.classList.toggle("hidden");
});

document.addEventListener("click", () => els.modelDropdown.classList.add("hidden"));
els.modelDropdown.addEventListener("click", (e) => e.stopPropagation());

els.addModelBtn.addEventListener("click", () => {
  const custom = els.customModelInput.value.trim();
  if (!custom) return;
  if (!state.defaultModels.includes(custom)) state.defaultModels.push(custom);
  if (state.chatMode !== "batch") {
    state.liveModel = custom;
    saveChatMode();
  } else {
    if (!state.selectedModels.includes(custom)) state.selectedModels.push(custom);
    saveModels();
  }
  els.customModelInput.value = "";
  renderModelCheckboxes();
});

// ---------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------
async function loadConversations() {
  const list = await api("/api/conversations");
  state.conversations = list;
  renderConversationList();
}

function renderConversationList() {
  els.conversationList.innerHTML = "";
  state.conversations.forEach((conv) => {
    const li = document.createElement("li");
    li.className = "conversation-item";
    if (conv.id === state.currentConversationId) li.classList.add("active");

    const title = document.createElement("div");
    title.className = "conversation-item-title";
    title.textContent = conv.title || "Untitled";

    const preview = document.createElement("div");
    preview.className = "conversation-item-preview";
    preview.textContent = conv.last_message || `${conv.message_count} messages`;

    const badges = document.createElement("div");
    badges.className = "conversation-badges";
    if (conv.kind === "batch") {
      const b = document.createElement("span");
      b.className = "badge batch";
      b.textContent = "batch";
      badges.appendChild(b);
    }
    if (conv.model) {
      const m = document.createElement("span");
      m.className = "badge model";
      m.textContent = conv.model;
      m.title = conv.model;
      badges.appendChild(m);
    }

    const actions = document.createElement("div");
    actions.className = "conversation-item-actions";

    const renameBtn = document.createElement("button");
    renameBtn.type = "button";
    renameBtn.className = "conversation-action";
    renameBtn.title = "Rename";
    renameBtn.textContent = "✎";
    renameBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      renameConversation(conv);
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "conversation-action";
    deleteBtn.title = "Delete";
    deleteBtn.textContent = "🗑";
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteConversation(conv);
    });

    actions.append(renameBtn, deleteBtn);

    li.append(title, preview, badges, actions);
    li.addEventListener("click", () => openConversation(conv.id));
    els.conversationList.appendChild(li);
  });
}

async function renameConversation(conv) {
  const next = prompt("Rename dialog:", conv.title || "");
  if (next === null) return;
  const title = next.trim();
  if (!title || title === conv.title) return;
  try {
    await api(`/api/conversations/${conv.id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
    conv.title = title;
    if (conv.id === state.currentConversationId) els.chatTitle.textContent = title;
    renderConversationList();
  } catch (err) {
    alert(`Rename failed: ${err.message}`);
  }
}

async function deleteConversation(conv) {
  if (!confirm(`Delete "${conv.title || "Untitled"}"? This cannot be undone.`)) return;
  try {
    await api(`/api/conversations/${conv.id}`, { method: "DELETE" });
    state.conversations = state.conversations.filter((c) => c.id !== conv.id);
    if (conv.id === state.currentConversationId) {
      state.currentConversationId = null;
      els.chatTitle.textContent = "Select or start a chat";
      els.messages.innerHTML = "";
    }
    renderConversationList();
  } catch (err) {
    alert(`Delete failed: ${err.message}`);
  }
}

async function openConversation(id) {
  state.currentConversationId = id;
  const conv = await api(`/api/conversations/${id}`);
  els.chatTitle.textContent = conv.title;
  els.cacheBtn.classList.toggle("active", !!conv.keepalive);
  els.cacheBtn.title = conv.keepalive
    ? "🔥 Cache keep-alive is ON for this dialog (pings every 45 min). Click to stop."
    : "🔥 Cache keep-alive is OFF. Click to warm this dialog's prompt cache every 45 min.";
  renderMessages(conv.messages);
  renderConversationList();
}

els.newChatBtn.addEventListener("click", async () => {
  const conv = await api("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title: "New chat" }),
  });
  state.conversations.unshift({
    id: conv.id,
    title: conv.title,
    message_count: 0,
    last_message: null,
  });
  renderConversationList();
  els.chatTitle.textContent = conv.title;
  els.messages.innerHTML = "";
  state.currentConversationId = conv.id;
  els.cacheBtn.classList.remove("active"); // new dialog: warming starts OFF
  els.chatInput.focus();
});

// ---------------------------------------------------------------
// Messages rendering
// ---------------------------------------------------------------
function renderMessages(messages) {
  els.messages.innerHTML = "";
  messages.forEach((msg) => appendMessage(msg));
  scrollToBottom();
}

function appendMessage(msg) {
  const div = document.createElement("div");
  div.className = `message ${msg.role}`;
  div.dataset.messageId = msg.id;

  if (msg.webSearch) {
    const webTag = document.createElement("span");
    webTag.className = "message-websearch";
    webTag.title = "Tavily web search results were injected into the prompt for this message";
    webTag.textContent = "🌐 Web search";
    div.appendChild(webTag);
  }

  if (msg.model) {
    const modelTag = document.createElement("span");
    modelTag.className = "message-model";
    modelTag.textContent = msg.model;
    div.appendChild(modelTag);
  }

  // Message date, DD.MM.YY HH.MM in the viewer's timezone. Server stamps are
  // naive UTC, so append "Z" before parsing.
  if (msg.created_at) {
    const d = new Date(msg.created_at.endsWith("Z") ? msg.created_at : msg.created_at + "Z");
    if (!isNaN(d)) {
      const p = (n) => String(n).padStart(2, "0");
      const dateTag = document.createElement("span");
      dateTag.className = "message-date";
      dateTag.textContent =
        `${p(d.getDate())}.${p(d.getMonth() + 1)}.${String(d.getFullYear()).slice(2)} ` +
        `${p(d.getHours())}.${p(d.getMinutes())}`;
      div.appendChild(dateTag);
    }
  }

  const text = document.createElement("div");
  text.className = "message-text";
  renderRichText(text, msg.content || "");
  div.appendChild(text);

  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "message-copy";
  copyBtn.title = "Copy raw text";
  copyBtn.textContent = "⧉ Copy";
  copyBtn.addEventListener("click", () => copyToClipboard(msg.content || "", copyBtn));
  div.appendChild(copyBtn);

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "message-delete";
  deleteBtn.title = "Delete this question/answer (archived on the server, removed on all synced devices)";
  deleteBtn.textContent = "✕ Delete";
  deleteBtn.addEventListener("click", () => deleteMessage(msg, div));
  div.appendChild(deleteBtn);

  els.messages.appendChild(div);
  scrollToBottom();
}

/** Delete one question/answer inside the open dialogue. The server archives
 * the text (soft delete + tombstone) and every synced device — including the
 * phone — drops it on its next sync. */
async function deleteMessage(msg, node) {
  if (!msg.id || !state.currentConversationId) {
    alert("This message has no id yet — reopen the conversation and try again.");
    return;
  }
  const preview = (msg.content || "").slice(0, 60).replace(/\s+/g, " ");
  if (!confirm(`Delete this ${msg.role === "user" ? "question" : "answer"}?\n\n"${preview}${(msg.content || "").length > 60 ? "…" : ""}"`)) return;
  try {
    await api(`/api/conversations/${state.currentConversationId}/messages/${msg.id}`, {
      method: "DELETE",
    });
    node.remove();
    const conv = state.conversations.find((c) => c.id === state.currentConversationId);
    if (conv && typeof conv.message_count === "number") {
      conv.message_count = Math.max(0, conv.message_count - 1);
      renderConversationList();
    }
  } catch (err) {
    alert(`Delete failed: ${err.message}`);
  }
}

/**
 * Render markdown + LaTeX safely — including BARE math with no delimiters.
 * Pipeline: (1) code fences/spans are stashed so nothing inside them is
 * touched, (2) explicit math ($…$, $$…$$, \(…\), \[…\]) is stashed,
 * (3) bare LaTeX / ASCII powers (X**2, \pi, x_i^2) are auto-wrapped in $…$
 * (port of the phone app's autoDelimitRawLatex), (4) markdown runs, (5) all
 * stashed fragments are restored (math via KaTeX) after sanitizing.
 */
function renderRichText(container, raw) {
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    container.textContent = raw; // libraries failed to load (offline CDN) — plain text fallback
    return;
  }

  const escapeHtml = (s) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // 1. Code first: fences and inline spans must not be touched by math
  //    wrapping or markdown emphasis. Stored as ready HTML.
  const codeStore = [];
  let text = raw
    .replace(/```[\s\S]*?```/g, (m) =>
      `@@BCSTASH${codeStore.push(`<pre><code>${escapeHtml(m.slice(3, -3))}</code></pre>`) - 1}@@`)
    .replace(/`[^`\n]+`/g, (m) =>
      `@@BCSTASH${codeStore.push(`<code>${escapeHtml(m.slice(1, -1))}</code>`) - 1}@@`);

  // 2. Bare LaTeX / ASCII powers WITHOUT any delimiters → wrap in $…$ / $$…$$
  //    FIRST (it protects already-delimited math itself), so that everything
  //    ends up in explicit delimiters…
  text = autoDelimitRawLatex(text);

  // 3. …which are stashed here. The single-$ rule mirrors the phone app: the
  //    char right inside each dollar must be a non-space, so "$5 and $10"
  //    stays money instead of becoming math.
  const mathStore = [];
  const stashMath = (expr, displayMode) => {
    const idx = mathStore.push({ expr, displayMode }) - 1;
    return `@@MATHPLACEHOLDER${idx}@@`;
  };
  text = text
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, expr) => stashMath(expr, true))
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, expr) => stashMath(expr, true))
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, expr) => stashMath(expr, false))
    .replace(/(^|[^\\$])\$([^\s$](?:[^$\n]*[^\s$])?)\$/g, (_, before, expr) => `${before}${stashMath(expr, false)}`);

  let html = DOMPurify.sanitize(marked.parse(text, { breaks: true }));

  html = html.replace(/@@MATHPLACEHOLDER(\d+)@@/g, (_, i) => {
    const { expr, displayMode } = mathStore[Number(i)];
    if (typeof katex === "undefined") return `$${expr}$`; // KaTeX failed to load — keep delimiters visible
    try {
      return katex.renderToString(expr, { displayMode, throwOnError: false });
    } catch {
      return `$${expr}$`;
    }
  });
  html = html.replace(/@@BCSTASH(\d+)@@/g, (_, i) => codeStore[Number(i)]);

  container.innerHTML = html;
}

// ---------------------------------------------------------------
// Bare-LaTeX auto-delimiting — JS port of the phone app's
// autoDelimitRawLatex. Wraps bare LaTeX commands/symbols and
// sub/superscripts in $…$ ($$…$$ for \begin…\end blocks), converts
// ASCII powers (X**2 → X^{2}), and leaves already-delimited math,
// money amounts, identifiers and code spans untouched.
// ---------------------------------------------------------------
const BARE_SYMBOLS = new Set([
  "frac", "dfrac", "tfrac", "cfrac", "sqrt",
  "sum", "int", "prod", "lim", "inf", "sup", "max", "min",
  "infty", "partial", "nabla", "ell", "hbar", "Re", "Im",
  "cdot", "times", "pm", "mp", "div",
  "leq", "geq", "neq", "approx", "equiv", "sim", "propto",
  "subset", "subseteq", "supset", "supseteq", "cup", "cap",
  "forall", "exists", "nexists", "emptyset", "varnothing",
  "rightarrow", "leftarrow", "Rightarrow", "Leftarrow",
  "Leftrightarrow", "to", "mapsto", "implies", "iff",
  "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon",
  "zeta", "eta", "theta", "vartheta", "iota", "kappa", "lambda",
  "mu", "nu", "xi", "rho", "varrho", "sigma", "tau", "upsilon",
  "phi", "varphi", "chi", "psi", "omega",
  "pi", "varpi", "varsigma", "imath", "jmath",
  "oplus", "ominus", "otimes", "oslash", "odot",
  "vee", "wedge", "neg", "top", "bot", "star", "ast",
  "lceil", "rceil", "lfloor", "rfloor", "langle", "rangle",
  "perp", "parallel", "mid",
  "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "coth",
  "deg", "bmod", "pmod",
  "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma",
  "Upsilon", "Phi", "Psi", "Omega",
  "sin", "cos", "tan", "cot", "sec", "csc",
  "log", "ln", "exp", "det", "gcd", "arg", "dim", "ker", "hom",
  "left", "right",
  "text", "mathbb", "mathrm", "mathbf", "mathit", "mathcal",
  "mathfrak", "mathsf", "mathtt", "operatorname",
  "displaystyle", "textstyle", "scriptstyle",
  "hat", "bar", "vec", "dot", "ddot", "tilde",
  "overline", "underline", "widehat", "widetilde",
  "binom", "cdots", "ldots", "vdots", "ddots", "dots", "dotsc",
  "quad", "qquad", "angle", "degree", "prime", "circ",
]);

const WEB_MATH_SEGMENT_PATTERN =
  /\$\$[\s\S]{1,4000}?\$\$|\\\[[\s\S]{1,4000}?\\\]|\\\([\s\S]{1,400}?\\\)|\$[^\s$](?:[^$]{0,398}[^\s$])?\$/g;
const WEB_MACRO_PATTERN =
  /\\[a-zA-Z]+\*?(?:\{(?:[^{}]|\{[^{}]*\})*\})*(?:[_^](?:\{(?:[^{}]|\{[^{}]*\})*\}|\\[a-zA-Z]+|[^\s{}]))*/;
const WEB_BARE_EXPONENT_PATTERN =
  /(?:[A-Za-z0-9\]]+|\([^()]*\))(?:[_^](?:\{[^{}]+\}|\\[a-zA-Z]+|-?[A-Za-z0-9]+)){1,2}/;
const WEB_ENVIRONMENT_PATTERN = /\\begin\{[^{}]*\}[\s\S]*?\\end\{[^{}]*\}/g;
const WEB_ASCII_POWER_PATTERN =
  /(^|[^A-Za-z0-9_*\\])((?:[A-Za-z0-9\]]+|\([^()\n]+\))\*\*(?:[A-Za-z0-9]+|\([^()\n]+\)))/g;
const WEB_FRAGMENT_PATTERN = new RegExp(
  `${WEB_MACRO_PATTERN.source}|${WEB_BARE_EXPONENT_PATTERN.source}`, "g");

function webCommandName(source) {
  const match = /^\\([a-zA-Z]+)/.exec(source);
  return match ? match[1] : "";
}

function webIsConnector(gap) {
  return /^[\s+\-*/=<>()[\]{},.:;^_|]*$/.test(gap);
}

function webFindMathAtoms(text) {
  const atoms = [];
  for (const match of text.matchAll(WEB_ENVIRONMENT_PATTERN)) {
    atoms.push({ start: match.index, end: match.index + match[0].length, display: true });
  }
  // ASCII powers (X**2 → X^{2}) as pre-converted atoms.
  for (const match of text.matchAll(WEB_ASCII_POWER_PATTERN)) {
    const start = match.index + match[1].length;
    const rawMatch = match[2];
    atoms.push({
      start,
      end: start + rawMatch.length,
      display: false,
      value: rawMatch.replace(/\*\*((?:[A-Za-z0-9]+|\([^()]+\)))/g, "^{$1}"),
    });
  }
  for (const match of text.matchAll(WEB_FRAGMENT_PATTERN)) {
    if (atoms.some((atom) => match.index >= atom.start && match.index < atom.end)) continue;
    const value = match[0];
    if (value[0] === "\\") {
      const bare = !/[{}_^]/.test(value);
      if (bare && !BARE_SYMBOLS.has(webCommandName(value))) continue;
    } else {
      const prev = text[match.index - 1];
      if (prev && /[A-Za-z0-9_]/.test(prev)) continue;
      if (value.includes("_")) {
        const base = /^[A-Za-z0-9\]]+/.exec(value)?.[0] ?? "";
        const script = /_(?:\{[^{}]+\}|\\[a-zA-Z]+|-?[A-Za-z0-9]+)/.exec(value)?.[0] ?? "";
        if (base.length > 1 && /[A-Za-z]/.test(base) && /^[A-Za-z]/.test(script.slice(1))) continue;
      }
    }
    atoms.push({ start: match.index, end: match.index + value.length, display: false });
  }
  return atoms.sort((a, b) => a.start - b.start);
}

function webMergeAtoms(atoms, text) {
  const runs = [];
  for (const atom of atoms) {
    const last = runs[runs.length - 1];
    if (last && !last.display && !atom.display && webIsConnector(text.slice(last.end, atom.start))) {
      const gap = text.slice(last.end, atom.start);
      if (last.value !== undefined || atom.value !== undefined) {
        last.value =
          (last.value ?? text.slice(last.start, last.end)) + gap +
          (atom.value ?? text.slice(atom.start, atom.end));
      }
      last.end = atom.end;
    } else {
      runs.push({ ...atom });
    }
  }
  return runs;
}

function autoDelimitRawLatex(text) {
  // An odd number of $$ means truncated text mid-formula — leave as-is.
  const displayDelimiters = (text.match(/\$\$/g) || []).length;
  if (displayDelimiters % 2 !== 0) return text;

  const protectedRanges = [];
  for (const match of text.matchAll(WEB_MATH_SEGMENT_PATTERN)) {
    protectedRanges.push({ start: match.index, end: match.index + match[0].length });
  }

  const atoms = webFindMathAtoms(text).filter(
    (atom) => !protectedRanges.some((r) => atom.start >= r.start && atom.start < r.end)
  );
  if (!atoms.length) return text;

  const runs = webMergeAtoms(atoms, text);
  let out = "";
  let lastIndex = 0;
  for (const run of runs) {
    out += text.slice(lastIndex, run.start);
    const content = run.value ?? text.slice(run.start, run.end);
    out += run.display ? `$$${content}$$` : `$${content}$`;
    lastIndex = run.end;
  }
  return out + text.slice(lastIndex);
}

/** Copy raw source text to the clipboard (works over plain HTTP too, unlike
 * the Clipboard API which requires a secure context). */
function copyToClipboard(text, btn) {
  const done = (ok) => {
    if (!btn) return;
    const original = btn.textContent;
    btn.textContent = ok ? "✓ Copied" : "✗ Failed";
    setTimeout(() => { btn.textContent = original; }, 1500);
  };

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => done(true), () => done(false));
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    done(document.execCommand("copy"));
  } catch {
    done(false);
  } finally {
    document.body.removeChild(textarea);
  }
}

function appendError(model, error) {
  const div = document.createElement("div");
  div.className = "message assistant";
  const modelTag = document.createElement("span");
  modelTag.className = "message-model";
  modelTag.textContent = model;
  const err = document.createElement("div");
  err.className = "message-err";
  err.textContent = `⚠ ${error}`;
  div.append(modelTag, err);
  els.messages.appendChild(div);
  scrollToBottom();
}

function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

// ---------------------------------------------------------------
// Web-search toggle — explicitly persisted, default ON.
// (State is saved explicitly so browsers can't silently restore a
// stale checkbox on soft reloads; unchecking it once keeps it off.)
// ---------------------------------------------------------------
els.webSearchToggle.checked = localStorage.getItem("bc_web_search") !== "0";
els.webSearchToggle.addEventListener("change", () => {
  localStorage.setItem("bc_web_search", els.webSearchToggle.checked ? "1" : "0");
});

// Reasoning effort (thinking budget): persisted like the other composer
// settings. Empty value = model default (nothing is sent to OpenRouter).
els.reasoningSelect.value = localStorage.getItem("bc_reasoning") || "";
els.reasoningSelect.addEventListener("change", () => {
  localStorage.setItem("bc_reasoning", els.reasoningSelect.value);
});

// 🔄 Sync now (web = the master server, so syncing means re-reading the
// master DB): refresh the dialog list and the open conversation — the same
// immediate effect as the phone's "Sync now" button.
els.syncBtn.addEventListener("click", async () => {
  els.syncBtn.classList.add("spinning");
  els.syncBtn.disabled = true;
  try {
    await loadConversations();
    if (state.currentConversationId !== null) {
      await openConversation(state.currentConversationId);
    }
  } catch (err) {
    alert(`Sync failed: ${err.message}`);
  } finally {
    els.syncBtn.classList.remove("spinning");
    els.syncBtn.disabled = false;
  }
});

// ---------------------------------------------------------------
// 🔥 Cache keep-alive toggle (per open dialog, opt-in)
// ---------------------------------------------------------------
els.cacheBtn.addEventListener("click", async () => {
  if (!state.currentConversationId) {
    alert("Open a dialog first — the 🔥 Cache button warms the open dialog's prompt cache.");
    return;
  }
  const enable = !els.cacheBtn.classList.contains("active");
  els.cacheBtn.disabled = true;
  try {
    const resp = await api(`/api/conversations/${state.currentConversationId}/keepalive`, {
      method: "POST",
      body: JSON.stringify({ enabled: enable }),
    });
    els.cacheBtn.classList.toggle("active", !!resp.keepalive);
    els.cacheBtn.title = resp.keepalive
      ? "🔥 Cache keep-alive is ON for this dialog (pings every 45 min). Click to stop."
      : "🔥 Cache keep-alive is OFF. Click to warm this dialog's prompt cache every 45 min.";
  } catch (err) {
    alert(`Cache toggle failed: ${err.message}`);
  } finally {
    els.cacheBtn.disabled = false;
  }
});

// ---------------------------------------------------------------
// Send / batch chat
// ---------------------------------------------------------------
els.chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.sending) return;
  const text = els.chatInput.value.trim();
  if (!text) return;
  const liveModel = state.liveModel
    || (state.selectedModels || [])[0]
    || state.defaultModels[0]
    || null;
  // Batch chat → all selected models in parallel; Live/Flex → one model
  // (Flex mode appends the ":flex" processing-tier suffix, the server turns
  // it into service_tier="flex" and falls back to standard if unsupported).
  const models = state.chatMode === "batch"
    ? (state.selectedModels || [])
    : [liveModel && !liveModel.endsWith(":flex") && state.chatMode === "flex"
        ? `${liveModel}:flex`
        : liveModel];
  if (!models.length || !models[0]) {
    alert(state.chatMode === "live"
      ? "Pick a model in the Model dropdown first."
      : "Select at least one model in the Models dropdown.");
    return;
  }

  state.sending = true;
  els.sendBtn.disabled = true;
  els.sendBtn.textContent = "Sending…";

  try {
    const resp = await api("/api/chat/send", {
      method: "POST",
      body: JSON.stringify({
        user_message: text,
        models,
        conversation_id: state.currentConversationId,
        web_search: els.webSearchToggle.checked,
        ...(els.reasoningSelect.value
          ? { reasoning_effort: els.reasoningSelect.value }
          : {}),
      }),
    });

    if (state.currentConversationId === null) {
      els.chatTitle.textContent = resp.conversation_title;
    }
    state.currentConversationId = resp.conversation_id;
    els.chatInput.value = "";

    appendMessage({ ...resp.user_message, webSearch: resp.web_search_used === true });
    resp.responses.forEach((r) => {
      if (r.ok) appendMessage({ id: r.message_id ?? null, role: "assistant", content: r.content, model: r.model });
      else appendError(r.model, r.error);
    });
    await loadConversations();
  } catch (err) {
    appendError(models.join(", "), err.message);
  } finally {
    state.sending = false;
    els.sendBtn.disabled = false;
    els.sendBtn.textContent = "Send";
  }
});

// Ctrl+Enter to send from the textarea
els.chatInput.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    els.chatForm.requestSubmit();
  }
});

// ---------------------------------------------------------------
// Footer menu (☰) — reveals Import / Settings / Log out
// ---------------------------------------------------------------
function closeFooterMenu() {
  els.menuPopover.classList.add("hidden");
}

els.menuBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  els.menuPopover.classList.toggle("hidden");
});

els.menuPopover.addEventListener("click", (e) => {
  // Keep the popover open while hovering/clicking inside; each button's own
  // handler runs, then the menu closes with the modal it opens.
  if (e.target.closest("button")) closeFooterMenu();
  e.stopPropagation();
});

document.addEventListener("click", closeFooterMenu);

// ---------------------------------------------------------------
// Import from the Android app
// ---------------------------------------------------------------
function openImport() {
  els.importStatus.className = "import-status";
  els.importStatus.textContent = "";
  els.importTextarea.value = "";
  els.importModal.classList.remove("hidden");
}

function closeImport() {
  els.importModal.classList.add("hidden");
}

els.importBtn.addEventListener("click", openImport);
els.importClose.addEventListener("click", closeImport);
els.importModal.addEventListener("click", (e) => {
  if (e.target === els.importModal) closeImport();
});

/**
 * Normalize a paste so the server always receives {dialogs, batches}.
 * Accepts a raw AsyncStorage dump (openrouter.dialogs.v1 / .batches.history.v1),
 * the shorthand arrays, or {dialogs, batches} directly.
 */
function normalizePhonePayload(raw) {
  if (raw === null || typeof raw !== "object") {
    throw new Error("Pasted JSON must be an object or array.");
  }

  // Whole AsyncStorage dump: {...key: value}
  if (!Array.isArray(raw)) {
    const dialogs = raw["openrouter.dialogs.v1"];
    const batches = raw["openrouter.batches.history.v1"];
    if (Array.isArray(dialogs) || Array.isArray(batches)) {
      return { dialogs: dialogs || [], batches: batches || [] };
    }
    // Already normalized {dialogs, batches}
    if (raw.dialogs || raw.batches) {
      return { dialogs: raw.dialogs || [], batches: raw.batches || [] };
    }
    throw new Error(
      "Could not find openrouter.dialogs.v1 or openrouter.batches.history.v1 in the JSON."
    );
  }

  // Bare list of dialogs
  const looksLikeDialog = (item) =>
    item && typeof item === "object" && Array.isArray(item.messages);
  if (raw.length === 0 || looksLikeDialog(raw[0])) {
    return { dialogs: raw, batches: [] };
  }
  throw new Error("Unrecognized array format. Paste a dialogs or batches export.");
}

els.importSubmit.addEventListener("click", async () => {
  const rawText = els.importTextarea.value.trim();
  if (!rawText) return;
  els.importSubmit.disabled = true;
  els.importStatus.className = "import-status";
  els.importStatus.textContent = "Importing…";

  try {
    const parsed = JSON.parse(rawText);
    const payload = normalizePhonePayload(parsed);
    const result = await api("/api/import/phone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    els.importStatus.classList.add("ok");
    els.importStatus.textContent =
      `Imported ${result.conversations_created} conversations, ` +
      `${result.messages_created} messages.`;
    await loadConversations();
  } catch (err) {
    els.importStatus.classList.add("err");
    els.importStatus.textContent = `Import failed: ${err.message}`;
  } finally {
    els.importSubmit.disabled = false;
  }
});

// ---------------------------------------------------------------
// JSONL batch (like GCP Vertex AI / AWS Bedrock, via OpenRouter)
// ---------------------------------------------------------------
function openBatch() {
  els.batchStatus.className = "import-status";
  els.batchStatus.textContent = "";
  els.batchModal.classList.remove("hidden");
  loadBatches();
  renderCachePings();
}

/** 🔥 Cache keep-alive verification feed (inside the 📊 Status modal). */
async function renderCachePings() {
  const box = els.cachePings;
  if (!box) return;
  box.textContent = "Loading…";
  try {
    const data = await api("/api/conversations/keepalive/pings");
    box.innerHTML = "";
    const enabled = data.enabled || [];
    const pings = (data.pings || []).slice().reverse(); // newest first
    if (enabled.length) {
      const line = document.createElement("div");
      line.className = "cache-ping-enabled";
      line.textContent = `Warming ${enabled.length} dialog(s): ` +
        enabled.map((e) => `#${e.conversation_id} ${e.title}`).join(", ");
      box.appendChild(line);
    }
    if (!pings.length) {
      const empty = document.createElement("div");
      empty.className = "cache-ping-empty";
      empty.textContent = enabled.length
        ? `No pings sent yet — the first one fires ≤${data.interval_minutes} min after the dialog's last answer.`
        : "No dialogs warming. Open a dialog and press 🔥 Cache to start.";
      box.appendChild(empty);
      return;
    }
    for (const p of pings) {
      const row = document.createElement("div");
      row.className = "cache-ping-row";
      const when = new Date(p.ts * 1000).toLocaleTimeString();
      const mark = document.createElement("span");
      mark.className = `cache-ping-mark ${p.ok ? "ok" : "fail"}`;
      mark.textContent = p.ok ? "✓" : "✗";
      const label = document.createElement("span");
      label.textContent = `${when} · ${p.title} · ${p.model}`;
      label.title = `${p.title} · ${p.model}`;
      row.append(mark, label);
      if (!p.ok) {
        const err = document.createElement("div");
        err.className = "cache-ping-error";
        err.textContent = p.error || "failed";
        row.appendChild(err);
      }
      box.appendChild(row);
    }
  } catch (err) {
    box.textContent = `Could not load ping log: ${err.message}`;
  }
}

function closeBatch() {
  els.batchModal.classList.add("hidden");
  if (state.batchRefreshTimer) {
    clearInterval(state.batchRefreshTimer);
    state.batchRefreshTimer = null;
  }
}

els.batchBtn.addEventListener("click", openBatch);
els.batchClose.addEventListener("click", closeBatch);
els.batchModal.addEventListener("click", (e) => {
  if (e.target === els.batchModal) closeBatch();
});

async function loadBatches() {
  try {
    state.batches = await api("/api/batches");
  } catch (err) {
    state.batches = [];
  }
  renderBatchJobs();
  const isActive = (b) =>
    b.status !== "completed" && b.status !== "failed" &&
    b.status !== "expired" && b.status !== "cancelled" && b.status !== "error";
  updateBatchBadge(state.batches.filter(isActive).length);
  const active = state.batches.some(isActive);
  if (active) {
    // Keep refreshing while jobs are in flight, and pull in new conversations.
    if (!state.batchRefreshTimer) {
      state.batchRefreshTimer = setInterval(() => {
        loadBatches();
        loadConversations().catch(() => {});
      }, 8000);
    }
  } else if (state.batchRefreshTimer) {
    clearInterval(state.batchRefreshTimer);
    state.batchRefreshTimer = null;
  }
}

/** Small counter on the 🧾 JSONL button showing in-flight batch jobs. */
function updateBatchBadge(count) {
  if (!els.batchBadge) return;
  if (count > 0) {
    els.batchBadge.textContent = String(count);
    els.batchBadge.classList.remove("hidden");
  } else {
    els.batchBadge.classList.add("hidden");
  }
}

function renderBatchJobs() {
  els.batchJobs.innerHTML = "";
  if (!state.batches.length) {
    els.batchJobs.textContent = "No batch jobs yet.";
    return;
  }
  // Terminal (dead) jobs can be cleared from the list; live ones cannot.
  const isDead = (s) =>
    s === "failed" || s === "expired" || s === "cancelled" || s === "error";
  state.batches.slice(0, 6).forEach((job) => {
    const row = document.createElement("div");
    row.className = "batch-job-row";
    const status = document.createElement("span");
    status.className = `batch-job-status ${job.status}`;
    status.textContent = job.status;
    const label = document.createElement("span");
    label.textContent = `#${job.id} · ${job.title} · ${job.completed_items}/${job.total_items}`;
    row.append(label, status);
    if (job.error) {
      // Show WHY a job failed (e.g. missing API key, provider rejection).
      const errLine = document.createElement("div");
      errLine.className = "batch-job-error";
      errLine.textContent = job.error;
      errLine.title = job.error;
      row.appendChild(errLine);
    }
    if (job.conversation_id) {
      row.addEventListener("click", () => {
        openConversation(job.conversation_id);
        closeBatch();
      });
      row.style.cursor = "pointer";
      row.title = "Open the resulting conversation";
    }
    if (isDead(job.status)) {
      const clear = document.createElement("button");
      clear.className = "batch-clear";
      clear.textContent = "✕";
      clear.title = "Clear this finished/failed job from the list";
      clear.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Clear batch job #${job.id} from the list?`)) return;
        try {
          await api(`/api/batches/${job.id}`, { method: "DELETE" });
          state.batches = state.batches.filter((b) => b.id !== job.id);
          renderBatchJobs();
          loadBatches().catch(() => {});
        } catch (err) {
          alert(`Could not clear job: ${err.message}`);
        }
      });
      row.appendChild(clear);
    }
    els.batchJobs.appendChild(row);
  });
}

els.batchSubmit.addEventListener("click", async () => {
  const jsonl = els.batchJsonl.value.trim();
  if (!jsonl) return;
  els.batchSubmit.disabled = true;
  els.batchStatus.className = "import-status";
  els.batchStatus.classList.remove("ok", "err");
  els.batchStatus.textContent = "Submitting…";

  try {
    const body = { jsonl };
    const model = els.batchModel.value.trim();
    if (model) body.model = model;
    const system = els.batchSystem.value.trim();
    if (system) body.system = system;
    const job = await api("/api/batches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    els.batchStatus.classList.add("ok");
    els.batchStatus.textContent =
      `Batch #${job.id} submitted (${job.total_items} requests, status: ${job.status}). ` +
      `Answers will appear as a new conversation.`;
    els.batchJsonl.value = "";
    loadBatches();
  } catch (err) {
    els.batchStatus.classList.add("err");
    els.batchStatus.textContent = `Submit failed: ${err.message}`;
  } finally {
    els.batchSubmit.disabled = false;
  }
});

// ---------------------------------------------------------------
// Settings (provider API keys — saved to the server DB, no restart needed)
// ---------------------------------------------------------------
function openSettings() {
  els.settingsStatus.className = "import-status";
  els.settingsStatus.textContent = "";
  els.settingsModal.classList.remove("hidden");
  loadSettings();
  loadAccount();
}

let accountPairCode = "";

async function loadAccount() {
  els.accountStatus.className = "import-status";
  els.accountStatus.textContent = "";
  els.accountCode.classList.add("hidden");
  els.accountCode.textContent = "";
  els.accountReveal.textContent = "👁 Reveal pairing code";
  try {
    const data = await api("/api/auth/account");
    accountPairCode = data.pair_code;
    els.accountStatus.classList.add("ok");
    els.accountStatus.textContent = `Account ID: ${data.account_id}`;
  } catch (err) {
    els.accountStatus.classList.add("err");
    els.accountStatus.textContent = `Account load failed: ${err.message}`;
  }
  // Account list (ids + labels only, no secrets).
  try {
    const rows = await api("/api/auth/accounts");
    if (rows.length > 0) {
      els.accountList.textContent =
        "Accounts: " +
        rows
          .map((a) => {
            const label = a.label ? ` (${a.label}${a.has_password ? " 🔑" : ""})` : a.has_password ? " (🔑)" : "";
            return `${a.account_id}${label}`;
          })
          .join(", ");
    } else {
      els.accountList.textContent = "";
    }
  } catch {
    els.accountList.textContent = "";
  }
}

els.accountCreate.addEventListener("click", async () => {
  const adminPassword = els.accountAdminPassword.value;
  const clientPassword = els.accountClientPassword.value;
  if (!adminPassword) {
    els.accountList.className = "import-status err";
    els.accountList.textContent = "Enter the master password to create a client account.";
    return;
  }
  if (clientPassword.length < 6) {
    els.accountList.className = "import-status err";
    els.accountList.textContent = "Client password is required (min 6 chars).";
    return;
  }
  els.accountCreate.disabled = true;
  els.accountList.className = "import-status";
  els.accountList.textContent = "Creating…";
  try {
    const data = await api("/api/auth/accounts", {
      method: "POST",
      body: JSON.stringify({
        admin_password: adminPassword,
        label: els.accountLabel.value.trim() || null,
        client_password: els.accountClientPassword.value || null,
      }),
    });
    els.accountAdminPassword.value = "";
    els.accountLabel.value = "";
    els.accountClientPassword.value = "";
    els.accountList.className = "import-status ok";
    els.accountList.textContent = `Created ${data.account_id} — its pairing code is shown below; copy it now.`;
    els.accountCode.textContent = data.pair_code;
    els.accountCode.classList.remove("hidden");
    await loadAccount();
  } catch (err) {
    els.accountList.className = "import-status err";
    els.accountList.textContent = `Create failed: ${err.message}`;
  } finally {
    els.accountCreate.disabled = false;
  }
});

els.accountReveal.addEventListener("click", () => {
  const hidden = els.accountCode.classList.contains("hidden");
  if (hidden && accountPairCode) {
    els.accountCode.textContent = accountPairCode;
    els.accountCode.classList.remove("hidden");
    els.accountReveal.textContent = "🙈 Hide pairing code";
  } else {
    els.accountCode.classList.add("hidden");
    els.accountCode.textContent = "";
    els.accountReveal.textContent = "👁 Reveal pairing code";
  }
});

els.accountCopy.addEventListener("click", async () => {
  if (!accountPairCode) return;
  try {
    await navigator.clipboard.writeText(accountPairCode);
    els.accountStatus.className = "import-status ok";
    els.accountStatus.textContent = "Pairing code copied — paste it in the phone app's Sync card (Password or pairing code field).";
  } catch {
    els.accountReveal.click(); // clipboard blocked — show it for manual selection
    els.accountStatus.className = "import-status err";
    els.accountStatus.textContent = "Clipboard unavailable — the code is shown below, select and copy it manually.";
  }
});

els.accountRotate.addEventListener("click", async () => {
  els.accountStatus.className = "import-status";
  els.accountStatus.textContent = "Rotating…";
  try {
    const data = await api("/api/auth/account/rotate", { method: "POST" });
    accountPairCode = data.pair_code;
    els.accountCode.classList.add("hidden");
    els.accountCode.textContent = "";
    els.accountReveal.textContent = "👁 Reveal pairing code";
    els.accountStatus.classList.add("ok");
    els.accountStatus.textContent = "New pairing key issued. Old codes no longer work; already-paired devices keep syncing.";
  } catch (err) {
    els.accountStatus.classList.add("err");
    els.accountStatus.textContent = `Rotate failed: ${err.message}`;
  }
});

function closeSettings() {
  els.settingsModal.classList.add("hidden");
}

els.settingsBtn.addEventListener("click", openSettings);
els.settingsClose.addEventListener("click", closeSettings);
els.settingsModal.addEventListener("click", (e) => {
  if (e.target === els.settingsModal) closeSettings();
});

async function loadSettings() {
  try {
    const data = await api("/api/settings");
    els.settingsOpenrouterHint.textContent = data.openrouter_api_key.configured
      ? `(saved: ${data.openrouter_api_key.hint})` : "(not set)";
    els.settingsTavilyHint.textContent = data.tavily_api_key.configured
      ? `(saved: ${data.tavily_api_key.hint})` : "(not set)";
    const cacheSeconds = data.cache_duration_seconds && data.cache_duration_seconds.value;
    if (cacheSeconds) els.settingsCacheDuration.value = String(cacheSeconds);
    const keepaliveHours = data.cache_keepalive_hours && data.cache_keepalive_hours.value;
    if (keepaliveHours !== undefined && keepaliveHours !== null) {
      els.settingsKeepalive.value = String(keepaliveHours);
    }
    els.settingsGoogleHint.textContent = data.google_service_account_json.configured
      ? `(saved: ${data.google_service_account_json.hint})` : "(not set)";
    els.settingsAwsKeyHint.textContent = data.aws_access_key_id.configured
      ? `(saved: ${data.aws_access_key_id.hint})` : "(not set)";
    els.settingsAwsSecretHint.textContent = data.aws_secret_access_key.configured
      ? `(saved: ${data.aws_secret_access_key.hint})` : "(not set)";
    els.settingsGoogleProject.value = data.google_project_id.value || "";
    els.settingsGoogleLocation.value = data.google_location.value || "";
    els.settingsAwsRegion.value = data.aws_region.value || "";
  } catch (err) {
    els.settingsStatus.classList.add("err");
    els.settingsStatus.textContent = `Failed to load: ${err.message}`;
  }
}

els.settingsSubmit.addEventListener("click", async () => {
  const body = {};
  const maybeAdd = (key, value) => {
    const trimmed = value.trim();
    if (trimmed) body[key] = trimmed;
  };
  maybeAdd("openrouter_api_key", els.settingsOpenrouterKey.value);
  maybeAdd("tavily_api_key", els.settingsTavilyKey.value);
  maybeAdd("google_project_id", els.settingsGoogleProject.value);
  maybeAdd("google_location", els.settingsGoogleLocation.value);
  maybeAdd("google_service_account_json", els.settingsGoogleJson.value);
  maybeAdd("aws_access_key_id", els.settingsAwsKey.value);
  maybeAdd("aws_secret_access_key", els.settingsAwsSecret.value);
  maybeAdd("aws_region", els.settingsAwsRegion.value);
  const cacheSeconds = parseInt(els.settingsCacheDuration.value, 10);
  if (cacheSeconds === 300 || cacheSeconds === 3600) {
    body.cache_duration_seconds = cacheSeconds;
  }
  const keepaliveHours = parseInt(els.settingsKeepalive.value, 10);
  if (!Number.isNaN(keepaliveHours) && keepaliveHours >= 0) {
    body.cache_keepalive_hours = keepaliveHours; // 0 = off — must be savable too
  }

  els.settingsSubmit.disabled = true;
  els.settingsStatus.className = "import-status";
  els.settingsStatus.textContent = "Saving…";
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    els.settingsOpenrouterKey.value = "";
    els.settingsTavilyKey.value = "";
    els.settingsGoogleJson.value = "";
    els.settingsAwsKey.value = "";
    els.settingsAwsSecret.value = "";
    els.settingsStatus.classList.add("ok");
    els.settingsStatus.textContent = "Saved. Applied immediately, no restart needed.";
    await loadSettings();
    await checkHealth();
    await loadModels();
  } catch (err) {
    els.settingsStatus.classList.add("err");
    els.settingsStatus.textContent = `Save failed: ${err.message}`;
  } finally {
    els.settingsSubmit.disabled = false;
  }
});

// ---------------------------------------------------------------
// Settings backup (single-file server migration)
// ---------------------------------------------------------------
els.settingsBackupDownload.addEventListener("click", async () => {
  els.settingsBackupStatus.className = "import-status";
  els.settingsBackupStatus.textContent = "Preparing backup…";
  try {
    const data = await api("/api/settings/backup");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `batch-chat-server-backup-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    els.settingsBackupStatus.classList.add("ok");
    els.settingsBackupStatus.textContent = "Backup downloaded. Keep this file private — it contains raw API keys.";
  } catch (err) {
    els.settingsBackupStatus.classList.add("err");
    els.settingsBackupStatus.textContent = `Backup failed: ${err.message}`;
  }
});

els.settingsBackupRestoreBtn.addEventListener("click", () => els.settingsBackupFile.click());

els.settingsBackupFile.addEventListener("change", async () => {
  const file = els.settingsBackupFile.files && els.settingsBackupFile.files[0];
  els.settingsBackupFile.value = "";
  if (!file) return;
  els.settingsBackupStatus.className = "import-status";
  els.settingsBackupStatus.textContent = "Restoring…";
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    await api("/api/settings/backup", {
      method: "POST",
      body: JSON.stringify(data),
    });
    els.settingsBackupStatus.classList.add("ok");
    els.settingsBackupStatus.textContent = "Backup restored. Applied immediately, no restart needed.";
    await loadSettings();
    await checkHealth();
    await loadModels();
  } catch (err) {
    els.settingsBackupStatus.classList.add("err");
    els.settingsBackupStatus.textContent = `Restore failed: ${err.message}`;
  }
});