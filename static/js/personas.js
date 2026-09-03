// personas.js — sidebar de personas, who-chips, upload de .wav en 2 fases (draft → aceptar/rechazar) y editor de persona.
import { state } from "./state.js";
import { api, avatarCss, initials, avatarEl, avatarUrl, toast } from "./utils.js";
import { refreshTtsLanguageOptions } from "./settings.js";
import * as layout from "./persona-layout.js";
import { getSpeakingPersona } from "./tts.js";

let uploading = false;
let draft = null;
let draftEls = null;
let folderEditing = null; // {name, isNew} — carpeta con input inline abierto
let dragging = null; // {kind: "persona"|"folder", name} — DnD (Task 5 completa)

// persona-sistema de voice design (instruct): sin audio de referencia; el
// backend (app/personas.py FOR_INSTRUCT_NAME) la auto-crea y la filtra de la
// API cuando show_for_instruct=false
const FOR_INSTRUCT = "For Instruct";

function visiblePersonas() {
  let list;
  if (state.room === "default") {
    list = state.personas;
  } else {
    const r = state.rooms.find((x) => x.name === state.room);
    if (!r) list = state.personas;
    else {
      const names = new Set(r.persona_names || []);
      list = state.personas.filter((p) => names.has(p.name));
    }
  }
  return list.slice().sort((a, b) => (b.name === FOR_INSTRUCT) - (a.name === FOR_INSTRUCT));
}

export function refreshRoomViews() {
  renderSidebar();
  renderWhoChips();
}

function renderSidebar() {
  const list = document.getElementById("persona-list");
  list.textContent = "";
  const btnNew = document.getElementById("btn-new-folder");
  if (btnNew) btnNew.hidden = state.room !== "default";
  if (state.room === "default") renderSidebarMain(list);
  else renderSidebarRoom(list);
}

function renderSidebarMain(list) {
  if (state.personas.length === 0) {
    const empty = document.createElement("div");
    empty.className = "list-empty";
    empty.textContent = "No personas — upload a .wav";
    list.appendChild(empty);
    return;
  }
  const byName = new Map(state.personas.map((p) => [p.name, p]));
  const pinned = byName.get(FOR_INSTRUCT);
  if (pinned) list.appendChild(personaRow(pinned, false, true));
  const collapsed = getCollapsedFolders();
  for (const entry of state.personaLayout) {
    if (entry.type === "persona") {
      const p = byName.get(entry.name);
      if (p) list.appendChild(personaRow(p, true, false));
    } else {
      const isCollapsed = !!collapsed[entry.name];
      list.appendChild(folderRow(entry, isCollapsed));
      if (!isCollapsed && entry.personas.length) {
        const kids = document.createElement("div");
        kids.className = "persona-children";
        kids.dataset.folder = entry.name;
        for (const m of entry.personas) {
          const p = byName.get(m);
          if (p) kids.appendChild(personaRow(p, true, false));
        }
        if (kids.children.length) list.appendChild(kids);
      }
    }
  }
}

function renderSidebarRoom(list) {
  const vis = visiblePersonas();
  if (vis.length === 0) {
    const empty = document.createElement("div");
    empty.className = "list-empty";
    empty.textContent = "No personas in this room — assign them from the room's personas icon";
    list.appendChild(empty);
    return;
  }
  for (const p of vis) list.appendChild(personaRow(p, false, false));
}

function personaRow(p, draggable = false, pinned = false) {
  const item = document.createElement("div");
  item.dataset.name = p.name;

  const info = document.createElement("div");
  if (pinned) {
    // persona-sistema: fila compacta, no card (no participa del grid)
    item.className = "persona pinned";
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
    item.append(avatarEl(p, "avatar"), info);
    return item;
  }

  item.className = "persona persona-card";
  // si suena su audio en este instante, la card arranca encendida (un
  // re-render no puede apagar lo que ya esta sonando)
  if (getSpeakingPersona() === p.name) item.classList.add("speaking");
  // imagen grande: cover de avatar_image, o radial gradient + iniciales
  const image = document.createElement("div");
  image.className = "pc-image";
  if (p.avatar_image) {
    const img = document.createElement("img");
    img.alt = p.name;
    img.src = avatarUrl(p.name);
    img.draggable = false;
    image.appendChild(img);
  } else {
    const color = /^#[0-9a-fA-F]{6}$/.test(p.avatar_color || "") ? p.avatar_color : "#5b8ef0";
    image.style.background = "radial-gradient(circle at 50% 35%, " + color + " 0%, #101016 100%)";
    const init = document.createElement("div");
    init.className = "pc-initials";
    const sp = document.createElement("span");
    sp.textContent = initials(p.name);
    init.appendChild(sp);
    image.appendChild(init);
  }

  info.className = "pc-info";
  const name = document.createElement("div");
  name.className = "pc-name";
  name.textContent = p.name;
  info.appendChild(name);
  if (p.reference_audio_language) {
    const lang = document.createElement("div");
    lang.className = "pc-lang";
    lang.textContent = p.reference_audio_language;
    info.appendChild(lang);
  }
  const body = document.createElement("div");
  body.className = "pc-body";
  body.appendChild(info);

  const actions = document.createElement("div");
  actions.className = "pc-actions";
  if (p.tts_capable) {
    const vol = document.createElement("i");
    vol.className = "ti ti-volume pc-vol";
    vol.title = "With TTS voice";
    actions.appendChild(vol);
  }
  const editBtn = document.createElement("button");
  editBtn.className = "pc-btn";
  editBtn.title = "Edit persona";
  editBtn.setAttribute("aria-label", "Edit " + p.name);
  const ei = document.createElement("i");
  ei.className = "ti ti-pencil";
  editBtn.appendChild(ei);
  editBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    openPersonaModal(p.name);
  });
  actions.appendChild(editBtn);
  body.appendChild(actions);

  item.append(image, body);
  // click en la card NO abre el modal: el acceso de edicion es solo el lapiz
  if (draggable) wireDraggableRow(item, "persona", p.name);
  return item;
}

