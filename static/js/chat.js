// chat.js — flujo de chat: envío POST /api/chat, streaming SSE, burbujas usuario/persona, cancel.
import { state } from "./state.js";
import { refreshContextUsage } from "./context.js";
import { toast, avatarEl } from "./utils.js";
import {
  feedAudioChunk,
  onTTSEvent,
  playChunkB64,
  playChunksB64,
  setActiveTTSMessage,
  ttsReady,
} from "./tts.js";
import {
  makeDeleteButton,
  attachSavedAudio,
  playSavedSequence,
  applyAudioMode,
  beginMessageEdit,
  stopHistoryAudio,
  showEmptyState,
} from "./persistence.js";

let initialized = false;
let ta = null;
let btnSend = null;
let btnImage = null;
let imageInput = null;
let imagePreviewEl = null;
let messagesEl = null;
let current = null;
// imagen adjunta pendiente (boton / ctrl+V / drag&drop): viaja con el
// proximo send y se limpia despues
let pendingImage = null;
let pendingImageDataUrl = "";
// burbujas por message_id: el audio de una oracion puede llegar cuando
// current ya apunta a la siguiente persona (la sintesis va atrasada)
let messageBubbles = new Map();
// karaoke: message_id -> burbuja con spans .kw. VIVE mas que messageBubbles
// (se limpia en finishStream) porque el audio suena despues del stream.
let wordBubbles = new Map();
// generacion por stream: al volver el boton a "enviar" con text_done se
// puede mandar un mensaje nuevo mientras la ronda anterior sigue entregando
// audio; solo la generacion vigente controla el boton y la limpieza global.
let streamGen = 0;

function removeEmptyState() {
  const es = messagesEl.querySelector(".empty-state");
  if (es) es.remove();
}

function nearBottom() {
  return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
}

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function resizeTextarea() {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 90) + "px";
}

function addUserBubble(text, messageId, imageUrl) {
  removeEmptyState();
  const msg = document.createElement("div");
  msg.className = "msg user";
  const av = document.createElement("div");
  av.className = "msg-avatar";
  av.style.cssText = "background: var(--bg2); color: var(--text-muted);";
  const ai = document.createElement("i");
  ai.className = "ti ti-user";
  ai.style.fontSize = "14px";
  av.appendChild(ai);
  const body = document.createElement("div");
  body.className = "msg-body";
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = "Tú";
  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";
  bubble.textContent = text;
  if (imageUrl) {
    const img = document.createElement("img");
    img.className = "msg-image";
    img.src = imageUrl;
    img.alt = "";
    bubble.appendChild(img);
  }
  bubble.title = "Doble click para editar";
  bubble.addEventListener("dblclick", () => beginMessageEdit(msg, bubble, messageId, state.room));
  const getText = () => {
    const first = bubble.firstChild;
    return first && first.nodeType === Node.TEXT_NODE ? first.nodeValue : text;
  };
  bubble.appendChild(
    makeMsgFoot(msg, messageId, state.room, [
      makeCopyButton(getText),
      makeReprocessButton(msg, bubble, messageId, state.room),
    ]),
  );
  body.append(meta, bubble);
  msg.append(av, body);
  messagesEl.appendChild(msg);
  scrollBottom();
}

function startPersonaBubble(name) {
  removeEmptyState();
  const persona = state.personas.find((p) => p.name === name);
  const msg = document.createElement("div");
  msg.className = "msg";
  const av = avatarEl(persona || { name, avatar_color: null }, "msg-avatar");
  const body = document.createElement("div");
  body.className = "msg-body";
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = name;
  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";
  const dots = document.createElement("div");
  dots.className = "typing-dots";
  for (let i = 0; i < 3; i++) {
    const d = document.createElement("div");
    d.className = "dot";
    dots.appendChild(d);
  }
  // span (no TextNode): el karaoke inserta spans .kw entre los tokens
  const textEl = document.createElement("span");
  textEl.className = "msg-text";
  const cursor = document.createElement("span");
  cursor.className = "cursor-blink";
  bubble.append(dots, textEl, cursor);
  body.append(meta, bubble);
  msg.append(av, body);
  messagesEl.appendChild(msg);
  scrollBottom();
  return {
    rootEl: msg,
    bodyEl: body,
    bubble,
    textEl,
    dots,
    cursor,
    persona: name,
    final: false,
    messageId: null,
    sounds: [],
    soundsEl: null,
  };
}

