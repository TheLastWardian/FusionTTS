// test_audio_queue.js — tests de la cola de playback serial AudioQueue (static/js/audio-queue.js), node --test con reloj falso.
const test = require("node:test");
const assert = require("node:assert/strict");
const { AudioQueue } = require("../static/js/audio-queue.js");

function makeClock() {
  let now = 0;
  const timers = [];
  return {
    get now() {
      return now;
    },
    setTimeout(fn, ms) {
      timers.push({ at: now + ms, fn, cancelled: false });
      return timers.length;
    },
    clearTimeout(id) {
      const t = timers[id - 1];
      if (t) t.cancelled = true;
    },
    advance(ms) {
      const until = now + ms;
      const due = timers
        .filter((t) => !t.cancelled && t.at <= until)
        .sort((a, b) => a.at - b.at);
      for (const t of due) {
        t.cancelled = true;
        now = t.at;
        t.fn();
      }
      now = until;
    },
  };
}

function makeQueue(gap = 80) {
  const clock = makeClock();
  const played = [];
  let drains = 0;
  const q = new AudioQueue({
    gap,
    onPlay(item) {
      played.push({ item, t: clock.now });
    },
    onDrain() {
      drains += 1;
    },
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
  });
  return { q, clock, played, drains: () => drains };
}

test("enqueue en idle → onPlay inmediato con el item, state playing", () => {
  const { q, played } = makeQueue();
  q.enqueue("a");
  assert.equal(q.state, "playing");
  assert.equal(q.current, "a");
  assert.equal(q.pendingCount, 0);
  assert.equal(played.length, 1);
  assert.equal(played[0].item, "a");
});

test("2do enqueue queda en pending; currentEnded → onPlay del 2do exactamente al llegar el gap", () => {
  const { q, clock, played } = makeQueue();
  q.enqueue("a");
  q.enqueue("b");
  assert.equal(q.pendingCount, 1);
  q.currentEnded();
  assert.equal(played.length, 1);
  clock.advance(79);
  assert.equal(played.length, 1);
  clock.advance(1);
  assert.equal(played.length, 2);
  assert.equal(played[1].item, "b");
  assert.equal(played[1].t, 80);
});

test("currentEnded sin pending → state idle + onDrain una vez", () => {
  const { q, played, drains } = makeQueue();
  q.enqueue("a");
  q.currentEnded();
  assert.equal(q.state, "idle");
  assert.equal(q.current, null);
  assert.equal(played.length, 1);
  assert.equal(drains(), 1);
});

test("pause con current sonando: currentEnded es no-op; resume sin nuevo onPlay", () => {
  const { q, played } = makeQueue();
  q.enqueue("a");
  q.pause();
  assert.equal(q.state, "paused");
  q.currentEnded();
  assert.equal(played.length, 1);
  q.resume();
  assert.equal(q.state, "playing");
  assert.equal(q.current, "a");
  assert.equal(played.length, 1);
});

test("pause durante el gap cancela el timer; resume → onPlay recién pasado el gap completo", () => {
  const { q, clock, played } = makeQueue();
  q.enqueue("a");
  q.enqueue("b");
  q.currentEnded();
  q.pause();
  assert.equal(q.state, "paused");
  clock.advance(100);
  assert.equal(played.length, 1);
  q.resume();
  assert.equal(q.state, "playing");
  clock.advance(79);
  assert.equal(played.length, 1);
  clock.advance(1);
  assert.equal(played.length, 2);
  assert.equal(played[1].item, "b");
  assert.equal(played[1].t, 180);
});

test("enqueue mientras está paused sigue paused; resume sin current reproduce el pendiente después del gap", () => {
  const { q, clock, played } = makeQueue();
  q.enqueue("a");
  q.enqueue("b");
  q.currentEnded();
  q.pause();
  q.enqueue("c");
  assert.equal(q.state, "paused");
  assert.equal(q.pendingCount, 2);
  assert.equal(played.length, 1);
  q.resume();
  assert.equal(q.state, "playing");
  clock.advance(79);
  assert.equal(played.length, 1);
  clock.advance(1);
  assert.equal(played.length, 2);
  assert.equal(played[1].item, "b");
  assert.equal(q.pendingCount, 1);
});

test("stop con pending y current → idle, pendingCount 0, onDrain; currentEnded posterior no-op", () => {
  const { q, played, drains } = makeQueue();
  q.enqueue("a");
  q.enqueue("b");
  q.stop();
  assert.equal(q.state, "idle");
  assert.equal(q.pendingCount, 0);
  assert.equal(q.current, null);
  assert.equal(drains(), 1);
  q.currentEnded();
  assert.equal(drains(), 1);
  assert.equal(played.length, 1);
});

test("pause en idle es no-op y no afecta un enqueue posterior", () => {
  const { q, played } = makeQueue();
  q.pause();
  assert.equal(q.state, "idle");
  q.enqueue("a");
  assert.equal(q.state, "playing");
  assert.equal(played.length, 1);
  assert.equal(played[0].item, "a");
});

test("cadena de 3: onPlay en orden 1→2→3, cada uno separado exactamente por gap ms", () => {
  const { q, clock, played } = makeQueue();
  q.enqueue("1");
  q.enqueue("2");
  q.enqueue("3");
  q.currentEnded();
  clock.advance(80);
  assert.equal(played.length, 2);
  q.currentEnded();
  clock.advance(79);
  assert.equal(played.length, 2);
  clock.advance(1);
  assert.equal(played.length, 3);
  assert.deepEqual(
    played.map((p) => p.item),
    ["1", "2", "3"],
  );
  assert.deepEqual(
    played.map((p) => p.t),
    [0, 80, 160],
  );
});

test("brand check: _armGap invoca setTimeout con this=global (sin Illegal invocation)", () => {
  // Simula el brand check de Chrome: el setTimeout de window exige this === window.
  const timers = [];
  let now = 0;
  const strictSet = function (fn, ms) {
    if (this !== globalThis) throw new TypeError("Illegal invocation");
    timers.push({ at: now + ms, fn, cancelled: false });
    return timers.length;
  };
  const strictClear = function (id) {
    if (this !== globalThis) throw new TypeError("Illegal invocation");
    timers[id - 1].cancelled = true;
  };
  const played = [];
  const q = new AudioQueue({
    gap: 80,
    onPlay: (item) => played.push(item),
    setTimeout: strictSet,
    clearTimeout: strictClear,
  });
  q.enqueue("a");
  q.enqueue("b");
  q.currentEnded(); // antes del fix tiraba Illegal invocation aqui
  assert.equal(played.length, 1);
  assert.equal(q.pendingCount, 1);
  for (const t of timers) {
    if (!t.cancelled && t.at <= 80) {
      t.cancelled = true;
      t.fn();
    }
  }
  assert.deepEqual(played, ["a", "b"]);
});

test("gap custom (25 ms): el advance mide 25 ms", () => {
  const { q, clock, played } = makeQueue(25);
  q.enqueue("a");
  q.enqueue("b");
  q.currentEnded();
  clock.advance(24);
  assert.equal(played.length, 1);
  clock.advance(1);
  assert.equal(played.length, 2);
  assert.equal(played[1].item, "b");
  assert.equal(played[1].t, 25);
});
