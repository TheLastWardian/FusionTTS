// personas.js — sidebar de personas, who-chips, upload de .wav en 2 fases (draft → aceptar/rechazar) y editor de persona.
import { state } from "./state.js";
import { api, avatarCss, initials, avatarEl, toast } from "./utils.js";
import { refreshTtsLanguageOptions } from "./settings.js";

let uploading = false;
let draft = null;
let draftEls = null;

function visiblePersonas() {
  if (state.room === "default") return state.personas;
  const r = state.rooms.find((x) => x.name === state.room);
  if (!r) return state.personas;
  const names = new Set(r.persona_names || []);
  return state.personas.filter((p) => names.has(p.name));
}

export function refreshRoomViews() {
  renderSidebar();
  renderWhoChips();
}

function renderSidebar() {
  const list = document.getElementById("persona-list");
  list.textContent = "";
  const vis = visiblePersonas();
  if (vis.length === 0) {
    const empty = document.createElement("div");
    empty.className = "list-empty";
    empty.textContent =
      state.personas.length === 0
        ? "Sin personas — subí un .wav"
        : "Sin personas en esta room — asígalas desde el ícono de personas de la room";
    list.appendChild(empty);
    return;
  }
  for (const p of vis) {
    const item = document.createElement("div");
    item.className = "persona";
    item.dataset.name = p.name;

    const av = avatarEl(p, "avatar");

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

    const editBtn = document.createElement("button");
    editBtn.className = "persona-edit";
    editBtn.title = "Editar persona";
    editBtn.setAttribute("aria-label", `Editar ${p.name}`);
    const ei = document.createElement("i");
    ei.className = "ti ti-pencil";
    editBtn.appendChild(ei);
    editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openPersonaModal(p.name);
    });
    item.appendChild(editBtn);

    item.append(av, info);
    if (p.tts_capable) {
      const vol = document.createElement("i");
      vol.className = "ti ti-volume persona-vol";
      item.appendChild(vol);
    }
    item.appendChild(editBtn);
    item.addEventListener("click", () => openPersonaModal(p.name));
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
  for (const drop of drops) {
    if (busy) {
      drop.classList.remove("dragover");
      drop.classList.add("busy");
      drop.textContent = "";
      const icon = document.createElement("i");
      icon.className = "ti ti-loader spinning";
      drop.append(icon, document.createTextNode(`\nTranscribiendo «${filename}»… (ASR + LLM)`));
    }
  }
}

async function uploadWav(file) {
  if (uploading) return;
  if (draft) return;
  if (!/\.wav$/i.test(file.name)) {
    toast("Solo se aceptan archivos .wav", "error");
    return;
  }
  uploading = true;
  renderUploadPanel();
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
    draft = body;
    if (body.warning) toast(body.warning, "warning");
  } catch (err) {
    toast(err.message || "Error al subir el .wav", "error");
  } finally {
    uploading = false;
    renderUploadPanel();
  }
}

function sectionLabel(text) {
  const l = document.createElement("div");
  l.className = "pd-slabel";
  l.textContent = text;
  return l;
}

function draftField(label, value, cls) {
  const f = document.createElement("div");
  f.className = "draft-field";
  const l = document.createElement("div");
  l.className = "pm-label";
  l.textContent = label;
  const ta = document.createElement("textarea");
  ta.className = cls;
  ta.value = value || "";
  f.append(l, ta);
  return f;
}

