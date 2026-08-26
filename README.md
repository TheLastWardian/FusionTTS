# FusionTTS

Chat multi-persona con TTS OmniVoice bajo demanda. "Recreación" de TalkWithMe con el corazón TTS de F5-TTS: modular, async, cero VRAM en idle.

## Estado

- [x] Plan de diseño (`FUSIONTTS_PLAN.md`)
- [x] Plan de implementación (`IMPLEMENTATION_PLAN.md`)
- [ ] T0: git + esqueleto
- [ ] T1–T20: ver `IMPLEMENTATION_PLAN.md`

## Requisitos (resumen — detalle en el README final, task-19)

- Python 3.11+, git
- GPU NVIDIA con CUDA para TTS (OmniVoice) y ASR (whisper)
- Pesos en caché HuggingFace: `k2-fsa/OmniVoice`, `Systran/faster-whisper-medium` (o internet para bajarlos)
- LLM OpenAI-compatible corriendo (llama.cpp por defecto, puerto 8080)
- Venv de OmniVoice existente (el server TTS usa su interprete; no se modifica ese proyecto)

## Uso

```
setup.bat    # primera vez: crea venv + instala (pide confirmación antes de instalar)
start.bat    # arranca todo y abre el navegador en http://localhost:8000
```
