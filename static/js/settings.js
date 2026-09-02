// settings.js — panel Config: campos reales de app/config.py (KeySpec) con carga y guardado.
import { state } from "./state.js";
import { api, toast, debounce } from "./utils.js";

// Panel LLM: todo lo que ata al modelo de lenguaje y a la conversacion
const LLM_GROUPS = [
  {
    group: "LLM",
    items: [
      { key: "llm_base_url", type: "text", label: "Base URL" },
      { key: "llm_model", type: "text", label: "Modelo", placeholder: "vacío = autodetect" },
      {
        key: "global_system_prompt",
        type: "textarea",
        label: "System prompt global (va antes del prompt de cada persona)",
        placeholder: "reglas globales, vacío = sin prompt global",
        rows: 4,
      },
      {
        key: "newcomer_prompt",
        type: "textarea",
        label: "Personajes nuevos que ingresan a la conversacion (se suma al system prompt)",
        placeholder: "regla de como tratar los mensajes que no presenciaron; vacio = desactivado",
        rows: 4,
      },
      {
        key: "vision_prompt",
        type: "textarea",
        label: "Descripción de imagenes subidas al chat (se envia al LLM)",
        placeholder: "como describir la imagen para que los personajes reaccionen a la accion; vacio = default",
        rows: 3,
      },
      { key: "llm_temperature", type: "range", min: 0, max: 2, step: 0.05, kind: "float", label: "Temperature" },
      { key: "llm_top_p", type: "range", min: 0, max: 1, step: 0.01, kind: "float", label: "Top-p" },
      { key: "llm_max_tokens", type: "number", min: 1, max: 100000, label: "Max tokens" },
    ],
  },
  {
    group: "General",
    items: [
      { key: "max_persona_replies", type: "range", min: 1, max: 5, step: 1, kind: "int", label: "Max replies" },
      { key: "auto_chat_max_turns", type: "range", min: 1, max: 100, step: 1, kind: "int", label: "Auto-chat turns (por mensaje)" },
      { key: "max_context_turns", type: "range", min: 0, max: 500, step: 1, kind: "int", label: "Context turns" },
      { key: "persona_name_mentions", type: "toggle", label: "Menciones por nombre" },
    ],
  },
  {
    group: "Persistencia",
    items: [
      { key: "save_history", type: "toggle", label: "Save history" },
    ],
  },
];

// Panel TTS: todo lo que ata a la sintesis de voz (incluye ASR del audio de
// referencia, que solo existe para alimentar al TTS)
const TTS_GROUPS = [
  {
    group: "TTS",
    items: [
      { key: "tts_mode", type: "select", options: ["sentences", "full"], label: "Modo de audio (por oraciones / bloque completo)" },
      { key: "tts_num_steps", type: "range", min: 1, max: 100, step: 1, kind: "int", label: "Steps" },
      { key: "tts_guidance_scale", type: "range", min: 0.1, max: 3, step: 0.1, kind: "float", label: "Guidance" },
      { key: "tts_speed", type: "range", min: 0.5, max: 2, step: 0.05, kind: "float", label: "Speed" },
      { key: "tts_language", type: "select", inline: true, label: "Language (auto = detectar por persona)" },
      { key: "tts_seed", type: "number", min: 0, max: 4294967295, label: "Seed", placeholder: "vacío = aleatorio", nullable: true },
      { key: "tts_sentence_timeout", type: "range", min: 5, max: 300, step: 1, kind: "int", label: "Timeout", suffix: " s" },
      { key: "silence_ms", type: "range", min: 0, max: 1000, step: 10, kind: "int", label: "Silence", suffix: " ms" },
      { key: "tts_alignment", type: "select", inline: true, options: ["off", "cpu", "gpu"], label: "Resaltado de palabras (karaoke)" },
    ],
  },
  {
    group: "Instruct (voice design)",
    items: [
      {
        key: "tts_instruct",
        type: "instruct",
        categories: [
          { key: "gender", label: "Género", options: ["male", "female"] },
          { key: "age", label: "Edad", options: ["child", "teenager", "young adult", "middle-aged", "elderly"] },
          { key: "pitch", label: "Pitch", options: ["very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"] },
          { key: "style", label: "Estilo", options: ["whisper"] },
          { key: "accent_en", label: "Acento (EN)", options: ["american accent", "british accent", "australian accent", "canadian accent", "indian accent", "chinese accent", "korean accent", "japanese accent", "portuguese accent", "russian accent"] },
          { key: "dialect_zh", label: "Dialecto (ZH)", options: ["河南话", "陕西话", "四川话", "贵州话", "云南话", "桂林话", "济南话", "石家庄话", "甘肃话", "宁夏话", "青岛话", "东北话"] },
        ],
      },
    ],
  },
  {
    group: "ASR",
    items: [
      { key: "asr_model", type: "select", options: ["tiny", "base", "small", "medium", "large-v3"], label: "Whisper model" },
      { key: "asr_device", type: "select", options: ["auto", "cuda", "cpu"], label: "Device" },
      { key: "asr_timeout", type: "range", min: 10, max: 600, step: 5, kind: "int", label: "Timeout", suffix: " s" },
    ],
  },
  {
    group: "VRAM",
    items: [
      { key: "tts_int8", type: "toggle", label: "INT8 (menos VRAM, misma calidad)" },
    ],
  },
  {
    group: "Persistencia",
    items: [
      { key: "save_audio", type: "toggle", label: "Save audio" },
    ],
  },
];

