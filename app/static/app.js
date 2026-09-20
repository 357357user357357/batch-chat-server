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
  // Model-picker search filter ("" = show everything)
  modelSearchQuery: "",
  // "new" | "output" = show the provider's FULL catalog sorted that way;
  // "" = the picked models only (a search still reaches the full catalog).
  modelSort: ["new", "output"].includes(localStorage.getItem("bc_model_sort"))
    ? localStorage.getItem("bc_model_sort")
    : "",
  modelCatalog: [],
  modelCatalogLoaded: false,
  // Retry picker: open modal context ({msg, btn, node, models:Set}) + filter
  // query + persisted sort ("picked" | "new" | "output").
  retryCtx: null,
  retryModelSearchQuery: "",
  retryModelSort: ["new", "output"].includes(localStorage.getItem("bc_retry_sort"))
    ? localStorage.getItem("bc_retry_sort")
    : "picked",
  // RikkaHub-style message branching: per-dialog selected variant of each
  // answer group ("12:345" -> variant index). Persisted in localStorage.
  branchSel: JSON.parse(localStorage.getItem("bc_branches") || "{}"),
  // The open dialog's flat message list — the source for branch grouping.
  currentMessages: [],
};

const els = {
  loginView: $("#login-view"),
  appView: $("#app-view"),
  loginForm: $("#login-form"),
  loginPassword: $("#login-password"),
  loginAccount: $("#login-account"),
  loginGoogle: $("#login-google"),
  loginRegisterToggle: $("#login-register-toggle"),
  registerForm: $("#register-form"),
  registerEmail: $("#register-email"),
  registerPassword: $("#register-password"),
  registerGoogle: $("#register-google"),
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
  modelSearchInput: $("#model-search-input"),
  modelSortSelect: $("#model-sort-select"),
  modeLiveBtn: $("#mode-live-btn"),
  modeFlexBtn: $("#mode-flex-btn"),
  modeBatchBtn: $("#mode-batch-btn"),
  chatModeBtn: $("#chat-mode-btn"),
  chatModePopover: $("#chat-mode-popover"),
  modelDropdown: $("#model-dropdown"),
  modelCheckboxes: $("#model-checkboxes"),
  customModelInput: $("#custom-model-input"),
  addModelBtn: $("#add-model-btn"),
  retryModal: $("#retry-modal"),
  retryModalTitle: $("#retry-modal-title"),
  retryModelSearch: $("#retry-model-search"),
  retryModelSort: $("#retry-model-sort"),
  retryModelList: $("#retry-model-list"),
  retryGo: $("#retry-go"),
  retryClose: $("#retry-close"),
  retryCancel: $("#retry-cancel"),
  menuBtn: $("#menu-btn"),
  menuPopover: $("#menu-popover"),
  syncBtn: $("#sync-btn"),
  toast: $("#toast"),
  cacheBtn: $("#cache-btn"),
  reasoningSelect: $("#reasoning-select"),
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
  settingsOwnerEmail: $("#settings-owner-email"),
  ownerEmailSave: $("#owner-email-save"),
  settingsIdentity: $("#settings-identity"),
  ownerAccessBlock: $("#owner-access-block"),
  cacheSettingsBlock: $("#cache-settings-block"),
  infraSettingsBlock: $("#infra-settings-block"),
  backupBlock: $("#backup-block"),
  settingsBackupStatus: $("#settings-backup-status"),
  accountStatus: $("#account-status"),
  accountReveal: $("#account-reveal"),
  accountCopy: $("#account-copy"),
  accountRotate: $("#account-rotate"),
  accountDanger: $("#account-danger"),
  accountDelete: $("#account-delete"),
  accountDangerNote: $("#account-danger-note"),
  accountCode: $("#account-code"),
  settingsPhoneExport: $("#settings-phone-export"),
  settingsPhoneStatus: $("#settings-phone-status"),
  settingsImportTextarea: $("#settings-import-textarea"),
  settingsImportStatus: $("#settings-import-status"),
  settingsImportSubmit: $("#settings-import-submit"),
  settingsBackupDownload: $("#settings-backup-download"),
  settingsBackupRestoreBtn: $("#settings-backup-restore-btn"),
  settingsBackupFile: $("#settings-backup-file"),
  keyStatusOpenrouter: $("#key-status-openrouter"),
  keyDeleteOpenrouter: $("#key-delete-openrouter"),
  keyStatusTavily: $("#key-status-tavily"),
  keyDeleteTavily: $("#key-delete-tavily"),
  keyStatusCustom: $("#key-status-custom"),
  keyDeleteCustom: $("#key-delete-custom"),
  settingsCustomKey: $("#settings-custom-key"),
  settingsCustomHint: $("#settings-custom-hint"),
  settingsCustomUrl: $("#settings-custom-url"),
  settingsCustomModel: $("#settings-custom-model"),
  usageOpenLink: $("#usage-open-link"),
  usageBtn: $("#usage-btn"),
  usageModal: $("#usage-modal"),
  usageClose: $("#usage-close"),
  usageSummary: $("#usage-summary"),
  usageChart: $("#usage-chart"),
  usageStatus: $("#usage-status"),
  usageRange: $("#usage-range"),
  usageRefresh: $("#usage-refresh"),
  metaModal: $("#meta-modal"),
  metaClose: $("#meta-close"),
  metaBody: $("#meta-body"),
  metaLogs: $("#meta-logs"),
  metaCopy: $("#meta-copy"),
};

// ---------------------------------------------------------------
// API helper
// ---------------------------------------------------------------
async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";

  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401 && !options.noLogout) {
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

// Same conventions as api(), but for the chat endpoints that stream their
// JSON answer as Server-Sent Events (": ping" keep-alive comments while the
// model queues, then one "data: {...}" event — "event: error" on failure).
// Reading the stream keeps the connection alive through NATs/middleboxes that
// kill silent connections during long ":flex" waits (raw "Failed to fetch").
async function apiStream(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";

  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401 && !options.noLogout) {
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
  if (!resp.body || !resp.body.getReader) return resp.json(); // very old browser

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let result = null;
  let streamError = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let isError = false;
      let dataText = null;
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue; // keep-alive ping
        if (line.startsWith("event:")) isError = line.slice(6).trim() === "error";
        else if (line.startsWith("data:")) dataText = line.slice(5).trim();
      }
      if (dataText == null) continue;
      try {
        const parsed = JSON.parse(dataText);
        if (isError) streamError = new Error(parsed.error || "Request failed");
        else result = parsed;
      } catch { /* ignore malformed events */ }
    }
  }
  if (!result && streamError) throw streamError;
  return result;
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
      noLogout: true,
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

