"""The voice conversation session: the "start listening" loop.

Started by a command (``jarvis voice``). It listens, and when you stop talking
it transcribes what you said, hands it to the same natural-language agent the
Discord bot uses, speaks the reply, and listens again - until you say "stop
listening" (or press Ctrl+C).

Recording uses simple energy-based silence detection: it calibrates the room's
background noise, waits for you to start speaking, then ends the utterance once
you've been quiet for ``VOICE_SILENCE_SECONDS``. No key-holding, no wake word.
"""

from __future__ import annotations

import logging
import queue
import random
import re
import threading

import numpy as np

from jarvis import config
from jarvis.brain import agent
from jarvis.voice import stt, tts

log = logging.getLogger("jarvis.voice")

SAMPLE_RATE = 16000
_BLOCK_SECONDS = 0.03  # 30 ms analysis blocks

# Things you can say to end the session.
_STOP_PHRASES = {
    "stop", "stop listening", "goodbye", "good bye", "bye",
    "exit", "quit", "that's all", "thats all", "shut down", "go away",
}

# Spoken while Jarvis works, to cover the (deliberate) processing time. These are
# pre-rendered to audio once at startup so they play back instantly.
_FILLER_PHRASES = [
    "One moment.",
    "Let me check on that.",
    "On it.",
    "Hold on a sec.",
    "Checking on that.",
    "Give me a second.",
]


def _speakable(text: str) -> str:
    """Strip markdown/emoji so the reply reads naturally aloud."""
    text = text.replace("**", "").replace("*", "")
    text = re.sub(r"#(\d+)", r"number \1", text)
    for junk in ("✅", "🔴", "⚪", "📝", "🔔", "✨", "·", "_"):
        text = text.replace(junk, " ")
    return re.sub(r"\s+", " ", text).strip()


def _rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(block, dtype=np.float64)) + 1e-12))


def record_utterance(
    *,
    silence_seconds: float | None = None,
    max_seconds: float = 30.0,
    start_timeout: float = 15.0,
) -> np.ndarray:
    """Record one utterance from the mic. Returns mono float32 @ 16 kHz (or empty)."""
    import sounddevice as sd

    silence_seconds = silence_seconds if silence_seconds is not None else config.VOICE_SILENCE_SECONDS
    block_frames = int(SAMPLE_RATE * _BLOCK_SECONDS)
    q: "queue.Queue[np.ndarray]" = queue.Queue()

    def _callback(indata, _frames, _time, status):  # noqa: ANN001
        if status:
            log.debug("audio status: %s", status)
        q.put(indata.copy())

    collected: list[np.ndarray] = []
    speaking = False
    silence_run = 0.0
    elapsed = 0.0

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32",
        blocksize=block_frames, callback=_callback,
    ):
        # Calibrate ambient noise for ~0.3s to set a sensible speech threshold.
        ambient_samples = []
        for _ in range(int(0.3 / _BLOCK_SECONDS)):
            ambient_samples.append(_rms(q.get().flatten()))
        ambient = float(np.median(ambient_samples)) if ambient_samples else 0.0
        threshold = max(ambient * 3.0, 0.012)
        log.debug("ambient=%.4f threshold=%.4f", ambient, threshold)

        while True:
            block = q.get().flatten()
            level = _rms(block)
            elapsed += _BLOCK_SECONDS

            if level >= threshold:
                speaking = True
                silence_run = 0.0
                collected.append(block)
            elif speaking:
                silence_run += _BLOCK_SECONDS
                collected.append(block)  # keep trailing audio for clean cutoff
                if silence_run >= silence_seconds:
                    break

            if not speaking and elapsed >= start_timeout:
                return np.zeros(0, dtype=np.float32)  # nobody spoke
            if elapsed >= max_seconds:
                break

    if not collected:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(collected).astype(np.float32)


def _prerender_fillers() -> list[tuple[np.ndarray, int]]:
    """Synthesize the filler phrases once so they can be played with no delay."""
    rendered: list[tuple[np.ndarray, int]] = []
    for phrase in _FILLER_PHRASES:
        try:
            rendered.append(tts.synthesize(phrase))
        except Exception:
            log.warning("could not pre-render filler %r", phrase)
    return rendered


def _respond(text: str, fillers: list[tuple[np.ndarray, int]]) -> None:
    """Answer the user: think in the background while a filler plays, then speak.

    Running the agent (which may make two LLM calls for a quality reply) in a
    thread lets the filler audio overlap the processing, so the pause never feels
    dead - Jarvis says "one moment" and is already working on the answer.
    """
    result: dict[str, str] = {}

    def _work() -> None:
        try:
            result["reply"] = agent.handle(text, spoken=True)
        except Exception as e:  # keep the session alive on any failure
            log.exception("agent failed")
            result["reply"] = "Sorry, I ran into a problem with that."

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()

    if fillers:
        samples, sr = random.choice(fillers)
        tts.play(samples, sr)  # plays while the agent thinks

    worker.join()
    reply = result.get("reply", "").strip() or "Done."
    print(f"  Jarvis: {reply}\n")
    tts.speak(_speakable(reply))


def run() -> int:
    """Run the interactive voice session. Blocks until stopped."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    # Fail early with a friendly message if the TTS models aren't present.
    try:
        tts.ensure_models()
    except FileNotFoundError as e:
        print(e)
        return 1

    print("Warming up voice models (first run may take a bit)...")
    stt.warm_up()
    fillers = _prerender_fillers()  # also warms up the TTS model

    print("\n🎙️  Voice session started. Speak after 'Listening...'.")
    print("    Say \"stop listening\" or press Ctrl+C to end.\n")
    tts.speak("Jarvis online. I'm listening.")

    try:
        while True:
            print("Listening...")
            audio = record_utterance()
            if audio.size == 0:
                continue

            text = stt.transcribe(audio)
            if not text:
                continue
            print(f"  You: {text}")

            normalized = re.sub(r"[^a-z' ]", "", text.lower()).strip()
            if normalized in _STOP_PHRASES:
                tts.speak("Goodbye.")
                break

            _respond(text, fillers)
    except KeyboardInterrupt:
        print("\nVoice session ended.")

    return 0
