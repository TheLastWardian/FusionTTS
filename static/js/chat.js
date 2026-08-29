// chat.js — flujo de chat: envío POST /api/chat, streaming SSE, burbujas usuario/persona, cancel.
import { state } from "./state.js";
import { refreshContextUsage } from "./context.js";
import { initials, toast, avatarCss } from "./utils.js";
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
let messagesEl = null;
let current = null;
// burbujas por message_id: el audio de una oracion puede llegar cuando
// current ya apunta a la siguiente persona (la sintesis va atrasada)
let messageBubbles = new Map();
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

function addUserBubble(text, messageId) {
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
  const av = document.createElement("div");
  av.className = "msg-avatar";
  av.style.cssText = avatarCss(persona || {});
  av.textContent = initials(name);
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
  const textEl = document.createTextNode("");
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

// boton de play por oracion (como TalkWithMe): reproduce el audio que ya
// llego por SSE, sin volver a sintetizar
function addSentenceSound(b, ev) {
  if (!ev.audio) return;
  if (!ttsReady()) return;
  b.sounds.push(ev);
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
    all.addEventListener("click", () => playChunksB64(b.sounds.map((e) => e.audio)));
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
  btn.addEventListener("click", () => playChunkB64(b64));
  b.soundsEl.appendChild(btn);
}

function appendToken(b, token) {
  if (!b || b.final) return;
  if (b.dots) {
    b.dots.remove();
    b.dots = null;
  }
  b.textEl.textContent += token;
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
export function makeReprocessButton(msgEl, bubbleEl, messageId, room) {
  const btn = document.createElement("button");
  btn.className = "msg-act msg-act-reproc";
  btn.title = "Reprocesar desde este mensaje (borra esto y todo lo que viene después)";
  btn.setAttribute("aria-label", "Reprocesar desde este mensaje");
  const ico = document.createElement("i");
  ico.className = "ti ti-refresh";
  btn.appendChild(ico);
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
  // la fila externa solo existe si hay audio (oraciones TTS live; el
  // replay + wavs guardados los anade attachSavedAudio al llegar "complete")
  if (b.soundsEl) {
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    actions.appendChild(b.soundsEl);
    b.bodyEl.appendChild(actions);
  }
  applyAudioMode(b.rootEl);
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
  if (!text || state.streaming) return;
  const messageId = crypto.randomUUID();
  ta.value = "";
  resizeTextarea();
  await doSend(text, messageId);
}

async function doSend(text, messageId) {
  addUserBubble(text, messageId);
  refreshContextUsage();
  state.streaming = true;
  updateSendButton();
  const gen = ++streamGen;
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        who_answers: state.who,
        chat_room: state.room,
        message_id: messageId,
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
    btnSend.disabled = !ta.value.trim();
  }
}

export function initChat() {
  if (initialized) return;
  initialized = true;
  ta = document.getElementById("chat-input");
  btnSend = document.getElementById("btn-send");
  messagesEl = document.getElementById("messages");

  ta.addEventListener("input", () => {
    resizeTextarea();
    if (!state.streaming) btnSend.disabled = !ta.value.trim();
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
  window.addEventListener("tts:status", () => {
    messagesEl.querySelectorAll(".msg").forEach((m) => applyAudioMode(m));
  });
  updateSendButton();
}
