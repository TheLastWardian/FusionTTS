// tts.js — chip de estado TTS con polling de /api/tts/status + controles pause/stop.
import { state } from "./state.js";
import { api, toast } from "./utils.js";

let watchdog = null;
let pendingEnable = false;
let ctx = null;
let player = null;
let currentSrc = null;
let audioHinted = false;
let gen = 0;
let decodeSeq = Promise.resolve();

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
    onPlay: scheduleSource,
    onDrain: () => {},
  });
  ctx.resume();
  if (ctx.state === "suspended" && !audioHinted) {
    audioHinted = true;
    toast("El navegador bloqueó el audio; tocá el chip TTS para habilitarlo", "error");
  }
  return ctx;
}

function scheduleSource(buf) {
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(ctx.destination);
  src.onended = () => {
    currentSrc = null;
    player.currentEnded();
  };
  src.start();
  currentSrc = src;
}

function base64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function stopLocal() {
  gen++;
  if (currentSrc) {
    try {
      currentSrc.stop();
    } catch {}
  }
  if (player) player.stop();
}

function decodeAndEnqueue(arrayBuffer) {
  const g = gen;
  const job = decodeSeq.then(async () => {
    if (g !== gen) return;
    const buf = await ctx.decodeAudioData(arrayBuffer);
    if (g !== gen) return;
    player.enqueue(buf);
  });
  decodeSeq = job.catch((err) => {
    console.warn("tts.js: no se pudo decodificar el audio:", err && err.message ? err.message : err);
  });
}

export function feedAudioChunk(ev) {
  if (!ttsReady()) return;
  ensureCtx();
  try {
    decodeAndEnqueue(base64ToBytes(ev.audio).buffer);
  } catch (err) {
    console.warn("tts.js: base64 inválido en audio_chunk:", err && err.message ? err.message : err);
  }
}

export function onTTSEvent(ev) {
  if (ev.state === "on") ensureCtx();
  else if (ev.state === "stopped") stopLocal();
}

export async function replayTTS(text, persona) {
  if (!ttsReady()) {
    toast("TTS no está activo", "error");
    return false;
  }
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
      toast("TTS no está activo", "error");
      return false;
    }
    ensureCtx();
    const bytes = new Uint8Array(await res.arrayBuffer());
    decodeAndEnqueue(bytes.buffer);
    return true;
  } catch (err) {
    toast(err && err.message ? err.message : "Error al reproducir", "error");
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
    loading: "TTS · cargando…",
    active: "TTS · listo",
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
  const { engine, dispatcher } = state.tts || {};
  if (!engine) return;
  const ready = engine.state === "running" && engine.server && engine.server.status === "ready";

  if (ready && pendingEnable) {
    pendingEnable = false;
    clearWatchdog();
  }

  let st;
  if (engine.state !== "running") st = "off";
  else if (engine.server && engine.server.status === "ready") st = "active";
  else st = "loading";
  setChip(st);

  const pauseBtn = document.getElementById("btn-tts-pause");
  const stopBtn = document.getElementById("btn-tts-stop");
  const paused = !!(dispatcher && dispatcher.paused);
  pauseBtn.disabled = !ready;
  stopBtn.disabled = !ready;
  pauseBtn.querySelector("i").className = "ti " + (paused ? "ti-player-play" : "ti-player-pause");
  pauseBtn.title = paused ? "Reanudar" : "Pausar";
  pauseBtn.setAttribute("aria-label", paused ? "Reanudar TTS" : "Pausar TTS");

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
    clearWatchdog();
    watchdog = setTimeout(async () => {
      pendingEnable = false;
      watchdog = null;
      const server = state.tts && state.tts.engine && state.tts.engine.server;
      if (!server || server.status !== "ready") {
        toast("TTS no quedó listo a tiempo; se desactiva", "error");
        try {
          await api("/api/tts/disable", { method: "POST" });
        } catch {
        }
        setChip("off");
      }
    }, 60000);
  } catch (err) {
    toast(err.message || "Error al encender el TTS", "error");
    setChip("error");
  }
}

async function disableTTS() {
  try {
    await api("/api/tts/disable", { method: "POST" });
    stopLocal();
  } catch (err) {
    toast(err.message || "Error al apagar el TTS", "error");
    setChip("error");
  }
}

async function onChip() {
  ensureCtx();
  const engine = state.tts && state.tts.engine;
  if (!engine) return;
  if (engine.state === "running") {
    if (!pendingEnable) disableTTS();
    return;
  }
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
    toast(err.message || "Error en el control de TTS", "error");
    poll();
  }
}

async function onStop() {
  ensureCtx();
  try {
    await api("/api/tts/stop", { method: "POST" });
    stopLocal();
  } catch (err) {
    toast(err.message || "Error al detener el TTS", "error");
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
