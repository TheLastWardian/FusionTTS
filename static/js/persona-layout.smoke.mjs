import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, "persona-layout.js"), "utf8");
const mod = await import("data:text/javascript," + encodeURIComponent(src));
const {
  normalize, addFolder, renameFolder, removeFolder,
  moveEntry, movePersona, reorderMembers,
} = mod;

const NAMES = ["Jean", "Zhongli", "Barbara", "For Instruct"];

function checkInvariants(layout, names) {
  const seen = [];
  for (const e of layout) {
    if (e.type === "persona") seen.push(e.name);
    else seen.push(...e.personas);
  }
  assert.equal(new Set(seen).size, seen.length, "persona duplicada en el layout");
  for (const n of names.filter((n) => n !== "For Instruct")) {
    assert.ok(seen.includes(n), "falta " + n + " en el layout");
  }
}

// normalize: tecla ausente -> todas sueltas, For Instruct fuera
assert.deepStrictEqual(normalize(null, NAMES), [
  { type: "persona", name: "Jean" },
  { type: "persona", name: "Zhongli" },
  { type: "persona", name: "Barbara" },
]);

// normalize: desconocidos, duplicados, faltantes
assert.deepStrictEqual(normalize([
  { type: "persona", name: "Fantasma" },
  { type: "folder", name: "A", personas: ["Jean", "Nadie", "Jean"] },
], NAMES), [
  { type: "folder", name: "A", personas: ["Jean"] },
  { type: "persona", name: "Zhongli" },
  { type: "persona", name: "Barbara" },
]);

const L = [
  { type: "persona", name: "Jean" },
  { type: "folder", name: "A", personas: ["Zhongli", "Barbara"] },
];

// addFolder al tope: carpeta VACIA (el fix de v1: no absorbe a nadie)
const added = addFolder(L, "Nueva", 0);
assert.deepStrictEqual(added[0], { type: "folder", name: "Nueva", personas: [] });
assert.equal(added.length, L.length + 1);

// renameFolder: ok, no-op mismo nombre, duplicado lanza
assert.deepStrictEqual(renameFolder(L, "A", "B"), [
  { type: "persona", name: "Jean" },
  { type: "folder", name: "B", personas: ["Zhongli", "Barbara"] },
]);
assert.deepStrictEqual(renameFolder(L, "A", "A"), L);
assert.throws(() => renameFolder([...L, { type: "folder", name: "B", personas: [] }], "A", "B"));
assert.throws(() => renameFolder(L, "A", ""));

// removeFolder: miembros a sueltas en la posicion de la carpeta
assert.deepStrictEqual(removeFolder(L, "A"), [
  { type: "persona", name: "Jean" },
  { type: "persona", name: "Zhongli" },
  { type: "persona", name: "Barbara" },
]);

// moveEntry: carpeta al tope; no-op moverse sobre si misma
assert.deepStrictEqual(moveEntry(L, 1, 0), [
  { type: "folder", name: "A", personas: ["Zhongli", "Barbara"] },
  { type: "persona", name: "Jean" },
]);
assert.deepStrictEqual(moveEntry(L, 0, 1), L);

// movePersona: carpeta -> tope (antes del primero)
assert.deepStrictEqual(movePersona(L, "Zhongli", { index: 0 }), [
  { type: "persona", name: "Zhongli" },
  { type: "persona", name: "Jean" },
  { type: "folder", name: "A", personas: ["Barbara"] },
]);

// movePersona: tope -> carpeta (al final y con index)
assert.deepStrictEqual(movePersona(L, "Jean", { folder: "A" }), [
  { type: "folder", name: "A", personas: ["Zhongli", "Barbara", "Jean"] },
]);
assert.deepStrictEqual(movePersona(L, "Jean", { folder: "A", index: 0 }), [
  { type: "folder", name: "A", personas: ["Jean", "Zhongli", "Barbara"] },
]);

// movePersona: dentro de la misma carpeta (reordenar)
assert.deepStrictEqual(movePersona(L, "Zhongli", { folder: "A", index: 2 }), [
  { type: "persona", name: "Jean" },
  { type: "folder", name: "A", personas: ["Barbara", "Zhongli"] },
]);

// reorderMembers: no-op y swap
assert.deepStrictEqual(reorderMembers(L, "A", 0, 0), L);
assert.deepStrictEqual(reorderMembers(L, "A", 1, 0), [
  { type: "persona", name: "Jean" },
  { type: "folder", name: "A", personas: ["Barbara", "Zhongli"] },
]);

// invariantes tras cada mutacion
for (const l of [added, removeFolder(L, "A"), moveEntry(L, 1, 0),
  movePersona(L, "Zhongli", { index: 0 }), movePersona(L, "Jean", { folder: "A" })]) {
  checkInvariants(l, NAMES);
}

console.log("OK: persona-layout");