function folderRow(entry, isCollapsed) {
  const row = document.createElement("div");
  row.className = "persona-folder";
  row.dataset.name = entry.name;

  const chev = document.createElement("i");
  chev.className = "ti " + (isCollapsed ? "ti-chevron-right" : "ti-chevron-down") + " folder-chevron";

  const editing = folderEditing && folderEditing.name === entry.name;
  if (editing) {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "persona-folder-input";
    input.value = entry.name;
    input.maxLength = 40;
    input.spellcheck = false;
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commitFolderRename(input, entry.name, editing.isNew);
      else if (e.key === "Escape") cancelFolderEdit(entry.name, editing.isNew);
    });
    input.addEventListener("blur", () => {
      if (folderEditing && folderEditing.name === entry.name) {
        commitFolderRename(input, entry.name, editing.isNew);
      }
    });
    row.append(chev, input);
    requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
  } else {
    const label = document.createElement("span");
    label.className = "persona-folder-name";
    label.textContent = entry.name || "new folder";
    row.appendChild(label);

    const renameBtn = document.createElement("button");
    renameBtn.className = "persona-edit folder-btn";
    renameBtn.title = "Rename folder";
    renameBtn.setAttribute("aria-label", "Rename folder " + (entry.name || "new"));
    renameBtn.innerHTML = '<i class="ti ti-pencil"></i>';
    renameBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      folderEditing = { name: entry.name, isNew: false };
      renderSidebar();
    });

    const delBtn = document.createElement("button");
    delBtn.className = "persona-edit folder-btn folder-del";
    delBtn.title = "Delete folder";
    delBtn.setAttribute("aria-label", "Delete folder " + (entry.name || "new"));
    delBtn.innerHTML = '<i class="ti ti-trash"></i>';
    delBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteFolder(entry.name);
    });

    row.append(chev, label, renameBtn, delBtn);
  }

  row.addEventListener("click", (e) => {
    if (e.target.closest("button") || e.target.tagName === "INPUT") return;
    toggleFolder(entry.name);
  });
  if (!editing) wireDraggableRow(row, "folder", entry.name);
  return row;
}

// ── colapso / CRUD de carpetas ──────────────────────────────────────────
const COLLAPSE_KEY = "persona-collapsed";

function getCollapsedFolders() {
  try {
    const raw = JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "{}");
    return raw && typeof raw === "object" ? raw : {};
  } catch {
    return {};
  }
}

function toggleFolder(name) {
  const cur = getCollapsedFolders();
  cur[name] = !cur[name];
  try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(cur)); } catch {}
  renderSidebar();
}

function createFolder() {
  if (state.room !== "default" || folderEditing) return;
  state.personaLayout = layout.addFolder(state.personaLayout, "", 0);
  folderEditing = { name: "", isNew: true };
  renderSidebar();
}

function cancelFolderEdit(name, isNew) {
  folderEditing = null;
  if (isNew) {
    state.personaLayout = state.personaLayout.filter(
      (e) => !(e.type === "folder" && e.name === name)
    );
  }
  renderSidebar();
}

function commitFolderRename(input, oldName, isNew) {
  if (!folderEditing || folderEditing.name !== oldName) return;
  folderEditing = null;
  const next = input.value.trim();
  if (!next) {
    if (isNew) {
      state.personaLayout = state.personaLayout.filter(
        (e) => !(e.type === "folder" && e.name === oldName)
      );
    }
    renderSidebar();
    return;
  }
  try {
    state.personaLayout = layout.renameFolder(state.personaLayout, oldName, next);
  } catch {
    toast("A folder named «" + next + "» already exists", "error");
    renderSidebar();
    return;
  }
  renderSidebar();
  saveLayout();
}

function deleteFolder(name) {
  const entry = state.personaLayout.find((e) => e.type === "folder" && e.name === name);
  if (!entry) return;
  const n = entry.personas.length;
  if (n > 0 && !window.confirm("Delete the folder «" + name + "»? Its " + n + " persona(s) move to the main list.")) return;
  state.personaLayout = layout.removeFolder(state.personaLayout, name);
  renderSidebar();
  saveLayout();
}

async function saveLayout() {
  try {
    await api("/api/personas/layout", {
      method: "PUT",
      body: { layout: state.personaLayout, columns: state.layoutColumns },
    });
  } catch (err) {
    toast("Could not save the folder order: " + (err.message || err), "error");
    await refreshPersonas();
  }
}

