// towncrier/static/vendor/publisher.js
var MAX_POST_BYTES = 151;
var encoder = new TextEncoder();
function validateMessage(text, limit = MAX_POST_BYTES) {
  const bytes = encoder.encode(String(text)).length;
  return { bytes, limit, valid: bytes <= limit, remaining: limit - bytes };
}
function secondsToMilliseconds(seconds) {
  const value = Number(seconds);
  if (!Number.isInteger(value) || value < 1) throw new Error("Delay must be a whole number of seconds.");
  return value * 1e3;
}
function buildPreviewMessages(messages) {
  return messages.map((text, index) => ({ number: index + 1, text: String(text), bytes: validateMessage(text).bytes }));
}
function reorderMessages(messages, from, to) {
  if (from < 0 || from >= messages.length || to < 0 || to >= messages.length) return [...messages];
  const next = [...messages], [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}
function splitMessages(values, limit = MAX_POST_BYTES) {
  const maxBytes = Math.max(1, Number(limit) || MAX_POST_BYTES);
  const result = [];
  for (const value of values) {
    let chunk = "";
    for (const character of String(value)) {
      if (encoder.encode(chunk + character).length > maxBytes) {
        const boundary = chunk.lastIndexOf(" ");
        if (boundary > 0) {
          result.push(chunk.slice(0, boundary + 1));
          chunk = chunk.slice(boundary + 1) + character;
        } else {
          if (chunk) result.push(chunk);
          chunk = character;
        }
      } else {
        chunk += character;
      }
    }
    if (chunk) result.push(chunk);
  }
  return result;
}
async function sendInOrder(messages, send, wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), delayMs = 1500, isStopped = () => false, onProgress = () => {
}) {
  const delay = Math.max(250, Number(delayMs) || 1500);
  for (let i = 0; i < messages.length; i++) {
    if (isStopped()) throw new Error("Queue stopped.");
    await send(messages[i]);
    onProgress(i + 1, messages.length);
    if (i < messages.length - 1) await wait(delay);
  }
}
var TowncrierPublisher = class {
  async request(path, body) {
    const response = await fetch(`/publisher-api/${path}`, {
      method: body === void 0 ? "GET" : "POST",
      credentials: "same-origin",
      headers: body === void 0 ? {} : { "Content-Type": "application/json" },
      body: body === void 0 ? void 0 : JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Towncrier request failed (${response.status}).`);
    return result;
  }
  async roomServers() {
    return (await this.request("contacts")).rooms;
  }
  async login(publicKey, password) {
    return this.request("login", { public_key: publicKey, password });
  }
  async sendMessage(publicKey, text) {
    const size = validateMessage(text);
    if (!size.valid) throw new Error(`Message exceeds the room-server limit of ${size.limit} UTF-8 bytes (${size.bytes} bytes).`);
    return this.request("publish", { public_key: publicKey, text });
  }
  async close() {
  }
};

// towncrier/static/publisher.js
var MAX_POST_BYTES2 = 130;
var validateMessage2 = (text) => validateMessage(text, MAX_POST_BYTES2);
var splitMessages2 = (values) => splitMessages(values, MAX_POST_BYTES2);
var $ = (id) => document.getElementById(id);
var messagesEl = $("messages");
var publisher = null;
var loggedIn = false;
var running = false;
var stopRequested = false;
var PHONE_SCREEN_DEFAULT = { width: 390, height: 844 };
function messageValues() {
  return [...messagesEl.querySelectorAll("textarea")].map((x) => x.value);
}
function resizeMessage(ta) {
  ta.style.height = "auto";
  ta.style.height = `${Math.max(ta.scrollHeight, 48)}px`;
}
function clampMessage(ta) {
  const start = ta.selectionStart, end = ta.selectionEnd, value = ta.value;
  if (value !== ta.value) {
    ta.value = value;
    ta.setSelectionRange(Math.min(start, value.length), Math.min(end, value.length));
  }
}
function renderPreview() {
  const root = $("previewMessages");
  root.replaceChildren();
  for (const item of buildPreviewMessages(messageValues())) {
    const post = document.createElement("article");
    post.className = "phone-post received";
    const meta = document.createElement("div");
    meta.className = "phone-post-meta";
    meta.textContent = `Room member \xB7 Message ${item.number} \xB7 ${item.bytes}/${MAX_POST_BYTES2} bytes`;
    const bubble = document.createElement("div");
    bubble.className = "phone-bubble";
    bubble.textContent = item.text || "Empty message";
    post.append(meta, bubble);
    root.append(post);
  }
}
function updatePreviewScale() {
  const select = $("phoneSizeSelect"), option = select.selectedOptions[0], width = Number(option?.dataset.width) || PHONE_SCREEN_DEFAULT.width, height = Number(option?.dataset.height) || PHONE_SCREEN_DEFAULT.height, dialog = $("previewDialog"), padding = parseFloat(getComputedStyle(dialog).paddingLeft) || 0, availableWidth = dialog.clientWidth ? dialog.clientWidth - padding * 2 : window.innerWidth - 40, availableHeight = Math.max(240, window.innerHeight - 160), scale = Math.max(0.45, Math.min(1, availableWidth / width, availableHeight / height)), stage = $("previewStage"), screen = $("phoneScreen");
  stage.style.width = `${width * scale}px`;
  stage.style.height = `${height * scale}px`;
  screen.style.width = `${width}px`;
  screen.style.height = `${height}px`;
  screen.style.transform = `scale(${scale})`;
  screen.dataset.viewportWidth = String(width);
  screen.dataset.viewportHeight = String(height);
  screen.setAttribute("aria-label", `Phone screen preview, ${width} by ${height} pixels`);
  $("previewDimensions").textContent = `${width} \xD7 ${height} CSS PIXELS`;
  $("phoneSizeNote").textContent = `Phone viewport \xB7 ${width} \xD7 ${height}`;
}
function renderMessages(values = [""]) {
  values = values.flatMap((value) => validateMessage2(value).valid ? [String(value)] : splitMessages2([value]));
  messagesEl.replaceChildren();
  const moveMessage = (from, to) => {
    if (from === to) return;
    renderMessages(reorderMessages(messageValues(), from, to));
    messagesEl.querySelectorAll('[data-action="up"]')[to]?.focus({ preventScroll: true });
  };
  values.forEach((value, i) => {
    const row = document.createElement("div");
    row.className = "message";
    row.dataset.index = String(i);
    const editor = document.createElement("div");
    editor.className = "message-editor";
    const toolbar = document.createElement("div");
    toolbar.className = "message-toolbar";
    const title = document.createElement("strong");
    title.textContent = `Message ${i + 1}`;
    const count = document.createElement("small");
    count.className = "message-count";
    const actions = document.createElement("div");
    actions.className = "message-toolbar-actions";
    toolbar.append(title, count, actions);
    const editorBody = document.createElement("div");
    editorBody.className = "message-editor-body";
    const field = document.createElement("div");
    field.className = "message-field";
    const ta = document.createElement("textarea");
    ta.rows = 1;
    ta.placeholder = "Compose message\u2026";
    ta.value = value;
    ta.setAttribute("aria-label", `Message ${i + 1}`);
    function update() {
      const info = validateMessage2(ta.value);
      if (!info.valid) {
        const chunks = splitMessages2([ta.value]), next = [...messageValues()];
        next.splice(i, 1, ...chunks);
        renderMessages(next);
        const continuation = messagesEl.querySelectorAll(".message textarea")[i + chunks.length - 1];
        continuation?.focus();
        continuation?.setSelectionRange(continuation.value.length, continuation.value.length);
        return;
      }
      clampMessage(ta);
      resizeMessage(ta);
      count.textContent = `${info.bytes}/${MAX_POST_BYTES2} bytes`;
      count.classList.toggle("error", !info.valid);
      renderPreview();
    }
    ;
    ta.addEventListener("input", update);
    ta.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") $("sendButton").click();
    });
    const arrangement = document.createElement("div");
    arrangement.className = "message-rail";
    arrangement.setAttribute("role", "group");
    arrangement.setAttribute("aria-label", `Arrange message ${i + 1}`);
    const controls = document.createElement("div");
    controls.className = "message-actions";
    controls.setAttribute("role", "group");
    controls.setAttribute("aria-label", `Controls for message ${i + 1}`);
    const makeControl = (action, label, text, disabled = false) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "quiet message-control";
      if (action === "remove") button.classList.add("danger");
      button.dataset.action = action;
      button.textContent = text;
      button.title = label;
      button.setAttribute("aria-label", label);
      button.disabled = disabled;
      return button;
    };
    const add = makeControl("add", "Add message below", "+");
    add.onclick = () => {
      const next = [...messageValues()];
      next.splice(i + 1, 0, "");
      renderMessages(next);
      messagesEl.querySelectorAll(".message textarea")[i + 1]?.focus();
    };
    const remove = makeControl("remove", "Remove this message", "\u2212", values.length === 1);
    remove.onclick = () => {
      if (values.length === 1) return;
      const next = messageValues().filter((_, n) => n !== i);
      renderMessages(next);
      messagesEl.querySelectorAll(".message textarea")[Math.min(i, next.length - 1)]?.focus();
    };
    const up = makeControl("up", "Move message up", "\u2191", i === 0);
    up.onclick = () => {
      moveMessage(i, i - 1);
      messagesEl.querySelectorAll(".message")[i - 1]?.querySelector('[data-action="up"]')?.focus({ preventScroll: true });
    };
    const down = makeControl("down", "Move message down", "\u2193", i === values.length - 1);
    down.onclick = () => {
      moveMessage(i, i + 1);
      messagesEl.querySelectorAll(".message")[i + 1]?.querySelector('[data-action="down"]')?.focus({ preventScroll: true });
    };
    arrangement.append(up, down);
    controls.append(remove, add);
    field.append(ta);
    editorBody.append(arrangement, field, controls);
    editor.append(toolbar, editorBody);
    row.append(editor);
    messagesEl.append(row);
    update();
  });
}
var serverLists = {};
function lists() {
  return serverLists;
}
async function loadServerLists() {
  try {
    const response = await fetch("/publisher-api/lists");
    const data = await response.json();
    serverLists = Object.fromEntries((data.lists || []).map((item) => [item.name, item.messages]));
    renderSaved();
  } catch (error) {
    $("sendStatus").textContent = `Could not load saved pages: ${error.message}`;
  }
}
async function saveServerList(name, messages) {
  const response = await fetch("/publisher-api/lists", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, messages }) });
  if (!response.ok) throw Error((await response.json()).error || "save failed");
  const data = await response.json();
  serverLists = Object.fromEntries((data.lists || []).map((item) => [item.name, item.messages]));
  renderSaved();
}
async function deleteServerList(name) {
  const response = await fetch("/publisher-api/lists", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
  if (!response.ok) throw Error((await response.json()).error || "delete failed");
  const data = await response.json();
  serverLists = Object.fromEntries((data.lists || []).map((item) => [item.name, item.messages]));
  renderSaved();
}
var deletingPageName = "";
function deletePageDialog() {
  if ($("deletePageDialog")) return $("deletePageDialog");
  const dialog = document.createElement("dialog");
  dialog.id = "deletePageDialog";
  dialog.className = "modal";
  dialog.innerHTML = '<form method="dialog"><button class="modal-close" type="button" aria-label="Close">\xD7</button><p class="eyebrow">DELETE SAVED PAGE</p><h2>Delete this page?</h2><p id="deletePageWarning" class="muted"></p><p id="deletePageError" class="error-message" role="alert"></p><div class="actions"><button id="deletePageCancel" class="quiet" type="button">Cancel</button><button id="deletePageConfirm" class="danger" type="submit">Delete page</button></div></form>';
  document.body.append(dialog);
  $("deletePageDialog").querySelector(".modal-close").onclick = () => $("deletePageDialog").close();
  $("deletePageCancel").onclick = () => $("deletePageDialog").close();
  $("deletePageConfirm").onclick = async (event) => {
    event.preventDefault();
    const name = deletingPageName;
    if (!name) return;
    const button = $("deletePageConfirm");
    button.disabled = true;
    $("deletePageError").textContent = "";
    try {
      await deleteServerList(name);
      if ($("pageName").value === name) $("pageName").value = "";
      $("sendStatus").textContent = `Deleted \u201C${name}\u201D.`;
      dialog.close();
    } catch (error) {
      $("deletePageError").textContent = `Delete failed: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  };
  return dialog;
}
function confirmPageDelete(name) {
  const dialog = deletePageDialog();
  deletingPageName = name;
  $("deletePageWarning").textContent = `Delete \u201C${name}\u201D? This cannot be undone.`;
  $("deletePageError").textContent = "";
  dialog.showModal();
}
function renderSaved() {
  const root = $("savedLists");
  root.replaceChildren();
  const data = lists(), names = Object.keys(data).sort((a, b) => a.localeCompare(b));
  if (!names.length) {
    root.innerHTML = '<p class="status">No saved pages yet.</p>';
    return;
  }
  names.forEach((name) => {
    const row = document.createElement("div");
    row.className = "saved-item";
    const info = document.createElement("span");
    info.textContent = name;
    const count = document.createElement("small");
    count.textContent = `${data[name].length} messages`;
    const left = document.createElement("div");
    left.append(info, document.createTextNode(" \xB7 "), count);
    const actions = document.createElement("div");
    actions.className = "actions";
    const load = document.createElement("button");
    load.className = "quiet load-button";
    load.textContent = "Load";
    load.onclick = () => {
      renderMessages(data[name]);
      $("pageName").value = name;
      $("sendStatus").textContent = `Loaded \u201C${name}\u201D.`;
    };
    const exportButton = document.createElement("button");
    exportButton.className = "quiet export-button";
    exportButton.textContent = "Export";
    exportButton.onclick = () => exportPage(name, data[name]);
    const deleteButton = document.createElement("button");
    deleteButton.className = "quiet danger";
    deleteButton.textContent = "Delete";
    deleteButton.onclick = () => confirmPageDelete(name);
    actions.append(load, exportButton, deleteButton);
    row.append(left, actions);
    root.append(row);
  });
}
function exportPage(name, messages) {
  const blob = new Blob([JSON.stringify({ version: 1, pages: { [name]: messages } }, null, 2)], { type: "application/json" }), a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${name.replace(/[^a-z0-9_-]+/gi, "_")}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}
function updateButtons() {
  const connected = !!publisher, roomName = $("roomServerSelect").selectedOptions[0]?.textContent || "no room selected", badge = $("connectionBadge");
  badge.classList.toggle("connected", connected);
  badge.classList.toggle("disconnected", !connected);
  badge.title = connected ? "Radio connected" : "Radio disconnected";
  badge.setAttribute("aria-label", badge.title);
  $("railConnectionStatus").textContent = connected ? `${loggedIn ? "Logged in" : "Using MeshPage Publisher radio"} \xB7 ${roomName}` : "MeshPage Publisher radio unavailable";
  $("connectionOpenButton").textContent = "Choose room server";
  $("refreshServersButton").disabled = !connected;
  $("loginButton").disabled = !connected || loggedIn || running;
  $("sendButton").disabled = !connected || !loggedIn || running;
  for (const button of document.querySelectorAll(".move-up,.move-down,.remove")) button.disabled = running;
}
async function loadRoomServers() {
  if (!publisher) return;
  $("serverListStatus").textContent = "Reading room servers from radio contacts\u2026";
  $("refreshServersButton").disabled = true;
  try {
    const servers = await publisher.roomServers(), select = $("roomServerSelect");
    select.replaceChildren(new Option(servers.length ? "Select a room server\u2026" : "No room servers found", ""));
    for (const server of servers) select.add(new Option(`${server.name} \xB7 ${server.publicKeyHex.slice(0, 12)}\u2026`, server.publicKeyHex));
    $("serverListStatus").textContent = servers.length ? `Found ${servers.length} room server(s) in radio contacts.` : "No advertised room-server nodes in radio contacts. Add/refresh the node in MeshCore, or enter its key manually.";
    if (servers.length === 1) select.value = servers[0].publicKeyHex;
    updateButtons();
  } catch (e) {
    $("serverListStatus").textContent = `Could not read radio contacts: ${e.message}`;
  }
  $("refreshServersButton").disabled = !publisher;
}
$("refreshServersButton").onclick = loadRoomServers;
$("roomServerSelect").onchange = () => {
  $("roomInput").value = $("roomServerSelect").value;
  loggedIn = false;
  $("loginStatus").textContent = $("roomInput").value ? "Room server selected. Log in to continue." : "Select a room server.";
  updateButtons();
};
async function connect() {
  publisher = new TowncrierPublisher();
  updateButtons();
  $("connectionStatus").textContent = "Using the USB radio configured in Towncrier.";
  $("loginStatus").textContent = "Not logged in.";
  await loadRoomServers();
}
$("connectionOpenButton").onclick = () => $("connectionDialog").showModal();
var selected = () => $("roomServerSelect").value || $("roomInput").value.trim();
$("loginButton").onclick = async () => {
  const key = selected(), password = $("roomPassword").value;
  if (!key) {
    $("loginStatus").textContent = "Select or enter a room-server key.";
    return;
  }
  if (!password) {
    $("loginStatus").textContent = "Enter the room password.";
    return;
  }
  $("loginButton").disabled = true;
  $("loginStatus").textContent = "Sending login request\u2026";
  try {
    const response = await publisher.login(key, password);
    loggedIn = true;
    $("loginStatus").textContent = `Logged in successfully (permissions ${response.permissions ?? response.role ?? "confirmed"}).`;
    $("roomPassword").value = "";
    updateButtons();
  } catch (e) {
    loggedIn = false;
    $("loginStatus").textContent = `Login failed or timed out: ${e.message || "check node key/password and radio reachability"}`;
    updateButtons();
  }
};
$("roomInput").oninput = () => {
  loggedIn = false;
  $("roomServerSelect").value = "";
  updateButtons();
  $("loginStatus").textContent = "Room server changed. Log in again.";
};
$("previewButton").onclick = () => {
  renderPreview();
  $("previewDialog").showModal();
  updatePreviewScale();
};
$("phoneSizeSelect").onchange = updatePreviewScale;
$("previewCloseButton").onclick = () => $("previewDialog").close();
window.addEventListener("resize", () => {
  if ($("previewDialog").open) updatePreviewScale();
});
$("saveButton").onclick = async () => {
  const name = $("pageName").value.trim();
  if (!name) {
    $("pageName").focus();
    return;
  }
  try {
    await saveServerList(name, messageValues());
    window.towncrierFlashSaved?.($("saveButton"));
    $("sendStatus").textContent = `Saved \u201C${name}\u201D on the server.`;
  } catch (error) {
    $("sendStatus").textContent = `Save failed: ${error.message}`;
  }
};
$("importInput").onchange = async (e) => {
  try {
    const file = e.target.files[0];
    if (!file) return;
    const obj = JSON.parse(await file.text()), imported = obj.pages || obj;
    if (!imported || typeof imported !== "object" || Array.isArray(imported)) throw Error();
    for (const [k, v] of Object.entries(imported)) {
      if (!Array.isArray(v) || !v.every((x) => typeof x === "string")) throw Error();
      await saveServerList(k, v);
    }
    $("sendStatus").textContent = "Pages imported.";
  } catch {
    $("sendStatus").textContent = "Import failed: expected a JSON object of named string pages.";
  }
  e.target.value = "";
};
$("stopButton").onclick = () => {
  stopRequested = true;
  $("sendStatus").textContent = "Stopping after the current transmission\u2026";
};
$("sendButton").onclick = async () => {
  if (!publisher || !loggedIn) return;
  const key = $("roomServerSelect").value || $("roomInput").value.trim(), texts = messageValues().filter((text) => text.trim());
  if (!texts.length) {
    $("sendStatus").textContent = "Add at least one non-empty message.";
    return;
  }
  const invalid = texts.findIndex((text) => !validateMessage2(text).valid);
  if (invalid !== -1) {
    $("sendStatus").textContent = `Message ${invalid + 1} exceeds ${MAX_POST_BYTES2} UTF-8 bytes. Shorten it before publishing.`;
    messagesEl.querySelectorAll("textarea")[invalid]?.focus();
    return;
  }
  running = true;
  stopRequested = false;
  updateButtons();
  $("stopButton").disabled = false;
  $("progressBar").style.width = "0%";
  $("sendStatus").textContent = `Posting ${texts.length} message(s) to the authenticated room server\u2026`;
  try {
    await sendInOrder(texts, (text) => publisher.sendMessage(key, text), (ms) => new Promise((r) => setTimeout(r, ms)), secondsToMilliseconds($("delayInput").value), () => stopRequested, (done, total) => {
      $("progressBar").style.width = `${Math.round(done / total * 100)}%`;
      $("sendStatus").textContent = `Radio accepted ${done} of ${total} message(s).`;
    });
    $("sendStatus").textContent = `Radio accepted ${texts.length} message(s) for transmission.`;
  } catch (e) {
    $("sendStatus").textContent = e.message === "Queue stopped." ? "Queue stopped." : `Posting failed: ${e.message || "radio/server error"}`;
  } finally {
    running = false;
    $("stopButton").disabled = true;
    updateButtons();
  }
};
function buildScheduleDialog() {
  if ($("scheduleDialog")) return $("scheduleDialog");
  const dialog = document.createElement("dialog");
  dialog.id = "scheduleDialog";
  dialog.className = "modal";
  dialog.innerHTML = '<form id="scheduleForm"><button class="modal-close" type="button" aria-label="Close">\xD7</button><p class="eyebrow">SCHEDULE THIS SEQUENCE</p><h2 id="scheduleDialogTitle">Add to schedule</h2><label class="field">Schedule name<input id="scheduleName" required maxlength="80"></label><label class="field">Radio channels<select id="scheduleChannels" multiple size="6" required></select></label><fieldset><legend>Post at these local times each day</legend><div id="schedulePostTimes"></div><button class="quiet" id="addPostTime" type="button">Add a time</button></fieldset><div class="schedule-fields"><label class="field">Delay between messages (seconds)<input id="scheduleDelay" type="number" min="0" max="3600" value="2" required></label></div><p class="muted">Towncrier posts once at each selected time every day. Editing preserves the pending next post time.</p><p id="scheduleError" class="error-message" role="alert"></p><div class="actions"><button class="quiet" id="scheduleCancel" type="button">Cancel</button><button class="primary" id="scheduleSave" type="submit">Save schedule</button></div></form>';
  document.body.append(dialog);
  $("scheduleDialog").querySelector(".modal-close").onclick = () => $("scheduleDialog").close();
  $("scheduleCancel").onclick = () => $("scheduleDialog").close();
  $("scheduleForm").onsubmit = saveComposedSchedule;
  return dialog;
}
var editingScheduleId = null;
var editingScheduleChannels = [];
async function loadScheduleChannels(selected2 = []) {
  const select = $("scheduleChannels");
  select.replaceChildren();
  let channels = [];
  try {
    channels = (await publisher.request("channels")).channels || [];
  } catch {
  }
  if (!channels.length) channels = Array.from({ length: 40 }, (_, index) => ({ index, name: "", configured: false }));
  for (const channel of channels) {
    const option = new Option(`${channel.index} \xB7 ${channel.name || `Channel ${channel.index}`}${channel.configured === false ? " (may be empty)" : ""}`, String(channel.index));
    option.selected = selected2.includes(channel.index);
    select.add(option);
  }
}
async function openScheduleDialog() {
  buildScheduleDialog();
  $("scheduleError").textContent = "";
  if (!editingScheduleId && !$("schedulePostTimes").children.length) addPostTime("09:00");
  await loadScheduleChannels(editingScheduleId ? editingScheduleChannels : []);
  $("scheduleName").value = editingScheduleId ? $("scheduleName").value : "New schedule";
  $("scheduleDialogTitle").textContent = editingScheduleId ? "Edit schedule" : "Add to schedule";
  $("scheduleSave").textContent = editingScheduleId ? "Save changes" : "Save schedule";
  $("scheduleDialog").showModal();
}
function addPostTime(value = "") {
  const row = document.createElement("div");
  row.className = "schedule-time-row";
  const input = document.createElement("input");
  input.type = "time";
  input.required = true;
  input.className = "schedule-post-time";
  input.value = value;
  input.setAttribute("aria-label", "Daily post time");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "quiet danger";
  remove.textContent = "Remove";
  remove.onclick = () => row.remove();
  row.append(input, remove);
  $("schedulePostTimes").append(row);
}
if ($("addPostTime")) $("addPostTime").onclick = () => addPostTime();
async function saveComposedSchedule(event) {
  event.preventDefault();
  const messages = messageValues().filter((text) => text.trim()), channels = [...$("scheduleChannels").selectedOptions].map((option) => Number(option.value)), post_times = [...document.querySelectorAll(".schedule-post-time")].map((input) => input.value).filter(Boolean);
  if (!messages.length) {
    $("scheduleError").textContent = "Add at least one non-empty message.";
    return;
  }
  if (!channels.length) {
    $("scheduleError").textContent = "Select at least one radio channel.";
    return;
  }
  if (!post_times.length) {
    $("scheduleError").textContent = "Add at least one daily post time.";
    return;
  }
  const payload = { name: $("scheduleName").value.trim(), channels, messages, interval: 1800, message_delay: Number($("scheduleDelay").value), post_times, enabled: true };
  if (editingScheduleId) payload.id = editingScheduleId;
  $("scheduleSave").disabled = true;
  $("scheduleError").textContent = "Saving schedule\u2026";
  try {
    const result = await publisher.request("schedule", payload);
    editingScheduleId = result.id;
    history.replaceState({}, "", `/publisher?schedule=${result.id}`);
    $("scheduleError").textContent = "Saved. This same message sequence is now scheduled.";
    $("sendStatus").textContent = "Schedule saved. You can still publish this sequence to a room from here.";
    window.towncrierFlashSaved?.($("scheduleSave"));
    setTimeout(() => $("scheduleDialog").close(), 500);
  } catch (error) {
    $("scheduleError").textContent = error.message;
  } finally {
    $("scheduleSave").disabled = false;
  }
}
async function loadScheduleForComposer() {
  const query = new URLSearchParams(location.search), scheduleId = query.get("schedule");
  if (!scheduleId) {
    if (query.get("mode") === "schedule") openScheduleDialog();
    return;
  }
  try {
    const schedule = await publisher.request(`schedule?id=${encodeURIComponent(scheduleId)}`);
    editingScheduleId = schedule.id;
    editingScheduleChannels = schedule.channels || [];
    renderMessages(schedule.messages);
    $("delayInput").value = Math.max(1, schedule.message_delay || 2);
    buildScheduleDialog();
    $("scheduleName").value = schedule.name;
    $("scheduleDelay").value = schedule.message_delay || 0;
    $("schedulePostTimes").replaceChildren();
    for (const value of schedule.post_times || [schedule.send_at || "09:00"]) addPostTime(value);
    await loadScheduleChannels(schedule.channels);
    $("scheduleDialogTitle").textContent = "Edit scheduled sequence";
    $("scheduleSave").textContent = "Save changes";
    $("sendStatus").textContent = "Scheduled sequence loaded. Review or edit the messages, then choose Add to schedule to save changes.";
  } catch (error) {
    $("sendStatus").textContent = `Could not load schedule: ${error.message}`;
  }
}
if ($("scheduleOpenButton")) $("scheduleOpenButton").onclick = () => openScheduleDialog();
renderMessages([""]);
loadServerLists();
connect().then(loadScheduleForComposer);