// Karaoke: re-renderiza b.textEl con spans .kw en las palabras de las
// oraciones que llegaron con words (b.wordsBySentence: sentence_id ->
// {sentence, words}). Idempotente: se vuelve a correr con cada oracion
// nueva, sobre el texto parcial que ya hay (la sintesis va atrasada y el
// mensaje puede seguir streamando).
function wrapSentenceWords(b) {
  if (!b.wordsBySentence || !b.wordsBySentence.size) return;
  const full = b.textEl.textContent;
  const ranges = [];
  for (const [sid, entry] of b.wordsBySentence) {
    const idx = full.indexOf(entry.sentence);
    if (idx < 0) continue;
    const words = entry.words;
    // tokens de la oracion tal como aparecen en el mensaje (el server
    // alineo word a word sobre el mismo texto, menos la puntuacion)
    const parts = entry.sentence.split(/(\s+)/);
    let off = idx;
    let wi = 0;
    for (const part of parts) {
      if (!part) continue;
      if (/\s/.test(part[0])) {
        off += part.length;
        continue;
      }
      if (wi < words.length) ranges.push({ start: off, end: off + part.length, sid, w: wi });
      off += part.length;
      wi++;
    }
  }
  if (!ranges.length) return;
  ranges.sort((x, y) => x.start - y.start);
  b.textEl.textContent = "";
  const spans = new Map();
  let pos = 0;
  for (const r of ranges) {
    if (r.start < pos) continue;
    if (r.start > pos) b.textEl.append(full.slice(pos, r.start));
    const sp = document.createElement("span");
    sp.className = "kw";
    sp.textContent = full.slice(r.start, r.end);
    b.textEl.appendChild(sp);
    if (!spans.has(r.sid)) spans.set(r.sid, []);
    spans.get(r.sid)[r.w] = sp;
    pos = r.end;
  }
  if (pos < full.length) b.textEl.append(full.slice(pos));
  b.wordSpans = spans;
}

function clearKwActive(b) {
  if (b.activeKw) b.activeKw.classList.remove("kw-active");
  b.activeKw = null;
}

// boton de play por oracion (como TalkWithMe): reproduce el audio que ya
// llego por SSE, sin volver a sintetizar
function addSentenceSound(b, ev) {
  if (!ev.audio) return;
  if (!ttsReady()) return;
  b.sounds.push(ev);
  if (ev.words && ev.words.length && b.messageId) {
    if (!b.wordsBySentence) b.wordsBySentence = new Map();
    b.wordsBySentence.set(ev.sentence_id, { sentence: ev.text, words: ev.words });
    wrapSentenceWords(b);
    wordBubbles.set(b.messageId, b);
  }
  const follow = nearBottom();
  if (!b.soundsEl) {
    b.soundsEl = document.createElement("div");
    b.soundsEl.className = "msg-sounds";
    const actions = b.bodyEl.querySelector(".msg-actions");
    if (actions) actions.prepend(b.soundsEl);
    else b.bodyEl.appendChild(b.soundsEl);
    // play all: primer boton de la fila; lee b.sounds en el click, asi
    // incluye las oraciones que lleguen despues (la sintesis va atrasada)
    const all = document.createElement("button");
    all.className = "msg-sound-btn msg-sound-btn-all";
    all.title = "Play all";
    all.setAttribute("aria-label", "Reproducir todo el mensaje en orden");
    const ai = document.createElement("i");
    ai.className = "ti ti-player-play-filled";
    all.appendChild(ai);
    all.addEventListener("click", () => playChunksB64(b.sounds, b.persona));
    b.soundsEl.appendChild(all);
  }
  const btn = document.createElement("button");
  btn.className = "msg-sound-btn";
  btn.title = ev.text || "Oración";
  btn.setAttribute("aria-label", "Reproducir oración: " + (ev.text || ""));
  const ico = document.createElement("i");
  ico.className = "ti ti-player-play";
  btn.appendChild(ico);
    const b64 = ev.audio;
    btn.addEventListener("click", () =>
      playChunkB64(b64, ev.persona, {
        words: ev.words || null,
        messageId: b.messageId,
        sentenceId: ev.sentence_id,
      }),
    );
  b.soundsEl.appendChild(btn);
  if (follow) scrollBottom();
}

