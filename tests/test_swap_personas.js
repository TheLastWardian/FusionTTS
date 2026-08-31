// test_swap_personas.js — tests de swapPersonas (static/js/persona-layout.js), node --test.
const test = require("node:test");
const assert = require("node:assert/strict");

let swapPersonas;
test("setup: importar modulo ESM", async () => {
  ({ swapPersonas } = await import("../static/js/persona-layout.js"));
});

const F = (name, personas) => ({ type: "folder", name, personas });
const P = (name) => ({ type: "persona", name });

test("misma carpeta: intercambian el lugar exacto", () => {
  const l = [F("G", ["A", "X", "B"])];
  const out = swapPersonas(l, "A", "B");
  assert.deepEqual(out, [F("G", ["B", "X", "A"])]);
});

test("misma carpeta: adyacentes", () => {
  const l = [F("G", ["A", "B", "C"])];
  assert.deepEqual(swapPersonas(l, "A", "B"), [F("G", ["B", "A", "C"])]);
  assert.deepEqual(swapPersonas(l, "B", "C"), [F("G", ["A", "C", "B"])]);
});

test("misma carpeta: arrastrada DESPUES de la destino (regresion no-op)", () => {
  // caso real: Genshin [Jean, Keqing, Mona, ...], arrastrar Mona sobre Keqing
  const l = [F("G", ["Jean", "Keqing", "Mona", "Hu Tao"])];
  assert.deepEqual(
    swapPersonas(l, "Mona", "Keqing"),
    [F("G", ["Jean", "Mona", "Keqing", "Hu Tao"])]
  );
  // y en sentido inverso
  assert.deepEqual(
    swapPersonas(l, "Keqing", "Mona"),
    [F("G", ["Jean", "Mona", "Keqing", "Hu Tao"])]
  );
});

test("carpetas distintas: cada una toma la carpeta y el indice de la otra", () => {
  const l = [F("F1", ["A", "x"]), F("F2", ["y", "B", "z"])];
  const out = swapPersonas(l, "A", "B");
  assert.deepEqual(out, [F("F1", ["B", "x"]), F("F2", ["y", "A", "z"])]);
});

test("tope <-> carpeta", () => {
  const l = [P("P1"), F("F", ["B"]), P("P2"), P("A")];
  const out = swapPersonas(l, "A", "B");
  assert.deepEqual(out, [P("P1"), F("F", ["A"]), P("P2"), P("B")]);
});

test("tope <-> tope", () => {
  const l = [P("A"), P("X"), P("B")];
  assert.deepEqual(swapPersonas(l, "A", "B"), [P("B"), P("X"), P("A")]);
});

test("idempotente de a pares (ir y vuelta vuelve al original)", () => {
  const l = [F("G", ["A", "X", "B"]), P("C")];
  const once = swapPersonas(l, "A", "B");
  assert.deepEqual(swapPersonas(once, "B", "A"), l);
});

test("misma persona -> sin cambios", () => {
  const l = [F("G", ["A"])];
  assert.deepEqual(swapPersonas(l, "A", "A"), l);
});

test("nombre inexistente -> sin cambios", () => {
  const l = [F("G", ["A"])];
  assert.deepEqual(swapPersonas(l, "A", "Nadie"), l);
});

test("invariante: cada persona sigue apareciendo exactamente una vez", () => {
  const l = [F("F1", ["A", "x"]), F("F2", ["y", "B", "z"]), P("C")];
  const out = swapPersonas(l, "A", "B");
  const names = [];
  for (const e of out) {
    if (e.type === "persona") names.push(e.name);
    else names.push(...e.personas);
  }
  assert.equal(names.length, new Set(names).size);
  assert.deepEqual(names.sort(), ["A", "B", "C", "x", "y", "z"]);
});