const controls = {};
const pendingQueue = [];

const save = debounce(async () => {
  const jobs = pendingQueue.splice(0, pendingQueue.length);
  for (const job of jobs) {
    try {
      const res = await api("/api/config", { method: "POST", body: { key: job.key, value: job.value } });
      state.config[job.key] = res.value;
      // Si el control ya se movió desde que se encoló el job (drag en curso),
      // no volverlo a arrastrar por los valores intermedios
      if (controlUnchanged(job)) setControl(job.key, res.value);
    } catch (err) {
      toast(err.message || "Error al guardar la configuración", "error");
      setControl(job.key, state.config[job.key]);
    }
  }
}, 400);

function controlUnchanged(job) {
  const c = controls[job.key];
  if (!c) return true;
  if (c.field.type === "toggle") return c.input.classList.contains("on") === (job.value === true);
  if (c.field.type === "instruct") return buildInstructString(c.field, c.selects) === String(job.value ?? "");
  return String(c.input.value ?? "") === String(job.value ?? "");
}

function scheduleSave(key, value) {
  // un solo job por key: el ultimo valor del drag gana (sin cola de intermedios)
  const i = pendingQueue.findIndex((j) => j.key === key);
  if (i >= 0) pendingQueue[i] = { key, value };
  else pendingQueue.push({ key, value });
  save();
}

function fmtValue(v, f) {
  if (f.type !== "range") return String(v);
  const n = f.kind === "int" ? String(v) : String(Math.round(Number(v) * 100) / 100);
  return n + (f.suffix || "");
}

function setControl(key, value) {
  const c = controls[key];
  if (!c) return;
  if (c.field.type === "instruct") {
    const known = new Set();
    for (const cat of c.field.categories) for (const o of cat.options) known.add(o);
    const tokens = String(value ?? "").split(",").map((s) => s.trim()).filter(Boolean);
    const unknown = tokens.filter((t) => !known.has(t));
    for (const cat of c.field.categories) {
      const match = cat.options.find((o) => tokens.includes(o));
      c.selects[cat.key].value = match ?? "auto";
    }
    if (unknown.length) toast(`Instruct con valores no reconocidos: ${unknown.join(", ")} — se descartan al próximo cambio`, "warning");
    return;
  }
  if (c.field.type === "toggle") {
    c.input.classList.toggle("on", !!value);
    c.input.setAttribute("aria-pressed", value ? "true" : "false");
    return;
  }
  if (key === "tts_language") {
    c.input.value = value === null || value === undefined || value === "" ? "auto" : String(value);
    return;
  }
  c.input.value = value === null || value === undefined ? "" : String(value);
  if (c.readout) c.readout.textContent = fmtValue(value, c.field);
}

function ttsLanguageOptions() {
  const langs = new Set(["auto", "en"]);
  for (const p of state.personas) {
    if (p.reference_audio_language) langs.add(p.reference_audio_language);
  }
  return ["auto", "en", ...[...langs].filter((l) => l !== "auto" && l !== "en").sort()];
}

function ttsLanguageDisplay() {
  const v = state.config.tts_language;
  return v === null || v === undefined || v === "" ? "auto" : String(v);
}

// Opciones = auto + en + idiomas detectados en los audios de referencia de las
// personas. Si el valor guardado ya no existe (p. ej. se borro la persona cuyo
// audio era el unico en ese idioma) -> vuelve al default "en" con aviso.
export function refreshTtsLanguageOptions() {
  const c = controls["tts_language"];
  const opts = ttsLanguageOptions();
  const cur = ttsLanguageDisplay();
  if (c) {
    const input = c.input;
    input.textContent = "";
    for (const o of opts) {
      const el = document.createElement("option");
      el.value = o;
      el.textContent = o;
      input.appendChild(el);
    }
  }
  if (!opts.includes(cur)) {
    if (c) c.input.value = "en";
    if (state.config.tts_language !== "en") {
      toast(`tts_language "${cur}" ya no está disponible; vuelvo a "en"`, "warning");
      scheduleSave("tts_language", "en");
    }
    return;
  }
  if (c) c.input.value = cur;
}

// instruct (voice design): un selector por categoria (doc k2-fsa/OmniVoice
// docs/voice-design.md) que arma el string comma-separated; todo en "auto" -> ""
function buildInstructString(f, selects) {
  const parts = [];
  for (const cat of f.categories) {
    const v = selects[cat.key].value;
    if (v && v !== "auto") parts.push(v);
  }
  return parts.join(", ");
}

