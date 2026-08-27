// audio-queue.js — cola de playback serial (idle/playing/paused) con gap configurable y timers inyectables, sin DOM, dual export navegador/Node.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.FTTS = Object.assign({}, root.FTTS, api);
})(typeof self !== "undefined" ? self : globalThis, function () {
  class AudioQueue {
    constructor(opts = {}) {
      this.gap = opts.gap !== undefined ? opts.gap : 80;
      this.onPlay = opts.onPlay || (() => {});
      this.onDrain = opts.onDrain || (() => {});
      this._setTimeout = opts.setTimeout || setTimeout;
      this._clearTimeout = opts.clearTimeout || clearTimeout;
      this.state = "idle";
      this.pending = [];
      this.current = null;
      this._waiting = false;
      this._timer = null;
    }

    get pendingCount() {
      return this.pending.length;
    }

    enqueue(item) {
      this.pending.push(item);
      if (this.state === "idle") this._startNext();
    }

    _startNext() {
      this.current = this.pending.shift();
      this.state = "playing";
      this.onPlay(this.current);
    }

    _armGap() {
      this._waiting = true;
      this._timer = this._setTimeout(() => {
        this._timer = null;
        this._waiting = false;
        this._startNext();
      }, this.gap);
    }

    currentEnded() {
      if (this.state !== "playing") return;
      this.current = null;
      if (this.pending.length > 0) {
        this._armGap();
      } else {
        this.state = "idle";
        this.onDrain();
      }
    }

    pause() {
      if (this.state !== "playing") return;
      this.state = "paused";
      if (this._waiting) {
        this._clearTimeout(this._timer);
        this._timer = null;
      }
    }

    resume() {
      if (this.state !== "paused") return;
      this.state = "playing";
      if (this.current === null) this._armGap();
    }

    stop() {
      if (this._timer !== null) {
        this._clearTimeout(this._timer);
        this._timer = null;
      }
      this._waiting = false;
      this.pending = [];
      this.current = null;
      this.state = "idle";
      this.onDrain();
    }
  }

  return { AudioQueue };
});
