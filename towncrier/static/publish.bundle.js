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

// towncrier/static/publish.js
var $ = (id) => document.getElementById(id);
var publisher = null;
var loggedIn = false;
var pages = {};
function update() {
  const ready = loggedIn && !!$("pageSelect").value && !$("roomServerSelect").value ? false : true;
  $("connectionBadge").textContent = publisher ? "MeshPage Publisher radio" : "Disconnected";
  $("connectionBadge").classList.toggle("connected", !!publisher);
  $("publishButton").disabled = !publisher || !loggedIn || !$("pageSelect").value;
}
async function loadPages() {
  const data = await (await fetch("/publisher-api/lists")).json();
  pages = Object.fromEntries((data.lists || []).map((item) => [item.name, item.messages]));
  const select = $("pageSelect");
  select.replaceChildren(new Option("Select a saved page\u2026", ""));
  for (const name of Object.keys(pages).sort()) select.add(new Option(`${name} \xB7 ${pages[name].length} messages`, name));
  select.onchange = () => {
    $("pageSummary").textContent = select.value ? `${pages[select.value].length} message(s) ready to publish.` : "Select a saved page to see its messages.";
    update();
  };
}
async function loadRooms() {
  const data = await (await fetch("/publisher-api/contacts")).json(), select = $("roomServerSelect");
  select.replaceChildren(new Option("Select a room server\u2026", ""));
  for (const room of data.rooms || []) select.add(new Option(`${room.name || "Room server"} \xB7 ${room.publicKeyHex.slice(0, 12)}\u2026`, room.publicKeyHex));
  update();
}
async function connect() {
  publisher = new TowncrierPublisher();
  await loadPages();
  try {
    await loadRooms();
  } catch (e) {
    $("loginStatus").textContent = `Could not load rooms: ${e.message}`;
  }
  update();
}
$("refreshServersButton").onclick = loadRooms;
$("loginButton").onclick = async () => {
  const key = $("roomServerSelect").value, password = $("roomPassword").value;
  if (!key || !password) {
    $("loginStatus").textContent = "Select a room and enter its password.";
    return;
  }
  $("loginStatus").textContent = "Logging in\u2026";
  try {
    const r = await (await fetch("/publisher-api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ public_key: key, password }) })).json();
    if (!r || r.error) throw Error(r?.error || "login failed");
    loggedIn = true;
    $("roomPassword").value = "";
    $("loginStatus").textContent = "Logged in successfully.";
  } catch (e) {
    loggedIn = false;
    $("loginStatus").textContent = `Login failed: ${e.message}`;
  }
  update();
};
$("publishButton").onclick = async () => {
  const key = $("roomServerSelect").value, texts = pages[$("pageSelect").value] || [], bad = texts.findIndex((t) => !validateMessage(t).valid);
  if (bad >= 0) {
    $("publishStatus").textContent = `Message ${bad + 1} exceeds ${MAX_POST_BYTES} UTF-8 bytes.`;
    return;
  }
  $("publishButton").disabled = true;
  $("progressBar").style.width = "0%";
  $("publishStatus").textContent = `Publishing ${texts.length} message(s)\u2026`;
  try {
    await sendInOrder(texts, (text) => fetch("/publisher-api/publish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ public_key: key, text }) }).then(async (r) => {
      const x = await r.json();
      if (!r.ok) throw Error(x.error || "publish failed");
    }), (ms) => new Promise((r) => setTimeout(r, ms)), secondsToMilliseconds($("delayInput").value), () => false, (done, total) => {
      $("progressBar").style.width = `${Math.round(done / total * 100)}%`;
    });
    $("publishStatus").textContent = "Page published to the room.";
  } catch (e) {
    $("publishStatus").textContent = `Publishing failed: ${e.message}`;
  } finally {
    update();
  }
};
connect().catch((e) => $("loginStatus").textContent = e.message);
