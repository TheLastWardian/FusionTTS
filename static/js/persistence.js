// persistence.js — historial: carga al cambiar/crear room y al arrancar, mensajes pasados y playback de wavs guardados.
import { state } from "./state.js";
import { api, toast, avatarCss, initials } from "./utils.js";

let playing = null;

export function stopHistoryAudio() {
  if (!playing) return;
  playing.audio.pause();
  URL.revokeObjectURL(playing.url);
  playing.iconEl.className = "ti ti-music";
  playing.btn.title = playing.filename;
  playing = null;
}

async function playHistoryAudio(btn, room, filename) {
  if (playing && playing.btn === btn) {
    stopHistoryAudio();
    return;
  }
  stopHistoryAudio();
  const iconEl = btn.querySelector("i");
  iconEl.className = "ti ti-loader spinning";
  let res;
  try {
    res = await fetch("/api/rooms/" + encodeURIComponent(room) + "/file/" + encodeURIComponent(filename));
  } catch (err) {
    iconEl.className = "ti ti-music";
    toast("No se pudo cargar el audio: " + (err && err.message ? err.message : String(err)), "error");
    return;
  }
  if (!res.ok) {
    iconEl.className = "ti ti-music";
    toast("No se pudo cargar el audio (HTTP " + res.status + ")", "error");
    return;
  }
  const url = URL.createObjectURL(await res.blob());
  const audio = new Audio(url);
  audio.onended = () => stopHistoryAudio();
  playing = { btn, iconEl, audio, url, filename };
  try {
    await audio.play();
  } catch {
    stopHistoryAudio();
    toast("No se pudo reproducir el audio", "error");
    return;
  }
  iconEl.className = "ti ti-player-stop";
  btn.title = "Detener";
}

function showEmptyState(el) {
  const es = document.createElement("div");
  es.className = "empty-state";
  const i = document.createElement("i");
  i.className = "ti ti-messages";
  const span = document.createElement("span");
  span.textContent = "Sin mensajes todavía";
  es.append(i, span);
  el.appendChild(es);
}

// Elimina el mensaje del contexto de la conversacion del room (backend borra
// de history; los archivos de audio/imagen se quedan en disco).
export async function deleteMessage(messageId, room, el) {
  if (playing && el.contains(playing.btn)) stopHistoryAudio();
  let res;
  try {
    res = await fetch(
      "/api/rooms/" + encodeURIComponent(room) + "/messages/" + encodeURIComponent(messageId),
      { method: "DELETE" },
    );
  } catch (err) {
    toast("No se pudo eliminar: " + (err && err.message ? err.message : String(err)), "error");
    return false;
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
    return false;
  }
  el.remove();
  const elMessages = document.getElementById("messages");
  if (!elMessages.querySelector(".msg")) showEmptyState(elMessages);
  toast("Mensaje eliminado", "success");
  return true;
}

function makeDeleteButton(messageId, room, el) {
  const del = document.createElement("button");
  del.className = "msg-act msg-act-delete";
  del.title = "Eliminar del contexto";
  const di = document.createElement("i");
  di.className = "ti ti-trash";
  del.appendChild(di);
  del.addEventListener("click", () => deleteMessage(messageId, room, el));
  return del;
}

function renderHistoryMessage(el, m, room) {
  const isUser = m.role === "user";
  const persona = isUser ? null : state.personas.find((p) => p.name === m.sender);
  const msg = document.createElement("div");
  msg.className = isUser ? "msg user" : "msg";
  const av = document.createElement("div");
  av.className = "msg-avatar";
  if (isUser) {
    av.style.cssText = "background: var(--bg2); color: var(--text-muted);";
    const ai = document.createElement("i");
    ai.className = "ti ti-user";
    ai.style.fontSize = "14px";
    av.appendChild(ai);
  } else {
    av.style.cssText = avatarCss(persona || {});
    av.textContent = initials(m.sender);
  }
  const body = document.createElement("div");
  body.className = "msg-body";
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = isUser ? "Tú" : m.sender;
  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";
  bubble.textContent = m.text;
  if (m.image) {
    const img = document.createElement("img");
    img.className = "msg-image";
    img.src = "/api/rooms/" + encodeURIComponent(room) + "/file/" + encodeURIComponent(m.image);
    img.alt = "";
    bubble.appendChild(img);
  }
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
      await navigator.clipboard.writeText(m.text);
      toast("Copiado", "success");
    } catch {
      toast("No se pudo copiar", "error");
    }
  });
  actions.appendChild(copy);
  for (const f of m.audio || []) {
    const play = document.createElement("button");
    play.className = "msg-act msg-act-audio";
    play.title = f;
    const pi = document.createElement("i");
    pi.className = "ti ti-music";
    play.appendChild(pi);
    play.addEventListener("click", () => playHistoryAudio(play, room, f));
    actions.appendChild(play);
  }
  actions.appendChild(makeDeleteButton(m.uuid, room, msg));
  body.append(meta, bubble, actions);
  msg.append(av, body);
  el.appendChild(msg);
}

export async function loadHistory(room) {
  stopHistoryAudio();
  let data;
  try {
    data = await api("/api/session/history?room=" + encodeURIComponent(room));
  } catch (err) {
    toast(err.message || "Error al cargar el historial", "error");
    return;
  }
  const el = document.getElementById("messages");
  el.textContent = "";
  for (const m of data.messages || []) {
    renderHistoryMessage(el, m, room);
  }
  if (!data.messages || data.messages.length === 0) showEmptyState(el);
  el.scrollTop = el.scrollHeight;
}