// toggle global de columnas: clase en body (CSS grilla) + persistencia
function applyColumns() {
  const n = Math.max(1, Math.min(4, Number(state.layoutColumns) || 2));
  state.layoutColumns = n;
  document.body.classList.remove("cols-1", "cols-2", "cols-3", "cols-4");
  document.body.classList.add("cols-" + n);
  document.querySelectorAll("#col-toggle button").forEach((b) =>
    b.classList.toggle("active", Number(b.dataset.cols) === n)
  );
}

function initColToggle() {
  document.querySelectorAll("#col-toggle button").forEach((b) =>
    b.addEventListener("click", () => {
      state.layoutColumns = Number(b.dataset.cols);
      applyColumns();
      saveLayout();
    })
  );
}

// ── drag & drop del layout (solo Main) ──────────────────────────────────
let dropSlotEl = null;
let dropSlotTarget = null; // target que produjo el slot actual
let dropFolderEl = null;
let hoverEl = null; // card/bloque bajo el cursor en el ultimo dragover
let dragOverEl = null; // elemento que actualmente tiene la clase .drag-over
let lastLoggedTarget = "__init__"; // firma del ultimo target logueado (anti-spam)

// [dnd] log del layout: posicion de cada entrada (tope y dentro de carpetas)
function describeLayout(l) {
  return l
    .map((e, i) =>
      e.type === "folder"
        ? `[${i}] Carpeta "${e.name}" [${e.personas.join(", ")}]`
        : `[${i}] ${e.name}`
    )
    .join(" | ");
}

function topLevelRows() {
  const list = document.getElementById("persona-list");
  return [...list.querySelectorAll(":scope > .persona-card, :scope > .persona-folder")];
}

function isInFolder(name, folder) {
  const e = state.personaLayout.find((x) => x.type === "folder" && x.name === folder);
  return !!e && e.personas.includes(name);
}

function isTopLevel(name) {
  return state.personaLayout.some((x) => x.type === "persona" && x.name === name);
}

// 0 = "antes", 1 = "despues". En grid multicolonna decide por el eje
// dominante del cursor respecto al centro de la card: movimientos
// horizontales caen por X (mitad izq/der), verticales por Y (arriba/abajo).
// El viejo midpoint solo por Y daba coin-flip en movimientos horizontales.
function sideOf(rect, e) {
  if ((Number(state.layoutColumns) || 2) === 1 || !rect.width || !rect.height) {
    return e.clientY < rect.top + rect.height / 2 ? 0 : 1;
  }
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  const dx = Math.abs(e.clientX - cx) / (rect.width / 2);
  const dy = Math.abs(e.clientY - cy) / (rect.height / 2);
  if (dx >= dy) return e.clientX < cx ? 0 : 1;
  return e.clientY < cy ? 0 : 1;
}

// la fila mas cercana al cursor (por distancia al centro de cada rect)
function nearestRow(rows, e) {
  let best = null;
  let bestD = Infinity;
  for (const r of rows) {
    const rc = r.getBoundingClientRect();
    const d = Math.hypot(
      e.clientX - (rc.left + rc.width / 2),
      e.clientY - (rc.top + rc.height / 2)
    );
    if (d < bestD) { bestD = d; best = { el: r, rect: rc }; }
  }
  return best;
}

