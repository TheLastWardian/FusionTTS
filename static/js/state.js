// state.js — estado global de la SPA (config, personas, rooms, TTS, tema, layout).
function storedRoom() {
  // room activa persistida: F5/recarga no pierde la room donde estabas
  try {
    const r = localStorage.getItem("ft.room");
    return r && r !== "default" ? r : "default";
  } catch {
    return "default";
  }
}

export const state = {
  config: {},
  personas: [],
  personaLayout: [],
  rooms: [],
  room: storedRoom(),
  who: "router",
  streaming: false,
  tts: { engine: null, dispatcher: null },
  theme: "dark",
  layout: { left: 200, right: 264, leftCollapsed: false, rightCollapsed: false, roomsH: 190 },
  mode: "wide",
};
