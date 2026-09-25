"""Two-way voice inside a Discord voice channel: Jarvis joins, listens, replies.

discord.py can *play* audio into a voice channel but can't *receive* it, so we
use discord-ext-voice-recv to capture each speaker's PCM. The flow mirrors the
desk-mic session, but over Discord:

  speaker's audio -> buffer per user -> (gap = they stopped) -> Whisper (STT)
  -> the same agent -> Kokoro (TTS) -> ffmpeg -> played back into the channel

So you can sit in a VC (from your phone or desktop), talk to Jarvis, and manage
your task list by voice - the stepping stone toward the phone-call rehearsal.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import threading
import time

import numpy as np

import discord
from discord.ext import voice_recv

from jarvis import config
from jarvis.brain import agent
from jarvis.voice import session as voice_session
from jarvis.voice import stt, tts

log = logging.getLogger("jarvis.discord.voice")

DISCORD_SR = 48000   # Discord voice is 48 kHz stereo s16le
STT_SR = 16000       # what Whisper wants
_MIN_UTTERANCE_S = 0.4  # ignore sub-half-second blips (coughs, clicks)

_FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

# Things you can say in the channel to make Jarvis leave.
_LEAVE_PHRASES = {
    "leave", "leave the channel", "disconnect", "goodbye", "good bye", "bye",
    "go away", "that's all", "thats all", "stop listening", "you can go",
}


class UtteranceCollector:
    """Accumulates each speaker's PCM and hands back finished utterances.

    ``feed`` is called from the voice-recv thread for every ~20 ms frame; a frame
    only arrives while someone is actually speaking, so "no frames for a while"
    means they've stopped - that's how we detect the end of an utterance.
    """

    def __init__(self) -> None:
        self._buffers: dict[int, bytearray] = {}
        self._last: dict[int, float] = {}
        self._lock = threading.Lock()

    def feed(self, user, data) -> None:  # noqa: ANN001
        pcm = getattr(data, "pcm", None)
        if user is None or not pcm:
            return
        with self._lock:
            self._buffers.setdefault(user.id, bytearray()).extend(pcm)
            self._last[user.id] = time.monotonic()

    def drain_ready(self, silence: float) -> list[bytes]:
        """Return (and clear) audio for speakers quiet for >= ``silence`` seconds."""
        now = time.monotonic()
        ready: list[bytes] = []
        with self._lock:
            for uid, buf in list(self._buffers.items()):
                if buf and now - self._last.get(uid, 0.0) >= silence:
                    ready.append(bytes(buf))
                    self._buffers[uid] = bytearray()
        return ready


def _pcm_to_whisper(pcm: bytes) -> np.ndarray:
    """Convert Discord PCM (48 kHz stereo s16le) to mono float32 @ 16 kHz."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size < 2:
        return np.zeros(0, dtype=np.float32)
    if samples.size % 2:
        samples = samples[:-1]
    mono = samples.reshape(-1, 2).mean(axis=1)  # stereo -> mono
    n = int(len(mono) * STT_SR / DISCORD_SR)     # 48k -> 16k
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.linspace(0.0, 1.0, len(mono), dtype=np.float32)
    x_new = np.linspace(0.0, 1.0, n, dtype=np.float32)
    return np.interp(x_new, x_old, mono).astype(np.float32)


def _write_wav(samples: np.ndarray, sr: int) -> str:
    import soundfile as sf

    tmp = tempfile.NamedTemporaryFile(prefix="jarvis_tts_", suffix=".wav", delete=False)
    tmp.close()
    sf.write(tmp.name, samples, sr)
    return tmp.name


async def speak_in_vc(vc: "voice_recv.VoiceRecvClient", text: str) -> None:
    """Synthesize ``text`` with Kokoro and play it into the voice channel."""
    text = voice_session._speakable(text)
    if not text:
        return
    samples, sr = await asyncio.to_thread(tts.synthesize, text)
    path = await asyncio.to_thread(_write_wav, samples, sr)

    loop = asyncio.get_running_loop()
    done = asyncio.Event()

    def _after(err: Exception | None) -> None:
        if err:
            log.warning("VC playback error: %s", err)
        loop.call_soon_threadsafe(done.set)

    try:
        if vc.is_playing():
            vc.stop()
        vc.play(discord.FFmpegPCMAudio(path, executable=_FFMPEG), after=_after)
        await done.wait()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def build_greeting(names: list[str]) -> str:
    """A spoken hello that names whoever is already in the channel."""
    if not names:
        return "Hello, Jarvis here. How can I help?"
    if len(names) == 1:
        who = names[0]
    elif len(names) == 2:
        who = f"{names[0]} and {names[1]}"
    else:
        who = ", ".join(names[:-1]) + f", and {names[-1]}"
    return f"Hello {who}. Jarvis here. What can I help you with?"


async def converse(
    vc: "voice_recv.VoiceRecvClient",
    *,
    greeting: str | None = "Hi, I'm here. What can I do for you?",
    transcript_channel=None,  # a discord.TextChannel to mirror the conversation into
) -> None:
    """Listen/think/speak loop for as long as Jarvis is in the channel.

    If ``transcript_channel`` is given, everything Jarvis hears and says is also
    posted there as text - invaluable for seeing what the mic/STT actually caught.
    """
    collector = UtteranceCollector()
    vc.listen(voice_recv.BasicSink(collector.feed))
    log.info("listening in voice channel")

    async def _note(msg: str) -> None:
        if transcript_channel is not None:
            try:
                await transcript_channel.send(msg[:1900])
            except Exception:
                log.debug("couldn't post transcript line", exc_info=True)

    # Warm the STT model up front so the first utterance isn't slow.
    await _note("_(warming up speech recognition…)_")
    await asyncio.to_thread(stt.warm_up)

    if greeting:
        await speak_in_vc(vc, greeting)
    await _note("🎧 Listening. Speak any time; I'll show what I hear here.")

    try:
        while vc.is_connected():
            await asyncio.sleep(0.3)
            if vc.is_playing():
                continue  # don't process new speech while Jarvis is talking
            for pcm in collector.drain_ready(config.VOICE_SILENCE_SECONDS):
                audio = _pcm_to_whisper(pcm)
                dur = audio.size / STT_SR
                if audio.size < int(STT_SR * _MIN_UTTERANCE_S):
                    log.info("skipped short utterance (%.1fs)", dur)
                    continue
                text = await asyncio.to_thread(stt.transcribe, audio)
                if not text.strip():
                    log.info("empty transcription (%.1fs of audio)", dur)
                    continue
                log.info("VC heard (%.1fs): %s", dur, text)
                await _note(f"🗣️ **heard** ({dur:.1f}s): {text}")

                norm = re.sub(r"[^a-z' ]", "", text.lower()).strip()
                if norm in _LEAVE_PHRASES:
                    await speak_in_vc(vc, "Leaving now. Goodbye.")
                    await _note("👋 Leaving the channel.")
                    await vc.disconnect()
                    return

                reply = await asyncio.to_thread(agent.handle, text, spoken=True, channel="voice_channel")
                log.info("VC reply: %s", reply)
                await _note(f"🤖 {reply}")
                await speak_in_vc(vc, reply)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("VC conversation loop failed")
    finally:
        try:
            vc.stop_listening()
        except Exception:
            pass