// target: {index} (tope) o {folder, index?} (dentro de carpeta).
// Indices = posiciones en filas renderizadas, INCLUYENDO la card arrastrada
// (las funciones puras ajustan internamente).
function dropTargetAt(e) {
  hoverEl = null;
  if (!dragging || state.room !== "default") return null;
  const list = document.getElementById("persona-list");

  // si el cursor esta sobre la celda del slot (overlay, pointer-events:none),
  // mantener el target actual en vez de re-computar: la card que hay debajo
  // del slot es la card destino, y re-computar solo agregaria inestabilidad.
  if (dropSlotEl && dropSlotTarget) {
    const r = dropSlotEl.getBoundingClientRect();
    if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
      return dropSlotTarget;
    }
  }

  const at = document.elementFromPoint(e.clientX, e.clientY);
  if (!at || !at.closest || !list.contains(at)) return null;

  // sobre un miembro de carpeta
  const memberRow = at.closest(".persona-children > .persona-card");
  if (memberRow && memberRow.dataset.name) {
    if (memberRow.dataset.name === dragging.name) return null; // propia card: no-op
    hoverEl = memberRow;
    const kids = memberRow.parentElement;
    if (dragging.kind === "persona") {
      if (isInFolder(dragging.name, kids.dataset.folder)) {
        // misma carpeta: SWAP (intercambian de lugar)
        return { folder: kids.dataset.folder, swap: memberRow.dataset.name };
      }
      // viniendo de afuera (tope u otra carpeta): INSERTAR en la posicion de
      // la card pasada (la card se correa; nadie sale de la carpeta)
      const rows = [...kids.querySelectorAll(":scope > .persona-card")];
      return { folder: kids.dataset.folder, index: rows.indexOf(memberRow) };
    }
    // carpeta sobre bloque de otra carpeta: gap antes/despues del bloque
    const rows = topLevelRows();
    const fi = rows.findIndex((r) => r.classList.contains("persona-folder") && r.dataset.name === kids.dataset.folder);
    const rect = kids.getBoundingClientRect();
    return { index: fi + sideOf(rect, e) };
  }

  // sobre la header de una carpeta
  const folderEl = at.closest(".persona-folder");
  if (folderEl && folderEl.dataset.name !== undefined) {
    if (dragging.kind === "persona") return { folder: folderEl.dataset.name };
    if (folderEl.dataset.name === dragging.name) return null; // propia carpeta: no-op
    hoverEl = folderEl;
    const rows = topLevelRows();
    return { index: rows.indexOf(folderEl) }; // antes de esa carpeta
  }

  // sobre una card suelta: SWAP si la arrastrada tambien esta en el tope;
  // INSERT si viene saliendo de una carpeta
  const personaEl = at.closest(".persona-card");
  if (personaEl && personaEl.dataset.name && !personaEl.closest(".persona-children")) {
    if (personaEl.dataset.name === dragging.name) return null; // propia card (fantasma)
    hoverEl = personaEl;
    if (isTopLevel(dragging.name)) return { swap: personaEl.dataset.name };
    const rows = topLevelRows();
    const i = rows.indexOf(personaEl);
    if (i !== -1) return { index: i };
  }

  // gap o margen (contenedor, no card): resolver a la fila mas cercana.
  // El viejo fallback al final del tope teletransportaba el preview y el
  // drop arrancaba la persona de la carpeta cuando el cursor caia en un gap.
  if (dragging.kind === "persona") {
    let kidsEl = at.closest(".persona-children");
    if (!kidsEl) {
      // margen del list (borde izq/der): se atribuye por eje Y a la carpeta
      // cuyo bloque de hijos cubre esa altura. Con el check de X el margen
      // nunca matcheaba (el bloque esta indentado) y el drag por el borde
      // sacaba la persona al tope, tapando la carpeta siguiente.
      for (const k of list.querySelectorAll(":scope > .persona-children")) {
        const r = k.getBoundingClientRect();
        if (e.clientY >= r.top - 4 && e.clientY <= r.bottom + 4) { kidsEl = k; break; }
      }
    }
    if (kidsEl) {
      const rows = [...kidsEl.querySelectorAll(":scope > .persona-card")];
      const near = nearestRow(rows, e);
      if (!near) return null;
      if (near.el.dataset.name === dragging.name) return null;
      hoverEl = near.el;
      return { folder: kidsEl.dataset.folder, index: rows.indexOf(near.el) + sideOf(near.rect, e) };
    }
  }
  const rows = topLevelRows();
  const near = nearestRow(rows, e);
  if (near) {
    if (near.el.dataset.name === dragging.name) return null;
    hoverEl = near.el;
    return { index: rows.indexOf(near.el) + sideOf(near.rect, e) };
  }
  return { index: state.personaLayout.length };
}

function clearDropIndicator() {
  if (dropSlotEl) { dropSlotEl.remove(); dropSlotEl = null; }
  dropSlotTarget = null;
  if (dropFolderEl) { dropFolderEl.classList.remove("drop-target"); dropFolderEl = null; }
  if (dragOverEl) { dragOverEl.classList.remove("drag-over"); dragOverEl = null; }
}