// Client self-registration (e-mail + mandatory password, confirmation mail).
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
const startGoogleOAuth = () => { location.href = "/api/auth/oauth/google/start?client=web"; };
els.loginGoogle.addEventListener("click", startGoogleOAuth);
els.registerGoogle.addEventListener("click", startGoogleOAuth);
els.registerForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  els.registerError.classList.add("hidden");
  try {
    const data = await api("/api/auth/register", {
      method: "POST",
      noLogout: true,
      body: JSON.stringify({
        email: els.registerEmail.value.trim(),
        password: els.registerPassword.value,
      }),
    });
    els.registerForm.classList.add("hidden");
    els.loginForm.classList.remove("hidden");
    els.loginAccount.value = els.registerEmail.value.trim();
    els.loginError.textContent = data.detail || "Confirmation e-mail sent — check your inbox";
    els.loginError.classList.remove("hidden");
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
// OAuth (Google) returns to "/#token=…" — adopt the session token;
  // confirm-email returns to "/#confirmed=1" — show a hint.
(function consumeUrlFragment() {
  const hash = location.hash.replace(/^#/, "");
  if (!hash) return;
  const params = new URLSearchParams(hash);
  const oauthToken = params.get("token");
  if (oauthToken) {
    localStorage.setItem("bc_token", oauthToken);
    location.hash = "";
    location.reload();
    return;
  }
  if (params.get("confirmed")) {
    setTimeout(() => alert("E-mail confirmed — you can log in now with your e-mail and password."), 50);
  }
})();

function showApp() {
  els.loginView.classList.add("hidden");
  els.appView.classList.remove("hidden");
  updateHeaderControls(); // per-dialog controls start hidden (no dialog open)
  loadModels();
  loadConversations();
  checkHealth();
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
const CHAT_MODE_LABELS = { live: "💬 Live", flex: "🧊 Flex", batch: "⚡ Batch" };

function saveChatMode() {
  localStorage.setItem("bc_chat_mode", state.chatMode);
  localStorage.setItem("bc_live_model", state.liveModel || "");
}

function applyChatMode() {
  // One combined button shows the current mode; the popover lists all three.
  els.chatModeBtn.textContent = `${CHAT_MODE_LABELS[state.chatMode] || "💬 Live"} ▾`;
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

// The single mode button toggles the mode picker; clicking elsewhere closes it.
els.chatModeBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  els.chatModePopover.classList.toggle("hidden");
});
document.addEventListener("click", () => els.chatModePopover.classList.add("hidden"));
els.chatModePopover.addEventListener("click", (e) => e.stopPropagation());

function pickChatMode(mode) {
  state.chatMode = mode;
  saveChatMode();
  applyChatMode();
  els.chatModePopover.classList.add("hidden");
}

els.modeLiveBtn.addEventListener("click", () => pickChatMode("live"));

els.modeFlexBtn.addEventListener("click", () => pickChatMode("flex"));

els.modeBatchBtn.addEventListener("click", () => pickChatMode("batch"));

// Row cap for the full-catalog list (the provider serves 300+ models).
const MODEL_ROW_CAP = 300;

function renderModelCheckboxes() {
  els.modelCheckboxes.innerHTML = "";
  const query = (state.modelSearchQuery || "").trim().toLowerCase();
  let shown = 0;
  const listedIds = new Set();

  const addRow = (id, name, price) => {
    // ":batch" ids are async-only — they can't answer a live request.
    if (state.chatMode !== "batch" && id.endsWith(":batch")) return;
    listedIds.add(id);
    const label = document.createElement("label");
    label.className = "model-check";
    const cb = document.createElement("input");
    if (state.chatMode !== "batch") {
      // Live & Flex chat: single choice, like the phone app's chat tab.
      cb.type = "radio";
      cb.name = "live-model";
      cb.checked = id === state.liveModel;
      cb.addEventListener("change", () => {
        state.liveModel = id;
        saveChatMode();
        renderModelCheckboxes();
      });
    } else {
      // Batch chat: any number of models in parallel.
      cb.type = "checkbox";
      cb.checked = (state.selectedModels || []).includes(id);
      cb.addEventListener("change", () => toggleModel(id, cb.checked));
    }
    const text = document.createElement("span");
    text.textContent = name;
    text.title = id;
    label.append(cb, text);
    if (price) {
      const span = document.createElement("span");
      span.className = "model-price";
      span.title = `USD per 1M tokens (prompt / completion) — ${state.chatMode} tier`;
      span.textContent = `· $${fmtPrice(price.prompt)} / $${fmtPrice(price.completion)} /1M`;
      label.append(span);
    }
    shown++;
    els.modelCheckboxes.appendChild(label);
  };

  const matches = (m) =>
    !query || `${m.id} ${m.name || ""}`.toLowerCase().includes(query);

  if (state.modelSort === "new" || state.modelSort === "output") {
    // Full provider catalog sorted by release date (novelty) or by the
    // outgoing (completion) token price; the search box filters it too.
    const rows = state.modelCatalog
      .filter(matches)
      .sort(
        state.modelSort === "new"
          ? (a, b) => (b.created || 0) - (a.created || 0) || a.id.localeCompare(b.id)
          : (a, b) => a.completion - b.completion || a.prompt - b.prompt || a.id.localeCompare(b.id),
      );
    rows.slice(0, MODEL_ROW_CAP).forEach((m) => addRow(m.id, m.name || m.id, catalogPrice(m)));
    if (rows.length > MODEL_ROW_CAP) {
      const note = document.createElement("div");
      note.className = "model-search-empty";
      note.textContent = `Showing the first ${MODEL_ROW_CAP} of ${rows.length} — type to narrow it down.`;
      els.modelCheckboxes.appendChild(note);
    }
  } else {
    // Picked models; typing letters also greps the provider's full catalog
    // (phone-style interactive search over everything the provider has).
    state.defaultModels.forEach((model) => {
      if (query && !model.toLowerCase().includes(query)) return;
      addRow(model, model, modePrice(model));
    });
    if (query && state.modelCatalog.length) {
      state.modelCatalog
        .filter((m) => !listedIds.has(m.id) && matches(m))
        .slice(0, 60)
        .forEach((m) => {
          const price = catalogPrice(m);
          addRow(m.id, m.name || m.id, price);
        });
    }
  }

  if (!shown) {
    const none = document.createElement("div");
    none.className = "model-search-empty";
    none.textContent = query
      ? `No models match "${state.modelSearchQuery.trim()}" — add it below as a custom model.`
      : "No models available.";
    els.modelCheckboxes.appendChild(none);
  }
}

// Catalog row price per chat mode: the picker's plain ids run at the
// standard tier (the ⚡ parallel chat), Flex runs at the 50% discount.
// Provider-prefixed ids (custom:/vertex:/bedrock:) have no Flex tier of
// their own — the server runs them at the standard price.
function catalogPrice(m) {
  const prefixed = /^(custom|vertex|bedrock):/.test(m.id);
  const factor = state.chatMode === "flex" && !prefixed ? 0.5 : 1;
  return { prompt: m.prompt * factor, completion: m.completion * factor };
}

// Full provider catalog for the picker's search + sorted "all models" list.
async function loadModelCatalog() {
  if (state.modelCatalogLoaded) return;
  try {
    const data = await api("/api/chat/models/all");
    state.modelCatalog = data.models || [];
    state.modelCatalogLoaded = true;
  } catch { /* catalog stays empty — the picked models still work */ }
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
  const opening = els.modelDropdown.classList.contains("hidden");
  els.modelDropdown.classList.toggle("hidden");
  if (opening) {
    // Fresh start every time the picker opens: clear any previous search.
    state.modelSearchQuery = "";
    els.modelSearchInput.value = "";
    els.modelSortSelect.value = state.modelSort;
    renderModelCheckboxes();
    els.modelSearchInput.focus();
    // The provider's full catalog arrives async (cached after the first open).
    loadModelCatalog().then(renderModelCheckboxes);
  }
});

// Model search: re-render the list on every keystroke.
els.modelSearchInput.addEventListener("input", () => {
  state.modelSearchQuery = els.modelSearchInput.value;
  renderModelCheckboxes();
});

// All-models sorting: novelty (newest release first) or outgoing token price.
els.modelSortSelect.addEventListener("change", () => {
  state.modelSort = els.modelSortSelect.value;
  localStorage.setItem("bc_model_sort", state.modelSort);
  loadModelCatalog().then(renderModelCheckboxes);
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
  state.modelSearchQuery = "";
  els.modelSearchInput.value = "";
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
      updateHeaderControls();
      els.chatTitle.textContent = "Select or start a chat";
      els.messages.innerHTML = "";
    }
    renderConversationList();
  } catch (err) {
    alert(`Delete failed: ${err.message}`);
  }
}

