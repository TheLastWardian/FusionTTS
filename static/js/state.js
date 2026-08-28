// state.js — estado global de la SPA (config, personas, rooms, TTS, tema, layout).
export const state = {
  config: {},
  personas: [],
  rooms: [],
  room: "default",
  who: "router",
  streaming: false,
  tts: { engine: null, dispatcher: null },
  theme: "dark",
  layout: { left: 200, right: 264, leftCollapsed: false, rightCollapsed: false, roomsH: 190 },
  mode: "wide",
};
