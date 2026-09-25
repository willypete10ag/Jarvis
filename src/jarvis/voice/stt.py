"""Speech-to-text via faster-whisper, running on the CPU.

The model is loaded once and cached. ``int8`` compute keeps it fast and light on
a strong CPU (the i7 handles base.en comfortably) while the GPU stays free for
the language model.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from jarvis import config

log = logging.getLogger("jarvis.voice.stt")

SAMPLE_RATE = 16000  # what Whisper expects


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel

    log.info("loading STT model '%s' (first run downloads it)...", config.STT_MODEL)
    return WhisperModel(config.STT_MODEL, device="cpu", compute_type="int8")


def transcribe(audio: np.ndarray) -> str:
    """Transcribe mono float32 audio (16 kHz) to text. Empty string if silence."""
    if audio is None or audio.size == 0:
        return ""
    audio = np.asarray(audio, dtype=np.float32).flatten()
    segments, _info = _model().transcribe(audio, language="en", beam_size=1)
    return " ".join(seg.text.strip() for seg in segments).strip()


def warm_up() -> None:
    """Load the model ahead of time so the first real transcription is snappy."""
    _model()
