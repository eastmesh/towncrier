export const MAX_POST_BYTES = 151;
const encoder = new TextEncoder();
export function validateMessage(text, limit = MAX_POST_BYTES) {
  const bytes = encoder.encode(String(text)).length;
  return {bytes, limit, valid: bytes <= limit, remaining: limit - bytes};
}
export function truncateUtf8(text, maxBytes = MAX_POST_BYTES) {
  const limit = Math.max(0, Number(maxBytes) || 0);
  let bytes = 0, result = '';
  for (const character of String(text)) {
    const size = encoder.encode(character).length;
    if (bytes + size > limit) break;
    result += character;
    bytes += size;
  }
  return result;
}
export function secondsToMilliseconds(seconds) {
  const value = Number(seconds);
  if (!Number.isInteger(value) || value < 1) throw new Error('Delay must be a whole number of seconds.');
  return value * 1000;
}
export function insertEmojiAtSelection(text, start, end, emoji) {
  const value = String(text);
  const from = Math.max(0, Math.min(value.length, Number(start) || 0));
  const to = Math.max(from, Math.min(value.length, Number(end) || 0));
  return {value:value.slice(0,from)+emoji+value.slice(to),cursor:from+String(emoji).length};
}
export function buildPreviewMessages(messages) {
  return messages.map((text,index)=>({number:index+1,text:String(text),bytes:validateMessage(text).bytes}));
}
export function reorderMessages(messages, from, to) {
  if (from < 0 || from >= messages.length || to < 0 || to >= messages.length) return [...messages];
  const next = [...messages], [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

export function splitMessages(values) {
  return values.map(value => String(value)).filter(value => value.trim());
}

export function normalizeRoomTarget(target) {
  if (target?.kind !== 'room-server') throw new Error('Choose a room-server node.');
  const hex = String(target.publicKey || '').replace(/[^0-9a-f]/gi, '');
  if (hex.length !== 64) throw new Error('Room-server public key must be 64 hexadecimal characters.');
  return Uint8Array.from(hex.match(/.{2}/g), byte => parseInt(byte, 16));
}

export async function sendInOrder(messages, send, wait = ms => new Promise(resolve => setTimeout(resolve, ms)), delayMs = 1500, isStopped = () => false, onProgress = () => {}) {
  const delay = Math.max(250, Number(delayMs) || 1500);
  for (let i = 0; i < messages.length; i++) {
    if (isStopped()) throw new Error('Queue stopped.');
    await send(messages[i]);
    onProgress(i + 1, messages.length);
    if (i < messages.length - 1) await wait(delay);
  }
}

export class TowncrierPublisher {
  async request(path, body) {
    const response = await fetch(`/publisher-api/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      headers: body === undefined ? {} : {'Content-Type':'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Towncrier request failed (${response.status}).`);
    return result;
  }
  async roomServers() { return (await this.request('contacts')).rooms; }
  async login(publicKey, password) {
    return this.request('login', {public_key:publicKey,password});
  }
  async sendMessage(publicKey, text) {
    const size = validateMessage(text);
    if (!size.valid) throw new Error(`Message exceeds the room-server limit of ${size.limit} UTF-8 bytes (${size.bytes} bytes).`);
    return this.request('publish', {public_key:publicKey,text});
  }
  async close() {}
}
