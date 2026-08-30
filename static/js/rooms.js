// rooms.js — lista de rooms en la sidebar (main fija + rooms creadas), switching, echo chamber y creación con asignación de personajes.
import { state } from "./state.js";
import { api, toast } from "./utils.js";
import { cancelChat } from "./chat.js";
import { loadHistory, stopHistoryAudio, showEmptyState } from "./persistence.js";
import { refreshContextUsage } from "./context.js";
import { refreshRoomViews } from "./personas.js";

const MAIN_ROOM = "default";
const MAIN_LABEL = "main";

// Cambia la room activa y la persiste (F5 vuelve a esta room)
function setRoom(name) {
  state.room = name;
  try {
    localStorage.setItem("ft.room", name);
  } catch {}
}

function mainRow() {
  const row = document.createElement("div");
  // "active" va en la fila: el borde dorado de seleccion esta en .room-row.active
  row.className = "room-row" + (state.room === MAIN_ROOM ? " active" : "");
  const b = document.createElement("button");
  b.className = "room-item";
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
  row.className = "room-row" + (r.name === state.room ? " active" : "");
  const b = document.createElement("button");
  b.className = "room-item";
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
  setRoom(name);
  renderRooms();
  refreshRoomViews();
  updateLabel();
  toast("Room: " + (name === MAIN_ROOM ? MAIN_LABEL : name), "info");
  await loadHistory(name);
  refreshContextUsage();
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

// Borrar todo el contexto de la room actual (solo contexto: los wavs/imagenes
// guardados en disco no se tocan)
async function clearRoomChat() {
  if (state.streaming) {
    toast("Esperá a que termine la respuesta antes de borrar", "warning");
    return;
  }
  const el = document.getElementById("messages");
  if (!el.querySelector(".msg")) {
    toast("No hay mensajes para borrar en esta room", "info");
    return;
  }
  const label = state.room === MAIN_ROOM ? MAIN_LABEL : state.room;
  if (
    !confirm(
      "¿Borrar todo el contexto de la room '" + label + "'?\n" +
        "Se eliminan todos los mensajes del chat. El audio y las imágenes " +
        "guardados en disco no se tocan.",
    )
  ) {
    return;
  }
  try {
    await api("/api/rooms/" + encodeURIComponent(state.room) + "/messages", {
      method: "DELETE",
    });
    stopHistoryAudio();
    el.textContent = "";
    showEmptyState(el);
    refreshContextUsage();
    toast("Contexto de la room borrado", "success");
  } catch (err) {
    toast(err.message || "Error al borrar el contexto", "error");
  }
}

// Compactar contexto: el server resume todo lo que no este en los ultimos
// 10 mensajes (resumen rolling) y marca esos mensajes como "compacted".
async function compactRoomChat() {
  const btn = document.getElementById("btn-compact");
  if (btn.disabled) return;
  if (state.streaming) {
    toast("Esperá a que termine la respuesta antes de compactar", "warning");
    return;
  }
  const icon = btn.querySelector("i");
  btn.disabled = true;
  icon.className = "ti ti-loader spinning";
  try {
    const d = await api(
      "/api/rooms/" + encodeURIComponent(state.room) + "/compact",
      { method: "POST" },
    );
    // el tooltip del boton pasa a mostrar el ultimo resumen (para hojearlo)
    btn.title = "Ultimo resumen:\n\n" + d.summary;
    btn.setAttribute("aria-label", "Ver ultimo resumen / compactar contexto");
    toast(
      "Contexto compactado: " + d.compacted + " mensajes resumidos " +
        (d.summary_tokens != null ? "(" + d.summary_tokens + " tokens)" : ""),
      "success",
    );
    // re-render para que la flag "compacted" se vea (reprocesar deshabilitado)
    await loadHistory(state.room);
    refreshContextUsage();
  } catch (err) {
    toast(err.message || "Error al compactar el contexto", "error");
  } finally {
    btn.disabled = false;
    icon.className = "ti ti-minimize";
  }
}

export async function initRooms() {
  const data = await api("/api/rooms");
  state.rooms = data.rooms || [];
  // la room persistida pudo haber sido eliminada del server desde la ultima vez
  if (state.room !== MAIN_ROOM && !state.rooms.some((r) => r.name === state.room)) {
    setRoom(MAIN_ROOM);
  }
  renderRooms();
  updateLabel();
  // initPersonas corre en paralelo: si renderizo antes que este, la sidebar
  // quedo filtrada por una lista de rooms vacia; re-renderizo con la real
  refreshRoomViews();
  refreshContextUsage();

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
      const icon = document.createElement("i");
      icon.className = "ti ti-check";
      label.append(s, icon, cb);
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
      setRoom(created.name);
      closeForm();
      renderRooms();
      refreshRoomViews();
      updateLabel();
      toast("Room creado: " + created.name, "success");
      await loadHistory(created.name);
    } catch (err) {
      toast(err.message || "Error al crear el room", "error");
    }
  };

  document.getElementById("btn-clear-room").addEventListener("click", clearRoomChat);
  document
    .getElementById("btn-compact")
    .addEventListener("click", compactRoomChat);

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
      setRoom(MAIN_ROOM);
      renderRooms();
      refreshRoomViews();
      updateLabel();
      await loadHistory(MAIN_ROOM);
      return toast("Room eliminada: " + name, "success");
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
    const icon = document.createElement("i");
    icon.className = "ti ti-check";
    label.append(s, icon, cb);
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
    if (state.room === r.name) {
      refreshRoomViews();
      refreshContextUsage();
    }
    toast("Personajes actualizados: " + r.name, "success");
  } catch (err) {
    toast(err.message || "Error al guardar los personajes", "error");
  }
}
