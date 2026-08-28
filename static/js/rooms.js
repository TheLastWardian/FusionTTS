// rooms.js — lista de rooms en la sidebar (main fija + rooms creadas), switching, echo chamber y creación con asignación de personajes.
import { state } from "./state.js";
import { api, toast } from "./utils.js";
import { cancelChat } from "./chat.js";
import { loadHistory } from "./persistence.js";

const MAIN_ROOM = "default";
const MAIN_LABEL = "main";

function mainRow() {
  const row = document.createElement("div");
  row.className = "room-row";
  const b = document.createElement("button");
  b.className = "room-item" + (state.room === MAIN_ROOM ? " active" : "");
  const i = document.createElement("i");
  i.className = "ti ti-home";
  const span = document.createElement("span");
  span.textContent = MAIN_LABEL;
  b.append(i, span);
  b.title = "Todos los personajes";
  b.addEventListener("click", () => switchRoom(MAIN_ROOM));
  row.appendChild(b);
  return row;
}

function roomRow(r) {
  const row = document.createElement("div");
  row.className = "room-row";
  const b = document.createElement("button");
  b.className = "room-item" + (r.name === state.room ? " active" : "");
  const i = document.createElement("i");
  i.className = "ti ti-messages";
  const span = document.createElement("span");
  span.textContent = r.name;
  b.append(i, span);
  b.addEventListener("click", () => switchRoom(r.name));
  const echo = document.createElement("button");
  echo.className = "room-echo" + (r.echo_chamber ? " on" : "");
  echo.title = "Echo chamber";
  const ei = document.createElement("i");
  ei.className = "ti ti-repeat";
  echo.appendChild(ei);
  echo.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleEcho(r.name);
  });
  const personas = document.createElement("button");
  personas.className = "room-personas";
  personas.title = "Personajes de la room";
  const pi = document.createElement("i");
  pi.className = "ti ti-users";
  personas.appendChild(pi);
  personas.addEventListener("click", (e) => {
    e.stopPropagation();
    openRoomPersonaModal(r.name);
  });
  const del = document.createElement("button");
  del.className = "room-del";
  del.title = "Eliminar room";
  const di = document.createElement("i");
  di.className = "ti ti-trash";
  del.appendChild(di);
  del.addEventListener("click", (e) => {
    e.stopPropagation();
    deleteRoom(r.name);
  });
  row.append(b, echo, personas, del);
  return row;
}

function renderRooms() {
  const list = document.getElementById("room-list");
  list.textContent = "";
  list.appendChild(mainRow());
  for (const r of state.rooms) list.appendChild(roomRow(r));
}

async function switchRoom(name) {
  if (name === state.room) return;
  if (state.streaming) await cancelChat();
  state.room = name;
  renderRooms();
  updateLabel();
  toast("Room: " + (name === MAIN_ROOM ? MAIN_LABEL : name), "info");
  await loadHistory(name);
}

async function toggleEcho(name) {
  const r = state.rooms.find((x) => x.name === name);
  if (!r) return;
  try {
    const updated = await api("/api/rooms/" + encodeURIComponent(name), {
      method: "PUT",
      body: {
        name: r.name,
        persona_names: r.persona_names,
        echo_chamber: !r.echo_chamber,
      },
    });
    Object.assign(r, updated);
    renderRooms();
    toast("Echo chamber " + (r.echo_chamber ? "ON" : "OFF") + ": " + r.name, "info");
  } catch (err) {
    toast(err.message || "Error al cambiar echo chamber", "error");
  }
}

function updateLabel() {
  document.getElementById("room-label").textContent =
    state.room === MAIN_ROOM ? MAIN_LABEL : state.room;
}

export async function initRooms() {
  const data = await api("/api/rooms");
  state.rooms = data.rooms || [];
  renderRooms();
  updateLabel();

  const form = document.getElementById("room-form");
  const input = document.getElementById("room-name");
  const newBtn = document.getElementById("btn-new-room");
  const okBtn = document.getElementById("room-form-ok");
  const cancelBtn = document.getElementById("room-form-cancel");
  const checksEl = document.getElementById("room-form-personas");

  const buildChecks = () => {
    checksEl.textContent = "";
    for (const p of state.personas) {
      const label = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = p.name;
      const s = document.createElement("span");
      s.textContent = p.name;
      label.append(s, cb);
      checksEl.appendChild(label);
    }
  };

  const selectedNames = () =>
    [...checksEl.querySelectorAll("input[type=checkbox]:checked")].map((cb) => cb.value);

  const closeForm = () => {
    form.hidden = true;
    newBtn.hidden = false;
    input.value = "";
  };

  const submit = async () => {
    const name = input.value.trim();
    if (!name) return;
    const names = selectedNames();
    if (names.length === 0) {
      toast("Elegí al menos 1 personaje para la room", "error");
      return;
    }
    try {
      const created = await api("/api/rooms", {
        method: "POST",
        body: { name, persona_names: names, echo_chamber: false },
      });
      state.rooms.push(created);
      state.room = created.name;
      closeForm();
      renderRooms();
      updateLabel();
      toast("Room creado: " + created.name, "success");
      await loadHistory(created.name);
    } catch (err) {
      toast(err.message || "Error al crear el room", "error");
    }
  };

  newBtn.addEventListener("click", () => {
    if (state.personas.length === 0) {
      toast("Personas todavía no cargadas", "error");
      return;
    }
    buildChecks();
    form.hidden = false;
    newBtn.hidden = true;
    input.value = "";
    input.focus();
  });
  okBtn.addEventListener("click", submit);
  cancelBtn.addEventListener("click", closeForm);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape") {
      closeForm();
    }
  });
}

