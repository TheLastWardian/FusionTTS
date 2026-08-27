// chat.js — flujo de chat: envío POST /api/chat, streaming SSE, burbujas usuario/persona, cancel.
import { state } from "./state.js";
import { initials, toast, avatarCss } from "./utils.js";

let initialized = false;
let ta = null;
let btnSend = null;
let messagesEl = null;
let current = null;

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

function addUserBubble(text) {
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
  return { bodyEl: body, textEl, dots, cursor, final: false };
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
  if (b.dots) b.dots.remove();
  b.cursor.remove();
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  const copy = document.createElement("button");
  copy.className = "msg-act";
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
  const replay = document.createElement("button");
  replay.className = "msg-act";
  replay.disabled = true;
  replay.title = "Disponible en T16";
  const ri = document.createElement("i");
  ri.className = "ti ti-volume";
  replay.appendChild(ri);
  actions.append(copy, replay);
  b.bodyEl.appendChild(actions);
}

function finishStream() {
  finalizeBubble(current, null);
  current = null;
  state.streaming = false;
  updateSendButton();
}

function onEvent(ev) {
  if (ev.type === "start") {
    current = startPersonaBubble(ev.persona);
  } else if (ev.type === "token") {
    appendToken(current, ev.token);
  } else if (ev.type === "done") {
    finalizeBubble(current, ev.text);
    current = null;
  } else if (ev.type === "error") {
    toast(ev.message || "Error en el chat", "error");
  } else if (ev.type === "complete") {
    if (ev.cancelled) toast("Chat cancelado", "info");
    finishStream();
  }
}

async function send() {
  const text = ta.value.trim();
  if (!text || state.streaming) return;
  addUserBubble(text);
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
        message_id: crypto.randomUUID(),
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
  updateSendButton();
}