function renderUploadPanel() {
  const panel = document.getElementById("rpanel-personas");
  panel.textContent = "";
  draftEls = null;
  if (!draft) {
    panel.appendChild(buildDropZone());
    const hint = document.createElement("div");
    hint.className = "pd-hint";
    hint.textContent = "El nombre sale del archivo: <Nombre>.wav, <Nombre>_Eng.wav o <Nombre>_Latino.wav";
    panel.appendChild(hint);
    return;
  }

  const head = document.createElement("div");
  head.className = "pd-head";
  const av = document.createElement("div");
  av.className = "pd-avatar";
  av.style.cssText = avatarCss({ name: draft.name, avatar_color: draft.avatar_color, avatar_image: null });
  av.textContent = initials(draft.name);
  const meta = document.createElement("div");
  meta.className = "draft-meta";
  const nameInput = document.createElement("input");
  nameInput.className = "draft-name";
  nameInput.type = "text";
  nameInput.value = draft.name;
  nameInput.spellcheck = false;
  meta.appendChild(nameInput);
  if (draft.language) {
    const lang = document.createElement("div");
    lang.className = "persona-lang";
    lang.textContent = draft.language;
    meta.appendChild(lang);
  }
  head.append(av, meta);
  panel.appendChild(head);

  if (draft.warning) {
    const warn = document.createElement("div");
    warn.className = "draft-warning";
    warn.textContent = draft.warning;
    panel.appendChild(warn);
  }

  const persona = document.createElement("div");
  persona.className = "pd-section";
  persona.appendChild(sectionLabel("Personaje"));
  const descField = draftField("Descripción", draft.description, "pm-textarea");
  const promptField = draftField("System prompt", draft.system_prompt, "pm-textarea tall");
  persona.append(descField, promptField);
  panel.appendChild(persona);

  const transcript = document.createElement("div");
  transcript.className = "pd-section";
  transcript.appendChild(sectionLabel("Transcripción"));
  const box = document.createElement("textarea");
  box.className = "transcript-box";
  box.value = draft.transcript ?? "";
  box.placeholder = "Sin transcripción";
  const reBtn = document.createElement("button");
  reBtn.className = "wav-btn";
  const ri = document.createElement("i");
  ri.className = "ti ti-refresh";
  reBtn.append(ri, document.createTextNode(" Re-transcribe"));
  reBtn.addEventListener("click", () => retranscribeDraft(reBtn, box));
  const tactions = document.createElement("div");
  tactions.className = "transcript-actions";
  tactions.appendChild(reBtn);
  transcript.append(box, tactions);
  panel.appendChild(transcript);

  const actions = document.createElement("div");
  actions.className = "draft-actions";
  const cancel = document.createElement("button");
  cancel.className = "pm-btn danger";
  cancel.textContent = "Cancelar";
  cancel.addEventListener("click", rejectDraft);
  const ok = document.createElement("button");
  ok.className = "pm-btn primary";
  ok.textContent = "Aceptar persona";
  ok.addEventListener("click", acceptDraft);
  actions.append(cancel, ok);
  panel.appendChild(actions);

  draftEls = {
    name: nameInput,
    desc: descField.querySelector("textarea"),
    prompt: promptField.querySelector("textarea"),
    box,
    ok,
  };
}

