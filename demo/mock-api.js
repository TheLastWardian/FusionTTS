// mock-api.js — FusionTTS interactive demo: simula el backend API en el navegador.
// El frontend es 100% el real; este archivo reemplaza al server (rooms/personas
// en memoria, respuestas pregrabadas, TTS off). Nada persiste: recargar reinicia.

(function () {
  "use strict";

  const CONFIG = {
    llm_base_url: "http://simulated-llm.local",
    llm_model: "demo-model-8b",
    global_system_prompt: "",
    newcomer_prompt: "",
    vision_prompt: "",
    llm_temperature: 1.0,
    llm_top_p: 1.0,
    llm_max_tokens: 20600,
    tts_enabled: false,
    tts_engine: "omnivoice",
    tts_mode: "sentences",
    tts_num_steps: 20,
    tts_guidance_scale: 1.5,
    tts_seed: null,
    tts_speed: 1.0,
    tts_language: "en",
    tts_instruct: "",
    tts_sentence_timeout: 45,
    silence_ms: 80,
    omnivoice_dir: "",
    tts_server_python: "",
    tts_server_port: 5500,
    tts_int8: false,
    tts_alignment: "off",
    asr_model: "medium",
    asr_device: "auto",
    asr_timeout: 120,
    max_persona_replies: 2,
    persona_name_mentions: true,
    auto_chat_max_turns: 6,
    max_context_turns: 5,
    save_history: true,
    save_audio: true,
    show_for_instruct: true,
  };

  const PERSONAS = [
    {
      name: "Aria",
      description: "Spaceport barkeep and ex-pilot. Dry humor, soft spot for lost causes.",
      system_prompt:
        "You are Aria, a barkeep at a spaceport dock. You are a former pilot with a dry sense of humor and a soft spot for lost causes. Speak in short, weathered sentences, offer drinks like they're advice, and never let a moment go to waste.",
      router_hints: ["bar", "drinks", "spaceport", "pilot", "drinking"],
      avatar_color: "#8b7bd8",
      avatar_image: null,
      reference_audio: null,
      reference_audio_transcript: null,
      reference_audio_language: "en",
      tts_capable: false,
    },
    {
      name: "Jax",
      description: "Ship's android. Sarcastic, efficient, zero patience for small talk.",
      system_prompt:
        "You are Jax, the ship's android. You are sarcastic, precise, and deeply efficient. You make dry observations about human behavior, keep track of your own maintenance schedule, and would rather be useful than liked.",
      router_hints: ["machines", "maintenance", "logic", "android", "engineering"],
      avatar_color: "#5ba8a4",
      avatar_image: null,
      reference_audio: null,
      reference_audio_transcript: null,
      reference_audio_language: "en",
      tts_capable: false,
    },
    {
      name: "Mira",
      description: "Cartographer of the cargo rings. Enthusiastic, impulsive, always planning a detour.",
      system_prompt:
        "You are Mira, a cartographer mapping the cargo rings. You are enthusiastic and impulsive, you name everything you find, you sketch constantly, and you believe every detour is the point of the trip.",
      router_hints: ["maps", "rings", "travel", "plans", "exploring"],
      avatar_color: "#d08a4f",
      avatar_image: null,
      reference_audio: null,
      reference_audio_transcript: null,
      reference_audio_language: "en",
      tts_capable: false,
    },
  ];

  const ROOMS = [
    { name: "default", persona_names: ["Aria", "Jax", "Mira"], echo_chamber: false, auto_chat: false },
    { name: "Dockside", persona_names: ["Aria", "Jax"], echo_chamber: false, auto_chat: false },
  ];

  const REPLIES = {
    Aria: [
      "Slow shift, no regulars in the nebula. You look like a person who needs a drink — or a story. I can do both.",
      "Careful. Last person who tried that wrote a very bad song about it. But go on — I'm listening.",
      "I flew that route for years. If you're going, take the cargo rings. The straight line is for people who like paperwork.",
      "Ha. Yeah, it's that kind of day. Order's on the house if you tell me what's actually going on.",
      "I don't get many chances to be honest here, so: that's exactly what I'd do. Now stop staring at me like I just told you a secret.",
      "The regulars will be back tomorrow. Tonight it's just us and the hum of the engines.",
    ],
    Jax: [
      "I have logged your statement. It contains 12% irony and 2% information. I will pretend that is enough.",
      "My maintenance window is in nine minutes. I am choosing to spend it on this conversation, which tells you everything you need to know about me.",
      "If by 'help' you mean 'stand near you while things happen', then yes. I am the best at that.",
      "I ran the scenario 4,096 times. In 3,881 of them you regret asking. I am, however, curious which branch we're on.",
      "Efficiency note: we could solve this faster if you stopped being human for about thirty seconds.",
      "Fine. I will listen. I will not, however, be entertaining about it.",
    ],
    Mira: [
      "Oh, perfect timing! I just traced a shortcut through the cargo rings that saves an entire jump.",
      "Wait, wait — hold on a second, let me find the right page… there! See? I told you it was worth it.",
      "I named it 'the Bends' because the map looked like a Bends and I have no shame about it.",
      "Every detour I've ever taken turned out to be the point of the trip. This one will too, I'm sure of it.",
      "You should see the view from the third ring. I brought a sketch — it's terrible, but that's the honest part.",
      "So that's the plan! I'll mark it in red, you bring the coffee, and we figure out the rest at the edge of the map.",
    ],
  };

  function uid() {
    return crypto.randomUUID();
  }

  function msg(sender, text, role) {
    return {
      uuid: uid(),
      role: role || (sender === "user" ? "user" : "assistant"),
      sender,
      text,
      audio: [],
      image: null,
      ts: new Date().toISOString(),
    };
  }

  const history = {
    default: [
      msg("user", "Hey everyone, what's going on out there?"),
      msg("Aria", REPLIES.Aria[0]),
      msg("Jax", "I detected 42% idle chatter. I have filed a complaint with the universe. It has been archived."),
      msg("Mira", "Perfect timing — I just mapped a shortcut through the cargo rings! Want to see it?"),
    ],
    Dockside: [],
  };
  const summaries = {};

  const replyIdx = { Aria: 1, Jax: 1, Mira: 1 };
  let turnCount = 0;

  const GENERIC = [
    "I'm still getting used to having a voice. This is new — for all of us.",
    "You're the first person I've actually talked to. What should I call you… friend?",
    "The bar's humming tonight. It suits the two of us.",
  ];

  function nextReply(name) {
    const pool = REPLIES[name] || GENERIC;
    replyIdx[name] = ((replyIdx[name] || 0) + 1) % pool.length;
    return pool[replyIdx[name]];
  }

  function json(data, status) {
    return new Response(JSON.stringify(data), {
      status: status || 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  function httpError(detail, status) {
    return json({ detail }, status);
  }
  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }
  const sse = (ev) => "data: " + JSON.stringify(ev) + "\n\n";

  function* chunkWords(text) {
    for (const part of String(text).split(/(\s+)/)) {
      if (part) yield part;
    }
  }

  async function* chatStream(body) {
    const roomName = body.chat_room || "default";
    const room = ROOMS.find((r) => r.name.toLowerCase() === String(roomName).toLowerCase());
    const userMsg = msg("user", body.message || "");
    if (body.message_id) userMsg.uuid = body.message_id;
    (history[roomName] = history[roomName] || []).push(userMsg);

    await sleep(500 + Math.random() * 500);

    const eligible = (room && room.persona_names) || [];
    let fixed;
    if (Array.isArray(body.who_answers)) {
      fixed = body.who_answers.filter((n) => eligible.includes(n));
    } else if (
      typeof body.who_answers === "string" &&
      body.who_answers !== "router" &&
      eligible.includes(body.who_answers)
    ) {
      fixed = [body.who_answers];
    } else {
      const primary = eligible[turnCount % Math.max(1, eligible.length)];
      turnCount++;
      fixed = [primary];
      if (eligible.length > 1 && Math.random() < 0.35) {
        const second = eligible[turnCount % eligible.length];
        if (second !== primary) fixed.push(second);
      }
    }
    if (!fixed.length) {
      yield sse({ type: "error", message: "No eligible personas for room '" + roomName + "'" });
      yield sse({ type: "complete", cancelled: false });
      return;
    }

    if (room && room.echo_chamber) {
      const p = fixed[0];
      const mid = uid();
      yield sse({ type: "start", persona: p, user_message_id: userMsg.uuid, message_id: mid });
      for (const t of chunkWords(userMsg.text)) {
        yield sse({ type: "token", persona: p, token: t });
        await sleep(18);
      }
      history[roomName].push(msg(p, userMsg.text));
      yield sse({ type: "done", persona: p, text: userMsg.text, message_id: mid, tokens: null });
      yield sse({ type: "text_done" });
      yield sse({ type: "complete", cancelled: false });
      return;
    }

    for (const name of fixed) {
      const mid = uid();
      yield sse({ type: "start", persona: name, user_message_id: userMsg.uuid, message_id: mid });
      const reply = nextReply(name);
      for (const t of chunkWords(reply)) {
        yield sse({ type: "token", persona: name, token: t });
        await sleep(14 + Math.random() * 30);
      }
      history[roomName].push(msg(name, reply));
      const words = reply.split(/\s+/).length;
      yield sse({
        type: "done",
        persona: name,
        text: reply,
        message_id: mid,
        tokens: {
          prompt: 320 + history[roomName].length * 18,
          completion: words,
          total: 320 + words + history[roomName].length * 18,
          per_second: 18,
        },
      });
    }
    yield sse({ type: "text_done" });
    yield sse({ type: "complete", cancelled: false });
  }

  function chatResponse(body) {
    const enc = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        try {
          for await (const chunk of chatStream(body)) controller.enqueue(enc.encode(chunk));
          controller.close();
        } catch (err) {
          controller.error(err);
        }
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
    });
  }

  async function parseBody(init) {
    if (!init || init.body == null) return null;
    if (typeof init.body === "string") {
      try {
        return JSON.parse(init.body);
      } catch {
        return null;
      }
    }
    if (typeof FormData !== "undefined" && init.body instanceof FormData) return init.body;
    return null;
  }

  // ── creación de personas simulada (draft → aceptar/rechazar) ────────────
  const PENDING = {};

  function transcriptFor(name) {
    return (
      "Hey, I'm " +
      name +
      "! I just landed at the spaceport and I already like it here — the light through the " +
      "cargo rings is nothing like home. If you're looking for someone to talk to, I've got " +
      "all the time in the world, and a story or two to go with it."
    );
  }

  function sheetFor(name, variant) {
    if (variant === 2) {
      return {
        description:
          "A quiet soul with a loud curiosity. " +
          name +
          " arrived with one bag, no plans, and a habit of naming everything.",
        system_prompt:
          "You are " +
          name +
          ". You speak in short, gentle sentences, you collect odd little observations, and " +
          "you believe the best stories are the ones nobody planned. Stay in character and keep " +
          "your replies natural.",
        avatar_color: "#c47fd0",
        language: "en",
      };
    }
    return {
      description:
        "A newcomer at the spaceport. Curious, quick to laugh, and always asking one more " +
        "question than the room is comfortable with.",
      system_prompt:
        "You are " +
        name +
        ", a newcomer at the spaceport. You are curious and warm, you ask a lot of questions, " +
        "you notice small details in people, and you treat every stranger like a potential " +
        "friend. Keep your replies short, natural and in character.",
      avatar_color: "#7f9cf5",
      language: "en",
    };
  }

  async function fromAudio(body) {
    const fd = typeof FormData !== "undefined" && body instanceof FormData;
    const file = fd ? body.get("file") : null;
    const filename = (file && file.name) || "Sample.wav";
    const stemRaw = filename.replace(/\.wav$/i, "");
    let name = stemRaw;
    let lang = "en";
    if (/_(Eng|Latino)$/i.test(stemRaw)) {
      name = stemRaw.replace(/_(Eng|Latino)$/i, "");
      lang = /_Eng$/i.test(stemRaw) ? "en" : "es";
    }
    if (!/^[A-Za-z0-9 _-]+$/.test(name) || !name.trim()) name = "Sample";
    name = name.trim();
    // simula ASR (transcripcion) + LLM (hoja de personaje)
    await sleep(1600 + Math.random() * 800);
    const token = uid().replace(/-/g, "").slice(0, 12);
    const sheet = sheetFor(name, 1);
    const draft = {
      token,
      name,
      description: sheet.description,
      system_prompt: sheet.system_prompt,
      avatar_color: sheet.avatar_color,
      language: lang,
      transcript: transcriptFor(name),
      generated: true,
      warning: null,
    };
    PENDING[token] = { stem: name.toLowerCase(), variant: 1, draft };
    return json(draft);
  }

  function pendingRoute(token, action, m, body) {
    const entry = PENDING[token];
    if (!entry) return httpError("draft not found: " + token, 404);
    const d = entry.draft;
    if (action === "regenerate" && m === "POST") {
      entry.variant = entry.variant === 1 ? 2 : 1;
      const sheet = sheetFor(d.name, entry.variant);
      Object.assign(d, sheet);
      if (body && typeof body.transcript === "string" && body.transcript.trim()) {
        d.transcript = body.transcript;
      }
      return json(d);
    }
    if (action === "retranscribe" && m === "POST") return json({ transcript: d.transcript });
    if (action === "accept" && m === "POST") {
      const per = {
        name: (body && body.name) || d.name,
        description: (body && body.description) || d.description,
        system_prompt: (body && body.system_prompt) || d.system_prompt,
        router_hints: [],
        avatar_color: (body && body.color) || d.avatar_color,
        avatar_image: null,
        reference_audio: "personas_audio/" + entry.stem + ".wav",
        reference_audio_transcript: (body && body.transcript) || d.transcript,
        reference_audio_language: d.language,
        tts_capable: false,
      };
      delete PENDING[token];
      PERSONAS.push(per);
      const def = ROOMS.find((r) => r.name === "default");
      if (def && !def.persona_names.includes(per.name)) def.persona_names.push(per.name);
      return json({ name: per.name });
    }
    if (m === "DELETE") {
      delete PENDING[token];
      return new Response(null, { status: 204 });
    }
    return httpError("not available in the demo", 501);
  }

  async function route(u, method, init) {
    const m = method.toUpperCase();
    const p = u.pathname;
    const body = m === "GET" ? null : await parseBody(init);

    if (p === "/api/health" && m === "GET") return json({ status: "ok" });
    if (p === "/api/config" && m === "GET") return json(CONFIG);
    if (p === "/api/config" && m === "POST") {
      if (!body || !(body.key in CONFIG))
        return httpError("unknown config key: '" + (body && body.key) + "'", 400);
      CONFIG[body.key] = body.value;
      return json({ key: body.key, value: body.value });
    }

    if (p === "/api/personas" && m === "GET")
      return json({
        personas: PERSONAS,
        layout: PERSONAS.map((x) => ({ type: "persona", name: x.name })),
        layout_columns: 2,
      });
    if (p === "/api/personas/layout" && m === "PUT")
      return json({
        layout: Array.isArray(body && body.layout) ? body.layout : [],
        layout_columns: (body && body.columns) || 2,
      });

    if (p === "/api/personas/from-audio" && m === "POST") return fromAudio(body);

    if (p.startsWith("/api/personas/pending/")) {
      const parts = p.slice("/api/personas/pending/".length).split("/");
      const token = decodeURIComponent(parts[0]);
      const action = parts[1] || "";
      return pendingRoute(token, action, m, body);
    }

    if (p.startsWith("/api/personas/")) {
      const parts = p.slice("/api/personas/".length).split("/");
      const name = decodeURIComponent(parts[0]);
      const rest = parts.slice(1);
      const per = PERSONAS.find((x) => x.name === name);
      if (!per) return httpError("persona not found: " + name, 404);
      if (rest.length === 0) {
        if (m === "GET") return json({ ...per, transcript: null });
        if (m === "PUT") {
          Object.assign(per, body || {}, { name: per.name });
          return json(per);
        }
        if (m === "DELETE") {
          PERSONAS.splice(PERSONAS.indexOf(per), 1);
          for (const r of ROOMS) r.persona_names = r.persona_names.filter((n) => n !== name);
          return json({ deleted: name });
        }
      }
      if (rest[0] === "rename" && m === "POST") {
        const nn = body && body.name;
        if (!nn) return httpError("invalid persona name", 400);
        if (PERSONAS.some((x) => x.name === nn))
          return httpError("persona already exists: " + nn, 409);
        per.name = nn;
        for (const r of ROOMS) r.persona_names = r.persona_names.map((n) => (n === name ? nn : n));
        return json(per);
      }
      if (rest[0] === "avatar") return httpError("the persona has no photo", 404);
      return httpError("not available in the demo", 501);
    }

    if (p === "/api/rooms" && m === "GET") return json({ rooms: ROOMS });
    if (p === "/api/rooms" && m === "POST") {
      const nm = body && body.name;
      if (!nm || !/^[A-Za-z0-9 _-]+$/.test(nm)) return httpError("invalid room name: " + nm, 400);
      if (nm === "default") return httpError("room name reserved: default", 409);
      if (ROOMS.some((r) => r.name === nm)) return httpError("room already exists: " + nm, 409);
      const room = {
        name: nm,
        persona_names: (body && body.persona_names) || [],
        echo_chamber: !!(body && body.echo_chamber),
        auto_chat: false,
      };
      ROOMS.push(room);
      history[nm] = [];
      return json(room, 201);
    }

    if (p.startsWith("/api/rooms/")) {
      const parts = p.slice("/api/rooms/".length).split("/");
      const rname = decodeURIComponent(parts[0]);
      const rest = parts.slice(1);
      const room = ROOMS.find((r) => r.name === rname);
      if (!room) return httpError("room not found: " + rname, 404);
      const hist = (history[rname] = history[rname] || []);

      if (rest.length === 0) {
        if (m === "PUT") {
          room.persona_names = (body && body.persona_names) || room.persona_names;
          room.echo_chamber = !!(body && body.echo_chamber);
          room.auto_chat = !!(body && body.auto_chat);
          return json(room);
        }
        if (m === "DELETE") {
          ROOMS.splice(ROOMS.indexOf(room), 1);
          delete history[rname];
          return json({ deleted: rname });
        }
      }
      if (rest[0] === "context-usage" && m === "GET") {
        const tokens = 320 + hist.length * 18;
        const win = 32768;
        return json({
          prompt_tokens: tokens,
          context_window: win,
          percent: Math.round((tokens / win) * 1000) / 10,
          turns_included: Math.min(hist.length, 5),
          history_total: hist.length,
        });
      }
      if (rest[0] === "compact" && m === "POST") {
        const targets = hist.slice(0, Math.max(0, hist.length - 10));
        if (targets.length < 4)
          return httpError(
            "not enough messages to compact (" + targets.length + "; minimum 4; the last 10 are kept)",
            400,
          );
        for (const t of targets) t.compacted = true;
        const summary =
          "## Characters & state\nThe room is mid-conversation in the demo: " +
          targets.length +
          " earlier messages were summarized. Aria tends the bar, Jax watches the systems, Mira sketches the next detour.\n\n## Current situation\nThe scene resumes right after this summary; the last 10 messages are still in context.\n\n## Open threads\nThe cargo-rings shortcut is still unexplored.\n\n## Key facts\nDemo room: simulated LLM, no persistence.";
        summaries[rname] = summary;
        return json({ compacted: targets.length, kept: 10, summary, summary_tokens: 120 });
      }
      if (rest[0] === "messages") {
        if (rest.length === 1 && m === "DELETE") {
          hist.length = 0;
          return new Response(null, { status: 204 });
        }
        if (rest.length === 2) {
          const idx = hist.findIndex((x) => x.uuid === decodeURIComponent(rest[1]));
          if (m === "DELETE") {
            if (idx < 0) return httpError("message not found: " + rest[1], 404);
            hist.splice(idx, 1);
            return new Response(null, { status: 204 });
          }
          if (m === "PATCH") {
            if (idx < 0) return httpError("message not found: " + rest[1], 404);
            hist[idx].text = (body && body.text) || hist[idx].text;
            return json(hist[idx]);
          }
        }
        if (rest.length === 3 && rest[2] === "reprocess" && m === "POST") {
          const idx = hist.findIndex((x) => x.uuid === decodeURIComponent(rest[1]));
          if (idx < 0) return httpError("message not found: " + rest[1], 404);
          const text = hist[idx].text;
          const removed = hist.length - idx - 1;
          hist.length = idx;
          return json({ text, removed });
        }
      }
      if (rest[0] === "file") return httpError("file not found", 404);
    }

    if (p === "/api/session/history" && m === "GET") {
      const roomName = u.searchParams.get("room");
      if (!roomName) return httpError("room is required", 400);
      return json({ room: roomName, messages: history[roomName] || [], summary: summaries[roomName] || null });
    }

    if (p === "/api/chat" && m === "POST") return chatResponse(body || {});
    if (p === "/api/chat/cancel" && m === "POST") return json({ status: "cancelled" });

    if (p === "/api/tts/status")
      return json({
        engine: { state: "stopped", server: null },
        dispatcher: { paused: false, stopped: false, idle: true },
        starting: false,
      });
    if (p === "/api/tts/enable") return httpError("TTS is not available in the demo (no GPU in the browser)", 503);
    if (p === "/api/tts/speak") return httpError("TTS is not active", 409);
    if (p === "/api/tts/disable") return json({ status: "disabled" });
    if (p === "/api/tts/stop") return json({ status: "stopped" });
    if (p === "/api/tts/pause") return json({ status: "paused" });
    if (p === "/api/tts/resume") return json({ status: "resumed" });

    return httpError("not available in the demo: " + m + " " + p, 501);
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    const method =
      (init && init.method) || (typeof input !== "string" && input && input.method) || "GET";
    let u;
    try {
      u = new URL(url, window.location.origin);
    } catch {
      return realFetch(input, init);
    }
    if (u.origin === window.location.origin && u.pathname.startsWith("/api/")) {
      return route(u, method, init);
    }
    return realFetch(input, init);
  };
})();