/** 🧠 Reasoning + 🔥 Cache are per-dialog controls: only meaningful with an
 * open dialog, so they stay hidden on the main (no dialog) screen. */
function updateHeaderControls() {
  const inDialog = state.currentConversationId !== null;
  els.reasoningSelect.classList.toggle("hidden", !inDialog);
  els.cacheBtn.classList.toggle("hidden", !inDialog);
}

async function openConversation(id) {
  state.currentConversationId = id;
  updateHeaderControls();
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
  state.currentMessages = []; // new dialog: start with an empty flat list
  updateHeaderControls(); // new dialog: reasoning + cache controls appear
  els.cacheBtn.classList.remove("active"); // new dialog: warming starts OFF
  els.chatInput.focus();
});

// ---------------------------------------------------------------
// Messages rendering
// ---------------------------------------------------------------
/** Group a flat message list for RikkaHub-style branching: consecutive
 * assistant answers form one answer group (variants of the same question);
 * everything else renders as its own node. Pure — unit-testable. */
function groupAnswerNodes(messages) {
  const nodes = [];
  let i = 0;
  while (i < messages.length) {
    if (messages[i].role !== "assistant") {
      nodes.push({ type: "single", msg: messages[i] });
      i += 1;
      continue;
    }
    const start = i;
    while (i < messages.length && messages[i].role === "assistant") i += 1;
    const variants = messages.slice(start, i);
    nodes.push(
      variants.length === 1
        ? { type: "single", msg: variants[0] }
        : { type: "branch", variants },
    );
  }
  return nodes;
}

function renderMessages(messages) {
  state.currentMessages = messages;
  els.messages.innerHTML = "";
  // RikkaHub-style branching: consecutive assistant answers that follow one
  // question are VARIANTS of the same answer group — only the selected one
  // shows, with ‹ N/M › arrows to flip between them (all variants stay in
  // the DB and sync to the phone exactly as before).
  for (const node of groupAnswerNodes(messages)) {
    if (node.type === "single") appendMessage(node.msg);
    else renderBranchNode(node.variants);
  }
  scrollToBottom();
}

/** Key identifying an answer group: the question's id when present, else the
 * first variant's id (question deleted on another device). */
function branchNodeKey(convId, variants) {
  return `${convId}:${variants[0].id ?? variants[0].content?.slice(0, 40) ?? ""}`;
}

function branchSelectedIndex(key, count) {
  const saved = state.branchSel[key];
  // Default to the NEWEST variant (RikkaHub shows the latest answer first).
  const idx = Number.isInteger(saved) && saved >= 0 && saved < count ? saved : count - 1;
  return idx;
}

function saveBranchSelection() {
  // Keep the map bounded: newest 200 entries are enough context.
  const keys = Object.keys(state.branchSel);
  if (keys.length > 200) {
    for (const k of keys.slice(0, keys.length - 200)) delete state.branchSel[k];
  }
  localStorage.setItem("bc_branches", JSON.stringify(state.branchSel));
}

/** One answer group with several variants: wrapper div with the active
 * bubble inside plus the RikkaHub ‹ N/M › selector under it. */
function renderBranchNode(variants) {
  const convId = state.currentConversationId;
  const key = branchNodeKey(convId, variants);
  const idx = branchSelectedIndex(key, variants.length);

  const wrap = document.createElement("div");
  wrap.className = "branch-node";
  wrap.dataset.branchKey = key;

  const bubble = appendMessage(variants[idx], { parent: wrap });
  bubble.classList.add("branch-bubble");

  const selector = document.createElement("div");
  selector.className = "branch-selector";
  const prev = document.createElement("button");
  prev.type = "button";
  prev.className = "branch-btn";
  prev.title = "Previous variant";
  prev.textContent = "‹";
  prev.disabled = idx === 0;
  const counter = document.createElement("span");
  counter.className = "branch-counter";
  counter.textContent = `${idx + 1}/${variants.length}`;
  const next = document.createElement("button");
  next.type = "button";
  next.className = "branch-btn";
  next.title = "Next variant";
  next.textContent = "›";
  next.disabled = idx === variants.length - 1;

  const flip = (delta) => {
    const cur = branchSelectedIndex(key, variants.length);
    const target = Math.min(variants.length - 1, Math.max(0, cur + delta));
    if (target === cur) return;
    state.branchSel[key] = target;
    saveBranchSelection();
    // Re-render just this group in place: build the new wrapper (it lands at
    // the list end) and move it into the old wrapper's slot, keeping scroll.
    const rebuilt = renderBranchNode(variants);
    wrap.replaceWith(rebuilt);
  };
  prev.addEventListener("click", () => flip(-1));
  next.addEventListener("click", () => flip(1));

  selector.append(prev, counter, next);
  wrap.appendChild(selector);
  els.messages.appendChild(wrap);
  return wrap;
}

// ---------------------------------------------------------------
// Per-message OpenRouter metadata: reasoning effort, serving
// provider, generation id and exact usage/cost — shown as a small
// chip under assistant bubbles and a full popup on click.
// ---------------------------------------------------------------
function formatCost(cost) {
  if (typeof cost !== "number" || !Number.isFinite(cost)) return "";
  return `$${cost.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")}`;
}

function hasMessageMeta(msg) {
  return Boolean(
    msg.reasoning || msg.provider || msg.gen_id ||
    msg.total_tokens != null || msg.cost != null,
  );
}

function metaLabel(msg) {
  const parts = [];
  if (msg.reasoning) parts.push(`🧠 ${msg.reasoning}`);
  if (msg.provider) parts.push(msg.provider);
  if (msg.total_tokens != null) parts.push(`${msg.total_tokens.toLocaleString()} tok`);
  const cost = formatCost(msg.cost);
  if (cost) parts.push(cost);
  return parts.join(" · ");
}

function openMetaModal(msg) {
  const push = (label, value) => {
    if (value == null || value === "") return;
    const div = document.createElement("div");
    div.className = "meta-row";
    const k = document.createElement("span");
    k.className = "meta-key";
    k.textContent = label;
    const v = document.createElement("span");
    v.className = "meta-val";
    v.textContent = value;
    div.append(k, v);
    els.metaBody.appendChild(div);
  };

  els.metaBody.innerHTML = "";
  push("Reasoning", msg.reasoning);
  push("Provider", msg.provider);
  push("Generation id", msg.gen_id);
  if (msg.tokens_prompt != null || msg.tokens_completion != null || msg.total_tokens != null) {
    const tok = [msg.tokens_prompt ?? "—", msg.tokens_completion ?? "—", msg.total_tokens ?? "—"];
    push("Tokens (prompt / completion / total)", tok.join(" / "));
  }
  push("Cost", formatCost(msg.cost));

  if (msg.gen_id) {
    els.metaLogs.style.display = "";
    els.metaLogs.dataset.genId = msg.gen_id;
  } else {
    els.metaLogs.style.display = "none";
  }

  els.metaModal.classList.remove("hidden");
}