function showDropIndicator(target) {
  // target igual al actual: no re-crear el slot ni re-tocar clases
  // (el slot es overlay, no mueve cards; esto solo evita churn innecesario)
  if (target && dropSlotTarget && JSON.stringify(target) === JSON.stringify(dropSlotTarget)) {
    if (dragOverEl && dragOverEl !== hoverEl) dragOverEl.classList.remove("drag-over");
    if (hoverEl) hoverEl.classList.add("drag-over");
    dragOverEl = hoverEl;
    return;
  }
  clearDropIndicator();
  if (!target) return;
  if (target.swap) {
    // swap: sin slot (la card destino se mueve al lugar de la arrastrada, no
    // hay hueco que previsualizar): solo se resalta la card destino
    dropSlotTarget = target;
    if (hoverEl) hoverEl.classList.add("drag-over");
    dragOverEl = hoverEl;
    return;
  }
  // target no-op (resuelve en la posicion actual): sin slot. Mostrar el slot
  // en la celda destino engana: el drop no mueve nada.
  if (dragging.kind === "persona") {
    const preview = layout.movePersona(state.personaLayout, dragging.name, target);
    if (JSON.stringify(preview) === JSON.stringify(state.personaLayout)) return;
  } else {
    const from = state.personaLayout.findIndex((x) => x.type === "folder" && x.name === dragging.name);
    if (from === -1) return;
    const preview = layout.moveEntry(state.personaLayout, from, target.index);
    if (JSON.stringify(preview) === JSON.stringify(state.personaLayout)) return;
  }
  const list = document.getElementById("persona-list");
  if (target.folder && target.index == null) {
    // persona sobre header de carpeta: se agrega a esa carpeta
    const el = list.querySelector('.persona-folder[data-name="' + CSS.escape(target.folder) + '"]');
    if (el) { el.classList.add("drop-target"); dropFolderEl = el; }
    return;
  }
  // slot donde quedara: overlay absoluto sobre la celda destino exacta. No es
  // un item del grid: las cards no se mueven, la geometria bajo el cursor se
  // mantiene estable y no hay reflow (el reflow era lo que hacia saltar el
  // target en loop).
  let grid;
  let rows;
  if (target.folder) {
    grid = list.querySelector('.persona-children[data-folder="' + CSS.escape(target.folder) + '"]');
    if (!grid) return;
    rows = [...grid.querySelectorAll(":scope > .persona-card")];
  } else {
    grid = list;
    // mismo espacio de indices que el target: tope completo (cards + carpetas)
    rows = topLevelRows();
  }
  const draggedEl = rows.find((r) => r.dataset.name === dragging.name) || null;
  const i = Math.max(0, Math.min(target.index, rows.length));
  const oldIndex = draggedEl ? rows.indexOf(draggedEl) : -1;
  const adj = i > oldIndex ? i - 1 : i;
  const rowsExcl = rows.filter((r) => r !== draggedEl);
  const cellEl = rowsExcl[adj] || null;

  const slot = document.createElement("div");
  slot.className = "drop-slot";
  const lr = list.getBoundingClientRect();
  if (cellEl) {
    const r = cellEl.getBoundingClientRect();
    slot.style.left = (r.left - lr.left) + "px";
    slot.style.top = (r.top - lr.top + list.scrollTop) + "px";
    slot.style.width = r.width + "px";
    slot.style.height = r.height + "px";
  } else {
    // final: debajo del ultimo item del grid destino (en el tope el ultimo
    // item puede ser el bloque de hijos de la ultima carpeta)
    const items = [...grid.children].filter(
      (c) => c !== slot &&
        (c.classList.contains("persona-card") ||
         c.classList.contains("persona-folder") ||
         c.classList.contains("persona-children"))
    );
    const last = items[items.length - 1];
    if (!last) return;
    const r = last.getBoundingClientRect();
    const cardRef = dragging.kind === "folder"
      ? null
      : rowsExcl.find((x) => x.classList.contains("persona-card")) ||
        list.querySelector(".persona-card") || last;
    const h = dragging.kind === "folder" ? 36 : cardRef.offsetHeight;
    slot.style.left = (r.left - lr.left) + "px";
    slot.style.top = (r.bottom - lr.top + list.scrollTop + 8) + "px";
    slot.style.width = r.width + "px";
    slot.style.height = h + "px";
  }
  list.appendChild(slot);
  dropSlotEl = slot;
  dropSlotTarget = target;
  if (hoverEl) hoverEl.classList.add("drag-over");
  dragOverEl = hoverEl;
}

function applyDrop(target) {
  if (!dragging || !target) return;
  const was = dragging;
  let next;
  if (was.kind === "persona") {
    next = target.swap
      ? layout.swapPersonas(state.personaLayout, was.name, target.swap)
      : layout.movePersona(state.personaLayout, was.name, target);
  } else {
    const from = state.personaLayout.findIndex((e) => e.type === "folder" && e.name === was.name);
    if (from === -1) return;
    next = layout.moveEntry(state.personaLayout, from, target.index);
  }
  const same = JSON.stringify(next) === JSON.stringify(state.personaLayout);
  dragging = null;
  if (same) return;
  state.personaLayout = next;
  renderSidebar();
  saveLayout();
}

function initLayoutDnd() {
  const list = document.getElementById("persona-list");
  list.addEventListener("dragover", (e) => {
    if (!dragging || state.room !== "default") return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const target = dropTargetAt(e);
    const sig = target ? JSON.stringify(target) : "null";
    if (sig !== lastLoggedTarget) {
      lastLoggedTarget = sig;
      console.log(
        `[dnd] dragover target=${sig} @ (${Math.round(e.clientX)},${Math.round(e.clientY)})`
      );
    }
    showDropIndicator(target);
  });
  list.addEventListener("dragleave", (e) => {
    if (!list.contains(e.relatedTarget)) clearDropIndicator();
  });
  list.addEventListener("drop", (e) => {
    if (!dragging || state.room !== "default") return;
    e.preventDefault();
    // el drop cae donde quedo el ultimo preview (slot o card resaltada por
    // swap): re-computar aca veria el slot en el DOM (elementFromPoint lo
    // salta) y daria un target distinto al que se vio
    const fromPreview = !!dropSlotTarget;
    const target = dropSlotTarget || dropTargetAt(e);
    console.log(
      `[dnd] drop target=${target ? JSON.stringify(target) : "null"} ` +
      `(${fromPreview ? "preview" : "re-computado"}) @ (${Math.round(e.clientX)},${Math.round(e.clientY)})`
    );
    console.log("[dnd] ANTES: " + describeLayout(state.personaLayout));
    const before = JSON.stringify(state.personaLayout);
    clearDropIndicator();
    applyDrop(target);
    const changed = JSON.stringify(state.personaLayout) !== before;
    console.log(
      "[dnd] DESPUES: " + describeLayout(state.personaLayout) +
      (changed ? "" : "  <- SIN CAMBIO (no-op)")
    );
  });
}

