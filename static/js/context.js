// Indicador de uso de contexto del room (topbar): tokens que ocupa el
// contexto real que se enviaria al LLM (sonda al server por evento).
import { api } from "./utils.js";
import { state } from "./state.js";

let inFlight = false;
let lastGood = null;

function fmt(n) {
  if (n == null) return "—";
  if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return String(n);
}

function renderUsage(d, box, num, stale) {
  box.hidden = false;
  const pct = d.percent ?? 0;
  // Piso de visibilidad: con 160k de ventana el uso real casi siempre es
  // <1% (lineal seria sub-pixel, invisible). Si hay uso, se marca con un
  // piso chico (~5%, "la linea inicial"); de ahi crece 1:1 con el % real.
  const hasUsage = (d.prompt_tokens ?? 0) > 0;
  const visual = hasUsage ? Math.max(5, Math.min(pct, 100)) : 0;
  box.style.setProperty("--ctx-pct", visual);
  box.classList.toggle("warn", pct >= 50 && pct < 80);
  box.classList.toggle("crit", pct >= 80);
  box.classList.toggle("stale", stale);
  num.textContent = d.context_window
    ? fmt(d.prompt_tokens) + "/" + fmt(d.context_window)
    : fmt(d.prompt_tokens);
  const turnsPart =
    d.turns_included === d.history_total
      ? d.history_total + " messages in context"
      : "the last " + d.turns_included + " of " + d.history_total +
        " messages (limit: " + d.turns_included + ")";
  const pctPart = d.context_window ? " (" + pct + "%)" : "";
  box.title =
    "Context: " + fmt(d.prompt_tokens) + " of " +
    (d.context_window ? fmt(d.context_window) : "?") + " tokens" + pctPart +
    " · " + turnsPart +
    ". The limit is changed in Config → LLM → 'Context turns' (max_context_turns): " +
    "how many history messages the LLM sees. The circle has a visibility " +
    "floor (~5%) when there is usage; from there it grows 1:1 with the real %." +
    (stale ? " · stale value: the LLM server did not answer the last probe" : "");
}

export async function refreshContextUsage() {
  if (inFlight) return;
  inFlight = true;
  const box = document.getElementById("ctx-usage");
  const num = document.getElementById("ctx-num");
  try {
    const d = await api(
      "/api/rooms/" + encodeURIComponent(state.room) + "/context-usage",
    );
    lastGood = d;
    renderUsage(d, box, num, false);
  } catch {
    // server LLM ocupado/caido: con un ultimo valor conocido se mantiene
    // (opaco); sin uno, "..." mientras la sonda no pueda responder
    if (lastGood) {
      renderUsage(lastGood, box, num, true);
    } else {
      box.hidden = false;
      box.classList.remove("warn", "crit", "stale");
      box.style.setProperty("--ctx-pct", 0);
      num.textContent = "…";
      box.title = "Querying context usage from the LLM server…";
    }
  } finally {
    inFlight = false;
  }
}