function closeMetaModal() {
  els.metaModal.classList.add("hidden");
}

els.metaClose.addEventListener("click", closeMetaModal);
els.metaModal.addEventListener("click", (e) => {
  if (e.target === els.metaModal) closeMetaModal();
});

els.metaLogs.addEventListener("click", () => {
  const id = els.metaLogs.dataset.genId;
  if (id) window.open(`https://openrouter.ai/activity?generation_id=${encodeURIComponent(id)}`, "_blank");
});

els.metaCopy.addEventListener("click", async () => {
  const lines = [];
  els.metaBody.querySelectorAll(".meta-row").forEach((row) => {
    const k = row.querySelector(".meta-key");
    const v = row.querySelector(".meta-val");
    if (k && v) lines.push(`${k.textContent}: ${v.textContent}`);
  });
  try {
    await navigator.clipboard.writeText(lines.join("\n"));
    els.metaCopy.textContent = "✓ Copied";
  } catch {
    els.metaCopy.textContent = "Clipboard blocked";
  }
  setTimeout(() => { els.metaCopy.textContent = "⧉ Copy"; }, 1600);
});

function appendMessage(msg, opts = {}) {
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

  if (msg.role === "assistant" && hasMessageMeta(msg)) {
    const metaBtn = document.createElement("button");
    metaBtn.type = "button";
    metaBtn.className = "message-meta";
    metaBtn.title = "Reasoning, provider, generation id and usage";
    metaBtn.textContent = `🧠 ${metaLabel(msg)}`;
    metaBtn.addEventListener("click", () => openMetaModal(msg));
    div.appendChild(metaBtn);
  }

  if (msg.role === "user") {
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "message-edit";
    editBtn.title = "✏️ Edit this question (the previous wording stays archived on the server)";
    editBtn.textContent = "✏️ Edit";
    editBtn.addEventListener("click", () => editMessage(msg, div));
    div.appendChild(editBtn);

    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "message-retry";
    retryBtn.title = "🔄 Re-ask this question with other model(s) — the fresh answers appear right under it";
    retryBtn.textContent = "🔄 Retry";
    retryBtn.addEventListener("click", () => retryMessage(msg, retryBtn, div));
    div.appendChild(retryBtn);
  }

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "message-delete";
  deleteBtn.title = "Delete this question/answer (archived on the server, removed on all synced devices)";
  deleteBtn.textContent = "✕ Delete";
  deleteBtn.addEventListener("click", () => deleteMessage(msg, div));
  div.appendChild(deleteBtn);

  if (opts.afterNode && opts.afterNode.parentNode === els.messages) {
    // 🔄 Retry: the fresh answers appear right after the retried one.
    opts.afterNode.after(div);
    div.scrollIntoView({ block: "nearest", behavior: "smooth" });
  } else if (opts.parent) {
    // Branch node wrapper: the bubble renders inside the group container.
    opts.parent.appendChild(div);
  } else {
    els.messages.appendChild(div);
    scrollToBottom();
  }
  return div;
}

/** Delete one question/answer inside the open dialogue. The server archives
 * the text (soft delete + tombstone) and every synced device — including the
 * phone — drops it on its next sync. */
async function deleteMessage(msg, node) {  if (!msg.id || !state.currentConversationId) {
    alert("This message has no id yet — reopen the conversation and try again.");
    return;
  }
  const preview = (msg.content || "").slice(0, 60).replace(/\s+/g, " ");
  if (!confirm(`Delete this ${msg.role === "user" ? "question" : "answer"}?\n\n"${preview}${(msg.content || "").length > 60 ? "…" : ""}"`)) return;
  try {
    await api(`/api/conversations/${state.currentConversationId}/messages/${msg.id}`, {
      method: "DELETE",
    });
    // Keep the flat list in sync and re-render grouped — deleting one variant
    // of a branch group must also drop it from the ‹ N/M › selector.
    const pos = state.currentMessages.findIndex((m) => m.id === msg.id);
    if (pos !== -1) {
      state.currentMessages.splice(pos, 1);
      renderMessages(state.currentMessages);
    } else {
      node.remove();
    }
    const conv = state.conversations.find((c) => c.id === state.currentConversationId);
    if (conv && typeof conv.message_count === "number") {
      conv.message_count = Math.max(0, conv.message_count - 1);
      renderConversationList();
    }
  } catch (err) {
    alert(`Delete failed: ${err.message}`);
  }
}

/** ✏️ Edit one of your own questions in place: swaps the bubble into an
 * inline editor, PATCHes the new wording to the server (the previous text is
 * tombstoned, so stale device pushes can't resurrect it) and re-renders the
 * bubble. Then 🔄 Retry on the edited question re-answers it in place. */
async function editMessage(msg, node) {
  if (!msg.id || !state.currentConversationId) {
    alert("This message has no id yet — reopen the conversation and try again.");
    return;
  }
  const textDiv = node.querySelector(".message-text");
  if (!textDiv || node.dataset.editing === "1") return;
  node.dataset.editing = "1";

  const ta = document.createElement("textarea");
  ta.value = msg.content || "";
  ta.rows = Math.min(10, Math.max(2, (msg.content || "").split("\n").length + 1));
  const actions = document.createElement("div");
  actions.className = "message-edit-actions";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "message-edit-save";
  saveBtn.textContent = "✓ Save";
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "message-edit-cancel";
  cancelBtn.textContent = "✕ Cancel";
  actions.append(saveBtn, cancelBtn);
  textDiv.innerHTML = "";
  textDiv.append(ta, actions);
  ta.focus();

  const restore = () => {
    node.dataset.editing = "";
    renderRichText(textDiv, msg.content || "");
  };
  cancelBtn.addEventListener("click", restore);
  ta.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      saveBtn.click();
    }
    if (e.key === "Escape") {
      e.preventDefault();
      restore();
    }
  });
  saveBtn.addEventListener("click", async () => {
    const next = ta.value.trim();
    if (!next) return;
    if (next === msg.content) { restore(); return; }
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
      await api(`/api/conversations/${state.currentConversationId}/messages/${msg.id}`, {
        method: "PATCH",
        body: JSON.stringify({ content: next }),
      });
      msg.content = next;
      restore();
      loadConversations().catch(() => {}); // sidebar preview may be stale
    } catch (err) {
      alert(`Edit failed: ${err.message}`);
      restore();
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "✓ Save";
    }
  });
}

/** 🔄 Re-answer one assistant reply — or re-ask one question — with other
 * model(s): opens the retry model picker (searchable catalog, multi-select
 * to compare answers as variants), then POSTs /api/chat/retry via doRetry.
 * Old answers are kept — flip between variants or delete any you don't want. */