function appendToken(b, token) {
  if (!b || b.final) return;
  if (b.dots) {
    b.dots.remove();
    b.dots = null;
  }
  b.textEl.append(token);
  if (nearBottom()) scrollBottom();
}

// Pie de tokens dentro de la burbuja (usage del stream del LLM).
export function tokenFooterText(tokens) {
  const parts = [];
  if (typeof tokens.completion === "number") parts.push(tokens.completion + " tok");
  if (typeof tokens.per_second === "number")
    parts.push(tokens.per_second.toFixed(1) + " tok/s");
  return parts.join(" · ");
}

export function tokenFooterTitle(tokens) {
  const bits = [];
  if (typeof tokens.prompt === "number") {
    bits.push(
      "prompt " +
        tokens.prompt +
        (typeof tokens.cached === "number" ? " (cache " + tokens.cached + ")" : ""),
    );
  }
  if (typeof tokens.completion === "number")
    bits.push("completion " + tokens.completion);
  if (typeof tokens.total === "number") bits.push("total " + tokens.total);
  if (typeof tokens.prompt_ms === "number")
    bits.push("prompt " + Math.round(tokens.prompt_ms) + "ms");
  return bits.join(" · ");
}

// Boton de copiar (mismo que el que habia en .msg-actions, ahora en la
// barra interior de la burbuja).
export function makeCopyButton(getText) {
  const copy = document.createElement("button");
  copy.className = "msg-act msg-act-copy";
  copy.title = "Copiar";
  copy.setAttribute("aria-label", "Copiar mensaje");
  const ci = document.createElement("i");
  ci.className = "ti ti-copy";
  copy.appendChild(ci);
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(getText());
      toast("Copiado", "success");
    } catch {
      toast("No se pudo copiar", "error");
    }
  });
  return copy;
}

// Span con los tokens de la respuesta (usage del stream del LLM).
export function makeTokensSpan(tokens) {
  if (!tokens) return null;
  const text = tokenFooterText(tokens);
  if (!text) return null;
  const s = document.createElement("span");
  s.className = "msg-foot-tokens";
  s.textContent = text;
  s.title = tokenFooterTitle(tokens);
  return s;
}

// Barra interior de la burbuja (el bloque separado): [leftEls…] [borrar]
// borrar va a la derecha (margin-left:auto de .msg-act-delete).
export function makeMsgFoot(msgEl, messageId, room, leftEls, onDeleted) {
  const foot = document.createElement("div");
  foot.className = "msg-foot";
  for (const el of leftEls) foot.appendChild(el);
  foot.appendChild(makeDeleteButton(messageId, room, msgEl, onDeleted));
  return foot;
}

// Boton de reprocesar (mensajes de usuario): rewind desde este mensaje —
// lo borra junto con todo lo posterior y re-envia el texto para que la
// room vuelva a responder.
export function makeReprocessButton(msgEl, bubbleEl, messageId, room, disabled = false) {
  const btn = document.createElement("button");
  btn.className = "msg-act msg-act-reproc";
  btn.title = disabled
    ? "Este mensaje ya está dentro del resumen de compactación (no se puede reprocesar)"
    : "Reprocesar desde este mensaje (borra esto y todo lo que viene después)";
  btn.setAttribute("aria-label", "Reprocesar desde este mensaje");
  const ico = document.createElement("i");
  ico.className = "ti ti-refresh";
  btn.appendChild(ico);
  if (disabled) btn.disabled = true;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    reprocessFrom(msgEl, bubbleEl, messageId, room);
  });
  btn.addEventListener("dblclick", (e) => e.stopPropagation());
  return btn;
}