async function retranscribeDraft(btn, box) {
  if (!draft) return;
  btn.disabled = true;
  btn.textContent = "";
  const icon = document.createElement("i");
  icon.className = "ti ti-loader spinning";
  btn.append(icon, document.createTextNode(" Re-transcribiendo…"));
  try {
    const body = await api(`/api/personas/pending/${draft.token}/retranscribe`, { method: "POST" });
    draft.transcript = body.transcript ?? "";
    box.value = draft.transcript;
    toast("Transcripción actualizada", "success");
  } catch (err) {
    toast(err.message || "Error al re-transcribir", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "";
    const r = document.createElement("i");
    r.className = "ti ti-refresh";
    btn.append(r, document.createTextNode(" Re-transcribe"));
  }
}

async function acceptDraft() {
  if (!draft || !draftEls) return;
  const name = draftEls.name.value.trim();
  if (!name) {
    toast("El nombre no puede estar vacío", "error");
    return;
  }
  draftEls.ok.disabled = true;
  try {
    const body = await api(`/api/personas/pending/${draft.token}/accept`, {
      method: "POST",
      body: {
        name,
        description: draftEls.desc.value,
        system_prompt: draftEls.prompt.value,
        color: draft.avatar_color,
        transcript: draftEls.box.value,
      },
    });
    toast(`«${body.name}» creada`, "success");
    if (body.warning) toast(body.warning, "warning");
    draft = null;
    await refreshPersonas();
    renderUploadPanel();
  } catch (err) {
    toast(err.message || "Error al aceptar la persona", "error");
    draftEls.ok.disabled = false;
  }
}

async function rejectDraft() {
  if (!draft) return;
  try {
    await api(`/api/personas/pending/${draft.token}`, { method: "DELETE" });
  } catch {
    // el draft puede no existir en el server; se cancela de todos modos
  }
  draft = null;
  renderUploadPanel();
}

let personaModal = null;
let cropModal = null;

function closePersonaModal() {
  if (!personaModal) return;
  document.removeEventListener("keydown", personaModalKey);
  personaModal.remove();
  personaModal = null;
}

function personaModalKey(e) {
  // si el modal de recorte esta encima, el Escape lo cierra a el (no al perfil)
  if (e.key === "Escape" && !cropModal) closePersonaModal();
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

async function openPersonaModal(name) {
  if (personaModal) return;
  let p;
  try {
    p = await api("/api/personas/" + encodeURIComponent(name));
  } catch (err) {
    toast(err.message || "Error al cargar la persona", "error");
    return;
  }
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
  nameInput.spellcheck = false;
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

  const voiceF = mkField("Voz de referencia");
  const voiceRow = document.createElement("div");
  voiceRow.className = "wav-row";
  const ico = document.createElement("i");
  ico.className = "ti ti-file-music";
  ico.style.fontSize = "15px";
  ico.style.color = "var(--text-muted)";
  voiceRow.appendChild(ico);
  const wavName = document.createElement("span");
  wavName.className = "wav-name";
  wavName.textContent = p.reference_audio ? p.reference_audio.split("/").pop() : "sin referencia de voz";
  voiceRow.appendChild(wavName);
  const reBtn = document.createElement("button");
  reBtn.className = "wav-btn";
  const rri = document.createElement("i");
  rri.className = "ti ti-refresh";
  reBtn.append(rri, document.createTextNode(" Re-transcribe"));
  if (p.reference_audio) {
    reBtn.addEventListener("click", () => retranscribeModal(p, reBtn, trInput));
  } else {
    reBtn.disabled = true;
    reBtn.title = "Sin referencia de voz";
  }
  voiceRow.appendChild(reBtn);
  voiceF.appendChild(voiceRow);

  const trF = mkField("Transcripción");
  const trInput = document.createElement("textarea");
  trInput.className = "pm-textarea tall";
  trInput.value = p.transcript ?? "";
  trInput.placeholder = "Sin transcripción";
  trF.appendChild(trInput);

  // foto: preview (solo muestra) + botones explícitos de subir/quitar
  const avF = mkField("Foto");
  const avRow = document.createElement("div");
  avRow.className = "pm-avatar-row";
  const avWrap = document.createElement("div");
  avWrap.className = "pm-avatar-wrap";
  avWrap.title = "Subir o arrastrar una imagen acá";
  let avPrevEl = avatarEl(p, "pm-avatar");
  const badge = document.createElement("span");
  badge.className = "pm-avatar-badge";
  const badgeIco = document.createElement("i");
  badgeIco.className = "ti ti-camera";
  badge.appendChild(badgeIco);
  avWrap.append(avPrevEl, badge);
  const avButtons = document.createElement("div");
  avButtons.className = "pm-avatar-btns";
  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = ".png,.jpg,.jpeg,.webp,.gif,image/*";
  fileInput.hidden = true;
  const upBtn = document.createElement("button");
  upBtn.className = "pm-btn";
  upBtn.textContent = "Subir foto";
  const rmBtn = document.createElement("button");
  rmBtn.className = "pm-btn danger";
  rmBtn.textContent = "Quitar";
  rmBtn.style.display = p.avatar_image ? "" : "none";
  avButtons.append(upBtn, rmBtn);
  avRow.append(avWrap, avButtons, fileInput);
  avF.appendChild(avRow);

  const refreshAvPrev = () => {
    const next = avatarEl(p, "pm-avatar");
    avWrap.replaceChild(next, avPrevEl);
    avPrevEl = next;
    rmBtn.style.display = p.avatar_image ? "" : "none";
  };
  upBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    fileInput.value = "";
    if (!f) return;
    openCropModal(p, f, refreshAvPrev);
  });
  // drag&drop sobre el preview (la fila es estable: el preview se re-renderiza)
  ["dragenter", "dragover"].forEach((ev) =>
    avRow.addEventListener(ev, (e) => {
      e.preventDefault();
      avRow.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    avRow.addEventListener(ev, (e) => {
      e.preventDefault();
      avRow.classList.remove("dragover");
    })
  );
  avRow.addEventListener("drop", (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    if (f.type && !f.type.startsWith("image/")) {
      toast("Solta una imagen (.png, .jpg, .webp o .gif)", "error");
      return;
    }
    openCropModal(p, f, refreshAvPrev);
  });
  rmBtn.addEventListener("click", () => {
    rmBtn.disabled = true;
    api(`/api/personas/${encodeURIComponent(p.name)}/avatar`, { method: "DELETE" })
      .then((body) => {
        p.avatar_image = body.avatar_image ?? null;
        refreshAvPrev();
        toast("Foto quitada", "success");
      })
      .catch((err) => toast(err.message || "Error al quitar la foto", "error"))
      .finally(() => { rmBtn.disabled = false; });
  });

  body.append(avF, nameF, descF, promptF, colorF, voiceF, trF);

  const { foot } = buildModalFoot();
  const del = document.createElement("button");
  del.className = "pm-btn danger";
  del.textContent = "Borrar";
  del.style.marginRight = "auto";
  del.addEventListener("click", () => deletePersona(p, del));
  const save = document.createElement("button");
  save.className = "pm-btn primary";
  save.textContent = "Guardar";
  save.addEventListener("click", () =>
    savePersonaModal(p, { name: nameInput, desc: descInput, prompt: promptInput, color: colorInput, transcript: trInput }, save)
  );
  foot.append(del, foot.firstChild, save);
  boxEl.appendChild(foot);

  document.body.appendChild(overlay);
  personaModal = overlay;
  document.addEventListener("keydown", personaModalKey);
}

async function retranscribeModal(p, btn, trInput) {
  btn.disabled = true;
  btn.textContent = "";
  const icon = document.createElement("i");
  icon.className = "ti ti-loader spinning";
  btn.append(icon, document.createTextNode(" Re-transcribiendo…"));
  try {
    const body = await api(`/api/personas/${encodeURIComponent(p.name)}/retranscribe`, { method: "POST" });
    p.transcript = body.transcript ?? "";
    trInput.value = p.transcript;
    toast("Transcripción actualizada", "success");
  } catch (err) {
    toast(err.message || "Error al re-transcribir", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "";
    const r = document.createElement("i");
    r.className = "ti ti-refresh";
    btn.append(r, document.createTextNode(" Re-transcribe"));
  }
}

async function savePersonaModal(p, els, saveBtn) {
  const newName = els.name.value.trim();
  saveBtn.disabled = true;
  try {
    let current = p;
    if (newName !== p.name) {
      if (Array.isArray(state.who)) {
        state.who = state.who.map((n) => (n === p.name ? newName : n));
      } else if (state.who === p.name) {
        state.who = newName;
      }
      await api(`/api/personas/${encodeURIComponent(p.name)}/rename`, {
        method: "POST",
        body: { name: newName },
      });
      current = { ...p, name: newName };
      // el rename renombro el archivo de avatar server-side; el PUT que sigue
      // debe mandar el path nuevo, no el viejo
      if (current.avatar_image) {
        const dot = current.avatar_image.lastIndexOf(".");
        const ext = dot >= 0 ? current.avatar_image.slice(dot) : "";
        current.avatar_image = current.avatar_image.replace(/[^/\\]+$/, newName + ext);
      }
    }
    await api(`/api/personas/${encodeURIComponent(current.name)}`, {
      method: "PUT",
      body: {
        name: current.name,
        description: els.desc.value,
        system_prompt: els.prompt.value,
        router_hints: current.router_hints || [],
        avatar_color: els.color.value,
        avatar_image: current.avatar_image ?? null,
        reference_audio: current.reference_audio ?? null,
        reference_audio_transcript: current.reference_audio_transcript ?? null,
        reference_audio_language: current.reference_audio_language ?? null,
      },
    });
    if (els.transcript.value !== (current.transcript ?? "")) {
      await api(`/api/personas/${encodeURIComponent(current.name)}/transcript`, {
        method: "PUT",
        body: { transcript: els.transcript.value },
      });
    }
    closePersonaModal();
    toast("Persona actualizada", "success");
    await refreshPersonas();
  } catch (err) {
    toast(err.message || "Error al guardar la persona", "error");
    saveBtn.disabled = false;
  }
}

async function deletePersona(p, btn) {
  if (!window.confirm(`¿Borrar «${p.name}»? Se pierde su voz de referencia.`)) return;
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
  if (Array.isArray(state.who)) {
    state.who = state.who.filter((n) => n !== p.name);
    if (state.who.length === 0) state.who = "router";
  } else if (state.who === p.name) {
    state.who = "router";
  }
  await refreshPersonas();
  renderUploadPanel();
}

// ---------- recorte de foto (canvas, sin dependencias) ----------
// flow: elegir/dropear imagen -> modal con viewport circular (zoom con rueda,
// pan arrastrando) -> "Aplicar" renderiza 512x512 a PNG y sube al endpoint de
// avatar. El downscale al exportar tambien resuelve el peso del archivo.

const CROP_SIZE = 280; // viewport en px CSS
const CROP_OUT = 512; // lado del canvas de salida
const CROP_MAX_ZOOM = 4;

function uploadAvatarFile(p, data, filename, refresh, onDone) {
  const fd = new FormData();
  fd.append("file", data, filename);
  return fetch(`/api/personas/${encodeURIComponent(p.name)}/avatar`, {
    method: "PUT",
    body: fd,
  })
    .then(async (res) => {
      let body = null;
      try {
        body = await res.json();
      } catch {
        body = null;
      }
      if (!res.ok) {
        let detail = body ? body.detail : res.statusText;
        if (typeof detail !== "string") detail = JSON.stringify(detail);
        throw new Error(detail || `HTTP ${res.status}`);
      }
      p.avatar_image = body.avatar_image ?? null;
      refresh();
      toast("Foto actualizada", "success");
      if (onDone) onDone();
    })
    .catch((err) => {
      toast(err.message || "Error al subir la foto", "error");
      if (onDone) onDone(err);
    });
}

function openCropModal(p, file, refresh) {
  if (cropModal) return;
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => buildCropModal(p, file, img, url, refresh);
  img.onerror = () => {
    URL.revokeObjectURL(url);
    toast("No se pudo leer la imagen", "error");
  };
  img.src = url;
}

function buildCropModal(p, file, img, url, refresh) {
  const nw = img.naturalWidth;
  const nh = img.naturalHeight;
  // escala base: la imagen "cover" el viewport con zoom 1
  const base = Math.max(CROP_SIZE / nw, CROP_SIZE / nh);
  let zoom = 1;
  let px = 0;
  let py = 0;

  const overlay = document.createElement("div");
  overlay.className = "crop-overlay";
  const box = document.createElement("div");
  box.className = "crop-modal";

  const head = document.createElement("div");
  head.className = "crop-head";
  const title = document.createElement("div");
  title.className = "crop-title";
  title.textContent = `Recortar foto de «${p.name}»`;
  const x = document.createElement("button");
  x.className = "persona-modal-x";
  x.title = "Cerrar";
  x.setAttribute("aria-label", "Cerrar");
  const xi = document.createElement("i");
  xi.className = "ti ti-x";
  x.appendChild(xi);
  head.append(title, x);

  const stage = document.createElement("div");
  stage.className = "crop-stage";
  img.className = "crop-img";
  img.draggable = false;
  img.style.width = nw + "px";
  img.style.height = nh + "px";
  img.style.marginLeft = -nw / 2 + "px";
  img.style.marginTop = -nh / 2 + "px";
  const mask = document.createElement("div");
  mask.className = "crop-mask";
  stage.append(img, mask);

  const hint = document.createElement("div");
  hint.className = "crop-hint";
  hint.textContent = "Rueda: zoom · Arrastrar: mover";

  const foot = document.createElement("div");
  foot.className = "crop-foot";
  const cancel = document.createElement("button");
  cancel.className = "pm-btn";
  cancel.textContent = "Cancelar";
  const apply = document.createElement("button");
  apply.className = "pm-btn primary";
  apply.textContent = "Aplicar foto";
  foot.append(cancel, apply);

  box.append(head, stage, hint, foot);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  cropModal = overlay;

  const applyTransform = () => {
    const scale = base * zoom;
    const maxX = Math.max(0, (nw * scale - CROP_SIZE) / 2);
    const maxY = Math.max(0, (nh * scale - CROP_SIZE) / 2);
    px = Math.min(maxX, Math.max(-maxX, px));
    py = Math.min(maxY, Math.max(-maxY, py));
    img.style.transform = `translate(${px}px, ${py}px) scale(${scale})`;
  };
  applyTransform();

  stage.addEventListener("wheel", (e) => {
    e.preventDefault();
    zoom = Math.min(CROP_MAX_ZOOM, Math.max(1, zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1)));
    applyTransform();
  }, { passive: false });

  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  const onMove = (e) => {
    if (!dragging) return;
    px += e.clientX - lastX;
    py += e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;
    applyTransform();
  };
  const onUp = () => {
    dragging = false;
  };
  stage.addEventListener("mousedown", (e) => {
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
  });
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);

  const close = () => {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    document.removeEventListener("keydown", onCropKey);
    overlay.remove();
    cropModal = null;
    URL.revokeObjectURL(url);
  };

  const onCropKey = (e) => {
    if (e.key === "Escape") close();
  };
  document.addEventListener("keydown", onCropKey);

  x.addEventListener("click", close);
  cancel.addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });

  apply.addEventListener("click", () => {
    apply.disabled = true;
    cancel.disabled = true;
    apply.textContent = "Aplicando…";
    // el viewport [0,CROP_SIZE] se mapea a [0,CROP_OUT]; la imagen vista es
    // scale * (punto - centro) + (px,py) respecto al centro del viewport
    const k = CROP_OUT / CROP_SIZE;
    const scale = base * zoom;
    const canvas = document.createElement("canvas");
    canvas.width = CROP_OUT;
    canvas.height = CROP_OUT;
    const ctx = canvas.getContext("2d");
    ctx.translate(CROP_OUT / 2 + k * px, CROP_OUT / 2 + k * py);
    ctx.scale(k * scale, k * scale);
    ctx.drawImage(img, -nw / 2, -nh / 2);
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          toast("No se pudo generar la imagen", "error");
          apply.disabled = false;
          cancel.disabled = false;
          apply.textContent = "Aplicar foto";
          return;
        }
        uploadAvatarFile(p, blob, "avatar.png", refresh, () => close());
      },
      "image/png"
    );
  });
}

