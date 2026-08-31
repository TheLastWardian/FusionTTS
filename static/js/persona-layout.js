// persona-layout.js — funciones puras del layout de carpetas (sin DOM).
// Invariantes (spec v2): cada persona aparece exactamente una vez (tope o en
// una sola carpeta), sin carpetas duplicadas, "For Instruct" nunca aparece.
// Semantica de indices (spec): "posicion de insercion" 0..len en la lista que
// incluye el elemento movido; las funciones ajustan internamente si el
// elemento estaba antes del destino.

const FOR_INSTRUCT = "For Instruct";

export function normalize(raw, personaNames) {
  const existing = new Set(personaNames);
  existing.delete(FOR_INSTRUCT);
  const out = [];
  const seen = new Set();
  const folders = new Set();
  if (Array.isArray(raw)) {
    for (const entry of raw) {
      if (!entry || typeof entry !== "object") continue;
      if (typeof entry.name !== "string" || !entry.name) continue;
      if (entry.type === "persona") {
        if (existing.has(entry.name) && !seen.has(entry.name)) {
          seen.add(entry.name);
          out.push({ type: "persona", name: entry.name });
        }
      } else if (entry.type === "folder") {
        if (folders.has(entry.name)) continue;
        // seen se actualiza al filtrar: un miembro duplicado DENTRO de la
        // misma carpeta tambien queda descartado (gana la primera)
        const members = [];
        if (Array.isArray(entry.personas)) {
          for (const m of entry.personas) {
            if (typeof m === "string" && existing.has(m) && !seen.has(m)) {
              seen.add(m);
              members.push(m);
            }
          }
        }
        folders.add(entry.name);
        out.push({ type: "folder", name: entry.name, personas: members });
      }
    }
  }
  for (const name of personaNames) {
    if (existing.has(name) && !seen.has(name)) {
      seen.add(name);
      out.push({ type: "persona", name });
    }
  }
  return out;
}

export function addFolder(layout, name, index = 0) {
  const out = layout.slice();
  const i = Math.max(0, Math.min(index, out.length));
  out.splice(i, 0, { type: "folder", name, personas: [] });
  return out;
}

export function renameFolder(layout, oldName, newName) {
  if (typeof newName !== "string" || !newName) throw new Error("nombre de carpeta vacio");
  if (layout.some((e) => e.type === "folder" && e.name === newName && e.name !== oldName)) {
    throw new Error("carpeta duplicada: " + newName);
  }
  return layout.map((e) =>
    e.type === "folder" && e.name === oldName ? { ...e, name: newName } : e
  );
}

export function removeFolder(layout, name) {
  // los miembros pasan a personas sueltas, en la posicion de la carpeta, en orden
  const out = [];
  for (const entry of layout) {
    if (entry.type === "folder" && entry.name === name) {
      for (const m of entry.personas) out.push({ type: "persona", name: m });
    } else {
      out.push(entry);
    }
  }
  return out;
}

export function moveEntry(layout, fromIndex, toIndex) {
  const out = layout.slice();
  const moved = out.splice(fromIndex, 1)[0];
  let i = toIndex;
  if (i > fromIndex) i -= 1;
  i = Math.max(0, Math.min(i, out.length));
  out.splice(i, 0, moved);
  return out;
}

export function movePersona(layout, name, target) {
  // quita a `name` de donde este (tope u otra carpeta) y la inserta en target:
  //   {index}          -> tope, posicion de insercion (incluye al movido)
  //   {folder, index?} -> dentro de la carpeta; index entre miembros (incluye
  //                       al movido si estaba en esa carpeta); sin index: al final
  let oldTop = -1;
  let oldMember = -1;
  const out = [];
  for (let i = 0; i < layout.length; i++) {
    const entry = layout[i];
    if (entry.type === "persona") {
      if (entry.name === name) {
        oldTop = i;
      } else {
        out.push(entry);
      }
    } else if (entry.type === "folder") {
      const wasMember = entry.personas.includes(name);
      const members = entry.personas.filter((m) => m !== name);
      if (wasMember && target.folder === entry.name) oldMember = entry.personas.indexOf(name);
      out.push(wasMember ? { type: "folder", name: entry.name, personas: members } : entry);
    }
  }
  if (target.folder) {
    const idx = out.findIndex((e) => e.type === "folder" && e.name === target.folder);
    if (idx === -1) throw new Error("carpeta inexistente: " + target.folder);
    const members = out[idx].personas.slice();
    let i = target.index == null ? members.length : target.index;
    if (oldMember !== -1 && i > oldMember) i -= 1;
    i = Math.max(0, Math.min(i, members.length));
    members.splice(i, 0, name);
    out[idx] = { type: "folder", name: out[idx].name, personas: members };
    return out;
  }
  let i = target.index;
  if (oldTop !== -1 && i > oldTop) i -= 1;
  i = Math.max(0, Math.min(i, out.length));
  out.splice(i, 0, { type: "persona", name });
  return out;
}

export function reorderMembers(layout, folderName, fromIdx, toIdx) {
  return layout.map((e) => {
    if (e.type !== "folder" || e.name !== folderName) return e;
    const members = e.personas.slice();
    const m = members.splice(fromIdx, 1)[0];
    members.splice(toIdx, 0, m);
    return { type: "folder", name: folderName, personas: members };
  });
}