export async function reprocessFrom(msgEl, bubbleEl, messageId, room) {
  if (state.streaming) {
    toast("Esperá a que termine la respuesta antes de reprocesar", "warning");
    return;
  }
  if (msgEl.querySelector(".msg-edit-ta")) {
    toast("Guardá o cancelá la edición antes de reprocesar", "warning");
    return;
  }
  const first = bubbleEl.firstChild;
  const text =
    first && first.nodeType === Node.TEXT_NODE ? first.nodeValue.trim() : "";
  if (!text) {
    toast("El mensaje no tiene texto para reprocesar", "warning");
    return;
  }
  const url =
    "/api/rooms/" +
    encodeURIComponent(room) +
    "/messages/" +
    encodeURIComponent(messageId) +
    "/reprocess";
  const post = (confirmFlag) =>
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: confirmFlag }),
    });
  let res;
  try {
    res = await post(false);
  } catch (err) {
    toast("No se pudo reprocesar: " + (err && err.message ? err.message : String(err)), "error");
    return;
  }
  if (res.status === 409) {
    // hay mensajes de usuario debajo: confirmar antes del rewind
    let n = null;
    try {
      const body = await res.json();
      if (body && body.detail && typeof body.detail === "object") n = body.detail.users_after;
    } catch {}
    const info =
      n != null
        ? "Hay " + n + " mensaje" + (n === 1 ? "" : "s") + " de usuario debajo; el rewind los borrará."
        : "Hay mensajes debajo; el rewind los borrará.";
    if (!confirm(info + "\n¿Reprocesar desde este mensaje?")) return;
    try {
      res = await post(true);
    } catch (err) {
      toast("No se pudo reprocesar: " + (err && err.message ? err.message : String(err)), "error");
      return;
    }
  }
  if (!res.ok) {
    let detail = "HTTP " + res.status;
    try {
      const body = await res.json();
      if (body && body.detail !== undefined) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {}
    toast(detail, "error");
    return;
  }
  // rewind OK: limpiar el DOM desde esta burbuja en adelante y re-enviar
  stopHistoryAudio();
  let node = msgEl;
  while (node) {
    const next = node.nextSibling;
    node.remove();
    node = next;
  }
  const elMessages = document.getElementById("messages");
  if (!elMessages.querySelector(".msg")) showEmptyState(elMessages);
  refreshContextUsage();
  await doSend(text, crypto.randomUUID());
}

function finalizeBubble(b, fullText, tokens) {
  if (!b || b.final) return;
  b.final = true;
  // si el usuario seguia el scroll, el pie (tokens/audios/borrar) que se
  // anade aca no debe quedar cortado debajo del fold
  const follow = nearBottom();
  if (typeof fullText === "string") b.textEl.textContent = fullText;
  if (!b.textEl.textContent.trim()) {
    b.rootEl.remove();
    return;
  }
  if (b.dots) b.dots.remove();
  b.cursor.remove();
  // barra interior: [copiar][tokens…] [borrar]
  const left = [makeCopyButton(() => b.textEl.textContent)];
  const ts = makeTokensSpan(tokens);
  if (ts) left.push(ts);
  b.bubble.appendChild(
    makeMsgFoot(b.rootEl, b.messageId, state.room, left, () =>
      messageBubbles.delete(b.messageId),
    ),
  );
  // el re-render de fullText borro los spans .kw: si ya hay oraciones
  // alineadas, re-ajustarlas al texto final (el audio puede seguir sonando)
  if (b.wordsBySentence && b.wordsBySentence.size) wrapSentenceWords(b);
  // la fila externa solo existe si hay audio (oraciones TTS live; el
  // replay + wavs guardados los anade attachSavedAudio al llegar "complete")
  if (b.soundsEl) {
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    actions.appendChild(b.soundsEl);
    b.bodyEl.appendChild(actions);
  }
  applyAudioMode(b.rootEl);
  if (follow) scrollBottom();
}

function finishStream() {
  finalizeBubble(current, null);
  current = null;
  messageBubbles.clear();
  setActiveTTSMessage(null);
  state.streaming = false;
  updateSendButton();
}

