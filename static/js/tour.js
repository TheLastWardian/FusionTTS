// tour.js — tour guiado: spotlight + tarjeta con flecha sobre la UI, sin dependencias.
// Modo "app": arranca solo el primer ingreso (flag localStorage), repetible con el boton "?".
// Modo "demo": arranca solo siempre (lo usa la pagina demo del repo).

import { uiZoom } from "./utils.js";

const SEEN_KEY = "ft.tour.seen";

const APP_STEPS = [
  {
    sel: "#room-list",
    title: "Rooms",
    body: "Each room is an independent conversation with its own history. Switch between them here, or create a new one with the + button below.",
  },
  {
    sel: "#persona-list",
    title: "Characters",
    body: "The personas you can talk to. In the full app each one carries its own voice and photo, and you can add new ones from a .wav sample (right panel → Add persona).",
  },
  {
    sel: "#who-chips",
    title: "Who answers",
    body: "Choose who responds to your next message: the router picks automatically, or click a character's chip to pick them — ctrl+click to select several at once.",
  },
  {
    sel: "#chat-input",
    title: "Chat",
    body: "Write a message and press Enter to send. You can also paste or drag images into the chat — the characters react to what they see.",
  },
  {
    sel: "#tts-chip",
    title: "Text to speech",
    body: "Turn the chip on and the characters read their replies aloud, each with their own reference voice. Pause and stop live right next to it.",
  },
  {
    sel: "#btn-compact",
    title: "Archive & clear",
    body: "The archive summarizes the conversation to free context for the LLM; the trash bin next to it wipes the whole room's history.",
  },
  {
    sel: "#right",
    title: "Config panel",
    body: "Everything is tunable from here, in three tabs: LLM (base URL, model, system prompts, temperature), TTS (steps, speed, language, voice design) and Add persona — upload a .wav sample to clone a new voice. Open it any time from the gear in the top bar.",
  },
];

const DEMO_STEPS = APP_STEPS.map((s) =>
  s.sel === "#tts-chip"
    ? {
        ...s,
        body: "In the full app this chip turns on text-to-speech: the characters read their replies aloud with their own voices. In this demo it stays off — there is no GPU in the browser.",
      }
    : s,
);

const STEPS = { app: APP_STEPS, demo: DEMO_STEPS };

let tour = null;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

export function stopTour() {
  if (!tour) return;
  tour.veil.remove();
  document.removeEventListener("keydown", onKey);
  window.removeEventListener("resize", position);
  tour = null;
}

function finish() {
  if (!tour) return;
  if (tour.mode === "app") {
    try {
      localStorage.setItem(SEEN_KEY, "1");
    } catch {
      // sin localStorage (modo privado estricto): el tour se repira, nada roto
    }
  }
  stopTour();
}

function show(i) {
  if (!tour) return;
  if (i < 0) i = 0;
  if (i >= tour.steps.length) {
    finish();
    return;
  }
  tour.i = i;
  const st = tour.steps[i];
  tour.title.textContent = st.title;
  tour.body.textContent = st.body;
  tour.counter.textContent = (i + 1) + " / " + tour.steps.length;
  tour.back.style.visibility = i === 0 ? "hidden" : "visible";
  tour.next.textContent = i === tour.steps.length - 1 ? "Done" : "Next";
  position();
}

function next() {
  if (!tour) return;
  show(tour.i + 1);
}

function onKey(e) {
  if (!tour) return;
  if (e.key === "Escape") finish();
  else if (e.key === "ArrowRight" || e.key === "Enter") next();
  else if (e.key === "ArrowLeft") show(tour.i - 1);
}

function position() {
  if (!tour) return;
  const step = tour.steps[tour.i];
  const target = document.querySelector(step.sel);
  if (!target) {
    next();
    return;
  }
  target.scrollIntoView({ block: "center", inline: "nearest" });
  const z = uiZoom();
  const R = target.getBoundingClientRect();
  const r = { left: R.left / z, top: R.top / z, width: R.width / z, height: R.height / z, bottom: R.bottom / z };
  const pad = 6;
  tour.spot.style.left = r.left - pad + "px";
  tour.spot.style.top = r.top - pad + "px";
  tour.spot.style.width = r.width + pad * 2 + "px";
  tour.spot.style.height = r.height + pad * 2 + "px";

  const c = tour.card;
  const cw = c.offsetWidth;
  const ch = c.offsetHeight;
  const vw = window.innerWidth / z;
  const vh = window.innerHeight / z;
  const fitsBelow = r.bottom + 14 + ch <= vh - 10;
  const top = fitsBelow ? r.bottom + 14 : Math.max(10, r.top - 14 - ch);
  const cx = r.left + r.width / 2;
  const left = Math.min(Math.max(10, cx - cw / 2), vw - cw - 10);
  c.style.left = left + "px";
  c.style.top = top + "px";

  const a = tour.arrow;
  a.className = "tour-card-arrow " + (fitsBelow ? "at-top" : "at-bottom");
  const ax = Math.min(Math.max(cx - left - 8, 16), cw - 32);
  a.style.left = ax + "px";
}

export function startTour(mode) {
  stopTour();
  const steps = STEPS[mode] || STEPS.app;
  const veil = el("div", "tour-veil");
  const spot = el("div", "tour-spot");
  const card = el("div", "tour-card");
  const arrow = el("div", "tour-card-arrow");
  const title = el("div", "tour-card-title");
  const body = el("div", "tour-card-body");
  const foot = el("div", "tour-card-foot");
  const skip = el("button", "tour-btn", "Skip");
  const back = el("button", "tour-btn", "Back");
  const counter = el("span", "tour-counter");
  const nextBtn = el("button", "tour-btn tour-btn-primary", "Next");
  foot.append(skip, back, counter, nextBtn);
  card.append(arrow, title, body, foot);
  veil.append(spot, card);
  document.body.appendChild(veil);

  tour = { mode, steps, i: 0, veil, spot, card, arrow, title, body, back, counter, next: nextBtn };
  skip.addEventListener("click", finish);
  back.addEventListener("click", () => show(tour.i - 1));
  nextBtn.addEventListener("click", next);
  veil.addEventListener("click", (e) => {
    if (e.target === veil) next();
  });
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", position);
  show(0);
}

let initialized = false;

export function initTour(isDemo) {
  if (initialized) return;
  initialized = true;
  const btn = el("button", "tb-icon tb-icon-sm");
  btn.id = "btn-tour";
  btn.title = "Quick tour";
  btn.setAttribute("aria-label", "Quick tour");
  const ico = el("i", "ti ti-question-mark");
  btn.appendChild(ico);
  const settingsBtn = document.getElementById("btn-settings");
  if (settingsBtn) settingsBtn.parentNode.insertBefore(btn, settingsBtn);
  const start = () => startTour(isDemo ? "demo" : "app");
  btn.addEventListener("click", start);
  window.FusionTour = { start: (m) => startTour(m), stop: stopTour };
  let seen = false;
  if (!isDemo) {
    try {
      seen = !!localStorage.getItem(SEEN_KEY);
    } catch {
      seen = true;
    }
  }
  if (!seen) setTimeout(start, 600);
}
