// test_who_fit.js — tests de fitChips (static/js/chips-fit.js), node --test.
const test = require("node:test");
const assert = require("node:assert/strict");
const { fitChips } = require("../static/js/chips-fit.js");

test("lista vacia -> sin ocultos, sin +N, sin errores", () => {
  const r = fitChips([], 100, -1, 5, 0);
  assert.deepEqual(r.visible, []);
  assert.deepEqual(r.hidden, []);
  assert.equal(r.plusFits, true);
});

test("todo cabe (con seleccionado) -> sin ocultos", () => {
  const r = fitChips([30, 30, 30], 100, 0, 5, 0);
  assert.deepEqual(r.visible, [true, true, true]);
  assert.deepEqual(r.hidden, []);
  assert.equal(r.plusFits, true);
});

test("todo cabe sin seleccionado -> sin ocultos", () => {
  const r = fitChips([30, 30], 70, -1, 5, 0);
  assert.deepEqual(r.visible, [true, true]);
  assert.deepEqual(r.hidden, []);
});

test("algunos no caben -> ocultos son el sufijo en orden de lista, N correcto", () => {
  const r = fitChips([30, 30, 30, 30], 80, -1, 5, 0);
  assert.deepEqual(r.visible, [true, true, false, false]);
  assert.deepEqual(r.hidden, [2, 3]);
  assert.equal(r.hidden.length, 2);
  assert.equal(r.plusFits, true);
});

test("seleccionado en posicion tardia -> siempre visible", () => {
  const r = fitChips([30, 30, 30, 30, 30], 45, 4, 5, 0);
  assert.deepEqual(r.visible, [false, false, false, false, true]);
  assert.deepEqual(r.hidden, [0, 1, 2, 3]);
});

test("seleccionado en el medio -> visible, se degrada lo que no deja entrar el +N", () => {
  const r = fitChips([30, 30, 30, 30], 100, 1, 5, 40);
  assert.deepEqual(r.visible, [false, true, false, false]);
  assert.deepEqual(r.hidden, [0, 2, 3]);
  assert.equal(r.plusFits, true);
});

test("reserva de espacio para +N degrada un chip visible no seleccionado", () => {
  const r = fitChips([30, 30, 30], 100, -1, 5, 35);
  assert.deepEqual(r.visible, [true, false, false]);
  assert.deepEqual(r.hidden, [1, 2]);
  assert.equal(r.plusFits, true);
});

test("caso patologico (seleccionado + plus > available) -> +N oculto, seleccionado visible", () => {
  const r = fitChips([80, 40], 100, 0, 5, 30);
  assert.deepEqual(r.visible, [true, false]);
  assert.deepEqual(r.hidden, [1]);
  assert.equal(r.plusFits, false);
});

test("prefijo estricto: un chip que no cabe corta la fila (los siguientes no entran, segun spec)", () => {
  const r = fitChips([20, 50, 10, 10], 60, -1, 5, 0);
  assert.deepEqual(r.visible, [true, false, false, false]);
  assert.deepEqual(r.hidden, [1, 2, 3]);
});

test("selectedIdx fuera de rango se trata como -1", () => {
  const r = fitChips([30, 30], 60, 5, 5, 0);
  assert.deepEqual(r.visible, [true, false]);
  assert.deepEqual(r.hidden, [1]);
});