function buildInstructField(f) {
  // sin label propio: el grupo se renderiza con su titulo .cfg-label como los otros
  const wrap = document.createElement("div");
  wrap.className = "cfg-instruct";
  const grid = document.createElement("div");
  grid.className = "instruct-grid";
  const selects = {};
  for (const cat of f.categories) {
    const l = document.createElement("label");
    l.textContent = cat.label;
    const sel = document.createElement("select");
    for (const o of ["auto", ...cat.options]) {
      const el = document.createElement("option");
      el.value = o;
      el.textContent = o;
      sel.appendChild(el);
    }
    sel.addEventListener("change", () => scheduleSave(f.key, buildInstructString(f, selects)));
    selects[cat.key] = sel;
    grid.append(l, sel);
  }
  wrap.appendChild(grid);
  const note = document.createElement("div");
  note.className = "cfg-note";
  note.textContent = "Con audio de referencia (voice cloning) no cambia rasgos: la referencia gana los conflictos y el instruct solo refuerza rasgos consistentes (p. ej. dialecto chino). Sin referencia (auto/design) define la voz.";
  wrap.appendChild(note);
  controls[f.key] = { field: f, input: null, readout: null, selects };
  setControl(f.key, state.config[f.key]);
  return wrap;
}

function buildField(f) {
  if (f.type === "instruct") return buildInstructField(f);
  let wrap, input, readout = null;

  if (f.type === "range") {
    wrap = document.createElement("div");
    wrap.className = "slider-r";
    const label = document.createElement("label");
    label.textContent = f.label;
    input = document.createElement("input");
    input.type = "range";
    input.min = String(f.min);
    input.max = String(f.max);
    input.step = String(f.step);
    readout = document.createElement("span");
    wrap.append(label, input, readout);
  } else if (f.type === "toggle") {
    wrap = document.createElement("div");
    wrap.className = "toggle-r";
    const label = document.createElement("label");
    label.textContent = f.label;
    input = document.createElement("button");
    input.type = "button";
    input.className = "tog";
    input.setAttribute("aria-label", f.label);
    wrap.append(label, input);
  } else if (f.type === "select") {
    wrap = document.createElement("div");
    wrap.className = f.inline ? "cfg-field cfg-field-inline" : "cfg-field";
    const label = document.createElement("label");
    label.textContent = f.label;
    input = document.createElement("select");
    for (const opt of f.options || []) {
      const o = document.createElement("option");
      o.value = opt;
      o.textContent = opt;
      input.appendChild(o);
    }
    wrap.append(label, input);
  } else {
    wrap = document.createElement("div");
    wrap.className = "cfg-field";
    const label = document.createElement("label");
    label.textContent = f.label;
    if (f.type === "textarea") {
      input = document.createElement("textarea");
      input.rows = f.rows || 2;
    } else {
      input = document.createElement("input");
      input.type = f.type;
      if (f.type === "number") {
        input.min = String(f.min);
        input.max = String(f.max);
      }
      if (f.placeholder) input.placeholder = f.placeholder;
    }
    input.spellcheck = false;
    wrap.append(label, input);
  }

  controls[f.key] = { field: f, input, readout };
  if (f.key === "tts_language") refreshTtsLanguageOptions();
  else setControl(f.key, state.config[f.key]);
  wireField(f.key);
  return wrap;
}

function wireField(key) {
  const { field: f, input, readout } = controls[key];
  if (f.type === "range") {
    input.addEventListener("input", () => {
      const v = f.kind === "int" ? parseInt(input.value, 10) : parseFloat(input.value);
      if (readout) readout.textContent = fmtValue(v, f);
      scheduleSave(key, v);
    });
  } else if (f.type === "toggle") {
    input.addEventListener("click", () => {
      const next = !input.classList.contains("on");
      input.classList.toggle("on", next);
      input.setAttribute("aria-pressed", next ? "true" : "false");
      scheduleSave(key, next);
    });
  } else if (f.type === "select") {
    input.addEventListener("change", () => {
      const v = key === "tts_language" && input.value === "auto" ? "" : input.value;
      scheduleSave(key, v);
    });
  } else if (f.type === "number") {
    input.addEventListener("change", () => {
      if (input.value === "") {
        if (f.nullable) scheduleSave(key, null);
        else setControl(key, state.config[key]);
        return;
      }
      const v = parseInt(input.value, 10);
      if (Number.isNaN(v)) {
        setControl(key, state.config[key]);
        return;
      }
      scheduleSave(key, v);
    });
  } else {
    input.addEventListener("input", () => scheduleSave(key, input.value));
  }
}

function buildPanel(panelId, groups) {
  const panel = document.getElementById(panelId);
  panel.textContent = "";
  for (const group of groups) {
    const g = document.createElement("div");
    g.className = "cfg-group";
    const label = document.createElement("div");
    label.className = "cfg-label";
    label.textContent = group.group;
    g.appendChild(label);
    for (const f of group.items) g.appendChild(buildField(f));
    panel.appendChild(g);
  }
}

export async function initSettings() {
  state.config = await api("/api/config");
  buildPanel("rpanel-llm", LLM_GROUPS);
  buildPanel("rpanel-tts", TTS_GROUPS);
}
