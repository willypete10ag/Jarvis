"""Voice-activity detection via Silero VAD (ONNX), on the CPU.

Decides when a speaker has finished talking in a Discord voice channel. It
replaces a fixed "no audio for N seconds" timer with real speech detection, so
Jarvis responds sooner after you stop and ignores non-speech noise. Like Kokoro,
it runs through onnxruntime (no PyTorch); the ~2 MB model downloads once into
``config.MODELS_DIR``.

The Silero v5 model is a small recurrent net: it takes 512-sample windows of
16 kHz mono audio plus a carried-over state and returns a speech probability per
window. :func:`endpoint` runs it over the audio buffered so far and reports
whether the utterance is still going, has just ended, or is only silence.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from functools import lru_cache

import numpy as np

from jarvis import config

log = logging.getLogger("jarvis.voice.vad")

SAMPLE_RATE = 16000  # Silero v5 expects 16 kHz mono
_WINDOW = 512        # samples per VAD frame at 16 kHz (a hard requirement)
_CONTEXT = 64        # Silero v5 also needs the previous 64 samples prepended
_MODEL_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/master/"
    "src/silero_vad/data/silero_vad.onnx"
)


def ensure_model() -> None:
    """Download the Silero VAD model into MODELS_DIR if it isn't there yet."""
    p = config.SILERO_VAD_PATH
    if p.exists():
        return
    config.ensure_dirs()
    log.info("downloading Silero VAD model to %s ...", p)
    tmp = str(p) + ".part"
    urllib.request.urlretrieve(_MODEL_URL, tmp)
    os.replace(tmp, p)


@lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort

    ensure_model()
    log.info("loading Silero VAD model...")
    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 1
    return ort.InferenceSession(
        str(config.SILERO_VAD_PATH), sess_options=opts, providers=["CPUExecutionProvider"]
    )


def is_available() -> bool:
    """True if the model can be loaded; logs and returns False otherwise."""
    if not config.VAD_ENABLED:
        return False
    try:
        _session()
        return True
    except Exception:
        log.warning("Silero VAD unavailable; using the silence timer instead", exc_info=True)
        return False


def warm_up() -> None:
    """Load the model up front so the first endpoint check isn't slow."""
    try:
        _session()
    except Exception:
        log.debug("VAD warm-up failed", exc_info=True)


def _speech_probs(samples: np.ndarray) -> np.ndarray:
    """Speech probability for each 512-sample window of 16 kHz mono audio."""
    sess = _session()
    sr = np.array(SAMPLE_RATE, dtype=np.int64)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    n = samples.size // _WINDOW
    probs = np.empty(n, dtype=np.float32)
    context = np.zeros(_CONTEXT, dtype=np.float32)  # previous 64 samples
    for i in range(n):
        win = samples[i * _WINDOW : (i + 1) * _WINDOW]
        chunk = np.concatenate([context, win])[None, :]  # 64 + 512 = 576 samples
        out, state = sess.run(None, {"input": chunk, "state": state, "sr": sr})
        probs[i] = out[0, 0]
        context = win[-_CONTEXT:]
    return probs


def endpoint(samples: np.ndarray) -> tuple[str, int]:
    """Classify the utterance buffered so far.

    ``samples`` is mono float32 audio at 16 kHz. Returns ``(state, cut)`` where
    ``state`` is one of:

      * ``"speaking"`` - speech is ongoing, or the trailing pause is still too
        short; keep buffering.
      * ``"endpoint"`` - the speaker has finished; ``cut`` is the sample index to
        slice the finished utterance at (speech end + a short pad).
      * ``"silence"`` - no real speech in the buffer yet; caller may keep waiting
        or discard if it has grown too long.
    """
    samples = np.asarray(samples, dtype=np.float32).flatten()
    if samples.size < _WINDOW:
        return ("silence", 0)

    probs = _speech_probs(samples)
    start_p, end_p = config.VAD_START_PROB, config.VAD_END_PROB

    in_speech = False
    first_speech = -1
    last_speech_end = 0  # window index (exclusive) where speech last stopped
    for i, p in enumerate(probs):
        if not in_speech and p >= start_p:
            in_speech = True
            if first_speech < 0:
                first_speech = i
        elif in_speech and p < end_p:
            in_speech = False
            last_speech_end = i + 1

    if first_speech < 0:
        return ("silence", 0)

    # Force an endpoint on a runaway utterance (someone talking without pausing).
    if samples.size >= int(config.VAD_MAX_UTTERANCE_S * SAMPLE_RATE):
        return ("endpoint", samples.size)

    if in_speech:
        return ("speaking", 0)  # still talking at the end of the buffer

    # Ignore a too-short blip (treat as noise; caller decides when to discard).
    speech_ms = (last_speech_end - first_speech) * _WINDOW / SAMPLE_RATE * 1000
    if speech_ms < config.VAD_MIN_SPEECH_MS:
        return ("silence", 0)

    speech_end_sample = last_speech_end * _WINDOW
    trailing = samples.size - speech_end_sample
    if trailing >= int(config.VAD_END_SILENCE_MS / 1000 * SAMPLE_RATE):
        pad = int(config.VAD_SPEECH_PAD_MS / 1000 * SAMPLE_RATE)
        return ("endpoint", min(samples.size, speech_end_sample + pad))

    return ("speaking", 0)
