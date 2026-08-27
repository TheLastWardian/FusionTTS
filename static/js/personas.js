// personas.js — sidebar de personas, panel de detalle, who-chips, upload de .wav y editor de persona.
import { state } from "./state.js";
import { api, avatarCss, initials, toast } from "./utils.js";
import { setRightTab } from "./layout.js";

let uploading = false;
let currentPersona = null;

function renderSidebar() {
  const list = document.getElementById("persona-list");
  list.textContent = "";
  if (state.personas.length === 0) {
    const empty = document.createElement("div");
    empty.className = "list-empty";
    empty.textContent = "Sin personas — subí un .wav";
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

function wireDropZone(dropEl) {
  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = ".wav";
  fileInput.hidden = true;
  fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    if (f) uploadWav(f);
    fileInput.value = "";
  });
  dropEl.appendChild(fileInput);
  dropEl.addEventListener("click", (e) => {
    if (e.target === fileInput) return;
    fileInput.click();
  });
  dropEl.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropEl.classList.add("dragover");
  });
  dropEl.addEventListener("dragleave", () => {
    dropEl.classList.remove("dragover");
  });
  dropEl.addEventListener("drop", (e) => {
    e.preventDefault();
    dropEl.classList.remove("dragover");
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) uploadWav(f);
  });
}

function buildDropZone() {
  const drop = document.createElement("div");
  drop.className = "drop-zone";
  const up = document.createElement("i");
  up.className = "ti ti-upload";
  drop.append(up, document.createTextNode("\nSoltá un .wav para agregar una persona"));
  wireDropZone(drop);
  return drop;
}

function setDropBusy(busy, filename) {
  const panel = document.getElementById("rpanel-personas");
  const drops = [...panel.querySelectorAll(".drop-zone")];
  if (busy) {
    for (const drop of drops) {
      drop.classList.remove("dragover");
      drop.classList.add("busy");
      drop.textContent = "";
      const icon = document.createElement("i");
      icon.className = "ti ti-loader spinning";
      drop.append(icon, document.createTextNode(`\nTranscribiendo «${filename}»… (ASR + LLM)`));
    }
  } else if (drops.some((d) => d.classList.contains("busy"))) {
    if (currentPersona) renderDetail(currentPersona);
    else renderEmptyDetail();
  }
}

async function uploadWav(file) {
  if (uploading) return;
  if (!/\.wav$/i.test(file.name)) {
    toast("Solo se aceptan archivos .wav", "error");
    return;
  }
  uploading = true;
  setDropBusy(true, file.name);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/personas/from-audio", { method: "POST", body: fd });
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
    await refreshPersonas();
    await selectPersona(body.name);
    toast(`«${body.name}» creada`, "success");
    if (body.warning) toast(body.warning, "warning");
  } catch (err) {
    toast(err.message || "Error al subir el .wav", "error");
  } finally {
    uploading = false;
    setDropBusy(false);
  }
}

