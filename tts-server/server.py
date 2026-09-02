"""
Servidor TTS basado en OmniVoice para FusionTTS.
Endpoints:
  GET  /health
  POST /load
  POST /unload
  GET  /status
  POST /synthesize

Carga lazy: el modelo NO se carga al arrancar (VRAM 0 en idle).
Se carga con POST /load y se libera con POST /unload.
El formato de /synthesize es invariante (compat TalkWithMe).
"""

import os

# Offline-first: los pesos estan en la caché HF local. No llamar a red.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import base64
import gc
import hashlib
import io
import logging
import tempfile
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from alignment import align_audio, mode as alignment_mode

# Import de torch tolerante: el modulo se puede importar sin torch
# (los tests corren en el venv de la app, que no trae torch).
try:
    import torch
    _TORCH = torch
except ImportError:
    _TORCH = None

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_NAME = os.getenv("OMNIVOICE_MODEL", "k2-fsa/OmniVoice")
# Cuantizacion INT8 weight-only (bitsandbytes): ~600 MB en vez de ~1.2 GB en VRAM
# para los pesos del LLM. Lo setea el app al spawnear (tts_int8 en settings).
INT8 = os.getenv("OMNIVOICE_INT8", "0") == "1"


def _cuda_available() -> bool:
    return _TORCH is not None and bool(_TORCH.cuda.is_available())


DEVICE = "cuda" if _cuda_available() else "cpu"

# Parametros de generacion por defecto
DEFAULT_NUM_STEPS = 32
DEFAULT_SPEED = 1.0
DEFAULT_DURATION = None

# ---------------------------------------------------------------------------
# Estado global (carga lazy, single-flight)
# ---------------------------------------------------------------------------

_status = "unloaded"  # "unloaded" | "loading" | "ready" | "unloading"
_model = None
_pending_unload = False  # /unload durante "loading": descargue al terminar

_state_lock = threading.Lock()
_state_cond = threading.Condition(_state_lock)

_PROMPT_CACHE_MAX = 16
_prompt_cache: "OrderedDict[str, object]" = OrderedDict()
_prompt_cache_lock = threading.Lock()

# generate() (difusion) no es thread-safe: serializa /synthesize concurrentes
# (worker del chat vs replay /api/tts/speak). _prompt_cache_lock se adquiere
# siempre DENTRO de _infer_lock, nunca al revés -> sin deadlock.
_infer_lock = threading.Lock()


def _release_memory():
    """gc + cache de CUDA (solo si hay torch y CUDA disponibles)."""
    gc.collect()
    if _TORCH is not None and _TORCH.cuda.is_available():
        _TORCH.cuda.empty_cache()


def _load_model_impl():
    """Carga el modelo OmniVoice (bloqueante). Exige torch."""
    global _model
    if _TORCH is None:
        raise RuntimeError("torch no disponible")
    mode = "BF16+INT8" if INT8 else "BF16"
    logger.info("Cargando OmniVoice: %s en %s (%s) ...", MODEL_NAME, DEVICE, mode)
    from omnivoice import OmniVoice

    kwargs = {"device_map": DEVICE, "torch_dtype": _TORCH.bfloat16}
    if INT8:
        # transformers >=4.50/5.x: load_in_8bit ya no es kwarg de from_pretrained;
        # se pasa via quantization_config (bitsandbytes).
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
    m = OmniVoice.from_pretrained(MODEL_NAME, **kwargs)
    logger.info("OmniVoice %s cargado correctamente.", mode)
    _model = m
    return m


def _warmup_model_impl():
    """Generacion dummy para calentar kernels CUDA y el camino de inferencia.
    Mismo patron que F5-TTS (tts_worker.load_model): 'ready' implica caliente,
    para que la primera oracion real no pague la inicializacion."""
    logger.info("Warmup: generacion dummy ...")
    _model.generate(
        "warming up.",
        language=None,
        instruct=None,
        duration=None,
        speed=DEFAULT_SPEED,
        voice_clone_prompt=None,
        num_step=DEFAULT_NUM_STEPS,
        guidance_scale=3.0,
    )
    _release_memory()
    logger.info("Warmup completo.")


def _unload_model_impl():
    """Libera el modelo, la caché de prompts y la memoria."""
    global _model
    _model = None
    with _prompt_cache_lock:
        _prompt_cache.clear()
    _release_memory()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SynthesizeRequest(BaseModel):
    text: str
    prompt_text: str = ""
    audio_base64: str = ""
    language: str = "en"
    num_steps: int = DEFAULT_NUM_STEPS
    guidance_scale: float = 3.0
    # seed: campo de compat de API. OmniVoice no soporta seed (from_dict lo
    # descarta en silencio) -> se ignora aqui. Un motor futuro si lo usara.
    seed: int | None = None
    # Campos adicionales de OmniVoice
    speed: float = DEFAULT_SPEED
    duration: float | None = DEFAULT_DURATION
    instruct: str = ""


class SynthesizeResponse(BaseModel):
    audio_base64: str
    sample_rate: int
    # Alineacion forzada por palabra (karaoke); None si el modo
    # TTS_ALIGNMENT es "off" o la alineacion fallo.
    words: list[dict] | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    model: str = MODEL_NAME
    device: str = DEVICE
    cuda_available: bool = _cuda_available()
    alignment: str = alignment_mode()


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _b64_to_wav_file(b64: str) -> str:
    """Decodifica base64 -> bytes WAV -> archivo temporal .wav."""
    if not b64:
        return ""
    raw = base64.b64decode(b64)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(raw)
    tmp.close()
    return tmp.name