async function retryMessage(msg, btn, node) {
  if (!msg.id || !state.currentConversationId) {
    alert("This message has no id yet — reopen the conversation and try again.");
    return;
  }
  // RikkaHub-style model picker instead of a bare prompt(): searchable
  // catalog, multi-select to compare answers side-by-side as variants.
  const fallback = msg.model
    || state.liveModel
    || (state.selectedModels || [])[0]
    || "";
  state.retryCtx = { msg, btn, node, models: new Set(fallback ? [fallback] : []) };
  els.retryModalTitle.textContent = msg.role === "user" ? "🔄 Re-ask with…" : "🔄 Re-answer with…";
  state.retryModelSearchQuery = "";
  els.retryModelSearch.value = "";
  els.retryModelSort.value = state.retryModelSort;
  renderRetryModelRows();
  els.retryModal.classList.remove("hidden");
  els.retryModelSearch.focus();
  // The provider's full catalog arrives async (cached after the first open).
  loadModelCatalog().then(renderRetryModelRows);
}

function closeRetryPicker() {
  els.retryModal.classList.add("hidden");
  state.retryCtx = null;
}

// Row list for the retry picker: picked models + typed-letter grep over the
// provider's full catalog (same search feel as the header model picker).
function renderRetryModelRows() {
  els.retryModelList.innerHTML = "";
  if (!state.retryCtx) return;
  const query = (state.retryModelSearchQuery || "").trim().toLowerCase();
  let shown = 0;
  const listedIds = new Set();

  const addRow = (id, name, price) => {
    // ":batch" ids are async-only — they can't answer a retry request.
    if (id.endsWith(":batch")) return;
    listedIds.add(id);
    const label = document.createElement("label");
    label.className = "model-check";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = state.retryCtx.models.has(id);
    cb.addEventListener("change", () => {
      if (cb.checked) state.retryCtx.models.add(id);
      else state.retryCtx.models.delete(id);
      els.retryGo.disabled = state.retryCtx.models.size === 0;
    });
    const text = document.createElement("span");
    text.textContent = name;
    text.title = id;
    label.append(cb, text);
    if (price) {
      const span = document.createElement("span");
      span.className = "model-price";
      span.title = "USD per 1M tokens (prompt / completion) — standard tier";
      span.textContent = `· $${fmtPrice(price.prompt)} / $${fmtPrice(price.completion)} /1M`;
      label.append(span);
    }
    shown++;
    els.retryModelList.appendChild(label);
  };

  const matches = (m) =>
    !query || `${m.id} ${m.name || ""}`.toLowerCase().includes(query);

  const retrySort = els.retryModelSort.value;
  if (retrySort === "new" || retrySort === "output") {
    const rows = state.modelCatalog
      .filter(matches)
      .sort(
        retrySort === "new"
          ? (a, b) => (b.created || 0) - (a.created || 0) || a.id.localeCompare(b.id)
          : (a, b) => a.completion - b.completion || a.prompt - b.prompt || a.id.localeCompare(b.id),
      );
    rows.slice(0, MODEL_ROW_CAP).forEach((m) => addRow(m.id, m.name || m.id, m));
    if (rows.length > MODEL_ROW_CAP) {
      const note = document.createElement("div");
      note.className = "model-search-empty";
      note.textContent = `Showing the first ${MODEL_ROW_CAP} of ${rows.length} — type to narrow it down.`;
      els.retryModelList.appendChild(note);
    }
  } else {
    (state.defaultModels || []).forEach((model) => {
      if (query && !model.toLowerCase().includes(query)) return;
      addRow(model, model, modePrice(model) || undefined);
    });
    if (query && state.modelCatalog.length) {
      state.modelCatalog
        .filter((m) => !listedIds.has(m.id) && matches(m))
        .slice(0, 60)
        .forEach((m) => addRow(m.id, m.name || m.id, m));
    }
  }

  // Typed something that isn't an exact catalog id? Offer it as a raw id.
  if (query && !listedIds.has(state.retryModelSearchQuery.trim())) {
    addRow(state.retryModelSearchQuery.trim(), `Use "${state.retryModelSearchQuery.trim()}" as a custom model id`);
  }

  if (!shown) {
    const none = document.createElement("div");
    none.className = "model-search-empty";
    none.textContent = "No models available.";
    els.retryModelList.appendChild(none);
  }
  els.retryGo.disabled = state.retryCtx.models.size === 0;
}

els.retryModelSearch.addEventListener("input", () => {
  state.retryModelSearchQuery = els.retryModelSearch.value;
  renderRetryModelRows();
});
els.retryModelSort.addEventListener("change", () => {
  state.retryModelSort = els.retryModelSort.value;
  localStorage.setItem("bc_retry_sort", state.retryModelSort);
  loadModelCatalog().then(renderRetryModelRows);
});
// Enter in the search box checks the first visible row.
els.retryModelSearch.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const first = els.retryModelList.querySelector("input[type=checkbox]");
  if (first) {
    first.checked = true;
    first.dispatchEvent(new Event("change"));
  }
});
els.retryClose.addEventListener("click", closeRetryPicker);
els.retryCancel.addEventListener("click", closeRetryPicker);
els.retryModal.addEventListener("click", (e) => {
  if (e.target === els.retryModal) closeRetryPicker();
});
els.retryGo.addEventListener("click", () => {
  if (!state.retryCtx) return;
  const models = [...state.retryCtx.models].slice(0, 20);
  if (!models.length) return;
  const { msg, btn, node } = state.retryCtx;
  closeRetryPicker();
  doRetry(msg, models, btn, node);
});