async function retranscribe(p, btn, box, syncSave) {
  btn.disabled = true;
  btn.textContent = "";
  const icon = document.createElement("i");
  icon.className = "ti ti-loader spinning";
  btn.append(icon, document.createTextNode(" Re-transcribiendo…"));
  try {
    const body = await api(`/api/personas/${encodeURIComponent(p.name)}/retranscribe`, { method: "POST" });
    p.transcript = body.transcript;
    box.value = body.transcript ?? "";
    toast("Transcripción actualizada", "success");
  } catch (err) {
    toast(err.message || "Error al re-transcribir", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "";
    const ri = document.createElement("i");
    ri.className = "ti ti-refresh";
    btn.append(ri, document.createTextNode(" Re-transcribe"));
    syncSave();
  }
}

async function saveTranscript(p, box, btn, syncSave) {
  const value = box.value;
  if (value === (p.transcript ?? "")) return;
  btn.disabled = true;
  try {
    await api(`/api/personas/${encodeURIComponent(p.name)}/transcript`, {
      method: "PUT",
      body: { transcript: value },
    });
    p.transcript = value;
    toast("Transcripción guardada", "success");
  } catch (err) {
    toast(err.message || "Error al guardar la transcripción", "error");
    box.value = p.transcript ?? "";
  } finally {
    syncSave();
  }
}

function renderDetail(p) {
  const panel = document.getElementById("rpanel-personas");
  panel.textContent = "";
  currentPersona = p;

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
  const ri = document.createElement("i");
  ri.className = "ti ti-refresh";
  reBtn.append(ri, document.createTextNode(" Re-transcribe"));
  if (p.reference_audio) {
    reBtn.addEventListener("click", () => retranscribe(p, reBtn, box, syncSave));
  } else {
    reBtn.disabled = true;
    reBtn.title = "Sin referencia de voz";
  }
  row.appendChild(reBtn);
  voice.append(vlabel, row);
  panel.appendChild(voice);

  const box = document.createElement("textarea");
  box.className = "transcript-box";
  box.value = p.transcript ?? "";
  box.placeholder = "Sin transcripción";
  const saveBtn = document.createElement("button");
  saveBtn.className = "wav-btn";
  const si = document.createElement("i");
  si.className = "ti ti-check";
  saveBtn.append(si, document.createTextNode(" Guardar"));
  const syncSave = () => {
    saveBtn.disabled = box.value === (p.transcript ?? "");
  };
  box.addEventListener("input", syncSave);
  saveBtn.addEventListener("click", () => saveTranscript(p, box, saveBtn, syncSave));
  syncSave();
  const tactions = document.createElement("div");
  tactions.className = "transcript-actions";
  tactions.appendChild(saveBtn);
  const transcript = document.createElement("div");
  transcript.className = "pd-section";
  const tlabel = document.createElement("div");
  tlabel.className = "pd-slabel";
  tlabel.textContent = "Transcripción";
  transcript.append(tlabel, box, tactions);
  panel.appendChild(transcript);

  const actions = document.createElement("div");
  actions.className = "pd-actions";
  const editBtn = document.createElement("button");
  const ei = document.createElement("i");
  ei.className = "ti ti-edit";
  editBtn.append(ei, document.createTextNode(" Edit"));
  editBtn.addEventListener("click", () => openPersonaModal(p));
  const delBtn = document.createElement("button");
  delBtn.className = "danger";
  const di = document.createElement("i");
  di.className = "ti ti-trash";
  delBtn.append(di, document.createTextNode(" Delete"));
  delBtn.addEventListener("click", () => openDeleteModal(p));
  actions.append(editBtn, delBtn);
  panel.appendChild(actions);

  panel.appendChild(buildDropZone());
}

function renderEmptyDetail() {
  const panel = document.getElementById("rpanel-personas");
  panel.textContent = "";
  currentPersona = null;
  const empty = document.createElement("div");
  empty.className = "pd-empty";
  empty.textContent = "Sin personas todavía";
  panel.appendChild(empty);
  panel.appendChild(buildDropZone());
  const hint = document.createElement("div");
  hint.className = "pd-hint";
  hint.textContent = "El nombre sale del archivo: <Nombre>.wav, <Nombre>_Eng.wav o <Nombre>_Latino.wav";
  panel.appendChild(hint);
}

let personaModal = null;

function closePersonaModal() {
  if (!personaModal) return;
  document.removeEventListener("keydown", personaModalKey);
  personaModal.remove();
  personaModal = null;
}

function personaModalKey(e) {
  if (e.key === "Escape") closePersonaModal();
}

function buildModalShell(title) {
  const overlay = document.createElement("div");
  overlay.className = "persona-modal-overlay";
  const boxEl = document.createElement("div");
  boxEl.className = "persona-modal";

  const head = document.createElement("div");
  head.className = "persona-modal-head";
  const titleEl = document.createElement("div");
  titleEl.className = "persona-modal-title";
  titleEl.textContent = title;
  const x = document.createElement("button");
  x.className = "persona-modal-x";
  x.title = "Cerrar";
  x.setAttribute("aria-label", "Cerrar");
  const xi = document.createElement("i");
  xi.className = "ti ti-x";
  x.appendChild(xi);
  x.addEventListener("click", closePersonaModal);
  head.append(titleEl, x);

  const body = document.createElement("div");
  body.className = "persona-modal-body";
  boxEl.append(head, body);
  overlay.appendChild(boxEl);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closePersonaModal();
  });
  return { overlay, boxEl, body };
}

function buildModalFoot() {
  const foot = document.createElement("div");
  foot.className = "persona-modal-foot";
  const cancel = document.createElement("button");
  cancel.className = "pm-btn";
  cancel.textContent = "Cancelar";
  cancel.addEventListener("click", closePersonaModal);
  foot.appendChild(cancel);
  return { foot, cancel };
}

async function savePersona(p, description, systemPrompt, color) {
  const payload = {
    name: p.name,
    description,
    system_prompt: systemPrompt,
    router_hints: p.router_hints || [],
    avatar_color: color,
    avatar_image: p.avatar_image ?? null,
    reference_audio: p.reference_audio ?? null,
    reference_audio_transcript: p.reference_audio_transcript ?? null,
    reference_audio_language: p.reference_audio_language ?? null,
  };
  try {
    await api(`/api/personas/${encodeURIComponent(p.name)}`, { method: "PUT", body: payload });
  } catch (err) {
    toast(err.message || "Error al guardar la persona", "error");
    return;
  }
  closePersonaModal();
  toast("Persona actualizada", "success");
  await selectPersona(p.name);
}

