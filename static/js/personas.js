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

const WHO_GAP = 5;
let whoWrap = null;
let whoChips = [];
let whoPlus = null;
let whoHidden = [];
let whoRo = null;
let whoModal = null;

function selectWho(value) {
  state.who = value;
  for (const c of whoChips) c.classList.toggle("sel", c.dataset.value === value);
  applyWhoFit();
}

function measurePlus(n) {
  whoPlus.textContent = "+" + n;
  whoPlus.style.display = "";
  whoPlus.style.visibility = "hidden";
  const w = whoPlus.offsetWidth;
  whoPlus.style.display = "none";
  return w;
}

function applyWhoFit() {
  if (!whoWrap || whoChips.length === 0) return;
  const avail = whoWrap.clientWidth;
  const widths = whoChips.map((c) => c.offsetWidth);
  const selIdx = whoChips.findIndex((c) => c.dataset.value === state.who);

  let res = window.FTTS.fitChips(widths, avail, selIdx, WHO_GAP, 0);
  if (res.hidden.length > 0) {
    let plusW = measurePlus(res.hidden.length);
    const n1 = res.hidden.length;
    res = window.FTTS.fitChips(widths, avail, selIdx, WHO_GAP, plusW);
    if (String(res.hidden.length).length > String(n1).length) {
      plusW = measurePlus(res.hidden.length);
      res = window.FTTS.fitChips(widths, avail, selIdx, WHO_GAP, plusW);
    }
  }

  whoHidden = res.hidden;
  whoChips.forEach((c, i) => {
    c.style.display = res.visible[i] ? "" : "none";
  });
  if (res.hidden.length > 0 && res.plusFits) {
    whoPlus.textContent = "+" + res.hidden.length;
    whoPlus.title = "Mostrar ocultos";
    whoPlus.style.display = "";
    whoPlus.style.visibility = "visible";
  } else {
    whoPlus.style.display = "none";
  }
}

function initWhoResize() {
  if (whoRo || typeof ResizeObserver === "undefined") return;
  let raf = 0;
  whoRo = new ResizeObserver(() => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      applyWhoFit();
    });
  });
  whoRo.observe(whoWrap);
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => applyWhoFit()).catch(() => {});
  }
}

function closeWhoModal() {
  if (!whoModal) return;
  document.removeEventListener("keydown", whoModalKey);
  whoModal.remove();
  whoModal = null;
}

function whoModalKey(e) {
  if (e.key === "Escape") closeWhoModal();
}

function openWhoModal() {
  if (whoModal || whoHidden.length === 0) return;
  const idxs = whoHidden.slice();

  const overlay = document.createElement("div");
  overlay.className = "who-modal-overlay";
  const box = document.createElement("div");
  box.className = "who-modal";

  const head = document.createElement("div");
  head.className = "who-modal-head";
  const title = document.createElement("div");
  title.className = "who-modal-title";
  title.textContent = "Elige quién responde";
  const x = document.createElement("button");
  x.className = "who-modal-x";
  x.title = "Cerrar";
  x.setAttribute("aria-label", "Cerrar");
  const xi = document.createElement("i");
  xi.className = "ti ti-x";
  x.appendChild(xi);
  head.append(title, x);

  const list = document.createElement("div");
  list.className = "who-modal-list";
  let first = null;

  for (const i of idxs) {
    const chip = whoChips[i];
    const value = chip.dataset.value;
    const p = state.personas.find((q) => q.name === value);
    const row = document.createElement("div");
    row.className = "persona who-modal-row";
    row.tabIndex = 0;
    if (p) {
      const av = document.createElement("div");
      av.className = "avatar";
      av.style.cssText = avatarCss(p);
      av.textContent = initials(p.name);
      const info = document.createElement("div");
      info.className = "persona-info";
      const nm = document.createElement("div");
      nm.className = "persona-name";
      nm.textContent = p.name;
      info.appendChild(nm);
      if (p.reference_audio_language) {
        const lg = document.createElement("div");
        lg.className = "persona-lang";
        lg.textContent = p.reference_audio_language;
        info.appendChild(lg);
      }
      row.append(av, info);
    } else {
      const info = document.createElement("div");
      info.className = "persona-info";
      const nm = document.createElement("div");
      nm.className = "persona-name";
      nm.textContent = chip.textContent;
      info.appendChild(nm);
      row.appendChild(info);
    }
    row.addEventListener("click", () => {
      closeWhoModal();
      selectWho(value);
    });
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        row.click();
      }
    });
    if (!first) first = row;
    list.appendChild(row);
  }

  box.append(head, list);
  overlay.appendChild(box);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeWhoModal();
  });
  x.addEventListener("click", closeWhoModal);
  document.body.appendChild(overlay);
  whoModal = overlay;
  document.addEventListener("keydown", whoModalKey);
  if (first) first.focus();
}

function renderWhoChips() {
  whoWrap = document.getElementById("who-chips");
  whoWrap.textContent = "";
  whoChips = [];
  const mk = (label, value) => {
    const b = document.createElement("button");
    b.className = "who-chip" + (value === state.who ? " sel" : "");
    b.textContent = label;
    b.dataset.value = value;
    b.addEventListener("click", () => selectWho(value));
    whoWrap.appendChild(b);
    whoChips.push(b);
  };
  mk("LLM router", "router");
  for (const p of state.personas) mk(p.name, p.name);
  mk("Random", "random");

  whoPlus = document.createElement("button");
  whoPlus.className = "who-chip who-chip-plus";
  whoPlus.style.display = "none";
  whoPlus.addEventListener("click", openWhoModal);
  whoWrap.appendChild(whoPlus);

  applyWhoFit();
  initWhoResize();
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