async function deleteRoom(name) {
  if (!confirm("¿Eliminar la room '" + name + "'?\nSe borrará la room y todo su historial.")) return;
  try {
    await api("/api/rooms/" + encodeURIComponent(name), { method: "DELETE" });
    state.rooms = state.rooms.filter((x) => x.name !== name);
    if (state.room === name) {
      state.room = MAIN_ROOM;
      await loadHistory(MAIN_ROOM);
    }
    renderRooms();
    updateLabel();
    toast("Room eliminada: " + name, "success");
  } catch (err) {
    toast(err.message || "Error al eliminar la room", "error");
  }
}

let roomModal = null;

function closeRoomModal() {
  if (!roomModal) return;
  document.removeEventListener("keydown", roomModalKey);
  roomModal.remove();
  roomModal = null;
}

function roomModalKey(e) {
  if (e.key === "Escape") closeRoomModal();
}

function buildRoomChecks(container, checkedNames) {
  for (const p of state.personas) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = p.name;
    if (checkedNames.includes(p.name)) cb.checked = true;
    const s = document.createElement("span");
    s.textContent = p.name;
    label.append(s, cb);
    container.appendChild(label);
  }
}

function openRoomPersonaModal(name) {
  if (roomModal) return;
  const r = state.rooms.find((x) => x.name === name);
  if (!r) return;
  if (state.personas.length === 0) {
    toast("Personas todavía no cargadas", "error");
    return;
  }
  const overlay = document.createElement("div");
  overlay.className = "persona-modal-overlay";
  const boxEl = document.createElement("div");
  boxEl.className = "persona-modal";

  const head = document.createElement("div");
  head.className = "persona-modal-head";
  const titleEl = document.createElement("div");
  titleEl.className = "persona-modal-title";
  titleEl.textContent = "Personajes de «" + r.name + "»";
  const x = document.createElement("button");
  x.className = "persona-modal-x";
  x.title = "Cerrar";
  x.setAttribute("aria-label", "Cerrar");
  const xi = document.createElement("i");
  xi.className = "ti ti-x";
  x.appendChild(xi);
  x.addEventListener("click", closeRoomModal);
  head.append(titleEl, x);

  const body = document.createElement("div");
  body.className = "persona-modal-body";
  const checks = document.createElement("div");
  checks.className = "persona-checks";
  buildRoomChecks(checks, r.persona_names || []);
  body.appendChild(checks);

  const foot = document.createElement("div");
  foot.className = "persona-modal-foot";
  const cancel = document.createElement("button");
  cancel.className = "pm-btn";
  cancel.textContent = "Cancelar";
  cancel.addEventListener("click", closeRoomModal);
  const save = document.createElement("button");
  save.className = "pm-btn primary";
  save.textContent = "Guardar";
  save.addEventListener("click", () => saveRoomPersonas(name, checks));
  foot.append(cancel, save);

  boxEl.append(head, body, foot);
  overlay.appendChild(boxEl);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeRoomModal();
  });
  document.body.appendChild(overlay);
  roomModal = overlay;
  document.addEventListener("keydown", roomModalKey);
}

async function saveRoomPersonas(name, checks) {
  const r = state.rooms.find((x) => x.name === name);
  if (!r) return;
  const names = [...checks.querySelectorAll("input[type=checkbox]:checked")].map((cb) => cb.value);
  if (names.length === 0) {
    toast("La room necesita al menos 1 personaje", "error");
    return;
  }
  try {
    const updated = await api("/api/rooms/" + encodeURIComponent(name), {
      method: "PUT",
      body: { name: r.name, persona_names: names, echo_chamber: r.echo_chamber },
    });
    Object.assign(r, updated);
    closeRoomModal();
    renderRooms();
    toast("Personajes actualizados: " + r.name, "success");
  } catch (err) {
    toast(err.message || "Error al guardar los personajes", "error");
  }
}
