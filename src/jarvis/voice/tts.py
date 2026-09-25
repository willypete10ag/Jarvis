"""Text-to-speech via Kokoro (ONNX), running on the CPU.

Kokoro gives a natural voice without a heavy PyTorch runtime (it uses
onnxruntime). The model files live in ``config.MODELS_DIR`` and are downloaded
once; see :func:`ensure_models`.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from jarvis import config

log = logging.getLogger("jarvis.voice.tts")

_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"


def ensure_models() -> None:
    """Raise a clear error if the Kokoro model files are missing."""
    missing = [p for p in (config.KOKORO_MODEL_PATH, config.KOKORO_VOICES_PATH) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Kokoro voice model files are missing:\n  "
            + "\n  ".join(str(p) for p in missing)
            + f"\nDownload them into {config.MODELS_DIR}:\n"
            + f"  {_MODEL_URL}\n  {_VOICES_URL}"
        )


@lru_cache(maxsize=1)
def _kokoro():
    from kokoro_onnx import Kokoro

    ensure_models()
    log.info("loading Kokoro TTS model...")
    return Kokoro(str(config.KOKORO_MODEL_PATH), str(config.KOKORO_VOICES_PATH))


def synthesize(text: str) -> tuple[np.ndarray, int]:
    """Return (samples, sample_rate) for ``text`` in Jarvis' configured voice."""
    samples, sample_rate = _kokoro().create(
        text,
        voice=config.KOKORO_VOICE,
        speed=config.KOKORO_SPEED,
        lang="en-us",
    )
    return samples, sample_rate


def speak(text: str) -> None:
    """Synthesize and play ``text`` through the default output device (blocking)."""
    text = (text or "").strip()
    if not text:
        return
    samples, sample_rate = synthesize(text)
    try:
        import sounddevice as sd

        sd.play(samples, sample_rate)
        sd.wait()
    except Exception as e:  # no audio device, etc. - don't crash the session
        log.warning("could not play audio: %s", e)
