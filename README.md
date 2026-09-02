# FusionTTS

Multi-persona voice chat driven by a local LLM. Each persona is a character with its own
system prompt and a cloned voice (zero-shot voice cloning with
[OmniVoice](https://github.com/k2-fsa/OmniVoice), 600+ languages). The TTS server runs
**on demand** (top-bar chip): 0 VRAM while it's off.

## Architecture

```
Browser ──► FusionTTS app (FastAPI, port 8000)
               ├─ LLM: any OpenAI-compatible endpoint (default http://localhost:8080)
               ├─ ASR: faster-whisper (mic input, on demand)
               └─ TTS server (tts-server/server.py, port 5500)
                    └─ OmniVoice model (k2-fsa/OmniVoice)
```

- **App**: this repo. FastAPI backend + web UI (chat, personas, settings).
- **TTS server**: a child process the app spawns when you enable TTS and kills when you
  disable it, so VRAM is freed completely. It runs with the **OmniVoice repo's venv**.
- **OmniVoice**: [k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice), cloned by the
  setup into `..\OmniVoice` (sibling folder).
- **LLM**: you manage it (llama.cpp / llama-server, or any OpenAI-compatible API).

## Requirements

- Windows 10/11
- Python 3.11.x in PATH (python.org → check "Add python.exe to PATH")
- git in PATH (e.g. `winget install --id Git.Git`)
- Internet — only for the setup; runtime is fully offline
- ~13 GB of disk: app venv ~1 GB + OmniVoice venv ~5 GB + models ~4.5 GB
- NVIDIA GPU with CUDA 12.1 recommended; CPU works but is much slower

## Install

```bat
setup.bat
```

Interactive: it prints a manifest of everything it will download (with sizes) and asks
for confirmation. Steps already present on disk are detected and skipped:

1. app venv + `requirements.txt`
2. clone `https://github.com/k2-fsa/OmniVoice.git` → `..\OmniVoice`
3. OmniVoice venv: `torch` cu121 + the `omnivoice` package + TTS server deps
4. TTS model `k2-fsa/OmniVoice` (~3 GB, HuggingFace cache)
5. (optional) pre-download of the ASR model `Systran/faster-whisper-medium` (~1.4 GB)
6. `settings.json`: created from `settings.example.json` if missing, fills
   `omnivoice_dir` / `tts_server_python`, touches nothing else

Already have OmniVoice somewhere else? Clone it wherever you like and set `omnivoice_dir`
in the settings panel (or in `settings.json`).

## LLM

The chat needs an OpenAI-compatible LLM. The default `llm_base_url` is
`http://localhost:8080`:

```bat
llama-server -m C:\llama\models\your-model.gguf --port 8080 -c 4096 -ngl 99
```

## Usage

1. Start your LLM (see above).
2. `start.bat` → open `http://localhost:8000` in your browser.
3. TTS: click the `TTS · off` chip in the top bar → the model loads (1-2 min) →
   `TTS · listo`. Disabling TTS frees the VRAM.
4. Voice input (ASR): use the mic in the chat; if you skipped step 5 of the setup,
   the model downloads on first use.

## Configuration

- `settings.json` (auto-created from `settings.example.json`): LLM endpoint, TTS
  parameters (steps, speed, language, instruct, word-by-word highlighting / karaoke,
  `omnivoice_dir`), ASR model, context/echo-chamber options.
- Settings panel in the UI (gear button): same values, no file editing needed.
- `personas.yaml`: the cast — name, description, system prompt, reference audio for
  voice cloning, avatar — plus the layout folders.
- `personas_audio/`: one `.wav` + transcript `.txt` pair per persona (the cloning
  reference).
- `chatrooms.yaml`: rooms and which personas are in each.

## Offline

After the setup, the runtime needs no internet: the TTS server runs with
`HF_HUB_OFFLINE=1` (models live in `%USERPROFILE%\.cache\huggingface`) and ASR uses the
local cache. Only the LLM calls go out if you point `llm_base_url` at a remote API.

## Tests

```bat
venv\Scripts\python -m pytest
```

## Troubleshooting

- TTS chip stuck on `error` / never becomes ready: open the newest file in
  `logs\tts-server\`. Common causes: missing OmniVoice venv or model, port 5500 busy.
- Moved the OmniVoice repo? Update `omnivoice_dir` in the settings panel.
- Ports 8000 / 8080 / 5500 busy: free them or change `tts_server_port` in
  `settings.json`.
- `start.bat` complains about a missing venv: run `setup.bat` first.
- `setup.bat` stopped on a prerequisite: install the missing tool (Python 3.11 / git)
  and run it again — it skips everything that is already done.

## Credits

This project was built with the assistance of an AI coding agent (Qwen 3.8 27B running
locally via llama.cpp).
