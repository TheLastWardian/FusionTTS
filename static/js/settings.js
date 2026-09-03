// settings.js — panel Config: campos reales de app/config.py (KeySpec) con carga y guardado.
import { state } from "./state.js";
import { api, toast, debounce } from "./utils.js";

// Panel LLM: todo lo que ata al modelo de lenguaje y a la conversacion
const LLM_GROUPS = [
  {
    group: "LLM",
    items: [
      { key: "llm_base_url", type: "text", label: "Base URL" },
      { key: "llm_model", type: "text", label: "Model", placeholder: "empty = autodetect" },
      {
        key: "global_system_prompt",
        type: "textarea",
        label: "System prompt global (goes before each persona's prompt)",
        placeholder: "global rules, empty = no global prompt",
        rows: 4,
      },
      {
        key: "newcomer_prompt",
        type: "textarea",
        label: "New characters entering the conversation (added to the system prompt)",
        placeholder: "rule for how to handle messages they did not witness; empty = disabled",
        rows: 4,
      },
      {
        key: "vision_prompt",
        type: "textarea",
        label: "Description of images uploaded to the chat (sent to the LLM)",
        placeholder: "how to describe the image so the characters react to the action; empty = default",
        rows: 3,
      },
      {
        key: "llm_temperature",
        type: "range",
        min: 0,
        max: 2,
        step: 0.05,
        kind: "float",
        label: "Temperature",
        tip: "Creativity of the replies: low = literal and focused, high = varied and wild. Lower it if a character drifts out of character.",
      },
      {
        key: "llm_top_p",
        type: "range",
        min: 0,
        max: 1,
        step: 0.01,
        kind: "float",
        label: "Top-p",
        tip: "Nucleus sampling: only candidates inside the top X% of probability are considered. Temperature alone is enough for most setups — leave it unless you know why you are changing it.",
      },
      {
        key: "llm_max_tokens",
        type: "number",
        min: 1,
        max: 100000,
        label: "Max tokens",
        tip: "Maximum length of a single character reply. Higher allows longer replies (slower, more VRAM); the reply also stops when the character is done.",
      },
    ],
  },
  {
    group: "General",
    items: [
      {
        key: "max_persona_replies",
        type: "range",
        min: 1,
        max: 5,
        step: 1,
        kind: "int",
        label: "Max replies",
        tip: "How many characters may answer one of your messages. The router decides who, in which order.",
      },
      {
        key: "auto_chat_max_turns",
        type: "range",
        min: 1,
        max: 100,
        step: 1,
        kind: "int",
        label: "Auto-chat turns (per message)",
        tip: "In auto-chat rooms the characters keep talking among themselves after your message. This is the cap of extra turns per message (the room stops when it is reached or you press stop).",
        tipLink: ".room-auto",
      },
      {
        key: "max_context_turns",
        type: "range",
        min: 0,
        max: 500,
        step: 1,
        kind: "int",
        label: "Context turns",
        tip: "How many recent turns of the room are sent to the LLM as context. Higher = more continuity, slower and more VRAM. 0 = every message starts from scratch.",
      },
      {
        key: "persona_name_mentions",
        type: "toggle",
        label: "Name mentions",
        tip: "Only used when “Who answers” is the LLM router: if your message names exactly one character (e.g. “Aria, …”), that character replies directly, skipping the router call. Two or more names (or none) go to the router as usual.",
      },
    ],
  },
  {
    group: "Persistence",
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
      { key: "omnivoice_dir", type: "text", label: "OmniVoice repo path (empty = sibling ..\\OmniVoice)", placeholder: "empty = ..\\OmniVoice" },
      {
        key: "tts_mode",
        type: "select",
        options: ["sentences", "full"],
        label: "Audio mode (per sentence / full block)",
        tip: "sentences: each sentence is synthesized while the reply is still being written (lowest latency). full: waits for the whole reply and synthesizes it in blocks (more natural phrasing, starts later).",
      },
      {
        key: "tts_num_steps",
        type: "range",
        min: 1,
        max: 100,
        step: 1,
        kind: "int",
        label: "Steps",
        tip: "OmniVoice diffusion steps: more steps = better voice quality, slower synthesis.",
      },
      {
        key: "tts_guidance_scale",
        type: "range",
        min: 0.1,
        max: 3,
        step: 0.1,
        kind: "float",
        label: "Guidance",
        tip: "How strongly the generated voice follows the reference voice / voice design: higher = more faithful to the reference, lower = freer.",
      },
      { key: "tts_speed", type: "range", min: 0.5, max: 2, step: 0.05, kind: "float", label: "Speed" },
      { key: "tts_language", type: "select", inline: true, label: "Language (auto = detect per persona)" },
      {
        key: "tts_seed",
        type: "number",
        min: 0,
        max: 4294967295,
        label: "Seed",
        placeholder: "empty = random",
        nullable: true,
        tip: "Same seed + same text = exactly the same audio (useful to reproduce a result you liked). Empty = random every time.",
      },
      {
        key: "tts_sentence_timeout",
        type: "range",
        min: 5,
        max: 300,
        step: 1,
        kind: "int",
        label: "Timeout",
        suffix: " s",
        tip: "Max seconds allowed to synthesize one sentence. If it takes longer the sentence is aborted and the rest of the reply keeps going. Raise it on slow hardware.",
      },
      {
        key: "silence_ms",
        type: "range",
        min: 0,
        max: 1000,
        step: 10,
        kind: "int",
        label: "Silence",
        suffix: " ms",
        tip: "Pause inserted between sentences while the character speaks.",
      },
      {
        key: "tts_alignment",
        type: "select",
        inline: true,
        options: ["off", "cpu", "gpu"],
        label: "Word highlight (karaoke)",
        tip: "Highlights the word being spoken. gpu = lowest latency (uses VRAM), cpu = slower, off = disabled. Applies when the TTS server is (re)started.",
      },
    ],
  },
  {
    group: "Instruct (voice design)",
    items: [
      {
        key: "tts_instruct",
        type: "instruct",
        categories: [
          { key: "gender", label: "Gender", options: ["male", "female"] },
          { key: "age", label: "Age", options: ["child", "teenager", "young adult", "middle-aged", "elderly"] },
          { key: "pitch", label: "Pitch", options: ["very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"] },
          { key: "style", label: "Style", options: ["whisper"] },
          { key: "accent_en", label: "Accent (EN)", options: ["american accent", "british accent", "australian accent", "canadian accent", "indian accent", "chinese accent", "korean accent", "japanese accent", "portuguese accent", "russian accent"] },
          { key: "dialect_zh", label: "Dialect (ZH)", options: ["河南话", "陕西话", "四川话", "贵州话", "云南话", "桂林话", "济南话", "石家庄话", "甘肃话", "宁夏话", "青岛话", "东北话"] },
        ],
      },
    ],
  },
  {
    group: "ASR",
    items: [
      {
        key: "asr_model",
        type: "select",
        options: ["tiny", "base", "small", "medium", "large-v3"],
        label: "Whisper model",
        tip: "ASR model used to transcribe your microphone and the .wav samples of new characters: bigger = more accurate, slower and heavier.",
      },
      { key: "asr_device", type: "select", options: ["auto", "cuda", "cpu"], label: "Device" },
      { key: "asr_timeout", type: "range", min: 10, max: 600, step: 5, kind: "int", label: "Timeout", suffix: " s" },
    ],
  },
  {
    group: "VRAM",
    items: [
      {
        key: "tts_int8",
        type: "toggle",
        label: "INT8 (less VRAM, same quality)",
        tip: "Runs the OmniVoice model in 8-bit: about half the VRAM with negligible quality loss. Enable it if the TTS server does not fit next to your LLM. Applies when the TTS server is (re)started.",
      },
    ],
  },
  {
    group: "Persistence",
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
      toast(err.message || "Error saving the settings", "error");
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
    if (unknown.length) toast(`Instruct with unrecognized values: ${unknown.join(", ")} — they will be dropped on the next change`, "warning");
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
      toast(`tts_language "${cur}" is no longer available; falling back to "en"`, "warning");
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
  note.textContent = "With a reference audio (voice cloning) it does not change traits: the reference wins conflicts and the instruct only reinforces consistent traits (e.g. Chinese dialect). Without a reference (auto/design) it defines the voice.";
  wrap.appendChild(note);
  controls[f.key] = { field: f, input: null, readout: null, selects };
  setControl(f.key, state.config[f.key]);
  return wrap;
}

