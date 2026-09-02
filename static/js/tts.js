// tts.js — chip de estado TTS con polling de /api/tts/status + controles pause/stop.
import { state } from "./state.js";
import { api, toast } from "./utils.js";

let watchdog = null;
let pendingEnable = false;
let pendingSince = 0;
let ctx = null;
let player = null;
let currentSrc = null;
let audioHinted = false;
let gen = 0;
let decodeSeq = Promise.resolve();
let activeMessageId = null;
let chunksOk = 0;
let chunksDropped = 0;
// Karaoke: tracking de la palabra activa del chunk que suena (rAF contra
// ctx.currentTime). ctx.suspend() (pausa) congela el clock solo.
let wordRaf = 0;
let wordTrack = null; // {item, t0, last}

function updateChipDebug() {
  const chip = document.getElementById("tts-chip");
  if (chip)
    chip.title = `TTS · ok: ${chunksOk} · dropped: ${chunksDropped} · queue: ${player ? player.pendingCount : 0}`;
}

// Persona cuyo audio suena AHORA (null = cola vacia). Las cards de la
// sidebar lo escuchan via el evento "persona:speaking"; el render de cards
// lo consulta para no perder el estado en un re-render.
let speakingPersona = null;

export function getSpeakingPersona() {
  return speakingPersona;
}

function setSpeaking(name) {
  if (speakingPersona === name) return;
  speakingPersona = name;
  window.dispatchEvent(new CustomEvent("persona:speaking", { detail: { persona: name } }));
}

// id del mensaje cuyo audio puede sonar (modelo F5-TTS currentTtsMsgId):
// un chunk de otro mensaje nunca se encola.
export function setActiveTTSMessage(id) {
  if (activeMessageId) {
    console.info(`[tts] fin de mensaje (msg ${String(activeMessageId).slice(0, 8)}): ok ${chunksOk} · descartados ${chunksDropped}`);
  }
  console.info("[tts] activeMessageId →", id ? String(id).slice(0, 8) : null);
  if (!id) {
    chunksOk = 0;
    chunksDropped = 0;
  }
  activeMessageId = id;
  updateChipDebug();
}

export function ttsReady() {
  const e = state.tts && state.tts.engine;
  return !!(e && e.state === "running" && e.server && e.server.status === "ready");
}