async function refreshPersonas() {
  state.personas = (await api("/api/personas")).personas || [];
  renderSidebar();
  renderWhoChips();
  refreshTtsLanguageOptions();
}

export async function initPersonas() {
  await refreshPersonas();
  renderUploadPanel();
}

const WHO_GAP = 5;
let whoWrap = null;
let whoChips = [];
let whoPlus = null;
let whoHidden = [];
let whoRo = null;
let whoModal = null;

function whoIsSel(value) {
  return Array.isArray(state.who) ? state.who.includes(value) : state.who === value;
}

function syncWhoSel() {
  for (const c of whoChips) c.classList.toggle("sel", whoIsSel(c.dataset.value));
}

function selectWho(value) {
  state.who = value;
  syncWhoSel();
  applyWhoFit();
}

function ctrlSelectWho(value) {
  const max = Math.max(1, Number(state.config.max_persona_replies) || 2);
  if (value === "router" || value === "random") {
    state.who = value;
  } else {
    let list = (Array.isArray(state.who) ? state.who : [state.who]).filter(
      (v) => v !== "router" && v !== "random"
    );
    const i = list.indexOf(value);
    if (i >= 0) {
      list.splice(i, 1);
    } else {
      list.push(value);
      while (list.length > max) list.shift();
    }
    state.who = list.length > 0 ? list : "router";
  }
  syncWhoSel();
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
  const selIdxs = whoChips
    .map((c, i) => (whoIsSel(c.dataset.value) ? i : -1))
    .filter((i) => i >= 0);

  let res = window.FTTS.fitChips(widths, avail, selIdxs, WHO_GAP, 0);
  if (res.hidden.length > 0) {
    let plusW = measurePlus(res.hidden.length);
    const n1 = res.hidden.length;
    res = window.FTTS.fitChips(widths, avail, selIdxs, WHO_GAP, plusW);
    if (String(res.hidden.length).length > String(n1).length) {
      plusW = measurePlus(res.hidden.length);
      res = window.FTTS.fitChips(widths, avail, selIdxs, WHO_GAP, plusW);
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
      const av = avatarEl(p, "avatar");
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
  const vis = visiblePersonas();
  const names = Array.isArray(state.who) ? state.who : [state.who];
  if (
    names.some(
      (v) => v !== "router" && v !== "random" && !vis.some((p) => p.name === v)
    )
  ) {
    state.who = "router";
  }
  const mk = (label, value) => {
    const b = document.createElement("button");
    b.className = "who-chip" + (whoIsSel(value) ? " sel" : "");
    b.textContent = label;
    b.dataset.value = value;
    b.addEventListener("click", (e) => {
      if (e.ctrlKey || e.metaKey) ctrlSelectWho(value);
      else selectWho(value);
    });
    whoWrap.appendChild(b);
    whoChips.push(b);
  };
  mk("LLM router", "router");
  for (const p of vis) mk(p.name, p.name);
  mk("Random", "random");

  whoPlus = document.createElement("button");
  whoPlus.className = "who-chip who-chip-plus";
  whoPlus.style.display = "none";
  whoPlus.addEventListener("click", openWhoModal);
  whoWrap.appendChild(whoPlus);

  applyWhoFit();
  initWhoResize();
}