/** The actual retry POST + variant insertion, shared by the picker flow. */
async function doRetry(msg, models, btn, node) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Retrying…";
  try {
    const resp = await apiStream("/api/chat/retry", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: state.currentConversationId,
        message_id: msg.id,
        models,
        ...(els.reasoningSelect.value
          ? { reasoning_effort: els.reasoningSelect.value }
          : {}),
      }),
    });
    // RikkaHub-style: fresh answers become new VARIANTS of the answer group
    // — update the flat message list at the anchor's position and re-render
    // grouped (the group selector flips between old and new answers).
    if (state.currentMessages.length && msg.id) {
      let anchorPos = state.currentMessages.findIndex((m) => m.id === msg.id);
      if (anchorPos === -1) anchorPos = state.currentMessages.length - 1;
      // Skip past any assistant variants already sitting after the anchor.
      let insertPos = anchorPos + 1;
      while (
        insertPos < state.currentMessages.length &&
        state.currentMessages[insertPos].role === "assistant"
      ) insertPos += 1;
      const fresh = [];
      resp.responses.forEach((r) => {
        if (r.ok) {
          fresh.push({
            id: r.message_id ?? null,
            role: "assistant",
            content: r.content,
            model: r.model,
            reasoning: r.reasoning,
            provider: r.provider,
            gen_id: r.gen_id,
            tokens_prompt: r.tokens_prompt,
            tokens_completion: r.tokens_completion,
            total_tokens: r.total_tokens,
            cost: r.cost,
          });
        } else {
          fresh.push({ id: null, role: "assistant", content: `⚠ ${r.error}`, model: r.model });
        }
      });
      if (fresh.length) {
        state.currentMessages.splice(insertPos, 0, ...fresh);
        renderMessages(state.currentMessages);
        const anchorEl = els.messages.querySelector(`[data-message-id="${msg.id}"]`);
        if (anchorEl) anchorEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    } else {
      // No open flat list (defensive): fall back to appending after the node.
      let anchor = node;
      resp.responses.forEach((r) => {
        if (!anchor) return;
        let inserted = null;
        if (r.ok) {
          inserted = appendMessage({
            id: r.message_id ?? null,
            role: "assistant",
            content: r.content,
            model: r.model,
            reasoning: r.reasoning,
            provider: r.provider,
            gen_id: r.gen_id,
            tokens_prompt: r.tokens_prompt,
            tokens_completion: r.tokens_completion,
            total_tokens: r.total_tokens,
            cost: r.cost,
          }, { afterNode: anchor });
        } else {
          inserted = appendError(r.model, r.error, anchor);
        }
        if (inserted) anchor = inserted;
      });
    }
    const conv = state.conversations.find((c) => c.id === state.currentConversationId);
    if (conv) {
      const added = resp.responses.filter((r) => r.ok).length;
      if (typeof conv.message_count === "number") {
        conv.message_count += added;
      }
      renderConversationList();
    }
    syncNow(); // retried answers also sync immediately
  } catch (err) {
    alert(`Retry failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
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

  // Zero-width / invisible formatting chars and PUA glyphs ride along when
  // math is copied from rendered pages; they render as tofu boxes or vanish,
  // so drop them before any processing. (Plain text never contains them.)
  // eslint-disable-next-line no-irregular-whitespace
  raw = raw.replace(/[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u206a-\u206f\ufeff\ue000-\uf8ff]/g, "");

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

function appendError(model, error, afterNode = null) {
  const div = document.createElement("div");
  div.className = "message assistant";
  const modelTag = document.createElement("span");
  modelTag.className = "message-model";
  modelTag.textContent = model;
  const err = document.createElement("div");
  err.className = "message-err";
  err.textContent = `⚠ ${error}`;
  div.append(modelTag, err);
  if (afterNode && afterNode.parentNode === els.messages) {
    afterNode.after(div);
  } else {
    els.messages.appendChild(div);
    scrollToBottom();
  }
  return div;
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

// 🔄 Auto-sync (web = the master server, so syncing means re-reading the
// master DB): refresh the dialog list and the open conversation. Runs
// automatically after every sent/answered message — no button needed.
async function syncNow() {
  try {
    await loadConversations();
    if (state.currentConversationId !== null) {
      await openConversation(state.currentConversationId);
    }
  } catch (err) {
    console.warn("Auto-sync failed:", err.message);
  }
}

// 🔄 Manual sync — the same refresh as the auto-sync, on demand from the
// ☰ Menu (next to ⚙ Settings). The outcome shows in a toast in the bottom-
// right corner, because the menu popover closes (and hides its buttons) the
// moment any button inside it is clicked — button text would never be seen.
function showToast(message, { error = false, duration = 3000 } = {}) {
  if (!els.toast) return;
  els.toast.textContent = message;
  els.toast.classList.remove("hidden", "toast-fade");
  els.toast.classList.toggle("toast-error", error);
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => {
    els.toast.classList.add("toast-fade");
    setTimeout(() => els.toast.classList.add("hidden"), 300);
  }, duration);
}

els.syncBtn.addEventListener("click", async () => {
  els.syncBtn.disabled = true;
  showToast("⏳ Syncing…", { duration: 15000 }); // stays until done/fails
  try {
    await syncNow();
    const n = state.conversations.length;
    showToast(`✅ Synced · ${n} dialog${n === 1 ? "" : "s"}`);
  } catch (err) {
    showToast(`⚠ Sync failed: ${err.message}`, { error: true, duration: 6000 });
  } finally {
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
    if (resp.warming_blocked) {
      // The toggle was saved, but Settings makes warming impossible — say so
      // instead of silently doing nothing.
      alert(resp.warming_blocked);
    }
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
    const resp = await apiStream("/api/chat/send", {
      method: "POST",
      body: JSON.stringify({
        user_message: text,
        models,
        conversation_id: state.currentConversationId,
        kind: state.chatMode === "batch" ? "batch" : "chat",
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

    // RikkaHub-style grouping: parallel answers to one question become
    // variants of a single answer group (‹ N/M › selector flips between them).
    const sentQuestion = { ...resp.user_message, webSearch: resp.web_search_used === true };
    const sentAnswers = resp.responses.map((r) => r.ok
      ? {
        id: r.message_id ?? null,
        role: "assistant",
        content: r.content,
        model: r.model,
        reasoning: r.reasoning,
        provider: r.provider,
        gen_id: r.gen_id,
        tokens_prompt: r.tokens_prompt,
        tokens_completion: r.tokens_completion,
        total_tokens: r.total_tokens,
        cost: r.cost,
      }
      : { id: null, role: "assistant", content: `⚠ ${r.error}`, model: r.model });
    state.currentMessages = [...state.currentMessages, sentQuestion, ...sentAnswers];
    renderMessages(state.currentMessages);
    await syncNow(); // every message syncs immediately (dialog list + open conversation)
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
// Paste cleanup — text copied out of pages that RENDER math (KaTeX/MathJax,
// messengers, ChatGPT) arrives as one glyph per line plus invisible junk
// (zero-width spaces, PUA glyphs). Strip the invisible characters and
// re-join runs of 1-2 char lines so such pastes stay readable.
// ---------------------------------------------------------------
const INVISIBLE_CHARS = /[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u206a-\u206f\ufeff\ue000-\uf8ff]/g;

function sanitizePastedText(text) {
  const t = text.replace(INVISIBLE_CHARS, "");
  const lines = t.split("\n");
  const out = [];
  let run = [];
  const flushRun = () => {
    if (run.length >= 3) {
      // KaTeX-copy artifact: join the glyph-per-line run back into one line.
      let joined = "";
      for (const raw of run) {
        const piece = raw.trim();
        if (!piece) continue;
        if (joined && /[\p{L}\p{N}_]$/u.test(joined) && /^[\p{L}\p{N}_]/u.test(piece)) {
          joined += " ";
        }
        joined += piece;
      }
      if (joined) out.push(joined);
    } else {
      out.push(...run);
    }
    run = [];
  };
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.length > 0 && trimmed.length <= 2) run.push(line);
    else { flushRun(); out.push(line); }
  }
  flushRun();
  return out.join("\n");
}

els.chatInput.addEventListener("paste", (e) => {
  const text = e.clipboardData ? e.clipboardData.getData("text/plain") : "";
  if (!text) return;
  const cleaned = sanitizePastedText(text);
  if (cleaned === text) return;
  e.preventDefault();
  const el = e.currentTarget;
  el.setRangeText(cleaned, el.selectionStart ?? el.value.length,
    el.selectionEnd ?? el.value.length, "end");
  el.dispatchEvent(new Event("input", { bubbles: true }));
});


// ---------------------------------------------------------------
// Footer menu (☰) — reveals Settings / Prompt cache / Log out
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
// Android transfer (inside the Settings modal): export this
// account's dialogs for the phone app, import a phone export back.
// ---------------------------------------------------------------
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

els.settingsImportSubmit.addEventListener("click", async () => {
  const rawText = els.settingsImportTextarea.value.trim();
  if (!rawText) return;
  els.settingsImportSubmit.disabled = true;
  els.settingsImportStatus.className = "import-status";
  els.settingsImportStatus.textContent = "Importing…";

  try {
    const parsed = JSON.parse(rawText);
    const payload = normalizePhonePayload(parsed);
    const result = await api("/api/import/phone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    els.settingsImportStatus.classList.add("ok");
    els.settingsImportStatus.textContent =
      `Imported ${result.conversations_created} conversations, ` +
      `${result.messages_created} messages.`;
    els.settingsImportTextarea.value = "";
    await loadConversations();
  } catch (err) {
    els.settingsImportStatus.classList.add("err");
    els.settingsImportStatus.textContent = `Import failed: ${err.message}`;
  } finally {
    els.settingsImportSubmit.disabled = false;
  }
});

els.settingsPhoneExport.addEventListener("click", async () => {
  els.settingsPhoneStatus.className = "import-status";
  els.settingsPhoneStatus.textContent = "Preparing export…";
  try {
    const data = await api("/api/export/phone");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `batch-chat-android-export-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    els.settingsPhoneStatus.classList.add("ok");
    els.settingsPhoneStatus.textContent =
      `Export downloaded: ${(data["openrouter.dialogs.v1"] || []).length} dialogs, ` +
      `${(data["openrouter.batches.history.v1"] || []).length} batches. Import it on the phone.`;
  } catch (err) {
    els.settingsPhoneStatus.classList.add("err");
    els.settingsPhoneStatus.textContent = `Export failed: ${err.message}`;
  }
});

