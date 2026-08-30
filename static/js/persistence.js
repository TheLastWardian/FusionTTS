// persistence.js — historial: carga al cambiar/crear room y al arrancar, mensajes pasados y playback de wavs guardados.
import { state } from "./state.js";
import { refreshContextUsage } from "./context.js";
import { api, toast, avatarCss, initials } from "./utils.js";
import { ttsReady } from "./tts.js";
import {
  makeCopyButton,
  makeTokensSpan,
  makeMsgFoot,
  makeReprocessButton,
} from "./chat.js";

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

export function showEmptyState(el) {
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
  refreshContextUsage();
  return true;
}

export function makeDeleteButton(messageId, room, el, onDeleted) {
  const del = document.createElement("button");
  del.className = "msg-act msg-act-delete";
  del.title = "Eliminar del contexto";
  const di = document.createElement("i");
  di.className = "ti ti-trash";
  del.appendChild(di);
  del.addEventListener("click", () => {
    deleteMessage(messageId, room, el).then((ok) => {
      if (ok && onDeleted) onDeleted();
    });
  });
  return del;
}

// Play all de los wavs guardados del mensaje (fila externa de audio).
function makeReplayButton(files, room, rootEl) {
  const replay = document.createElement("button");
  replay.className = "msg-act msg-act-replay";
  replay.title = "Play all";
  replay.setAttribute("aria-label", "Reproducir todo el mensaje en orden (audio guardado)");
  replay.disabled = true;
  const ri = document.createElement("i");
  ri.className = "ti ti-player-play-filled";
  replay.appendChild(ri);
  let busy = false;
  replay.addEventListener("click", () => {
    if (busy || !files.length) return;
    busy = true;
    replay.disabled = true;
    ri.className = "ti ti-loader spinning";
    // el play del primer archivo corre de forma sincrona en el click
    playSavedSequence(room, files, () => {
      busy = false;
      replay.disabled = false;
      ri.className = "ti ti-player-play-filled";
      applyAudioMode(rootEl);
    });
  });
  return replay;
}

// Doble click en un mensaje de usuario: edita el texto en el lugar.
// Actualiza el contexto del room (PATCH) sin borrar/reenviar; es la base
// para el futuro "reprocesar mensaje".
export function beginMessageEdit(msgEl, bubbleEl, messageId, room) {
  if (msgEl.querySelector(".msg-edit-ta")) return;
  const first = bubbleEl.firstChild;
  if (!first || first.nodeType !== Node.TEXT_NODE) return;
  const originalText = first.nodeValue;

  const taEl = document.createElement("textarea");
  taEl.className = "msg-edit-ta";
  taEl.value = originalText;
  taEl.title = "Enter = guardar · Esc = cancelar";
  taEl.spellcheck = false;
  bubbleEl.replaceChild(taEl, first);

  // los botones de editar se meten en la barra interior (junto al borrar)
  const okBtn = document.createElement("button");
  okBtn.className = "msg-act msg-act-edit-ok";
  okBtn.title = "Guardar";
  const oki = document.createElement("i");
  oki.className = "ti ti-check";
  okBtn.appendChild(oki);
  const cancelBtn = document.createElement("button");
  cancelBtn.className = "msg-act msg-act-edit-cancel";
  cancelBtn.title = "Cancelar";
  const xi = document.createElement("i");
  xi.className = "ti ti-x";
  cancelBtn.appendChild(xi);
  const del = msgEl.querySelector(".msg-act-delete");
  if (del) {
    del.parentNode.insertBefore(okBtn, del);
    del.parentNode.insertBefore(cancelBtn, del);
  }

  let busy = false;
  let exited = false;
  const onDocClick = (e) => {
    // clic fuera del mensaje = cancelar (no mientras hay guardado en vuelo)
    if (busy || exited) return;
    if (!msgEl.contains(e.target)) cancel();
  };
  const finish = (text) => {
    if (exited) return;
    exited = true;
    document.removeEventListener("click", onDocClick);
    bubbleEl.replaceChild(document.createTextNode(text), taEl);
    okBtn.remove();
    cancelBtn.remove();
  };
  const save = async () => {
    if (busy) return;
    const text = taEl.value.trim();
    if (!text) {
      toast("El mensaje no puede quedar vacío", "warning");
      return;
    }
    if (text === originalText) {
      finish(text);
      return;
    }
    busy = true;
    okBtn.disabled = true;
    cancelBtn.disabled = true;
    try {
      await api(
        "/api/rooms/" + encodeURIComponent(room) + "/messages/" + encodeURIComponent(messageId),
        { method: "PATCH", body: { text } },
      );
      finish(text);
      toast("Mensaje editado", "success");
      refreshContextUsage();
    } catch (err) {
      toast(err.message || "Error al editar el mensaje", "error");
      if (!exited) {
        busy = false;
        okBtn.disabled = false;
        cancelBtn.disabled = false;
      }
    }
  };
  const cancel = () => finish(originalText);
  document.addEventListener("click", onDocClick);
  okBtn.addEventListener("click", save);
  cancelBtn.addEventListener("click", cancel);
  taEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      save();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancel();
    }
  });
  taEl.focus();
  taEl.select();
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
    replay.title = "Play all";
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
    if (!actions.querySelector(".msg-act-replay")) {
      actions.appendChild(makeReplayButton(files, room, t.b.rootEl));
    }
    for (const f of files) {
      const play = document.createElement("button");
      play.className = "msg-act msg-act-audio";
      play.title = f;
      const pi = document.createElement("i");
      pi.className = "ti ti-music";
      play.appendChild(pi);
      play.addEventListener("click", () => playHistoryAudio(play, room, f));
      // despues del play all (replay) para que el dorado quede primero
      actions.appendChild(play);
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
  if (isUser) {
    // solo los propios se editan (doble click)
    bubble.title = "Doble click para editar";
    bubble.addEventListener("dblclick", () => beginMessageEdit(msg, bubble, m.uuid, room));
  }
  if (m.image) {
    const img = document.createElement("img");
    img.className = "msg-image";
    img.src = "/api/rooms/" + encodeURIComponent(room) + "/file/" + encodeURIComponent(m.image);
    img.alt = "";
    bubble.appendChild(img);
  }
  // barra interior: [copiar][reprocesar si usuario][tokens si respuesta] [borrar]
  const left = [makeCopyButton(() => m.text)];
  if (isUser) left.push(makeReprocessButton(msg, bubble, m.uuid, room, !!m.compacted));
  const ts = makeTokensSpan(m.tokens);
  if (ts) left.push(ts);
  bubble.appendChild(makeMsgFoot(msg, m.uuid, room, left));
  // la fila externa solo existe si hay audio guardado
  const audio = m.audio || [];
  let actions = null;
  if (audio.length) {
    actions = document.createElement("div");
    actions.className = "msg-actions";
    actions.appendChild(makeReplayButton(audio, room, msg));
    for (const f of audio) {
      const play = document.createElement("button");
      play.className = "msg-act msg-act-audio";
      play.title = f;
      const pi = document.createElement("i");
      pi.className = "ti ti-music";
      play.appendChild(pi);
      play.addEventListener("click", () => playHistoryAudio(play, room, f));
      actions.appendChild(play);
    }
  }
  body.append(meta, bubble);
  if (actions) body.appendChild(actions);
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
