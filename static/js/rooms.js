// rooms.js — lista de rooms en la sidebar, switching, echo chamber y creación de room nuevo.
import { state } from "./state.js";
import { api, toast } from "./utils.js";
import { cancelChat } from "./chat.js";
import { loadHistory } from "./persistence.js";

function renderRooms() {
  const list = document.getElementById("room-list");
  list.textContent = "";
  if (state.rooms.length === 0) {
    const empty = document.createElement("div");
    empty.className = "list-empty";
    empty.textContent = "Sin rooms";
    list.appendChild(empty);
  }
  for (const r of state.rooms) {
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
    row.append(b, echo);
    list.appendChild(row);
  }
}

async function switchRoom(name) {
  if (name === state.room) return;
  if (state.streaming) await cancelChat();
  state.room = name;
  renderRooms();
  updateLabel();
  toast("Room: " + name, "info");
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
  document.getElementById("room-label").textContent = state.room;
}

export async function initRooms() {
  const data = await api("/api/rooms");
  state.rooms = data.rooms || [];
  if (state.rooms.length > 0 && !state.rooms.some((r) => r.name === state.room)) {
    state.room = state.rooms[0].name;
  }
  renderRooms();
  updateLabel();

  const form = document.getElementById("room-form");
  const input = document.getElementById("room-name");
  const newBtn = document.getElementById("btn-new-room");
  const okBtn = document.getElementById("room-form-ok");

  const closeForm = () => {
    form.hidden = true;
    newBtn.hidden = false;
    input.value = "";
  };

  const submit = async () => {
    const name = input.value.trim();
    if (!name) return;
    try {
      const created = await api("/api/rooms", {
        method: "POST",
        body: {
          name,
          persona_names: state.personas.map((p) => p.name),
          echo_chamber: false,
        },
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
    form.hidden = false;
    newBtn.hidden = true;
    input.value = "";
    input.focus();
  });
  okBtn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape") {
      closeForm();
    }
  });
}
