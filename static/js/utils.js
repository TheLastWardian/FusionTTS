// utils.js — helpers: fetch JSON, escape, toasts, debounce, iniciales.
export async function api(path, opts = {}) {
  const options = { method: opts.method || "GET" };
  if (opts.body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, options);
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    let detail = body ? body.detail : undefined;
    if (detail === undefined) detail = body ? JSON.stringify(body) : res.statusText;
    if (typeof detail !== "string") detail = JSON.stringify(detail);
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return body;
}

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function toast(msg, type = "info") {
  const box = document.getElementById("toasts");
  const t = document.createElement("div");
  t.className = "toast toast-" + type;
  t.textContent = msg;
  t.title = "Click para cerrar";
  box.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  let closed = false;
  const kill = () => {
    if (closed) return;
    closed = true;
    t.classList.remove("show");
    setTimeout(() => t.remove(), 200);
  };
  setTimeout(kill, 4000);
  t.addEventListener("click", kill);
}

export function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

const HEX_COLOR = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

export function avatarCss(p) {
  const color = HEX_COLOR.test(p.avatar_color || "") ? p.avatar_color : "var(--accent)";
  return `background: color-mix(in srgb, ${color} 18%, var(--bg2)); color: ${color};`;
}

export function initials(name) {
  return String(name)
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
}
