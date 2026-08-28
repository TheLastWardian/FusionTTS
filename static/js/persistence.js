// persistence.js — historial: carga al cambiar/crear room y al arrancar, mensajes pasados y playback de wavs guardados.
import { state } from "./state.js";
import { api, toast, avatarCss, initials } from "./utils.js";
import { ttsReady } from "./tts.js";

let playing = null;
let seqToken = null;

export function stopHistoryAudio() {
  if (!playing) return;
  playing.audio.pause();
  playing.iconEl.className = "ti ti-music";
  playing.btn.title = playing.filename;
  playing = null;
}

function fileUrl(room, filename) {
  return "/api/rooms/" + encodeURIComponent(room) + "/file/" + encodeURIComponent(filename);
}

// IMPORTANTE: play() se llama SIEMPRE de forma sincrona dentro del click.
// Con fetch+blob habia una brecha async: si el server estaba ocupado (LLM/TTS
// corriendo) el fetch excedia la ventana de "user activation" del navegador y
// play() rechazaba con NotAllowedError (autoplay).

// Reproduce en secuencia los wavs guardados de un mensaje (fallback del
// boton replay cuando el TTS esta apagado).
let seqAudio = null;
let seqOnDone = null;

export function stopSavedSequence() {
  if (seqAudio) {
    seqAudio.onended = null;
    seqAudio.pause();
    seqAudio = null;
  }
  if (seqToken) {
    const cb = seqOnDone;
    seqToken.stopped = true;
    seqToken = null;
    seqOnDone = null;
    if (cb) cb();
  }
}

export function playSavedSequence(room, filenames, onDone) {
  stopSavedSequence();
  const token = { stopped: false };
  seqToken = token;
  seqOnDone = onDone || null;
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    if (seqToken === token) seqToken = null;
    if (seqOnDone === (onDone || null)) seqOnDone = null;
    if (onDone) onDone();
  };
  let i = 0;
  const playNext = () => {
    if (token.stopped || finished) return;
    if (i >= filenames.length) {
      finish();
      return;
    }
    const f = filenames[i++];
    const audio = new Audio(fileUrl(room, f));
    seqAudio = audio;
    audio.onended = playNext;
    audio.onerror = () => {
      toast("No se pudo cargar el audio (" + f + ")", "error");
      finish();
    };
    audio.play().catch((err) => {
      if (token.stopped || finished) return;
      toast(
        err && err.name === "NotAllowedError"
          ? "El navegador bloqueo el audio (autoplay)"
          : "No se pudo reproducir el audio",
        "error",
      );
      finish();
    });
  };
  playNext();
}

function playHistoryAudio(btn, room, filename) {
  if (playing && playing.btn === btn) {
    stopHistoryAudio();
    return;
  }
  stopHistoryAudio();
  const iconEl = btn.querySelector("i");
  const audio = new Audio(fileUrl(room, filename));
  audio.onerror = () => {
    if (playing && playing.btn === btn) playing = null;
    iconEl.className = "ti ti-music";
    btn.title = filename;
    toast("No se pudo cargar el audio", "error");
  };
  audio.onended = () => stopHistoryAudio();
  playing = { btn, iconEl, audio, filename };
  iconEl.className = "ti ti-loader spinning";
  audio
    .play()
    .then(
      () => {
        iconEl.className = "ti ti-player-stop";
        btn.title = "Detener";
      },
      (err) => {
        if (playing && playing.btn === btn) playing = null;
        iconEl.className = "ti ti-music";
        btn.title = filename;
        toast(
          err && err.name === "NotAllowedError"
            ? "El navegador bloqueo el audio (autoplay)"
            : "No se pudo reproducir el audio",
          "error",
        );
      },
    );
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

// Regla unica de visibilidad de audio por burbuja:
// - botones de oracion (TTS live) solo si TTS esta ON y existen
// - botones de disco solo si NO se puede usar el reproductor TTS
// - replay (secuencia desde disco) solo con TTS OFF y audio guardado
export function applyAudioMode(msgEl) {
  const actions = msgEl.querySelector(".msg-actions");
  if (!actions) return;
  const soundsEl = actions.querySelector(".msg-sounds");
  const hasTts = !!(soundsEl && soundsEl.children.length);
  const savedBtns = Array.from(actions.querySelectorAll(".msg-act-audio"));
  const showTts = ttsReady() && hasTts;
  if (soundsEl) soundsEl.style.display = showTts ? "" : "none";
  for (const b of savedBtns) b.style.display = showTts ? "none" : "";
  const replay = actions.querySelector(".msg-act-replay");
  if (replay) {
    const canReplay = !ttsReady() && savedBtns.length > 0;
    replay.style.display = canReplay ? "" : "none";
    replay.disabled = !canReplay;
    replay.title = "Reproducir audio guardado";
  }
}

// A las burbujas live finalizadas les anade los botones de audio guardado en
// disco (mismos que la vista de historial), para poder reproducir sin TTS.
// Se llama en "complete": ahi el dispatcher ya se drenó y audio[] es completo.
export async function attachSavedAudio(targets, room) {
  let data;
  try {
    data = await api("/api/session/history?room=" + encodeURIComponent(room));
  } catch {
    return;
  }
  const byUuid = new Map((data.messages || []).map((m) => [m.uuid, m]));
  for (const t of targets) {
    const m = byUuid.get(t.id);
    const files = (m && m.audio) || [];
    if (!files.length) continue;
    t.b.savedAudio = files;
    let actions = t.b.bodyEl.querySelector(".msg-actions");
    if (!actions) {
      actions = document.createElement("div");
      actions.className = "msg-actions";
      t.b.bodyEl.appendChild(actions);
    }
    const replay = actions.querySelector(".msg-act-replay");
    for (const f of files) {
      const play = document.createElement("button");
      play.className = "msg-act msg-act-audio";
      play.title = f;
      const pi = document.createElement("i");
      pi.className = "ti ti-music";
      play.appendChild(pi);
      play.addEventListener("click", () => playHistoryAudio(play, room, f));
      if (replay) actions.insertBefore(play, replay);
      else actions.appendChild(play);
    }
    applyAudioMode(t.b.rootEl);
  }
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
  if ((m.audio || []).length) {
    const replay = document.createElement("button");
    replay.className = "msg-act msg-act-replay";
    replay.title = "Reproducir audio guardado";
    replay.disabled = true;
    const ri = document.createElement("i");
    ri.className = "ti ti-volume";
    replay.appendChild(ri);
    let replayBusy = false;
    replay.addEventListener("click", () => {
      if (replayBusy) return;
      replayBusy = true;
      ri.className = "ti ti-loader spinning";
      playSavedSequence(room, m.audio, () => {
        replayBusy = false;
        ri.className = "ti ti-volume";
        applyAudioMode(msg);
      });
    });
    actions.appendChild(replay);
  }
  const copy = document.createElement("button");
  copy.className = "msg-act msg-act-copy";
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
  actions.appendChild(makeDeleteButton(m.uuid, room, msg));
  body.append(meta, bubble, actions);
  msg.append(av, body);
  el.appendChild(msg);
  applyAudioMode(msg);
}

export async function loadHistory(room) {
  stopHistoryAudio();
  stopSavedSequence();
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