function makeOnEvent(gen) {
  const ids = new Set();
  return (ev) => {
    if (ev.type === "start") {
      current = startPersonaBubble(ev.persona);
      current.messageId = ev.message_id;
      messageBubbles.set(ev.message_id, current);
      ids.add(ev.message_id);
      setActiveTTSMessage(ev.message_id);
    } else if (ev.type === "token") {
      appendToken(current, ev.token);
    } else if (ev.type === "done") {
      finalizeBubble(current, ev.text, ev.tokens);
      current = null;
    } else if (ev.type === "audio_chunk") {
      feedAudioChunk(ev);
      const b = messageBubbles.get(ev.message_id);
      if (b) addSentenceSound(b, ev);
    } else if (ev.type === "tts_state") {
      onTTSEvent(ev);
    } else if (ev.type === "error") {
      toast(ev.message || "Error en el chat", "error");
      if (gen === streamGen) finishStream();
    } else if (ev.type === "text_done") {
      // el texto de la ronda termino: el boton vuelve a "enviar" aunque el
      // TTS siga sintetizando/entregando audio (el boton es solo del LLM)
      if (gen === streamGen) {
        state.streaming = false;
        updateSendButton();
      }
    } else if (ev.type === "complete") {
      // anade a las burbujas de ESTE stream los audios guardados en disco
      // (fallback sin TTS); si ya hay una ronda mas nueva, no se toca el
      // estado global (su complete hara la limpieza)
      const targets = [...ids]
        .map((id) => ({ id, b: messageBubbles.get(id) }))
        .filter((t) => t.b && t.b.final);
      if (targets.length) attachSavedAudio(targets, state.room);
      if (gen === streamGen) {
        if (ev.cancelled) toast("Chat cancelado", "info");
        finishStream();
      }
      // la ronda quedo en el historial del server: contexto actualizado
      refreshContextUsage();
    }
  };
}

async function send() {
  const text = ta.value.trim();
  if ((!text && !pendingImage) || state.streaming) return;
  const messageId = crypto.randomUUID();
  ta.value = "";
  resizeTextarea();
  await doSend(text, messageId);
}

async function doSend(text, messageId) {
  // capturar ANTES de limpiar: la imagen viaja con este send y nada mas
  const imageToSend = pendingImage;
  const imageDataUrl = pendingImageDataUrl;
  addUserBubble(text, messageId, imageToSend ? imageDataUrl : null);
  if (imageToSend) clearPendingImage();
  refreshContextUsage();
  state.streaming = true;
  updateSendButton();
  const gen = ++streamGen;
  let imageBase64 = null;
  let imageMime = null;
  if (imageToSend && imageDataUrl) {
    imageBase64 = imageDataUrl.split(",")[1];
    imageMime = imageToSend.type;
  }
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        who_answers: state.who,
        chat_room: state.room,
        message_id: messageId,
        image_base64: imageBase64,
        image_mime: imageMime,
      }),
    });
    if (!res.ok) {
      let detail = "HTTP " + res.status;
      try {
        const body = await res.json();
        if (body && body.detail !== undefined) {
          detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
        }
      } catch {}
      toast(detail, "error");
      if (gen === streamGen) finishStream();
      return;
    }
    const parser = new FTTS.SSEParser(makeOnEvent(gen));
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    if (gen === streamGen) finishStream();
  } catch (err) {
    toast("Error de conexión: " + (err && err.message ? err.message : String(err)), "error");
    if (gen === streamGen) finishStream();
  }
}

export async function cancelChat() {
  if (!state.streaming) return;
  try {
    await fetch("/api/chat/cancel", { method: "POST" });
  } catch (err) {
    toast("No se pudo cancelar: " + (err && err.message ? err.message : String(err)), "error");
  }
}