// ---------------------------------------------------------------
// Settings (provider API keys — saved to the server DB, no restart needed)
// ---------------------------------------------------------------
function openSettings() {
  els.settingsStatus.className = "import-status";
  els.settingsStatus.textContent = "";
  els.settingsPhoneStatus.className = "import-status";
  els.settingsPhoneStatus.textContent = "";
  els.settingsImportStatus.className = "import-status";
  els.settingsImportStatus.textContent = "";
  els.settingsImportTextarea.value = "";
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
  // Danger zone: visible for everyone, but disabled for the owner (whose
  // account carries the provider keys and cannot be deleted).
  try {
    const me = await api("/api/auth/me");
    if (me.is_owner) {
      els.accountDelete.disabled = true;
      els.accountDangerNote.textContent =
        "The owner account cannot be deleted — it manages this server.";
    } else {
      els.accountDelete.disabled = false;
      els.accountDangerNote.textContent = "";
    }
  } catch {
    els.accountDanger.classList.add("hidden");
  }
}

els.accountDelete.addEventListener("click", async () => {
  const really = confirm(
    "Delete your account?\n\nALL your dialogs, messages and batches will be permanently removed, and your e-mail/login freed so you can register again from zero. This cannot be undone.",
  );
  if (!really) return;
  els.accountDelete.disabled = true;
  els.accountDangerNote.className = "import-status";
  els.accountDangerNote.textContent = "Deleting account…";
  try {
    const res = await api("/api/auth/account", { method: "DELETE" });
    alert(
      `Account deleted (${res.deleted_dialogs} dialog(s) removed). You can now register again with the same e-mail.`,
    );
    localStorage.removeItem("bc_token");
    location.reload();
  } catch (err) {
    els.accountDangerNote.className = "import-status err";
    els.accountDangerNote.textContent = `Delete failed: ${err.message}`;
    els.accountDelete.disabled = false;
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

let settingsIsOwner = false;

async function loadSettings() {
  try {
    const me = await api("/api/auth/me");
    settingsIsOwner = !!me.is_owner;
    const who = me.email || me.label || me.account_id;
    els.settingsIdentity.textContent = me.is_owner
      ? `Signed in as: ${who} — owner account`
      : `Signed in as: ${who}`;
    els.ownerAccessBlock.classList.toggle("hidden", !me.is_owner);
  } catch {
    settingsIsOwner = false;
    els.settingsIdentity.textContent = "";
    els.ownerAccessBlock.classList.add("hidden");
  }
  // Client accounts manage only the two shared provider keys (see / hide the
  // rest): no infra/cache/backup sections.
  els.cacheSettingsBlock.classList.toggle("hidden", !settingsIsOwner);
  els.infraSettingsBlock.classList.toggle("hidden", !settingsIsOwner);
  els.backupBlock.classList.toggle("hidden", !settingsIsOwner);
  try {
    const data = await api("/api/settings");
    els.settingsOpenrouterHint.textContent = data.openrouter_api_key.configured
      ? `(saved: ${data.openrouter_api_key.hint})` : "(not set)";
    els.settingsTavilyHint.textContent = data.tavily_api_key.configured
      ? `(saved: ${data.tavily_api_key.hint})` : "(not set)";
    els.settingsCustomHint.textContent = data.custom_api_key.configured
      ? `(saved: ${data.custom_api_key.hint})` : "(not set)";
    els.settingsCustomUrl.value = data.custom_base_url.value || "";
    els.settingsCustomUrl.placeholder = data.custom_base_url.value
      ? data.custom_base_url.value : "https://api.fastrouter.ai/v1";
    els.settingsCustomModel.value = data.custom_default_model.value || "";
    els.settingsCustomModel.placeholder = data.custom_default_model.value
      ? data.custom_default_model.value : "z-ai/glm-5.3-flash";
    renderKeyStatus(els.keyStatusOpenrouter, data.openrouter_api_key.status);
    renderKeyStatus(els.keyStatusTavily, data.tavily_api_key.status);
    renderKeyStatus(els.keyStatusCustom, data.custom_api_key.status);
    if (settingsIsOwner) {
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
      if (data.owner_email !== undefined) {
        els.settingsOwnerEmail.value = data.owner_email || "";
        els.settingsOwnerEmail.placeholder = data.owner_email
          ? data.owner_email : "you@example.com";
      }
    }
  } catch (err) {
    els.settingsStatus.classList.add("err");
    els.settingsStatus.textContent = err.message === "Owner account required"
      ? "Owner account required — log in leaving the e-mail field empty, "
        + "with the server master password (you can bind your e-mail to the "
        + "owner account there)."
      : `Failed to load: ${err.message}`;
  }
}

function renderKeyStatus(el, status) {
  el.textContent = "";
  if (!status) return;
  const labels = {
    valid: "✓ valid",
    invalid: "✕ invalid",
    error: "⚠ check failed",
    not_set: "— no key saved",
  };
  const className = {
    valid: "ok",
    invalid: "err",
    error: "err",
    not_set: "",
  }[status.status] || "";
  el.className = `key-status ${className}`.trim();
  const when = status.checked_at
    ? new Date(status.checked_at).toLocaleString()
    : "";
  el.textContent = `${labels[status.status] || status.status}` +
    `${status.detail ? ` — ${status.detail}` : ""}` +
    `${when ? ` (${when})` : ""}${status.stale ? " · stale" : ""}`;
}

async function deleteKey(field, btn) {
  const keyNames = {
    openrouter_api_key: "OpenRouter",
    tavily_api_key: "Tavily",
    custom_api_key: "Custom provider",
  };
  if (!confirm(`Delete the saved ${keyNames[field] || field} key from the server?\n\nChatting (or web search) through it will fail until a new key is pasted.`)) {
    return;
  }
  btn.disabled = true;
  try {
    await api(`/api/settings/keys/${field}`, { method: "DELETE" });
    await loadSettings();
    await checkHealth();
  } catch (err) {
    els.settingsStatus.classList.add("err");
    els.settingsStatus.textContent = `Delete failed: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

els.keyDeleteOpenrouter.addEventListener("click", () =>
  deleteKey("openrouter_api_key", els.keyDeleteOpenrouter));
els.keyDeleteTavily.addEventListener("click", () =>
  deleteKey("tavily_api_key", els.keyDeleteTavily));
els.keyDeleteCustom.addEventListener("click", () =>
  deleteKey("custom_api_key", els.keyDeleteCustom));

els.ownerEmailSave.addEventListener("click", async () => {
  const email = els.settingsOwnerEmail.value.trim();
  els.ownerEmailSave.disabled = true;
  els.settingsStatus.className = "import-status";
  els.settingsStatus.textContent = "Binding…";
  try {
    const res = await api("/api/settings/owner-email", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
    els.settingsStatus.classList.add("ok");
    els.settingsStatus.textContent = res.owner_email
      ? `Owner e-mail bound: ${res.owner_email}. Log out and sign in with it `
        + "(password or Google) to get owner rights."
      : "Owner e-mail unbound.";
  } catch (err) {
    els.settingsStatus.classList.add("err");
    els.settingsStatus.textContent = `Bind failed: ${err.message}`;
  } finally {
    els.ownerEmailSave.disabled = false;
  }
});

els.settingsSubmit.addEventListener("click", async () => {
  const body = {};
  const maybeAdd = (key, value) => {
    const trimmed = value.trim();
    if (trimmed) body[key] = trimmed;
  };
  maybeAdd("openrouter_api_key", els.settingsOpenrouterKey.value);
  maybeAdd("tavily_api_key", els.settingsTavilyKey.value);
  maybeAdd("custom_api_key", els.settingsCustomKey.value);
  {
    const customUrl = els.settingsCustomUrl.value.trim().replace(/\/+$/, "");
    if (customUrl) body.custom_base_url = customUrl;
    const customModel = els.settingsCustomModel.value.trim();
    if (customModel) body.custom_default_model = customModel;
  }
  if (settingsIsOwner) {
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
  }

  els.settingsSubmit.disabled = true;
  els.settingsStatus.className = "import-status";
  els.settingsStatus.textContent = "Saving…";
  try {
    const saved = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    els.settingsOpenrouterKey.value = "";
    els.settingsTavilyKey.value = "";
    els.settingsCustomKey.value = "";
    els.settingsGoogleJson.value = "";
    els.settingsAwsKey.value = "";
    els.settingsAwsSecret.value = "";
    els.settingsStatus.classList.add("ok");
    const keyLabels = {
      openrouter_api_key: "OpenRouter",
      tavily_api_key: "Tavily",
      custom_api_key: "Custom provider",
    };
    const verdicts = Object.entries(saved.checked_keys || {})
      .map(([field, st]) =>
        `${keyLabels[field] || field} key: ${
          st.status === "valid" ? "✓ valid" :
          st.status === "invalid" ? "✕ rejected by provider" :
          st.status === "not_set" ? "cleared" : `⚠ ${st.detail}`
        }`)
      .join("; ");
    els.settingsStatus.textContent =
      "Saved. Applied immediately, no restart needed." + (verdicts ? ` ${verdicts}.` : "");
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

// ---------------------------------------------------------------
// Prompt token caching usage (OpenRouter-dashboard style chart)
// ---------------------------------------------------------------
function fmtTokens(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
  return String(n);
}

function fmtDay(iso) {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

function openUsage() {
  els.usageModal.classList.remove("hidden");
  loadUsage();
}

async function loadUsage() {
  const days = parseInt(els.usageRange.value, 10) || 30;
  els.usageChart.innerHTML = "";
  els.usageSummary.textContent = "";
  els.usageStatus.className = "import-status";
  els.usageStatus.textContent = "Loading usage…";
  try {
    const data = await api(`/api/stats/prompt-cache?days=${days}`);
    renderUsageChart(data);
    els.usageStatus.textContent = "";
  } catch (err) {
    els.usageStatus.classList.add("err");
    els.usageStatus.textContent = `Load failed: ${err.message}`;
  }
}

function renderUsageChart(data) {
  const buckets = data.buckets || [];
  const totals = data.totals || {};
  els.usageSummary.textContent =
    `Prompt tokens: ${fmtTokens(totals.prompt || 0)} · ` +
    `Cached: ${fmtTokens(totals.cached || 0)} (${totals.cached_share || 0}%) · ` +
    `Uncached: ${fmtTokens(totals.uncached || 0)} · ` +
    `Cost: $${(totals.cost || 0).toFixed(2)}`;
  if (!buckets.length) {
    els.usageChart.textContent = "No data.";
    return;
  }
  const max = Math.max(1, ...buckets.map((b) => (b.cached || 0) + (b.uncached || 0)));

  const inner = document.createElement("div");
  inner.className = "usage-chart-inner";

  // Y axis: five gridline labels from 0 to the tallest day, 30M-style.
  const y = document.createElement("div");
  y.className = "usage-yaxis";
  for (let i = 4; i >= 0; i--) {
    const s = document.createElement("span");
    s.textContent = fmtTokens(Math.round((max * i) / 4));
    y.append(s);
  }

  const plotWrap = document.createElement("div");
  plotWrap.className = "usage-plotwrap";
  const plot = document.createElement("div");
  plot.className = "usage-plot";
  const labels = document.createElement("div");
  labels.className = "usage-xlabels";

  const every = Math.max(1, Math.ceil(buckets.length / 10));
  buckets.forEach((b, idx) => {
    const total = (b.cached || 0) + (b.uncached || 0);
    const col = document.createElement("div");
    col.className = "usage-col";
    if (total > 0) {
      col.style.height = `${Math.max(2, (total / max) * 100)}%`;
      col.title =
        `${fmtDay(b.date)}\n` +
        `Uncached: ${fmtTokens(b.uncached || 0)}\n` +
        `Cached: ${fmtTokens(b.cached || 0)}\n` +
        `Cost: $${(b.cost || 0).toFixed(4)}`;
      // Uncached stacked on top of the cached base (OpenRouter layout).
      const uncached = document.createElement("div");
      uncached.className = "usage-seg uncached";
      uncached.style.height = `${((b.uncached || 0) / total) * 100}%`;
      const cached = document.createElement("div");
      cached.className = "usage-seg cached";
      cached.style.height = `${((b.cached || 0) / total) * 100}%`;
      col.append(uncached, cached);
    } else {
      col.classList.add("empty");
      col.title = `${fmtDay(b.date)} — no prompt tokens`;
    }
    plot.append(col);

    const label = document.createElement("span");
    label.textContent = idx % every === 0 ? fmtDay(b.date) : "";
    labels.append(label);
  });

  plotWrap.append(plot, labels);
  inner.append(y, plotWrap);
  els.usageChart.append(inner);
}

els.usageBtn.addEventListener("click", openUsage);
els.usageOpenLink.addEventListener("click", (e) => {
  e.preventDefault();
  openUsage();
});
els.usageClose.addEventListener("click", () => els.usageModal.classList.add("hidden"));
els.usageModal.addEventListener("click", (e) => {
  if (e.target === els.usageModal) els.usageModal.classList.add("hidden");
});
els.usageRange.addEventListener("change", loadUsage);
els.usageRefresh.addEventListener("click", loadUsage);