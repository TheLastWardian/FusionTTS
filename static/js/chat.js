// chat.js — flujo de chat: envío POST /api/chat, streaming SSE, burbujas usuario/persona, cancel.
import { state } from "./state.js";
import { initials, toast, avatarCss } from "./utils.js";
import { feedAudioChunk, onTTSEvent, playChunkB64, setActiveTTSMessage, ttsReady } from "./tts.js";
import { deleteMessage, attachSavedAudio, playSavedSequence, applyAudioMode } from "./persistence.js";

let initialized = false;
let ta = null;
let btnSend = null;
let messagesEl = null;
let current = null;
// burbujas por message_id: el audio de una oracion puede llegar cuando
// current ya apunta a la siguiente persona (la sintesis va atrasada)
let messageBubbles = new Map();

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
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  const del = document.createElement("button");
  del.className = "msg-act msg-act-delete";
  del.title = "Eliminar del contexto";
  const di = document.createElement("i");
  di.className = "ti ti-trash";
  del.appendChild(di);
  del.addEventListener("click", () => deleteMessage(messageId, state.room, msg));
  actions.appendChild(del);
  body.append(meta, bubble, actions);
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

function finalizeBubble(b, fullText) {
  if (!b || b.final) return;
  b.final = true;
  if (typeof fullText === "string") b.textEl.textContent = fullText;
  if (!b.textEl.textContent.trim()) {
    b.rootEl.remove();
    return;
  }
  if (b.dots) b.dots.remove();
  b.cursor.remove();
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  if (b.soundsEl) actions.appendChild(b.soundsEl);
  const replay = document.createElement("button");
  replay.className = "msg-act msg-act-replay";
  replay.title = "Reproducir audio guardado";
  replay.disabled = true;
  const ri = document.createElement("i");
  ri.className = "ti ti-volume";
  replay.appendChild(ri);
  let replayBusy = false;
  replay.addEventListener("click", () => {
    if (replayBusy || !b.savedAudio || !b.savedAudio.length) return;
    replayBusy = true;
    replay.disabled = true;
    ri.className = "ti ti-loader spinning";
    // el play del primer archivo corre de forma sincrona en el click
    playSavedSequence(state.room, b.savedAudio, () => {
      replayBusy = false;
      ri.className = "ti ti-volume";
      applyAudioMode(b.rootEl);
    });
  });
  const copy = document.createElement("button");
  copy.className = "msg-act msg-act-copy";
  copy.title = "Copiar";
  const ci = document.createElement("i");
  ci.className = "ti ti-copy";
  copy.appendChild(ci);
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(b.textEl.textContent);
      toast("Copiado", "success");
    } catch {
      toast("No se pudo copiar", "error");
    }
  });
  const del = document.createElement("button");
  del.className = "msg-act msg-act-delete";
  del.title = "Eliminar del contexto";
  const di = document.createElement("i");
  di.className = "ti ti-trash";
  del.appendChild(di);
  del.addEventListener("click", () => {
    if (b.messageId && deleteMessage(b.messageId, state.room, b.rootEl)) {
      messageBubbles.delete(b.messageId);
    }
  });
  actions.append(replay, copy, del);
  b.bodyEl.appendChild(actions);
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

function onEvent(ev) {
  if (ev.type === "start") {
    current = startPersonaBubble(ev.persona);
    current.messageId = ev.message_id;
    messageBubbles.set(ev.message_id, current);
    setActiveTTSMessage(ev.message_id);
  } else if (ev.type === "token") {
    appendToken(current, ev.token);
  } else if (ev.type === "done") {
    finalizeBubble(current, ev.text);
    current = null;
  } else if (ev.type === "audio_chunk") {
    feedAudioChunk(ev);
    const b = messageBubbles.get(ev.message_id);
    if (b) addSentenceSound(b, ev);
  } else if (ev.type === "tts_state") {
    onTTSEvent(ev);
  } else if (ev.type === "error") {
    toast(ev.message || "Error en el chat", "error");
  } else if (ev.type === "complete") {
    if (ev.cancelled) toast("Chat cancelado", "info");
    // anade a las burbujas los audios ya guardados en disco (fallback sin TTS)
    const targets = [];
    for (const [id, b] of messageBubbles) {
      if (b.final) targets.push({ id, b });
    }
    if (targets.length) attachSavedAudio(targets, state.room);
    finishStream();
  }
}

async function send() {
  const text = ta.value.trim();
  if (!text || state.streaming) return;
  const messageId = crypto.randomUUID();
  addUserBubble(text, messageId);
  ta.value = "";
  resizeTextarea();
  state.streaming = true;
  updateSendButton();
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
      finishStream();
      return;
    }
    const parser = new FTTS.SSEParser(onEvent);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    finishStream();
  } catch (err) {
    toast("Error de conexión: " + (err && err.message ? err.message : String(err)), "error");
    finishStream();
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