function ensureCtx() {
  if (ctx) {
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  player = new FTTS.AudioQueue({
    gap: 80,
    onPlay: (item) => {
      console.info(
        `[tts] ▶ suena ${Math.round(item.audio.duration * 100) / 100}s · cola pendiente: ${player.pendingCount}`
      );
      setSpeaking(item.persona);
      updateChipDebug();
      scheduleSource(item);
      startWordTrack(item);
    },
    onDrain: () => {
      console.info("[tts] cola vacía (playback terminado)");
      setSpeaking(null);
      updateChipDebug();
    },
  });
  ctx.resume();
  if (ctx.state === "suspended" && !audioHinted) {
    audioHinted = true;
    toast("The browser blocked the audio; click the TTS chip to enable it", "error");
  }
  return ctx;
}

function scheduleSource(item) {
  const src = ctx.createBufferSource();
  src.buffer = item.audio;
  src.connect(ctx.destination);
  src.onended = () => {
    currentSrc = null;
    stopWordTrack();
    player.currentEnded();
  };
  src.start();
  currentSrc = src;
}

function emitWord(item, idx) {
  window.dispatchEvent(
    new CustomEvent("tts:word", {
      detail: {
        messageId: item.messageId || null,
        sentenceId: item.sentenceId ?? null,
        word: idx,
      },
    })
  );
}

function stopWordTrack() {
  if (wordRaf) {
    cancelAnimationFrame(wordRaf);
    wordRaf = 0;
  }
  if (wordTrack) {
    emitWord(wordTrack.item, -1);
    wordTrack = null;
  }
}

// Karaoke: recorre item.words (spans en ms) contra el clock del AudioContext
// y dispara "tts:word" cuando la palabra activa cambia (-1 = ninguna).
function startWordTrack(item) {
  stopWordTrack();
  if (!item.words || !item.words.length) return;
  const t0 = ctx.currentTime;
  const durMs = item.audio.duration * 1000;
  wordTrack = { item, t0, last: -2 };
  const step = () => {
    if (!wordTrack || wordTrack.item !== item) return;
    const pos = (ctx.currentTime - t0) * 1000;
    if (pos >= durMs) {
      stopWordTrack();
      return;
    }
    const ws = item.words;
    let idx = -1;
    for (let i = 0; i < ws.length; i++) {
      if (pos >= ws[i].start_ms && pos < ws[i].end_ms) {
        idx = i;
        break;
      }
    }
    if (idx !== wordTrack.last) {
      wordTrack.last = idx;
      emitWord(item, idx);
    }
    wordRaf = requestAnimationFrame(step);
  };
  wordRaf = requestAnimationFrame(step);
}

function base64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function stopLocal() {
  console.warn(`[tts] stopLocal: detengo fuente y vacío la cola (pendientes: ${player ? player.pendingCount : 0})`);
  gen++;
  stopWordTrack();
  if (currentSrc) {
    try {
      currentSrc.stop();
    } catch {}
  }
  if (player) player.stop();
  updateChipDebug();
}

function decodeAndEnqueue(arrayBuffer, persona, extra) {
  const g = gen;
  const t0 = performance.now();
  const job = decodeSeq.then(async () => {
    if (g !== gen) {
      console.warn("[tts] decode obsoleto: gen cambió antes de decodificar");
      return;
    }
    const buf = await ctx.decodeAudioData(arrayBuffer);
    if (g !== gen) return;
    console.info(
      `[tts] decodificado ${Math.round(buf.duration * 100) / 100}s en ${Math.round(performance.now() - t0)}ms → cola`
    );
    // el item lleva la persona: la cola es serial pero una ronda mezcla
    // hablantes, y la card se enciende por quien suena, no por el ultimo
    // encolado. extra = karaoke (words + ids para el highlight)
    player.enqueue({
      audio: buf,
      persona: persona || null,
      words: (extra && extra.words) || null,
      messageId: (extra && extra.messageId) || null,
      sentenceId: (extra && extra.sentenceId) ?? null,
    });
  });
  decodeSeq = job.catch((err) => {
    console.warn("[tts] no se pudo decodificar el audio:", err && err.message ? err.message : err);
  });
}

export function feedAudioChunk(ev) {
  if (!ttsReady()) {
    console.warn("[tts] chunk ignorado: ttsReady() false (oración", ev.sentence_id + ")");
    return;
  }
  // Sin filtro por message_id: la cola es FIFO y el orden de llegada = orden
  // de sintesis = orden conversacional (todo el msg 1, luego el msg 2). El
  // filtro antiguo tiraba la cola de la persona anterior mientras su audio
  // seguia sintetizandose (truncaba el mensaje a 1-2 oraciones).
  ensureCtx();
  try {
    const bytes = base64ToBytes(ev.audio);
    chunksOk++;
    console.info(
      `[tts] chunk ${ev.sentence_id} ok (msg ${String(ev.message_id || "-").slice(0, 8)}, ${bytes.length} bytes) → decodificar`
    );
    updateChipDebug();
    decodeAndEnqueue(bytes.buffer, ev.persona, {
      words: ev.words || null,
      messageId: ev.message_id || null,
      sentenceId: ev.sentence_id,
    });
  } catch (err) {
    console.warn("tts.js: base64 inválido en audio_chunk:", err && err.message ? err.message : err);
  }
}

// Play all (boton de mensaje): encola TODAS las oraciones en orden por la
// misma cola FIFO de decodificacion; no re-sintetiza nada. chunks = eventos
// de audio_chunk ({audio, words, sentence_id, message_id}).
export function playChunksB64(chunks, persona) {
  if (!chunks || !chunks.length) return;
  if (!ttsReady()) {
    toast("TTS is not active", "error");
    return;
  }
  console.info(`[tts] play all: ${chunks.length} oraciones → cola (en orden)`);
  ensureCtx();
  for (const c of chunks) {
    try {
      decodeAndEnqueue(base64ToBytes(c.audio).buffer, persona, {
        words: c.words || null,
        messageId: c.message_id || null,
        sentenceId: c.sentence_id,
      });
    } catch (err) {
      console.warn("tts.js: base64 inválido en playChunksB64:", err && err.message ? err.message : err);
    }
  }
}

// Reproduce una oracion desde su base64 ya descargado (botones por oracion):
// no re-sintetiza, suena al instante por la misma cola de decodificacion.
export function playChunkB64(b64, persona, extra) {
  if (!ttsReady()) {
    toast("TTS is not active", "error");
    return;
  }
  console.info(`[tts] botón de oración: ${b64.length} chars b64 → decodificar`);
  ensureCtx();
  try {
    decodeAndEnqueue(base64ToBytes(b64).buffer, persona, extra);
  } catch (err) {
    console.warn("tts.js: base64 inválido en playChunkB64:", err && err.message ? err.message : err);
  }
}

export function onTTSEvent(ev) {
  console.info("[tts] tts_state:", ev.state, ev.failed !== undefined ? `(failed=${ev.failed})` : "");
  if (ev.state === "on") ensureCtx();
  else if (ev.state === "stopped") stopLocal();
  else if (ev.state === "error")
    toast(`TTS: ${ev.failed ?? "?"} sentence(s) could not be synthesized`, "error");
}

export async function replayTTS(text, persona) {
  if (!ttsReady()) {
    toast("TTS is not active", "error");
    return false;
  }
  console.info(`[tts] replay burbuja (${persona}): ${text.length} chars → /api/tts/speak`);
  try {
    const res = await fetch("/api/tts/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, persona: persona || null }),
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
      return false;
    }
    if (!ttsReady()) {
      toast("TTS is not active", "error");
      return false;
    }
    ensureCtx();
    const bytes = new Uint8Array(await res.arrayBuffer());
    let words = null;
    const hw = res.headers.get("X-Words");
    if (hw) {
      try {
        words = JSON.parse(atob(hw));
      } catch {
        words = null;
      }
    }
    decodeAndEnqueue(bytes.buffer, persona, { words });
    return true;
  } catch (err) {
    toast(err && err.message ? err.message : "Error playing", "error");
    return false;
  }
}

function setChip(st) {
  const chip = document.getElementById("tts-chip");
  const label = document.getElementById("tts-label");
  chip.className =
    "tts-chip" + (st === "active" ? " active" : st === "loading" ? " loading" : st === "error" ? " error" : "");
  label.textContent = {
    off: "TTS · off",
    loading: "TTS · loading…",
    active: "TTS · ready",
    error: "TTS · error",
  }[st];
}

function clearWatchdog() {
  if (watchdog) {
    clearTimeout(watchdog);
    watchdog = null;
  }
}

function applyStatus() {
  const { engine, dispatcher, starting } = state.tts || {};
  if (!engine) return;
  const ready = engine.state === "running" && engine.server && engine.server.status === "ready";

  if (pendingEnable) {
    if (ready) {
      pendingEnable = false;
      clearWatchdog();
    } else if (!starting && Date.now() - pendingSince > 5000) {
      // el server ya no tiene un start en vuelo y no esta ready: el enable
      // fallo (crash del proceso, /load error, OOM...). Antes el flag quedaba
      // colgado 120s y el guard de onChip tragaba todos los clicks.
      pendingEnable = false;
      clearWatchdog();
      toast("TTS could not start; check logs/tts-server (latest file)", "error");
    }
  }

  // "starting" (server) cubre la fase de spawn: proceso arrancando, /status
  // todavia no responde -> antes mostraba "off" y parecia que no habia prendido.
  // running + "unloaded" = proceso calido con el modelo descargado = OFF
  // (VRAM libre); "loading" sigue siendo la fase de carga del modelo.
  const srv = engine.state === "running" && engine.server ? engine.server.status : null;
  let st;
  if (srv === "ready") st = "active";
  else if (srv === "loading" || starting) st = "loading";
  else st = "off";
  setChip(st);

  const pauseBtn = document.getElementById("btn-tts-pause");
  const stopBtn = document.getElementById("btn-tts-stop");
  const paused = !!(dispatcher && dispatcher.paused);
  pauseBtn.disabled = !ready;
  stopBtn.disabled = !ready;
  pauseBtn.querySelector("i").className = "ti " + (paused ? "ti-player-play" : "ti-player-pause");
  pauseBtn.title = paused ? "Resume" : "Pause";
  pauseBtn.setAttribute("aria-label", paused ? "Resume TTS" : "Pause TTS");

  window.dispatchEvent(new CustomEvent("tts:status", { detail: { ready } }));
}

async function poll() {
  try {
    const data = await api("/api/tts/status");
    state.tts = data;
    applyStatus();
  } catch {
    setChip("error");
  }
}

async function enableTTS() {
  try {
    await api("/api/tts/enable", { method: "POST" });
    setChip("loading");
    pendingEnable = true;
    pendingSince = Date.now();
    clearWatchdog();
    watchdog = setTimeout(async () => {
      pendingEnable = false;
      watchdog = null;
      const server = state.tts && state.tts.engine && state.tts.engine.server;
      if (!server || server.status !== "ready") {
        toast("TTS was not ready in time; turning off", "error");
        try {
          await api("/api/tts/disable", { method: "POST" });
        } catch {
        }
        setChip("off");
      }
    }, 300000); // backstop: el cold start completo (spawn + health 15s + /load 120s + ready poll)
    // puede llegar a ~140s; con 120s el watchdog mataba cargas lentas legítimas
  } catch (err) {
    toast(err.message || "Error turning on TTS", "error");
    setChip("error");
  }
}

async function disableTTS() {
  pendingEnable = false;
  pendingSince = 0;
  clearWatchdog();
  try {
    await api("/api/tts/disable", { method: "POST" });
    stopLocal();
  } catch (err) {
    toast(err.message || "Error turning off TTS", "error");
    setChip("error");
  }
}

async function onChip() {
  ensureCtx();
  const t = state.tts;
  const engine = t && t.engine;
  if (!engine) return;
  const srv = engine.state === "running" ? engine.server && engine.server.status : null;
  if (srv === "ready") {
    disableTTS();
    return;
  }
  if (srv === "loading" || (t && t.starting)) {
    disableTTS(); // la prensa cancela la carga/spawn en curso
    return;
  }
  // enable ya disparado pero sin "starting" en el server = fallo ya detectado
  // por applyStatus: permite reintentar de inmediato (antes el guard con
  // pendingEnable tragaba el click y el boton parecia roto)
  enableTTS();
}

async function onPause() {
  ensureCtx();
  const dispatcher = state.tts && state.tts.dispatcher;
  if (!dispatcher) return;
  const url = "/api/tts/" + (dispatcher.paused ? "resume" : "pause");
  try {
    await api(url, { method: "POST" });
    dispatcher.paused = !dispatcher.paused;
    if (dispatcher.paused) {
      if (player) player.pause();
      if (ctx) ctx.suspend();
    } else {
      if (player) player.resume();
      if (ctx) ctx.resume();
    }
    applyStatus();
  } catch (err) {
    toast(err.message || "Error in the TTS control", "error");
    poll();
  }
}

async function onStop() {
  ensureCtx();
  try {
    await api("/api/tts/stop", { method: "POST" });
    stopLocal();
  } catch (err) {
    toast(err.message || "Error stopping TTS", "error");
  }
  poll();
}

export async function initTTS() {
  await poll();
  setInterval(() => {
    if (!document.hidden) poll();
  }, 2000);
  document.getElementById("tts-chip").addEventListener("click", onChip);
  document.getElementById("btn-tts-pause").addEventListener("click", onPause);
  document.getElementById("btn-tts-stop").addEventListener("click", onStop);
}
