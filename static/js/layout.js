// layout.js — modos responsive (wide/mid/narrow), gutters arrastrables, colapso y drawers.
import { state } from "./state.js";
import { debounce } from "./utils.js";

const LS_KEY = "ftts_layout";
const LIMITS = {
  wide: { left: [150, 420], right: [220, 500], chatMin: 320 },
  mid: { left: [140, 320], right: [200, 420], chatMin: 320 },
};

function computeMode(w) {
  if (w >= 1200) return "wide";
  if (w >= 900) return "mid";
  return "narrow";
}

function restore() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_KEY) || "null");
    if (!raw || typeof raw !== "object") return;
    const clamp = (v, [a, b]) =>
      typeof v === "number" && isFinite(v) ? Math.min(b, Math.max(a, Math.round(v))) : undefined;
    const l = clamp(raw.left, LIMITS.wide.left);
    const r = clamp(raw.right, LIMITS.wide.right);
    if (l !== undefined) state.layout.left = l;
    if (r !== undefined) state.layout.right = r;
    const rh = clamp(raw.roomsH, [110, 800]);
    if (rh !== undefined) state.layout.roomsH = rh;
    state.layout.leftCollapsed = !!raw.leftCollapsed;
    state.layout.rightCollapsed = !!raw.rightCollapsed;
  } catch {
  }
}

const persist = debounce(() => {
  try {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify({
        left: state.layout.left,
        right: state.layout.right,
        leftCollapsed: state.layout.leftCollapsed,
        rightCollapsed: state.layout.rightCollapsed,
        roomsH: state.layout.roomsH,
      })
    );
  } catch {
  }
}, 250);

function applyWidths() {
  const app = document.getElementById("app");
  app.style.setProperty("--left-w", state.layout.leftCollapsed ? "0px" : state.layout.left + "px");
  app.style.setProperty("--right-w", state.layout.rightCollapsed ? "0px" : state.layout.right + "px");
  app.style.setProperty("--rooms-h", state.layout.roomsH + "px");
  app.classList.toggle("left-collapsed", state.layout.leftCollapsed);
  app.classList.toggle("right-collapsed", state.layout.rightCollapsed);
}

function clampLeft(w) {
  const lim = LIMITS[state.mode] || LIMITS.wide;
  const total = document.getElementById("app").clientWidth;
  const rightW = state.layout.rightCollapsed ? 0 : state.layout.right;
  const upper = Math.max(lim.left[0], Math.min(lim.left[1], total - rightW - lim.chatMin));
  return Math.min(upper, Math.max(lim.left[0], w));
}

function clampRight(w) {
  const lim = LIMITS[state.mode] || LIMITS.wide;
  const total = document.getElementById("app").clientWidth;
  const leftW = state.layout.leftCollapsed ? 0 : state.layout.left;
  const upper = Math.max(lim.right[0], Math.min(lim.right[1], total - leftW - lim.chatMin));
  return Math.min(upper, Math.max(lim.right[0], w));
}

function setMode(mode) {
  const wasNarrow = state.mode === "narrow";
  state.mode = mode;
  document.body.classList.remove("mode-wide", "mode-mid", "mode-narrow");
  document.body.classList.add("mode-" + mode);
  if (mode === "narrow") {
    closeDrawers();
  } else if (wasNarrow) {
    applyWidths();
  }
  updateCollapseButtons();
}

export function isNarrow() {
  return state.mode === "narrow";
}

export function openRightDrawer() {
  if (!isNarrow()) return;
  document.body.classList.remove("drawer-open-left");
  document.body.classList.add("drawer-open-right");
}

export function closeDrawers() {
  document.body.classList.remove("drawer-open-left", "drawer-open-right");
}

export function setRightTab(tab) {
  document.getElementById("tab-llm").classList.toggle("active", tab === "llm");
  document.getElementById("tab-tts").classList.toggle("active", tab === "tts");
  document.getElementById("tab-personas").classList.toggle("active", tab === "personas");
  document.getElementById("rpanel-llm").classList.toggle("active", tab === "llm");
  document.getElementById("rpanel-tts").classList.toggle("active", tab === "tts");
  document.getElementById("rpanel-personas").classList.toggle("active", tab === "personas");
  document.getElementById("btn-settings").classList.toggle("active-tab", tab === "llm" || tab === "tts");
  if (isNarrow()) openRightDrawer();
}

