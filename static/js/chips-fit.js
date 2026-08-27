// chips-fit.js — fit puro de la fila who-chips (anchos, gap, chip +N), sin DOM, dual export navegador/Node.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.FTTS = Object.assign({}, root.FTTS, api);
})(typeof self !== "undefined" ? self : globalThis, function () {
  function fitChips(widths, available, selectedIdx, gap, plusWidth) {
    const n = widths.length;
    const visible = new Array(n).fill(false);
    const hidden = [];
    if (n === 0) return { visible, hidden, plusFits: true };

    let sel = Number.isInteger(selectedIdx) ? selectedIdx : -1;
    if (sel < 0 || sel >= n) sel = -1;
    if (sel >= 0) visible[sel] = true;
    const reserved = sel >= 0 ? widths[sel] : 0;

    let used = 0;
    for (let i = 0; i < n; i++) {
      if (i === sel) continue;
      if (used + gap + widths[i] <= available - reserved) {
        visible[i] = true;
        used += gap + widths[i];
      }
    }

    let plusFits = true;
    for (let i = 0; i < n; i++) if (!visible[i]) hidden.push(i);
    if (hidden.length > 0) {
      let i = n - 1;
      while (reserved + used + gap + plusWidth > available && i >= 0) {
        if (i !== sel && visible[i]) {
          visible[i] = false;
          used -= gap + widths[i];
        }
        i--;
      }
      plusFits = reserved + used + gap + plusWidth <= available;
      hidden.length = 0;
      for (let i = 0; i < n; i++) if (!visible[i]) hidden.push(i);
    }
    return { visible, hidden, plusFits };
  }

  return { fitChips };
});
