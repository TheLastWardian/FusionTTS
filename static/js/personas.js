// personas.js — sidebar de personas, panel de detalle y who-chips (display; edición en T17).
import { state } from "./state.js";
import { api, avatarCss, initials, toast } from "./utils.js";
import { setRightTab } from "./layout.js";

function renderSidebar() {
  const list = document.getElementById("persona-list");
  list.textContent = "";
  if (state.personas.length === 0) {
    const empty = document.createElement("div");
    empty.className = "list-empty";
    empty.textContent = "Sin personas (upload llega en T17)";
    list.appendChild(empty);
    return;
  }
  for (const p of state.personas) {
    const item = document.createElement("div");
    item.className = "persona";
    item.dataset.name = p.name;

    const av = document.createElement("div");
    av.className = "avatar";
    av.style.cssText = avatarCss(p);
    av.textContent = initials(p.name);

    const info = document.createElement("div");
    info.className = "persona-info";
    const name = document.createElement("div");
    name.className = "persona-name";
    name.textContent = p.name;
    info.appendChild(name);
    if (p.reference_audio_language) {
      const lang = document.createElement("div");
      lang.className = "persona-lang";
      lang.textContent = p.reference_audio_language;
      info.appendChild(lang);
    }

    item.appendChild(av);
    item.appendChild(info);
    if (p.tts_capable) {
      const vol = document.createElement("i");
      vol.className = "ti ti-volume persona-vol";
      item.appendChild(vol);
    }
    item.addEventListener("click", () => selectPersona(p.name, { openPanel: true }));
    list.appendChild(item);
  }
}

function renderDetail(p) {
  const panel = document.getElementById("rpanel-personas");
  panel.textContent = "";

  const head = document.createElement("div");
  head.className = "pd-head";
  const av = document.createElement("div");
  av.className = "pd-avatar";
  av.style.cssText = avatarCss(p);
  av.textContent = initials(p.name);
  const meta = document.createElement("div");
  const name = document.createElement("div");
  name.className = "pd-name";
  name.textContent = p.name;
  const desc = document.createElement("div");
  desc.className = "pd-subdesc";
  desc.textContent = p.description || "";
  meta.append(name, desc);
  head.append(av, meta);
  panel.appendChild(head);

  const personality = document.createElement("div");
  personality.className = "pd-section";
  const plabel = document.createElement("div");
  plabel.className = "pd-slabel";
  plabel.textContent = "Personalidad";
  const ptext = document.createElement("p");
  ptext.textContent = p.system_prompt || "";
  personality.append(plabel, ptext);
  panel.appendChild(personality);

  const voice = document.createElement("div");
  voice.className = "pd-section";
  const vlabel = document.createElement("div");
  vlabel.className = "pd-slabel";
  vlabel.textContent = "Voice reference";
  const row = document.createElement("div");
  row.className = "wav-row";
  const ico = document.createElement("i");
  ico.className = "ti ti-file-music";
  ico.style.fontSize = "15px";
  ico.style.color = "var(--text-muted)";
  row.appendChild(ico);
  const wavName = document.createElement("span");
  wavName.className = "wav-name";
  wavName.textContent = p.reference_audio ? p.reference_audio.split("/").pop() : "sin referencia de voz";
  row.appendChild(wavName);
  const reBtn = document.createElement("button");
  reBtn.className = "wav-btn";
  reBtn.disabled = true;
  reBtn.title = "Disponible en T17";
  const ri = document.createElement("i");
  ri.className = "ti ti-refresh";
  reBtn.append(ri, document.createTextNode(" Re-transcribe"));
  row.appendChild(reBtn);
  voice.append(vlabel, row);
  panel.appendChild(voice);

  const actions = document.createElement("div");
  actions.className = "pd-actions";
  const editBtn = document.createElement("button");
  editBtn.disabled = true;
  editBtn.title = "Disponible en T17";
  const ei = document.createElement("i");
  ei.className = "ti ti-edit";
  editBtn.append(ei, document.createTextNode(" Edit"));
  const delBtn = document.createElement("button");
  delBtn.className = "danger";
  delBtn.disabled = true;
  delBtn.title = "Disponible en T17";
  const di = document.createElement("i");
  di.className = "ti ti-trash";
  delBtn.append(di, document.createTextNode(" Delete"));
  actions.append(editBtn, delBtn);
  panel.appendChild(actions);

  const drop = document.createElement("div");
  drop.className = "drop-zone disabled";
  drop.title = "Disponible en T17";
  const up = document.createElement("i");
  up.className = "ti ti-upload";
  drop.append(up, document.createTextNode("\nSoltá un .wav para agregar una persona"));
  panel.appendChild(drop);
}

function renderEmptyDetail() {
  const panel = document.getElementById("rpanel-personas");
  panel.textContent = "";
  const empty = document.createElement("div");
  empty.className = "pd-empty";
  empty.textContent = "Sin personas. El upload de .wav llega en T17.";
  panel.appendChild(empty);
}

function renderWhoChips() {
  const wrap = document.getElementById("who-chips");
  wrap.textContent = "";
  const mk = (label, value) => {
    const b = document.createElement("button");
    b.className = "who-chip" + (value === state.who ? " sel" : "");
    b.textContent = label;
    b.dataset.value = value;
    b.addEventListener("click", () => {
      state.who = value;
      wrap.querySelectorAll(".who-chip").forEach((c) => c.classList.remove("sel"));
      b.classList.add("sel");
    });
    return b;
  };
  wrap.appendChild(mk("LLM router", "router"));
  for (const p of state.personas) wrap.appendChild(mk(p.name, p.name));
  wrap.appendChild(mk("Random", "random"));
}

export function selectPersona(name, opts = {}) {
  const p = state.personas.find((x) => x.name === name);
  if (!p) return;
  document.querySelectorAll("#persona-list .persona").forEach((el) => {
    el.classList.toggle("active", el.dataset.name === name);
  });
  renderDetail(p);
  if (opts.openPanel !== false) setRightTab("personas");
}

export async function initPersonas() {
  const data = await api("/api/personas");
  state.personas = data.personas || [];
  renderSidebar();
  renderWhoChips();
  if (state.personas.length > 0) {
    selectPersona(state.personas[0].name, { openPanel: false });
  } else {
    renderEmptyDetail();
  }
  document.getElementById("btn-personas-admin").addEventListener("click", () => {
    setRightTab("personas");
  });
}
