const state = {
  models: [],
  chats: [],
  activeChat: null,
  busy: false,
  activity: null,
  toastTimer: null,
};

const elements = {
  list: document.querySelector("#chat-list"),
  count: document.querySelector("#chat-count"),
  title: document.querySelector("#current-chat-title"),
  model: document.querySelector("#current-model"),
  composerModel: document.querySelector("#composer-model"),
  modelMenu: document.querySelector("#model-menu"),
  messages: document.querySelector("#message-list"),
  welcome: document.querySelector("#welcome"),
  prompt: document.querySelector("#prompt"),
  form: document.querySelector("#composer"),
  send: document.querySelector("#send-button"),
  conversation: document.querySelector("#conversation"),
  toast: document.querySelector("#toast"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Ошибка ${response.status}`);
  return body;
}

function escapeText(value) {
  return String(value ?? "");
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 2600);
}

function modelName(filename) {
  return filename.replace(/\.litertlm$/i, "");
}

function activeModel() {
  return state.activeChat
    ? state.models.find((model) => model.filename === state.activeChat.model)
    : state.models[0];
}

function renderChatList() {
  elements.count.textContent = String(state.chats.length);
  elements.list.replaceChildren();
  for (const chat of state.chats) {
    const item = document.createElement("button");
    item.className = `chat-item${chat.id === state.activeChat?.id ? " active" : ""}`;
    item.type = "button";
    const icon = document.createElement("span");
    icon.className = "chat-item-icon";
    icon.textContent = "◌";
    const title = document.createElement("span");
    title.className = "chat-item-title";
    title.textContent = chat.title;
    const remove = document.createElement("span");
    remove.className = "delete-chat";
    remove.setAttribute("role", "button");
    remove.setAttribute("aria-label", "Удалить чат");
    remove.textContent = "×";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteChat(chat.id);
    });
    item.append(icon, title, remove);
    item.addEventListener("click", () => openChat(chat.id));
    elements.list.append(item);
  }
}

function renderHeader() {
  if (!state.activeChat) return;
  elements.title.textContent = state.activeChat.title;
  const selected = activeModel();
  const name = selected?.name || modelName(state.activeChat.model);
  elements.model.textContent = name;
  elements.composerModel.textContent = name;
  document.title = `${state.activeChat.title} — PyBomjoChat`;
}

function renderTranscript() {
  elements.messages.replaceChildren();
  elements.welcome.classList.toggle("hidden", state.activeChat.messages.length > 0);
  for (const message of state.activeChat.messages) appendMessage(message.role, message.content);
  if (state.busy) showActivity("Обдумываю запрос");
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
}

function appendMessage(role, content) {
  const row = document.createElement("article");
  row.className = `message ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = role === "user" ? "Я" : "P";
  const body = document.createElement("div");
  body.className = "message-content";
  body.textContent = escapeText(content);
  row.append(avatar, body);
  elements.messages.append(row);
  elements.welcome.classList.add("hidden");
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
  return body;
}

function showActivity(label) {
  if (!state.activity) {
    const activity = document.createElement("div");
    activity.className = "activity";
    const dots = document.createElement("span");
    dots.className = "activity-dots";
    dots.innerHTML = "<i></i><i></i><i></i>";
    const text = document.createElement("span");
    activity.append(dots, text);
    elements.messages.append(activity);
    state.activity = { element: activity, text };
  }
  state.activity.text.textContent = label;
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
}

function clearActivity() {
  state.activity?.element.remove();
  state.activity = null;
}

function addToolCard(data) {
  const card = document.createElement("div");
  card.className = "tool-card";
  const icon = document.createElement("div");
  icon.className = "tool-card-icon";
  icon.textContent = data.name === "web_search" ? "⌕" : "⌘";
  const body = document.createElement("div");
  body.className = "tool-card-body";
  const title = document.createElement("div");
  title.className = "tool-card-title";
  title.textContent = data.name === "web_search" ? "Ищу в интернете" : `Инструмент: ${data.name}`;
  const detail = document.createElement("div");
  detail.className = "tool-card-detail";
  detail.textContent = data.arguments?.query || JSON.stringify(data.arguments || {});
  const result = document.createElement("div");
  result.className = "tool-card-result";
  body.append(title, detail, result);
  const status = document.createElement("div");
  status.className = "tool-state";
  status.textContent = "выполняется";
  card.append(icon, body, status);
  elements.messages.append(card);
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
  return { card, result, status };
}

function openModelMenu() {
  if (!state.models.length) {
    showToast("В папке models пока нет моделей .litertlm.");
    return;
  }
  elements.modelMenu.replaceChildren();
  const label = document.createElement("div");
  label.className = "model-menu-label";
  label.textContent = "ВЫБЕРИТЕ МОДЕЛЬ";
  elements.modelMenu.append(label);
  for (const model of state.models) {
    const option = document.createElement("button");
    option.type = "button";
    option.className = `model-option${model.filename === state.activeChat.model ? " selected" : ""}`;
    option.setAttribute("role", "option");
    const dot = document.createElement("span");
    dot.className = "model-option-dot";
    const name = document.createElement("span");
    name.textContent = model.name;
    const file = document.createElement("small");
    file.textContent = model.filename;
    option.append(dot, name, file);
    option.addEventListener("click", () => selectModel(model));
    elements.modelMenu.append(option);
  }
  elements.modelMenu.hidden = false;
}

async function selectModel(model) {
  try {
    const result = await api(`/api/chats/${state.activeChat.id}/model`, {
      method: "POST",
      body: JSON.stringify({ model: model.filename }),
    });
    state.activeChat = result.chat;
    state.models = state.models.map((item) => item.filename === model.filename ? result.model : item);
    elements.modelMenu.hidden = true;
    renderHeader();
    renderChatList();
    showToast(`Модель переключена: ${model.name}`);
  } catch (error) {
    showToast(error.message);
  }
}

async function openChat(chatId) {
  try {
    state.activeChat = await api(`/api/chats/${chatId}`);
    localStorage.setItem("pybomjo-active-chat", chatId);
    renderChatList();
    renderHeader();
    renderTranscript();
    document.body.classList.remove("sidebar-open");
    elements.prompt.focus();
  } catch (error) {
    showToast(error.message);
  }
}

async function createChat() {
  if (state.busy) return;
  try {
    state.activeChat = await api("/api/chats", {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.chats.unshift({
      id: state.activeChat.id,
      title: state.activeChat.title,
      model: state.activeChat.model,
      updated_at: state.activeChat.updated_at,
    });
    localStorage.setItem("pybomjo-active-chat", state.activeChat.id);
    renderChatList();
    renderHeader();
    renderTranscript();
    elements.prompt.focus();
    document.body.classList.remove("sidebar-open");
  } catch (error) {
    showToast(error.message);
  }
}

async function deleteChat(chatId) {
  try {
    await api(`/api/chats/${chatId}`, { method: "DELETE" });
    state.chats = state.chats.filter((chat) => chat.id !== chatId);
    if (state.activeChat?.id === chatId) {
      state.activeChat = null;
      localStorage.removeItem("pybomjo-active-chat");
      if (state.chats.length) await openChat(state.chats[0].id);
      else await createChat();
    }
    renderChatList();
  } catch (error) {
    showToast(error.message);
  }
}

function parseSseBlock(block) {
  const line = block.split("\n").find((item) => item.startsWith("data: "));
  if (!line) return null;
  try {
    return JSON.parse(line.slice(6));
  } catch {
    return null;
  }
}

async function sendMessage(content) {
  if (state.busy || !state.activeChat || !content.trim()) return;
  const text = content.trim();
  state.activeChat.messages.push({ role: "user", content: text });
  appendMessage("user", text);
  elements.prompt.value = "";
  resizePrompt();
  state.busy = true;
  elements.send.disabled = true;
  elements.prompt.disabled = true;
  showActivity("Обдумываю запрос");
  let assistantBody = null;
  let assistantText = "";
  try {
    const response = await fetch(`/api/chats/${state.activeChat.id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text }),
    });
    if (!response.ok || !response.body) {
      const body = await response.json();
      throw new Error(body.error || `Ошибка ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const event = parseSseBlock(block);
        if (event) handleStreamEvent(event, (textDelta) => {
          if (!assistantBody) {
            clearActivity();
            assistantBody = appendMessage("assistant", "");
          }
          assistantText += textDelta;
          assistantBody.textContent = assistantText;
          const cursor = document.createElement("span");
          cursor.className = "stream-cursor";
          assistantBody.append(cursor);
          elements.conversation.scrollTop = elements.conversation.scrollHeight;
        });
      }
      if (done) break;
    }
    if (buffer.trim()) {
      const event = parseSseBlock(buffer);
      if (event) handleStreamEvent(event, () => {});
    }
    state.activeChat = await api(`/api/chats/${state.activeChat.id}`);
    renderChatList();
    renderHeader();
  } catch (error) {
    clearActivity();
    const note = document.createElement("div");
    note.className = "error-note";
    note.textContent = `Не удалось получить ответ: ${error.message}`;
    elements.messages.append(note);
  } finally {
    if (assistantBody) assistantBody.querySelector(".stream-cursor")?.remove();
    clearActivity();
    state.busy = false;
    elements.send.disabled = false;
    elements.prompt.disabled = false;
    elements.prompt.focus();
    elements.conversation.scrollTop = elements.conversation.scrollHeight;
  }
}

function handleStreamEvent(event, onDelta) {
  if (event.type === "phase") {
    showActivity(event.label);
  } else if (event.type === "delta") {
    onDelta(event.text);
  } else if (event.type === "tool_start") {
    clearActivity();
    const tool = addToolCard(event);
    state.lastTool = tool;
    showActivity(event.name === "web_search" ? "Ищу подходящие страницы" : "Выполняю действие");
  } else if (event.type === "tool_end") {
    clearActivity();
    if (state.lastTool) {
      state.lastTool.card.classList.add("done");
      state.lastTool.result.textContent = event.result;
      state.lastTool.status.textContent = "готово";
      state.lastTool = null;
    }
    showActivity("Обдумываю результаты");
  } else if (event.type === "complete") {
    clearActivity();
  } else if (event.type === "error") {
    clearActivity();
    const note = document.createElement("div");
    note.className = "error-note";
    note.textContent = `Ошибка модели: ${event.message}`;
    elements.messages.append(note);
  }
}

function resizePrompt() {
  elements.prompt.style.height = "auto";
  elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 180)}px`;
}

async function handleSlashCommand(value) {
  const match = value.trim().match(/^\/model(?:\s+(.+))?$/i);
  if (!match) return false;
  if (match[1]) {
    const requested = match[1].trim().toLowerCase();
    const model = state.models.find((item) =>
      item.name.toLowerCase() === requested || item.filename.toLowerCase() === requested,
    );
    if (!model) {
      showToast(`Модель «${match[1]}» не найдена. Введите /model для списка.`);
      return true;
    }
    await selectModel(model);
  } else {
    openModelMenu();
  }
  elements.prompt.value = "";
  resizePrompt();
  return true;
}

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = elements.prompt.value;
  if (await handleSlashCommand(content)) return;
  sendMessage(content);
});
elements.prompt.addEventListener("input", resizePrompt);
elements.prompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.form.requestSubmit();
  }
});
document.querySelector("#new-chat").addEventListener("click", createChat);
document.querySelector("#model-button").addEventListener("click", () => {
  elements.modelMenu.hidden ? openModelMenu() : (elements.modelMenu.hidden = true);
});
document.querySelector("#menu-toggle").addEventListener("click", () => document.body.classList.toggle("sidebar-open"));
document.querySelector("#sidebar-toggle").addEventListener("click", () => document.body.classList.toggle("sidebar-collapsed"));
document.querySelectorAll(".suggestion").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.prompt || ""));
});
document.addEventListener("click", (event) => {
  if (!elements.modelMenu.contains(event.target) && !event.target.closest("#model-button")) {
    elements.modelMenu.hidden = true;
  }
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    createChat();
  }
  if (event.key === "Escape") {
    document.body.classList.remove("sidebar-open");
    elements.modelMenu.hidden = true;
  }
});

async function start() {
  try {
    const initial = await api("/api/bootstrap");
    state.models = initial.models;
    state.chats = initial.chats;
    renderChatList();
    if (!state.models.length) {
      showToast("Добавьте файл .litertlm в папку models.");
      return;
    }
    const remembered = localStorage.getItem("pybomjo-active-chat");
    const chatId = state.chats.some((chat) => chat.id === remembered)
      ? remembered
      : state.chats[0]?.id;
    if (chatId) await openChat(chatId);
    else await createChat();
  } catch (error) {
    showToast(`Не удалось подключиться к серверу: ${error.message}`);
  }
}

start();
