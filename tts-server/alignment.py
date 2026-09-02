"""Alineacion forzada palabra a palabra (karaoke) para el server TTS.

Modelo: facebook/wav2vec2-base-960h (CTC English, ~377 MB, Apache-2.0).
Carga lazy en el primer /synthesize con alignment activo (env TTS_ALIGNMENT =
"cpu" | "gpu"; "off" u otro valor = desactivado). Si no hay VRAM suficiente
el modo "gpu" degrada a "cpu" con warning.

El Viterbi es la implementacion numpy del tutorial oficial de torchaudio:
el operador C++ torch.ops.torchaudio.forced_align de torchaudio 2.5.1 esta
roto (devuelve caminos invalidos que no consumen todos los tokens).

Import seguro: no trae torch a nivel de modulo (los tests corren en el venv
de la app, que no tiene torch); torch/transformers/torchaudio se importan
solo dentro de _ensure().
"""

import logging
import os
import threading

import numpy as np

logger = logging.getLogger(__name__)

MODEL_NAME = "facebook/wav2vec2-base-960h"
TARGET_SR = 16000
# Guard de VRAM: si hay menos que esto libre, "gpu" degrada a "cpu"
# (el peak medido del modelo en fp16 es ~358 MB).
MIN_FREE_VRAM_MB = 512
# Misma tokenizacion que el transcript de referencia: una palabra = run de
# chars (upper, solo los del vocab) + token de espacio "|".
_PUNCT_STRIP = ".,!?;:()\"'…-"


def mode() -> str:
    m = os.getenv("TTS_ALIGNMENT", "off").strip().lower()
    return m if m in ("cpu", "gpu") else "off"


def enabled() -> bool:
    return mode() != "off"


def viterbi_align(em: np.ndarray, tokens: np.ndarray) -> np.ndarray:
    """Viterbi CTC con constraint de monotonia (tutorial oficial torchaudio).
    em: (T, C) log-probs; tokens: (L,) ids de objetivo. Devuelve (T,) con la
    posicion del objetivo en cada frame."""
    Tt, _ = em.shape
    L = tokens.size
    blank = 0
    trellis = np.full((Tt, L), -np.inf, dtype=np.float32)
    trellis[:, 0] = np.concatenate([[0.0], np.cumsum(em[1:, blank])]).astype(np.float32)
    for t in range(Tt - 1):
        trellis[t + 1, 1:] = np.maximum(
            trellis[t, 1:] + em[t, blank],
            trellis[t, :-1] + em[t, tokens[1:]],
        )
    for t in range(Tt):
        min_j = (L - 1) - (Tt - 1 - t)
        if min_j > 0:
            trellis[t, :min_j] = -np.inf
    path = np.zeros(Tt, dtype=np.int64)
    path[Tt - 1] = L - 1
    j = L - 1
    for t in range(Tt - 1, 0, -1):
        stay = trellis[t - 1, j] + em[t - 1, blank]
        change = trellis[t - 1, j - 1] + em[t - 1, tokens[j]] if j > 0 else -np.inf
        if j > 0 and change > stay:
            j -= 1
        path[t - 1] = j
    return path


def _build_targets(words: list[str], vocab: dict[str, int]) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Ids de objetivo (1, L) + slice [inicio, fin) de cada palabra en L."""
    ids: list[int] = []
    slices: list[tuple[int, int]] = []
    for w in words:
        start = len(ids)
        for ch in w.upper():
            if ch in vocab:
                ids.append(vocab[ch])
        slices.append((start, len(ids)))
        ids.append(vocab["|"])
    return np.array([ids], dtype=np.int64), slices


def _extract_words(alignment: np.ndarray, ntok: int, slices: list[tuple[int, int]], fps: float):
    a = alignment.tolist()
    T = len(a)
    first_frame = [T] * (ntok + 1)
    pos = 0
    for t in range(T):
        while pos < ntok and a[t] >= pos:
            first_frame[pos] = t
            pos += 1
        if pos == ntok:
            break
    out = []
    for s, e in slices:
        start_f = first_frame[s]
        end_f = first_frame[e + 1] if e + 1 <= ntok else T
        if start_f >= T:
            start_f = max(0, end_f - 1)
        end_f = min(max(end_f, start_f + 1), T)
        out.append({
            "start_ms": round(start_f / fps * 1000.0, 1),
            "end_ms": round(end_f / fps * 1000.0, 1),
        })
    return out


class Aligner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._vocab: dict[str, int] | None = None
        self._device = None
        self._dtype = None
        self._broken = False

    def _ensure(self) -> bool:
        if self._broken:
            return False
        if self._model is not None:
            return True
        import torch
        from transformers import AutoTokenizer, Wav2Vec2ForCTC

        torch_device = "cpu"
        if mode() == "gpu" and torch.cuda.is_available():
            free_b, _ = torch.cuda.mem_get_info()
            if free_b / 1e6 < MIN_FREE_VRAM_MB:
                logger.warning(
                    "alignment: solo %.0f MiB libres en VRAM (< %d): uso cpu",
                    free_b / 1e6, MIN_FREE_VRAM_MB,
                )
            else:
                torch_device = "cuda"
        dtype = torch.float16 if torch_device == "cuda" else torch.float32
        logger.info("Cargando aligner %s en %s (%s) ...", MODEL_NAME, torch_device, dtype)
        tok = AutoTokenizer.from_pretrained(MODEL_NAME)
        model = (
            Wav2Vec2ForCTC.from_pretrained(MODEL_NAME, torch_dtype=dtype)
            .to(torch_device)
            .eval()
        )
        self._vocab = tok.vocab
        self._model = model
        self._device = torch_device
        self._dtype = dtype
        logger.info("Aligner listo (%s).", torch_device)
        return True

    def align(self, audio_np: np.ndarray, sr: int, text: str) -> list[dict] | None:
        """Alinea el texto sobre el audio. Devuelve
        [{text, start_ms, end_ms}] o None (desactivado / sin palabras / fallo)."""
        if not enabled():
            return None
        words = [w.strip(_PUNCT_STRIP) for w in text.split()]
        words = [w for w in words if w]
        if not words:
            return None
        if not self._ensure():
            return None
        try:
            import torch
            import torchaudio.functional as AF

            wav = np.asarray(audio_np, dtype=np.float32)
            if wav.ndim > 1:
                # (channels, samples) torchaudio u (samples, channels) soundfile:
                # el eje corto es el de canales
                wav = wav.mean(axis=0) if wav.shape[0] < wav.shape[1] else wav.mean(axis=1)
            duration = wav.size / float(sr)
            t = torch.from_numpy(wav).unsqueeze(0)
            if sr != TARGET_SR:
                t = AF.resample(t, sr, TARGET_SR)
            t = t.to(device=self._device, dtype=self._dtype)

            targets, slices = _build_targets(words, self._vocab)
            with torch.inference_mode():
                logits = self._model(t).logits
                log_probs = torch.log_softmax(logits, dim=-1)
            em = log_probs[0].cpu().numpy().astype(np.float32)
            alignment = viterbi_align(em, targets[0])
            fps = em.shape[0] / duration
            spans = _extract_words(alignment, targets.shape[1], slices, fps)
            if len(spans) != len(words):
                return None
            return [dict(s, text=w) for w, s in zip(words, spans)]
        except Exception:
            logger.exception("alignment falló; sigo sin words")
            return None


_aligner = Aligner()


def align_audio(audio_np: np.ndarray, sr: int, text: str) -> list[dict] | None:
    return _aligner.align(audio_np, sr, text)
