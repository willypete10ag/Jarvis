"""Text-to-speech.

Two engines, chosen by ``config.TTS_PROVIDER``:

- **Cartesia Sonic** (default when ``CARTESIA_API_KEY`` is set): a cloud model
  that sounds genuinely human and streams with very low latency. This is the
  daily driver - it's what makes Jarvis sound like a person instead of a
  robot. Reached over its REST ``/tts/bytes`` endpoint using only the stdlib
  (``urllib``), so it adds no dependency.
- **Kokoro** (local ONNX, CPU): free and offline, but robotic. Kept as a
  fallback for when there's no Cartesia key or no internet.

Both expose the same contract: ``synthesize(text) -> (samples, sample_rate)``
returning a float32 mono numpy array, so everything downstream (WAV write +
ffmpeg for Discord, sounddevice for the desk mic) is unchanged.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from functools import lru_cache

import numpy as np

from jarvis import config

log = logging.getLogger("jarvis.voice.tts")

_CARTESIA_URL = "https://api.cartesia.ai/tts/bytes"

_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"


class TTSError(RuntimeError):
    """Raised when speech synthesis fails."""


# ---------------------------------------------------------------------------
# Engine selection
# ---------------------------------------------------------------------------
def _provider() -> str:
    """Return the active TTS engine: 'cartesia' or 'kokoro'."""
    choice = config.TTS_PROVIDER
    if choice == "cartesia":
        return "cartesia"
    if choice == "kokoro":
        return "kokoro"
    # auto: prefer Cartesia when we have a key, else fall back to Kokoro.
    return "cartesia" if config.CARTESIA_API_KEY else "kokoro"


# ---------------------------------------------------------------------------
# Cartesia Sonic (cloud)
# ---------------------------------------------------------------------------
def _synth_cartesia(text: str) -> tuple[np.ndarray, int]:
    """Synthesize via Cartesia's /tts/bytes endpoint. Returns (float32 mono, sr)."""
    if not config.CARTESIA_API_KEY:
        raise TTSError(
            "No CARTESIA_API_KEY set. Add `CARTESIA_API_KEY=...` to the project's "
            ".env (free key at https://play.cartesia.ai), or set JARVIS_TTS=kokoro."
        )

    sr = config.CARTESIA_SAMPLE_RATE
    payload = {
        "model_id": config.CARTESIA_MODEL,
        "transcript": text,
        "voice": {"id": config.CARTESIA_VOICE_ID},
        # Raw little-endian float32 PCM: parses straight into a numpy array.
        "output_format": {"container": "raw", "encoding": "pcm_f32le", "sample_rate": sr},
    }
    req = urllib.request.Request(
        _CARTESIA_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {config.CARTESIA_API_KEY}",
            "Cartesia-Version": config.CARTESIA_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise TTSError(f"Cartesia returned HTTP {e.code}: {body[:300]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TTSError(f"Could not reach Cartesia ({e}).") from e

    samples = np.frombuffer(raw, dtype="<f4")
    if samples.size == 0:
        raise TTSError("Cartesia returned no audio.")
    # Meter characters against the free tier (best-effort; never break synth).
    try:
        from jarvis import usage

        usage.record_cartesia(len(text))
    except Exception:
        pass
    return samples, sr


# ---------------------------------------------------------------------------
# Kokoro (local ONNX fallback)
# ---------------------------------------------------------------------------
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


def _synth_kokoro(text: str) -> tuple[np.ndarray, int]:
    return _kokoro().create(
        text,
        voice=config.KOKORO_VOICE,
        speed=config.KOKORO_SPEED,
        lang="en-us",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def synthesize(text: str) -> tuple[np.ndarray, int]:
    """Return (samples, sample_rate) for ``text`` in Jarvis' configured voice."""
    if _provider() == "cartesia":
        return _synth_cartesia(text)
    return _synth_kokoro(text)


def play(samples: np.ndarray, sample_rate: int) -> None:
    """Play already-synthesized audio through the default output (blocking)."""
    if samples is None or len(samples) == 0:
        return
    try:
        import sounddevice as sd

        sd.play(samples, sample_rate)
        sd.wait()
    except Exception as e:  # no audio device, etc. - don't crash the session
        log.warning("could not play audio: %s", e)


def speak(text: str) -> None:
    """Synthesize and play ``text`` through the default output device (blocking)."""
    text = (text or "").strip()
    if not text:
        return
    samples, sample_rate = synthesize(text)
    play(samples, sample_rate)