def _audio_to_b64(audio_np: np.ndarray, sr: int) -> str:
    """Convierte numpy array -> WAV en memoria -> base64."""
    buf = io.BytesIO()
    sf.write(buf, audio_np, sr, format="WAV")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def _get_or_create_prompt(audio_b64: str, prompt_text: str):
    """
    VoiceClonePrompt con caché LRU clave = sha256(audio_base64 + "|" + prompt_text).
    Hit: prompt cacheado (sin decodificar ni re-crear).
    Miss: WAV temporal -> create_voice_clone_prompt -> guardar -> limpiar temp.
    """
    key = hashlib.sha256(f"{audio_b64}|{prompt_text}".encode("utf-8")).hexdigest()
    with _prompt_cache_lock:
        if key in _prompt_cache:
            _prompt_cache.move_to_end(key)
            return _prompt_cache[key]
    ref_wav = _b64_to_wav_file(audio_b64)
    try:
        prompt = _model.create_voice_clone_prompt(
            ref_audio=ref_wav,
            ref_text=prompt_text or None,
        )
    finally:
        os.unlink(ref_wav)
    with _prompt_cache_lock:
        _prompt_cache[key] = prompt
        while len(_prompt_cache) > _PROMPT_CACHE_MAX:
            _prompt_cache.popitem(last=False)
    return prompt


def _synthesize_sync(req: SynthesizeRequest) -> SynthesizeResponse:
    """Trabajo bloqueante de /synthesize (corre en thread pool)."""
    model = _model
    if model is None:
        raise HTTPException(status_code=503, detail="Modelo no cargado")

    with _infer_lock:
        prompt = None
        if req.audio_base64:
            prompt = _get_or_create_prompt(req.audio_base64, req.prompt_text)

        # Pass-through a generate() (3 modos: voice clone / instruct / auto).
        # seed: NO se pasa (OmniVoice no lo soporta).
        audio = model.generate(
            req.text,
            language=req.language or None,
            instruct=req.instruct or None,
            duration=req.duration,
            speed=req.speed,
            voice_clone_prompt=prompt,
            num_step=req.num_steps,
            guidance_scale=req.guidance_scale,
        )

    # audio es lista de np.ndarray, tomar el primero (o el array directo)
    audio_np = audio[0] if isinstance(audio, list) else audio
    sr = 24000  # OmniVoice genera a 24kHz

    _release_memory()

    # Karaoke: alineacion forzada del texto sobre el audio generado. Corre
    # fuera del _infer_lock (no compite con la generacion) y nunca tumba el
    # /synthesize: si falla, se responde sin words.
    words = align_audio(audio_np, sr, req.text)

    return SynthesizeResponse(
        audio_base64=_audio_to_b64(audio_np, sr), sample_rate=sr, words=words
    )


# ---------------------------------------------------------------------------
# Lifespan (carga lazy: NO carga nada al arrancar)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="OmniVoice TTS Server", version="1.1.0", lifespan=lifespan)

# CORS para que la app pueda llamarlo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check (solo verifica disponibilidad; no consume VRAM)."""
    return HealthResponse()


@app.get("/status")
def status():
    return {"status": _status, "model": MODEL_NAME, "device": DEVICE}


@app.post("/load")
def load():
    """Carga el modelo. Idempotente y single-flight (lock + condition).
    Si un /unload llego durante la carga, descargue al terminar (no queda
    un modelo cargado que nadie pidio)."""
    global _status, _pending_unload
    with _state_cond:
        # Si otro load esta en vuelo, esperar su resultado.
        _state_cond.wait_for(lambda: _status in ("ready", "unloaded"))
        if _status == "ready":
            return {"status": _status}
        _status = "loading"
    try:
        _load_model_impl()
        _warmup_model_impl()
    except Exception as e:
        with _state_cond:
            _pending_unload = False
            if _status == "loading":
                _status = "unloaded"
                _state_cond.notify_all()
        raise HTTPException(status_code=500, detail=str(e))
    with _state_cond:
        if _pending_unload:
            _pending_unload = False
            _status = "unloading"
            _state_cond.notify_all()
        else:
            _status = "ready"
            _state_cond.notify_all()
    if _status == "unloading":
        logger.info("Carga anulada por /unload pendiente: descargando.")
        _unload_model_impl()
        with _state_cond:
            _status = "unloaded"
            _state_cond.notify_all()
    return {"status": _status}


@app.post("/unload")
def unload():
    """Libera el modelo. Idempotente y no bloqueante: si hay carga en curso,
    marca descarga pendiente (la aplica /load al terminar) y responde al tiro."""
    global _status, _pending_unload
    with _state_cond:
        if _status == "ready":
            _status = "unloading"
        elif _status == "loading":
            _pending_unload = True
            return {"status": _status, "pending_unload": True}
        else:
            return {"status": _status}
    try:
        _unload_model_impl()
    finally:
        with _state_cond:
            _status = "unloaded"
            _state_cond.notify_all()
    return {"status": _status}


@app.post("/synthesize", response_model=SynthesizeResponse)
async def synthesize(req: SynthesizeRequest):
    """
    Sintetiza audio usando OmniVoice (trabajo bloqueante en thread pool).

    - audio_base64 + prompt_text -> voice clone (caché de VoiceClonePrompt)
    - instruct (sin audio)       -> voice design
    - sin ninguno                -> auto voice
    """
    if _status != "ready":
        raise HTTPException(status_code=503, detail="Modelo no cargado")
    if req.audio_base64 and not req.prompt_text:
        raise HTTPException(
            status_code=400,
            detail="prompt_text requerido con audio_base64 (el server no tiene ASR)",
        )
    try:
        return await run_in_threadpool(_synthesize_sync, req)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error en synthesize")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5500"))
    uvicorn.run(app, host=host, port=port)
