// app.js — bootstrap de la SPA: tema, layout, health check e inicialización de módulos.
import { api, toast } from "./utils.js";
import { initTheme } from "./theme.js";
import { initLayout, setRightTab, isNarrow, openRightDrawer } from "./layout.js";
import { initSettings } from "./settings.js";
import { initPersonas } from "./personas.js";
import { initRooms } from "./rooms.js";
import { initChat } from "./chat.js";
import { initTTS } from "./tts.js";
import { state } from "./state.js";
import { loadHistory } from "./persistence.js";

initTheme();
initLayout();

document.getElementById("btn-settings").addEventListener("click", () => {
  if (isNarrow()) openRightDrawer();
  else setRightTab("settings");
});
document.getElementById("tab-settings").addEventListener("click", () => setRightTab("settings"));
document.getElementById("tab-personas").addEventListener("click", () => setRightTab("personas"));

try {
  await api("/api/health");
  const results = await Promise.allSettled([initSettings(), initPersonas(), initRooms(), initChat(), initTTS()]);
  for (const r of results) {
    if (r.status === "rejected") {
      toast((r.reason && r.reason.message) || "Error al inicializar un módulo", "error");
    }
  }
} catch {
  document.getElementById("api-banner").classList.add("show");
}
if (state.room) loadHistory(state.room);