function updateSendButton() {
  const ico = btnSend.querySelector("i");
  if (state.streaming) {
    btnSend.classList.add("cancel");
    btnSend.disabled = false;
    btnSend.title = "Cancelar";
    btnSend.setAttribute("aria-label", "Cancelar chat");
    ico.className = "ti ti-x";
  } else {
    btnSend.classList.remove("cancel");
    btnSend.title = "Enviar";
    btnSend.setAttribute("aria-label", "Enviar mensaje");
    ico.className = "ti ti-send";
    btnSend.disabled = !ta.value.trim() && !pendingImage;
  }
}

function onImageFile(file) {
  if (!file || !file.type.startsWith("image/")) return;
  if (file.size > 10 * 1024 * 1024) {
    toast("Imagen muy grande (máx. 10 MB)", "error");
    return;
  }
  pendingImage = file;
  const reader = new FileReader();
  reader.onload = (ev) => {
    pendingImageDataUrl = ev.target.result;
    renderImagePreview();
    if (!state.streaming) btnSend.disabled = false;
  };
  reader.onerror = () => {
    pendingImage = null;
    toast("No se pudo leer la imagen", "error");
  };
  reader.readAsDataURL(file);
}

function renderImagePreview() {
  imagePreviewEl.textContent = "";
  const img = document.createElement("img");
  img.src = pendingImageDataUrl;
  img.alt = "preview";
  const rm = document.createElement("button");
  rm.type = "button";
  rm.className = "img-preview-remove";
  rm.title = "Quitar imagen";
  rm.textContent = "\u2715";
  rm.addEventListener("click", clearPendingImage);
  imagePreviewEl.append(img, rm);
  imagePreviewEl.classList.remove("hidden");
}

function clearPendingImage() {
  pendingImage = null;
  pendingImageDataUrl = "";
  imagePreviewEl.textContent = "";
  imagePreviewEl.classList.add("hidden");
  if (imageInput) imageInput.value = "";
  if (!state.streaming) btnSend.disabled = !ta.value.trim();
}

function onImagePaste(e) {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      e.preventDefault();
      const file = item.getAsFile();
      if (file) onImageFile(file);
      return;
    }
  }
}

export function initChat() {
  if (initialized) return;
  initialized = true;
  ta = document.getElementById("chat-input");
  btnSend = document.getElementById("btn-send");
  btnImage = document.getElementById("btn-image");
  imageInput = document.getElementById("image-input");
  imagePreviewEl = document.getElementById("image-preview");
  messagesEl = document.getElementById("messages");

  ta.addEventListener("input", () => {
    resizeTextarea();
    if (!state.streaming) btnSend.disabled = !ta.value.trim() && !pendingImage;
  });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  btnSend.addEventListener("click", () => {
    if (state.streaming) cancelChat();
    else send();
  });
  btnImage.addEventListener("click", () => imageInput.click());
  imageInput.addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) onImageFile(file);
    imageInput.value = "";
  });
  ta.addEventListener("paste", onImagePaste);
  // drag & drop de imagenes sobre el composer
  const inputRow = document.getElementById("input-row");
  if (inputRow) {
    inputRow.addEventListener("dragover", (e) => {
      e.preventDefault();
      inputRow.classList.add("drag-over");
    });
    inputRow.addEventListener("dragleave", () => inputRow.classList.remove("drag-over"));
    inputRow.addEventListener("drop", (e) => {
      e.preventDefault();
      inputRow.classList.remove("drag-over");
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) onImageFile(file);
    });
  }
  window.addEventListener("tts:status", () => {
    messagesEl.querySelectorAll(".msg").forEach((m) => applyAudioMode(m));
  });
  // karaoke: la palabra activa llega de tts.js (tracking contra el clock de
  // playback); -1 = limpiar
  window.addEventListener("tts:word", (e) => {
    const d = e.detail;
    if (!d || !d.messageId) return;
    const b = wordBubbles.get(d.messageId);
    if (!b || !b.rootEl.isConnected) {
      wordBubbles.delete(d.messageId);
      return;
    }
    clearKwActive(b);
    if (d.word < 0 || !b.wordSpans) return;
    const arr = b.wordSpans.get(d.sentenceId);
    if (arr && arr[d.word]) {
      b.activeKw = arr[d.word];
      b.activeKw.classList.add("kw-active");
    }
  });
  updateSendButton();
}
