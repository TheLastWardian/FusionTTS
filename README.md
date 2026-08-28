# FusionTTS

Chat multi-persona con TTS OmniVoice bajo demanda. "Recreación" de TalkWithMe con el corazón TTS de F5-TTS: modular, async, cero VRAM en idle.

## Estado

- [x] Plan de diseño (`FUSIONTTS_PLAN.md`)
- [x] Plan de implementación (`IMPLEMENTATION_PLAN.md`)
- [x] T0: git + esqueleto
- [ ] T1–T20: ver `IMPLEMENTATION_PLAN.md`

## Requisitos

### Por PC

- Windows 10/11
- Python 3.11.x en PATH (los venvs son 3.11.9)
- GPU NVIDIA con CUDA para TTS/ASR (CPU funciona, lento)
- Disco: app venv ≈ 2-3 GB + OmniVoice ≈ 5.2 GB + modelos ≈ 2 GB
- LLM OpenAI-compatible en `:8080` (ejemplo: `llama-server -m <model.gguf> -c 4096 --port 8080`)

### Qué se copia (portabilidad)

- `FusionTTS/` (repo completo: app, personas, rooms, settings)
- `OmniVoice/` como carpeta sibling (venv + paquete `omnivoice`)
- Caché HuggingFace (`%USERPROFILE%\.cache\huggingface`: `k2-fsa/OmniVoice` + `Systran/faster-whisper-medium`) o re-descarga con internet

### Qué necesita internet

- Solo `setup.bat` (pip) y el primer uso del modelo ASR (si no se pre-descargó)
- En runtime: nada (offline-first; el TTS server corre con `HF_HUB_OFFLINE=1`)

### Uso

1. `setup.bat` (primera vez; muestra un manifiesto y pide confirmación antes de tocar nada)
2. Iniciar el LLM en `:8080`
3. `start.bat`
4. Navegador en `http://localhost:8000`