// filas arrastrables (cards y headers de carpeta)
function wireDraggableRow(el, kind, name) {
  el.draggable = true;
  el.addEventListener("dragstart", (e) => {
    dragging = { kind, name };
    lastLoggedTarget = "__init__";
    el.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", name);
    console.log(`[dnd] dragstart kind=${kind} name="${name}"`);
    console.log("[dnd] layout inicial: " + describeLayout(state.personaLayout));
  });
  el.addEventListener("dragend", () => {
    const cancelled = !!dragging;
    dragging = null;
    el.classList.remove("dragging");
    clearDropIndicator(); // drag cancelado (fuera del list / Esc): sin restos
    if (cancelled) console.log("[dnd] dragend SIN drop (cancelado)");
  });
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
  drop.append(up, document.createTextNode("\nDrop a .wav to add a persona"));
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
      drop.append(icon, document.createTextNode(`\nTranscribing «${filename}»… (ASR + LLM)`));
    }
  }
}

async function uploadWav(file) {
  if (uploading) return;
  if (draft) return;
  if (!/\.wav$/i.test(file.name)) {
    toast("Only .wav files are accepted", "error");
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
    toast(err.message || "Error uploading the .wav", "error");
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

function buildForInstructRow() {
  const row = document.createElement("label");
  row.className = "fi-row";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = state.config.show_for_instruct !== false;
  cb.title = "Show or hide «For Instruct» across the app";
  const body = document.createElement("span");
  body.className = "fi-body";
  const nameEl = document.createElement("span");
  nameEl.className = "fi-name";
  nameEl.textContent = FOR_INSTRUCT;
  const sub = document.createElement("span");
  sub.className = "fi-sub";
  sub.textContent = "Voice without cloning — uses the TTS instruct";
  body.append(nameEl, sub);
  row.append(cb, body);
  cb.addEventListener("change", () => toggleForInstruct(cb.checked));
  return row;
}

async function toggleForInstruct(value) {
  try {
    const res = await api("/api/config", { method: "POST", body: { key: "show_for_instruct", value } });
    state.config.show_for_instruct = res.value;
    await refreshPersonas();
  } catch (err) {
    toast(err.message || "Error saving the visibility", "error");
  }
  renderUploadPanel();
}

function renderUploadPanel() {
  const panel = document.getElementById("rpanel-personas");
  panel.textContent = "";
  draftEls = null;
  panel.appendChild(buildForInstructRow());
  if (!draft) {
    panel.appendChild(buildDropZone());
    const hint = document.createElement("div");
    hint.className = "pd-hint";
    hint.textContent = "The name comes from the file: <Name>.wav, <Name>_Eng.wav or <Name>_Latino.wav";
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
  persona.appendChild(sectionLabel("Character"));
  const descField = draftField("Description", draft.description, "pm-textarea");
  const promptField = draftField("System prompt", draft.system_prompt, "pm-textarea tall");
  const genBtn = document.createElement("button");
  genBtn.className = "wav-btn";
  const gi = document.createElement("i");
  gi.className = "ti ti-refresh";
  genBtn.append(gi, document.createTextNode(" Re-generate sheet"));
  genBtn.addEventListener("click", () => regenerateDraft(genBtn));
  const pactions = document.createElement("div");
  pactions.className = "transcript-actions";
  pactions.appendChild(genBtn);
  persona.append(descField, promptField, pactions);
  panel.appendChild(persona);

  const transcript = document.createElement("div");
  transcript.className = "pd-section";
  transcript.appendChild(sectionLabel("Transcript"));
  const box = document.createElement("textarea");
  box.className = "transcript-box";
  box.value = draft.transcript ?? "";
  box.placeholder = "No transcript";
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
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", rejectDraft);
  const ok = document.createElement("button");
  ok.className = "pm-btn primary";
  ok.textContent = "Accept persona";
  ok.addEventListener("click", acceptDraft);
  actions.append(ok, cancel);
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
  btn.append(icon, document.createTextNode(" Re-transcribing…"));
  try {
    const body = await api(`/api/personas/pending/${draft.token}/retranscribe`, { method: "POST" });
    draft.transcript = body.transcript ?? "";
    box.value = draft.transcript;
    toast("Transcript updated", "success");
  } catch (err) {
    toast(err.message || "Error re-transcribing", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "";
    const r = document.createElement("i");
    r.className = "ti ti-refresh";
    btn.append(r, document.createTextNode(" Re-transcribe"));
  }
}

async function regenerateDraft(btn) {
  if (!draft || !draftEls) return;
  btn.disabled = true;
  btn.textContent = "";
  const icon = document.createElement("i");
  icon.className = "ti ti-loader spinning";
  btn.append(icon, document.createTextNode(" Generating sheet…"));
  try {
    const body = await api(`/api/personas/pending/${draft.token}/regenerate`, {
      method: "POST",
      body: { transcript: draftEls.box.value },
    });
    const nameVal = draftEls.name.value;
    Object.assign(draft, {
      name: body.name,
      description: body.description,
      system_prompt: body.system_prompt,
      avatar_color: body.avatar_color,
      language: body.language,
      transcript: body.transcript,
      generated: body.generated,
      warning: body.warning,
    });
    if (body.warning) toast(body.warning, "warning");
    else toast("Sheet re-generated", "success");
    renderUploadPanel();
    draftEls.name.value = nameVal;
  } catch (err) {
    toast(err.message || "Error re-generating the sheet", "error");
    btn.disabled = false;
    btn.textContent = "";
    const r = document.createElement("i");
    r.className = "ti ti-refresh";
    btn.append(r, document.createTextNode(" Re-generate sheet"));
  }
}

async function acceptDraft() {
  if (!draft || !draftEls) return;
  const name = draftEls.name.value.trim();
  if (!name) {
    toast("The name cannot be empty", "error");
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
    toast(`«${body.name}» created`, "success");
    if (body.warning) toast(body.warning, "warning");
    draft = null;
    await refreshPersonas();
    renderUploadPanel();
  } catch (err) {
    toast(err.message || "Error accepting the persona", "error");
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
  x.title = "Close";
  x.setAttribute("aria-label", "Close");
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
  cancel.textContent = "Cancel";
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
    toast(err.message || "Error loading the persona", "error");
    return;
  }
  const { overlay, boxEl, body } = buildModalShell(`Edit «${p.name}»`);

  const mkField = (label) => {
    const f = document.createElement("div");
    f.className = "pm-field";
    const l = document.createElement("div");
    l.className = "pm-label";
    l.textContent = label;
    f.appendChild(l);
    return f;
  };

  const nameF = mkField("Name");
  const nameInput = document.createElement("input");
  nameInput.className = "pm-input";
  nameInput.type = "text";
  nameInput.value = p.name;
  nameInput.spellcheck = false;
  nameF.appendChild(nameInput);

  const descF = mkField("Description");
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

  const voiceF = mkField("Reference voice");
  const voiceRow = document.createElement("div");
  voiceRow.className = "wav-row";
  const ico = document.createElement("i");
  ico.className = "ti ti-file-music";
  ico.style.fontSize = "15px";
  ico.style.color = "var(--text-muted)";
  voiceRow.appendChild(ico);
  const wavName = document.createElement("span");
  wavName.className = "wav-name";
  wavName.textContent = p.reference_audio ? p.reference_audio.split("/").pop() : "no reference voice";
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
    reBtn.title = "No reference voice";
  }
  voiceRow.appendChild(reBtn);
  voiceF.appendChild(voiceRow);

  const trF = mkField("Transcript");
  const trInput = document.createElement("textarea");
  trInput.className = "pm-textarea tall";
  trInput.value = p.transcript ?? "";
  trInput.placeholder = "No transcript";
  trF.appendChild(trInput);

  // foto: preview (solo muestra) + botones explícitos de subir/quitar
  const avF = mkField("Photo");
  const avRow = document.createElement("div");
  avRow.className = "pm-avatar-row";
  const avWrap = document.createElement("div");
  avWrap.className = "pm-avatar-wrap";
  avWrap.title = "Upload or drag an image here";
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
  upBtn.className = "pm-btn primary";
  upBtn.textContent = "Upload photo";
  const rmBtn = document.createElement("button");
  rmBtn.className = "pm-btn danger";
  rmBtn.textContent = "Remove";
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
      toast("Drop an image (.png, .jpg, .webp or .gif)", "error");
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
        toast("Photo removed", "success");
      })
      .catch((err) => toast(err.message || "Error removing the photo", "error"))
      .finally(() => { rmBtn.disabled = false; });
  });

  body.append(avF, nameF, descF, promptF, colorF, voiceF, trF);

  const { foot } = buildModalFoot();
  const del = document.createElement("button");
  del.className = "pm-btn danger";
  del.textContent = "Delete";
  del.style.marginLeft = "auto";
  // persona-sistema: no se borra desde el modal (la salida es el toggle
  // del panel Añadir persona); si se borrara a mano, se re-crea en el arranque
  if (p.name === FOR_INSTRUCT) del.style.display = "none";
  del.addEventListener("click", () => deletePersona(p, del));
  const save = document.createElement("button");
  save.className = "pm-btn primary";
  save.textContent = "Save";
  save.addEventListener("click", () =>
    savePersonaModal(p, { name: nameInput, desc: descInput, prompt: promptInput, color: colorInput, transcript: trInput }, save)
  );
  foot.append(save, foot.firstChild, del);
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
  btn.append(icon, document.createTextNode(" Re-transcribing…"));
  try {
    const body = await api(`/api/personas/${encodeURIComponent(p.name)}/retranscribe`, { method: "POST" });
    p.transcript = body.transcript ?? "";
    trInput.value = p.transcript;
    toast("Transcript updated", "success");
  } catch (err) {
    toast(err.message || "Error re-transcribing", "error");
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
    toast("Persona updated", "success");
    await refreshPersonas();
  } catch (err) {
    toast(err.message || "Error saving the persona", "error");
    saveBtn.disabled = false;
  }
}

