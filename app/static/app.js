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
  sending: false,
};

const els = {
  loginView: $("#login-view"),
  appView: $("#app-view"),
  loginForm: $("#login-form"),
  loginPassword: $("#login-password"),
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
  modelPickerBtn: $("#model-picker-btn"),
  modelDropdown: $("#model-dropdown"),
  modelCheckboxes: $("#model-checkboxes"),
  customModelInput: $("#custom-model-input"),
  addModelBtn: $("#add-model-btn"),
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
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password: els.loginPassword.value }),
    });
    state.token = data.token;
    localStorage.setItem("bc_token", data.token);
    showApp();
  } catch (err) {
    els.loginError.textContent = err.message;
    els.loginError.classList.remove("hidden");
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
}

async function checkHealth() {
  try {
    const data = await api("/api/health");
    els.serverStatus.classList.add("ok");
    els.serverStatus.classList.remove("down");
    els.serverStatus.title = data.openrouter_configured
      ? "Server OK · OpenRouter configured"
      : "Server OK · OpenRouter key NOT set — calls will fail";
  } catch {
    els.serverStatus.classList.remove("ok");
    els.serverStatus.classList.add("down");
  }
}

if (state.token) {
  api("/api/auth/me")
    .then(() => showApp())
    .catch(() => { /* login screen stays */ });
}

// ---------------------------------------------------------------
// Models
// ---------------------------------------------------------------
async function loadModels() {
  try {
    const data = await api("/api/chat/models");
    state.defaultModels = data.default_models || [];
    if (!state.selectedModels || !state.selectedModels.length) {
      state.selectedModels = state.defaultModels.slice(0, 3);
      saveModels();
    }
    renderModelCheckboxes();
  } catch { /* ignore */ }
}

function saveModels() {
  localStorage.setItem("bc_models", JSON.stringify(state.selectedModels));
}

function renderModelCheckboxes() {
  els.modelCheckboxes.innerHTML = "";
  state.defaultModels.forEach((model) => {
    const label = document.createElement("label");
    label.className = "model-check";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = (state.selectedModels || []).includes(model);
    cb.addEventListener("change", () => toggleModel(model, cb.checked));
    label.append(cb, model);
    els.modelCheckboxes.appendChild(label);
  });
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
  if (!state.selectedModels.includes(custom)) state.selectedModels.push(custom);
  els.customModelInput.value = "";
  renderModelCheckboxes();
  saveModels();
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

    li.append(title, preview);
    li.addEventListener("click", () => openConversation(conv.id));
    els.conversationList.appendChild(li);
  });
}

async function openConversation(id) {
  state.currentConversationId = id;
  const conv = await api(`/api/conversations/${id}`);
  els.chatTitle.textContent = conv.title;
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

  if (msg.model) {
    const modelTag = document.createElement("span");
    modelTag.className = "message-model";
    modelTag.textContent = msg.model;
    div.appendChild(modelTag);
  }

  const text = document.createElement("div");
  text.textContent = msg.content;
  div.appendChild(text);
  els.messages.appendChild(div);
  scrollToBottom();
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
// Send / batch chat
// ---------------------------------------------------------------
els.chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.sending) return;
  const text = els.chatInput.value.trim();
  if (!text) return;
  if (!state.selectedModels || !state.selectedModels.length) {
    alert("Select at least one model in the Models dropdown.");
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
        models: state.selectedModels,
        conversation_id: state.currentConversationId,
      }),
    });

    if (state.currentConversationId === null) {
      els.chatTitle.textContent = resp.conversation_title;
    }
    state.currentConversationId = resp.conversation_id;
    els.chatInput.value = "";

    appendMessage(resp.user_message);
    resp.responses.forEach((r) => {
      if (r.ok) appendMessage({ role: "assistant", content: r.content, model: r.model });
      else appendError(r.model, r.error);
    });
    await loadConversations();
  } catch (err) {
    appendError(state.selectedModels.join(", "), err.message);
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