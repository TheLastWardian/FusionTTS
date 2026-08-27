// theme.js — tema dark/light con persistencia en localStorage.
import { state } from "./state.js";

const KEY = "ftts_theme";

function apply(theme) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  const icon = document.getElementById("theme-icon");
  if (icon) icon.className = theme === "light" ? "ti ti-sun" : "ti ti-moon";
}

export function initTheme() {
  let saved = "dark";
  try {
    saved = localStorage.getItem(KEY) || "dark";
  } catch {
    saved = "dark";
  }
  apply(saved === "light" ? "light" : "dark");
  document.getElementById("btn-theme").addEventListener("click", toggleTheme);
}

export function toggleTheme() {
  const next = state.theme === "dark" ? "light" : "dark";
  try {
    localStorage.setItem(KEY, next);
  } catch {
  }
  apply(next);
}
