"""Tests del modulo de alineacion (tts-server/alignment.py) sin torch.

Solo la parte numpy: Viterbi, targets y extraction de spans. El modulo se
importa desde tts-server/ (no trae torch a nivel de modulo, por eso puede
correr en el venv de la app).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tts-server"))

import alignment  # noqa: E402
from alignment import (  # noqa: E402
    _build_targets,
    _extract_words,
    effective_mode,
    viterbi_align,
)


# ---------------------------------------------------------------------------
# effective_mode: el request gana, vacio/invalido cae al env
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "env", "expected"),
    [
        ("cpu", "off", "cpu"),
        ("gpu", "off", "gpu"),
        ("off", "gpu", "off"),
        ("CPU", "off", "cpu"),
        ("  gpu ", "off", "gpu"),
        ("", "cpu", "cpu"),
        (None, "gpu", "gpu"),
        ("banana", "cpu", "cpu"),
        ("", "banana", "off"),
        (None, None, "off"),
    ],
)
def test_effective_mode(requested, env, expected, monkeypatch):
    if env is None:
        monkeypatch.delenv("TTS_ALIGNMENT", raising=False)
    else:
        monkeypatch.setenv("TTS_ALIGNMENT", env)
    assert effective_mode(requested) == expected


def test_mode_env_default(monkeypatch):
    monkeypatch.delenv("TTS_ALIGNMENT", raising=False)
    assert alignment.mode() == "off"
    assert not alignment.enabled()
    monkeypatch.setenv("TTS_ALIGNMENT", "GPU")
    assert alignment.mode() == "gpu"
    assert alignment.enabled()
    monkeypatch.setenv("TTS_ALIGNMENT", "nada")
    assert alignment.mode() == "off"


# ---------------------------------------------------------------------------
# _build_targets: ids + slices por palabra
# ---------------------------------------------------------------------------


def test_build_targets_ids_and_slices():
    vocab = {"H": 1, "E": 2, "L": 3, "O": 4, "W": 5, "R": 6, "D": 7, "|": 8}
    targets, slices = _build_targets(["HELLO", "WORLD"], vocab)
    assert targets.shape == (1, 12)
    assert targets[0].tolist() == [1, 2, 3, 3, 4, 8, 5, 4, 6, 3, 7, 8]
    assert slices == [(0, 5), (6, 11)]


def test_build_targets_skips_unknown_chars():
    vocab = {"A": 1, "|": 2}
    targets, slices = _build_targets(["A1", "B"], vocab)
    # solo entran los chars del vocab; B (vacio) deja un slice de ancho 0
    assert targets[0].tolist() == [1, 2, 2]
    assert slices == [(0, 1), (2, 2)]


# ---------------------------------------------------------------------------
# viterbi_align: invariants del path (el bug del operador C++ era devolver
# paths que no consumian todos los tokens; aca el final esta forzado)
# ---------------------------------------------------------------------------


def _synthetic_emission():
    # C=4: [blank, A, B, SP]. targets [A, SP, B, SP] (dos palabras).
    # pico de A en el frame 2, pico de B en el frame 5.
    em = np.full((8, 4), -5.0, dtype=np.float32)
    em[:, 0] = -0.5
    em[2, 1] = 10.0
    em[5, 2] = 10.0
    return em, np.array([1, 3, 2, 3], dtype=np.int64)


def test_viterbi_path_invariants():
    em, tokens = _synthetic_emission()
    path = viterbi_align(em, tokens)
    assert path.shape == (8,)
    # monotonica no decreciente, dentro de rango
    assert np.all(np.diff(path) >= 0)
    assert path.min() >= 0
    assert path.max() <= tokens.size - 1
    # consume todos los tokens (el final esta forzado a L-1)
    assert path[-1] == tokens.size - 1
    # el primer target esta vigente desde el frame 0
    assert path[0] == 0


def test_viterbi_b_emitted_after_its_peak():
    em, tokens = _synthetic_emission()
    path = viterbi_align(em, tokens)
    # B es el token 2: su emision (pico frame 5) avanza el path a >= 2
    # en el frame siguiente
    first_b = int(np.argmax(path >= 2))
    assert first_b == 6


# ---------------------------------------------------------------------------
# _extract_words: spans en ms
# ---------------------------------------------------------------------------


def test_extract_words_spans():
    em, tokens = _synthetic_emission()
    path = viterbi_align(em, tokens)
    slices = _build_targets(["A", "B"], {"A": 1, "B": 2, "|": 3})[1]
    words = _extract_words(path, tokens.size, slices, fps=100.0)
    assert len(words) == 2
    # palabra 0: desde el inicio hasta que empieza la palabra 1
    assert words[0]["start_ms"] == 0.0
    assert words[0]["end_ms"] == words[1]["start_ms"]
    # palabra 1: hasta el fin del audio
    assert words[1]["end_ms"] == 80.0
    # spans validos: start < end, ordenados
    for w in words:
        assert w["start_ms"] < w["end_ms"]
    assert words[0]["end_ms"] <= words[1]["end_ms"]