function buildField(f) {
  if (f.type === "instruct") return buildInstructField(f);
  let wrap, input, readout = null, label = null;

  if (f.type === "range") {
    wrap = document.createElement("div");
    wrap.className = "slider-r";
    label = document.createElement("label");
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
    label = document.createElement("label");
    label.textContent = f.label;
    input = document.createElement("button");
    input.type = "button";
    input.className = "tog";
    input.setAttribute("aria-label", f.label);
    wrap.append(label, input);
  } else if (f.type === "select") {
    wrap = document.createElement("div");
    wrap.className = f.inline ? "cfg-field cfg-field-inline" : "cfg-field";
    label = document.createElement("label");
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
    label = document.createElement("label");
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

  if (f.tip && label) label.title = f.tip;
  // tipLink: mientras aparece el tooltip del label, parpadean los elementos
  // relacionados (delay ~900ms para sincronizarse con el tooltip nativo)
  if (f.tipLink && label) {
    let t = null;
    label.addEventListener("mouseenter", () => {
      t = setTimeout(
        () => document.querySelectorAll(f.tipLink).forEach((n) => n.classList.add("tip-link-pulse")),
        900
      );
    });
    label.addEventListener("mouseleave", () => {
      clearTimeout(t);
      document.querySelectorAll(f.tipLink).forEach((n) => n.classList.remove("tip-link-pulse"));
    });
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