async function deletePersona(p, btn) {
  if (!window.confirm(`Delete «${p.name}»? Its reference voice will be lost.`)) return;
  btn.disabled = true;
  try {
    await api(`/api/personas/${encodeURIComponent(p.name)}`, { method: "DELETE" });
  } catch (err) {
    toast(err.message || "Error deleting the persona", "error");
    btn.disabled = false;
    return;
  }
  closePersonaModal();
  toast(`«${p.name}» deleted`, "success");
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
// flow: elegir/dropear imagen -> modal con viewport en forma de card (zoom con
// rueda, pan arrastrando) -> "Aplicar" renderiza 512x461 (ratio 100:90, igual
// que .persona-card) a PNG y sube al endpoint de avatar. El preview es 1:1 con
// lo que mostrara la card; los avatares redondos hacen cover al centro.

const CROP_W = 280; // viewport en px CSS
const CROP_H = 252; // 280 * 90 / 100
const CROP_OUT_W = 512; // canvas de salida
const CROP_OUT_H = 461; // 512 * 252 / 280
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
      toast("Photo updated", "success");
      if (onDone) onDone();
    })
    .catch((err) => {
      toast(err.message || "Error uploading the photo", "error");
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
    toast("Could not read the image", "error");
  };
  img.src = url;
}