function updateCollapseButtons() {
  const gL = document.getElementById("gutter-left");
  const gR = document.getElementById("gutter-right");
  gL.querySelector("i").className = "ti " + (state.layout.leftCollapsed ? "ti-chevrons-right" : "ti-chevrons-left");
  gR.querySelector("i").className = "ti " + (state.layout.rightCollapsed ? "ti-chevrons-left" : "ti-chevrons-right");
  gL.querySelector("button").title = state.layout.leftCollapsed ? "Expand panel" : "Collapse panel";
  gR.querySelector("button").title = state.layout.rightCollapsed ? "Expand panel" : "Collapse panel";
}

function toggleCollapsed(side) {
  if (isNarrow()) return;
  if (side === "left") state.layout.leftCollapsed = !state.layout.leftCollapsed;
  else state.layout.rightCollapsed = !state.layout.rightCollapsed;
  applyWidths();
  updateCollapseButtons();
  persist();
}

function initGutter(gutter, which) {
  gutter.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".gutter-btn") || isNarrow()) return;
    e.preventDefault();
    gutter.setPointerCapture(e.pointerId);
    gutter.classList.add("active");
    document.body.classList.add("resizing");

    const onMove = (ev) => {
      const rect = document.getElementById("app").getBoundingClientRect();
      const w = which === "left" ? clampLeft(ev.clientX - rect.left) : clampRight(rect.right - ev.clientX);
      if (which === "left") {
        state.layout.leftCollapsed = false;
        state.layout.left = Math.round(w);
      } else {
        state.layout.rightCollapsed = false;
        state.layout.right = Math.round(w);
      }
      applyWidths();
      persist();
    };
    const onUp = (ev) => {
      if (gutter.hasPointerCapture && gutter.hasPointerCapture(ev.pointerId)) {
        gutter.releasePointerCapture(ev.pointerId);
      }
      gutter.removeEventListener("pointermove", onMove);
      gutter.removeEventListener("pointerup", onUp);
      gutter.removeEventListener("pointercancel", onUp);
      gutter.classList.remove("active");
      document.body.classList.remove("resizing");
      updateCollapseButtons();
      persist();
    };
    gutter.addEventListener("pointermove", onMove);
    gutter.addEventListener("pointerup", onUp);
    gutter.addEventListener("pointercancel", onUp);
  });
}

function initRoomsGutter() {
  const gutter = document.getElementById("gutter-rooms");
  if (!gutter) return;
  gutter.addEventListener("pointerdown", (e) => {
    if (isNarrow()) return;
    e.preventDefault();
    gutter.setPointerCapture(e.pointerId);
    gutter.classList.add("active");
    document.body.classList.add("resizing-v");
    const left = document.getElementById("left");

    const onMove = (ev) => {
      const rect = left.getBoundingClientRect();
      const h = Math.round(rect.bottom - ev.clientY);
      state.layout.roomsH = Math.min(rect.height - 130, Math.max(110, h));
      applyWidths();
      persist();
    };
    const onUp = (ev) => {
      if (gutter.hasPointerCapture && gutter.hasPointerCapture(ev.pointerId)) {
        gutter.releasePointerCapture(ev.pointerId);
      }
      gutter.removeEventListener("pointermove", onMove);
      gutter.removeEventListener("pointerup", onUp);
      gutter.removeEventListener("pointercancel", onUp);
      gutter.classList.remove("active");
      document.body.classList.remove("resizing-v");
      persist();
    };
    gutter.addEventListener("pointermove", onMove);
    gutter.addEventListener("pointerup", onUp);
    gutter.addEventListener("pointercancel", onUp);
  });
}

export function initLayout() {
  restore();
  applyWidths();
  setMode(computeMode(window.innerWidth));

  let timer = null;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => setMode(computeMode(window.innerWidth)), 150);
  });

  initGutter(document.getElementById("gutter-left"), "left");
  initGutter(document.getElementById("gutter-right"), "right");
  initRoomsGutter();
  document.getElementById("btn-collapse-left").addEventListener("click", () => toggleCollapsed("left"));
  document.getElementById("btn-collapse-right").addEventListener("click", () => toggleCollapsed("right"));
  document.getElementById("drawer-overlay").addEventListener("click", closeDrawers);
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawers();
  });
}
