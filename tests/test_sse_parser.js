// test_sse_parser.js — tests del parser SSE (static/js/sse.js), node --test.
const test = require("node:test");
const assert = require("node:assert/strict");
const { SSEParser } = require("../static/js/sse.js");

test("evento completo en un solo feed", () => {
  const events = [];
  const p = new SSEParser((ev) => events.push(ev));
  p.feed('data: {"type":"start","persona":"Jean"}\n\n');
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], { type: "start", persona: "Jean" });
});

test("evento partido en 2 feeds (corte a mitad de JSON)", () => {
  const events = [];
  const p = new SSEParser((ev) => events.push(ev));
  p.feed('data: {"type":"token","token":"hol');
  assert.equal(events.length, 0);
  p.feed('a"}\n\n');
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], { type: "token", token: "hola" });
});

test("3 eventos en un solo chunk", () => {
  const events = [];
  const p = new SSEParser((ev) => events.push(ev));
  p.feed('data: {"i":1}\n\ndata: {"i":2}\n\ndata: {"i":3}\n\n');
  assert.equal(events.length, 3);
  assert.deepEqual(events, [{ i: 1 }, { i: 2 }, { i: 3 }]);
});

test("unicode/emoji y acentos intactos", () => {
  const events = [];
  const p = new SSEParser((ev) => events.push(ev));
  p.feed('data: {"type":"token","token":"¿Hola, señoría? 🎉 café — ñandú"}\n\n');
  assert.equal(events.length, 1);
  assert.equal(events[0].token, "¿Hola, señoría? 🎉 café — ñandú");
});

test("bloque con 2 lineas data: concatenadas con \\n y parseadas", () => {
  const events = [];
  const p = new SSEParser((ev) => events.push(ev));
  p.feed('data: {"nombre":\ndata: "Jean"}\n\n');
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], { nombre: "Jean" });
});

test("linea de comentario se ignora", () => {
  const events = [];
  const p = new SSEParser((ev) => events.push(ev));
  p.feed(": ping\n\n");
  assert.equal(events.length, 0);
  p.feed(": otro comentario\ndata: {\"ok\":true}\n\n");
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], { ok: true });
});

test("JSON invalido -> warn + skip (nunca throw, nunca onEvent)", () => {
  const events = [];
  const warnings = [];
  const origWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(" "));
  try {
    const p = new SSEParser((ev) => events.push(ev));
    assert.doesNotThrow(() => p.feed("data: {no es json}\n\n"));
    assert.equal(events.length, 0);
    assert.ok(warnings.length >= 1);
    p.feed('data: {"recupera":true}\n\n');
    assert.equal(events.length, 1);
    assert.deepEqual(events[0], { recupera: true });
  } finally {
    console.warn = origWarn;
  }
});

test("fin de stream sin \\n\\n final: no pierde eventos previos", () => {
  const events = [];
  const p = new SSEParser((ev) => events.push(ev));
  p.feed('data: {"n":1}\n\ndata: {"n":2}\n\ndata: {"n":3}\n\n');
  p.feed('data: {"n":4}');
  assert.equal(events.length, 3);
  assert.deepEqual(events, [{ n: 1 }, { n: 2 }, { n: 3 }]);
});