function buildCropModal(p, file, img, url, refresh) {
  const nw = img.naturalWidth;
  const nh = img.naturalHeight;
  // escala base: la imagen "cover" el viewport (rectangular) con zoom 1
  const base = Math.max(CROP_W / nw, CROP_H / nh);
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
  title.textContent = `Crop photo of «${p.name}»`;
  const x = document.createElement("button");
  x.className = "persona-modal-x";
  x.title = "Close";
  x.setAttribute("aria-label", "Close");
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
  hint.textContent = "Wheel: zoom · Drag: move";

  const foot = document.createElement("div");
  foot.className = "crop-foot";
  const cancel = document.createElement("button");
  cancel.className = "pm-btn";
  cancel.textContent = "Cancel";
  const apply = document.createElement("button");
  apply.className = "pm-btn primary";
  apply.textContent = "Apply photo";
  foot.append(cancel, apply);

  box.append(head, stage, hint, foot);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  cropModal = overlay;

  const applyTransform = () => {
    const scale = base * zoom;
    const maxX = Math.max(0, (nw * scale - CROP_W) / 2);
    const maxY = Math.max(0, (nh * scale - CROP_H) / 2);
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
    apply.textContent = "Applying…";
    // el viewport [0,CROP_W]x[0,CROP_H] se mapea a [0,CROP_OUT_W]x[0,CROP_OUT_H];
    // la imagen vista es scale * (punto - centro) + (px,py) respecto al centro
    const kx = CROP_OUT_W / CROP_W;
    const ky = CROP_OUT_H / CROP_H;
    const scale = base * zoom;
    const canvas = document.createElement("canvas");
    canvas.width = CROP_OUT_W;
    canvas.height = CROP_OUT_H;
    const ctx = canvas.getContext("2d");
    ctx.translate(CROP_OUT_W / 2 + kx * px, CROP_OUT_H / 2 + ky * py);
    ctx.scale(kx * scale, ky * scale);
    ctx.drawImage(img, -nw / 2, -nh / 2);
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          toast("Could not generate the image", "error");
          apply.disabled = false;
          cancel.disabled = false;
          apply.textContent = "Apply photo";
          return;
        }
        uploadAvatarFile(p, blob, "avatar.png", refresh, () => close());
      },
      "image/png"
    );
  });
}

async function refreshPersonas() {
  const data = await api("/api/personas");
  state.personas = data.personas || [];
  state.personaLayout = Array.isArray(data.layout)
    ? data.layout
    : layout.normalize(null, state.personas.map((p) => p.name));
  state.layoutColumns = Number(data.layout_columns) || 2;
  applyColumns();
  renderSidebar();
  renderWhoChips();
  refreshTtsLanguageOptions();
}

// Card encendida mientras suena el audio de esa persona (evento
// "persona:speaking" de tts.js; null = cola vacia). Todas las ocurrencias:
// la misma persona puede aparecer en el tope y en una carpeta/room.
function applySpeaking(name) {
  document.querySelectorAll(".persona-card").forEach((el) => {
    el.classList.toggle("speaking", !!name && el.dataset.name === name);
  });
}

export async function initPersonas() {
  // corre en paralelo con initSettings: si la config todavia no llego, la
  // traemos para que el toggle de For Instruct arranque con el estado real
  if (Object.keys(state.config).length === 0) {
    try {
      state.config = await api("/api/config");
    } catch {
      // si falla, initSettings la carga igualmente; el checkbox queda en default
    }
  }
  window.addEventListener("persona:speaking", (e) => applySpeaking(e.detail.persona));
  await refreshPersonas();
  document.getElementById("btn-new-folder").addEventListener("click", createFolder);
  initColToggle();
  initLayoutDnd();
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
    whoPlus.title = "Show hidden";
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
  title.textContent = "Choose who responds";
  const x = document.createElement("button");
  x.className = "who-modal-x";
  x.title = "Close";
  x.setAttribute("aria-label", "Close");
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
  const mk = (label, value, title) => {
    const b = document.createElement("button");
    b.className = "who-chip" + (whoIsSel(value) ? " sel" : "");
    b.textContent = label;
    b.dataset.value = value;
    if (title) b.title = title;
    b.addEventListener("click", (e) => {
      if (e.ctrlKey || e.metaKey) ctrlSelectWho(value);
      else selectWho(value);
    });
    whoWrap.appendChild(b);
    whoChips.push(b);
  };
  mk("LLM router", "router");
  for (const p of vis) mk(p.name, p.name, "ctrl+click to select multiple people");
  mk("Random", "random");

  whoPlus = document.createElement("button");
  whoPlus.className = "who-chip who-chip-plus";
  whoPlus.style.display = "none";
  whoPlus.addEventListener("click", openWhoModal);
  whoWrap.appendChild(whoPlus);

  applyWhoFit();
  initWhoResize();
}