function openPersonaModal(p) {
  if (personaModal) return;
  const { overlay, boxEl, body } = buildModalShell(`Editar «${p.name}»`);

  const mkField = (label) => {
    const f = document.createElement("div");
    f.className = "pm-field";
    const l = document.createElement("div");
    l.className = "pm-label";
    l.textContent = label;
    f.appendChild(l);
    return f;
  };

  const nameF = mkField("Nombre");
  const nameInput = document.createElement("input");
  nameInput.className = "pm-input";
  nameInput.type = "text";
  nameInput.value = p.name;
  nameInput.disabled = true;
  nameF.appendChild(nameInput);

  const descF = mkField("Descripción");
  const descInput = document.createElement("textarea");
  descInput.className = "pm-textarea";
  descInput.value = p.description || "";
  descF.appendChild(descInput);

  const promptF = mkField("System prompt");
  const promptInput = document.createElement("textarea");
  promptInput.className = "pm-textarea tall";
  promptInput.value = p.system_prompt || "";
  promptF.appendChild(promptInput);

  const colorF = mkField("Color");
  const colorWrap = document.createElement("div");
  colorWrap.className = "pm-color";
  const hex = /^#[0-9a-fA-F]{6}$/.test(p.avatar_color || "") ? p.avatar_color : "#888888";
  const colorInput = document.createElement("input");
  colorInput.type = "color";
  colorInput.value = hex;
  const hexText = document.createElement("span");
  hexText.className = "pm-color-hex";
  hexText.textContent = hex;
  colorInput.addEventListener("input", () => {
    hexText.textContent = colorInput.value;
  });
  colorWrap.append(colorInput, hexText);
  colorF.appendChild(colorWrap);

  body.append(nameF, descF, promptF, colorF);

  const { foot } = buildModalFoot();
  const save = document.createElement("button");
  save.className = "pm-btn primary";
  save.textContent = "Guardar";
  save.addEventListener("click", () =>
    savePersona(p, descInput.value, promptInput.value, colorInput.value)
  );
  foot.appendChild(save);
  boxEl.appendChild(foot);

  document.body.appendChild(overlay);
  personaModal = overlay;
  document.addEventListener("keydown", personaModalKey);
}

async function deletePersona(p, btn) {
  btn.disabled = true;
  try {
    await api(`/api/personas/${encodeURIComponent(p.name)}`, { method: "DELETE" });
  } catch (err) {
    toast(err.message || "Error al borrar la persona", "error");
    btn.disabled = false;
    return;
  }
  closePersonaModal();
  toast(`«${p.name}» borrada`, "success");
  await refreshPersonas();
  if (state.personas.length > 0) {
    await selectPersona(state.personas[0].name, { openPanel: false });
  } else {
    renderEmptyDetail();
  }
}

function openDeleteModal(p) {
  if (personaModal) return;
  const { overlay, boxEl, body } = buildModalShell(`Borrar «${p.name}»`);

  const msg = document.createElement("p");
  msg.className = "pm-text";
  msg.textContent = `¿Borrar «${p.name}»? Se pierde su voz de referencia.`;
  body.appendChild(msg);

  const { foot } = buildModalFoot();
  const del = document.createElement("button");
  del.className = "pm-btn danger";
  del.textContent = "Borrar";
  del.addEventListener("click", () => deletePersona(p, del));
  foot.appendChild(del);
  boxEl.appendChild(foot);

  document.body.appendChild(overlay);
  personaModal = overlay;
  document.addEventListener("keydown", personaModalKey);
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
  whoChips.forEach((c) => { c.style.display = ""; });
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

export async function selectPersona(name, opts = {}) {
  const p = state.personas.find((x) => x.name === name);
  if (!p) return;
  document.querySelectorAll("#persona-list .persona").forEach((el) => {
    el.classList.toggle("active", el.dataset.name === name);
  });
  if (opts.openPanel !== false) setRightTab("personas");
  let full;
  try {
    full = await api("/api/personas/" + encodeURIComponent(name));
  } catch (err) {
    toast(err.message || "Error al cargar la persona", "error");
    return;
  }
  const idx = state.personas.findIndex((x) => x.name === name);
  if (idx >= 0) state.personas[idx] = full;
  renderDetail(full);
}

async function refreshPersonas() {
  state.personas = (await api("/api/personas")).personas || [];
  renderSidebar();
  renderWhoChips();
}

export async function initPersonas() {
  await refreshPersonas();
  if (state.personas.length > 0) {
    await selectPersona(state.personas[0].name, { openPanel: false });
  } else {
    renderEmptyDetail();
  }
  document.getElementById("btn-personas-admin").addEventListener("click", () => {
    setRightTab("personas");
  });
}
