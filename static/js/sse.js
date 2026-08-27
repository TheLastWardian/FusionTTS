// sse.js — parser SSE puro (buffer por bloques \n\n, líneas data:), sin DOM, dual export navegador/Node.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.FTTS = Object.assign({}, root.FTTS, api);
})(typeof self !== "undefined" ? self : globalThis, function () {
  class SSEParser {
    constructor(onEvent) {
      this._onEvent = onEvent;
      this._buf = "";
    }

    feed(chunk) {
      this._buf += chunk;
      let idx;
      while ((idx = this._buf.indexOf("\n\n")) !== -1) {
        const block = this._buf.slice(0, idx);
        this._buf = this._buf.slice(idx + 2);
        this._handleBlock(block);
      }
    }

    _handleBlock(block) {
      const dataLines = [];
      for (const line of block.split("\n")) {
        if (!line.startsWith("data:")) continue;
        dataLines.push(line.slice(5).replace(/^ /, ""));
      }
      if (dataLines.length === 0) return;
      try {
        this._onEvent(JSON.parse(dataLines.join("\n")));
      } catch (err) {
        console.warn("sse.js: JSON inválido, se salta el evento:", err.message, dataLines.join("\n"));
      }
    }
  }

  return { SSEParser };
});
