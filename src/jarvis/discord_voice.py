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
from jarvis.voice import stt, tts, vad

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
        # Diagnostics: how many frames we've seen, how many carried decoded PCM,
        # and how many arrived before the speaker's SSRC was mapped to a user.
        self.frames = 0
        self.frames_pcm = 0
        self.frames_no_user = 0
        self.bytes_total = 0

    def feed(self, user, data) -> None:  # noqa: ANN001
        pcm = getattr(data, "pcm", None)
        with self._lock:
            self.frames += 1
            if not pcm:
                return
            self.frames_pcm += 1
            self.bytes_total += len(pcm)
            # A frame can arrive before voice-recv has mapped its SSRC to a user;
            # bucket those under a sentinel key so we don't lose the audio.
            uid = user.id if user is not None else -1
            if user is None:
                self.frames_no_user += 1
            self._buffers.setdefault(uid, bytearray()).extend(pcm)
            self._last[uid] = time.monotonic()

    def stats(self) -> tuple[int, int, int, int]:
        with self._lock:
            return (self.frames, self.frames_pcm, self.frames_no_user, self.bytes_total)

    def drain_ready(self, silence: float) -> list[bytes]:
        """Return (and clear) audio for speakers quiet for >= ``silence`` seconds.

        Used only by the timer fallback when Silero VAD isn't available.
        """
        now = time.monotonic()
        ready: list[bytes] = []
        with self._lock:
            for uid, buf in list(self._buffers.items()):
                if buf and now - self._last.get(uid, 0.0) >= silence:
                    ready.append(bytes(buf))
                    self._buffers[uid] = bytearray()
        return ready

    def snapshot(self) -> dict[int, bytes]:
        """Copy each speaker's buffered audio so far (without clearing it)."""
        with self._lock:
            return {uid: bytes(buf) for uid, buf in self._buffers.items() if buf}

    def clear(self, uid: int) -> None:
        """Drop a speaker's buffered audio (after we've consumed an utterance)."""
        with self._lock:
            self._buffers[uid] = bytearray()


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


# Split a reply for streaming TTS. The goal: a SMALL first chunk so Jarvis starts
# talking fast, then LARGE remaining chunks so there are few synth boundaries
# (each boundary risks a gap if synthesis can't keep up with playback under load).
_UNIT_RE = re.compile(r"[^.!?,;:\n]+[.!?,;:]*", re.UNICODE)
_FIRST_MAX_CHARS = 50   # first spoken chunk: keep it short for fast first word
_REST_MAX_CHARS = 200   # later chunks: keep them big to avoid choppy playback


def _split_sentences(text: str) -> list[str]:
    units = [m.group().strip() for m in _UNIT_RE.finditer(text)]
    units = [u for u in units if u]
    if not units:
        return []

    # First chunk: pack clause units up to a small cap (always take at least one).
    first = ""
    i = 0
    while i < len(units) and (not first or len(first) + 1 + len(units[i]) <= _FIRST_MAX_CHARS):
        first = f"{first} {units[i]}".strip()
        i += 1
    chunks = [first]

    # Remaining chunks: pack up to a larger cap for smooth, gap-free playback.
    cur = ""
    for u in units[i:]:
        if cur and len(cur) + 1 + len(u) > _REST_MAX_CHARS:
            chunks.append(cur)
            cur = u
        else:
            cur = f"{cur} {u}".strip()
    if cur:
        chunks.append(cur)
    return chunks


async def speak_in_vc(vc: "voice_recv.VoiceRecvClient", text: str) -> tuple[float, float, float]:
    """Speak ``text`` into the voice channel, streaming it sentence by sentence.

    Kokoro synthesizes at roughly 1x realtime, so synthesizing a whole long reply
    up front means seconds of silence before Jarvis says anything. Instead we
    split into sentences and pipeline: start playing sentence 1 as soon as it's
    synthesized while sentence 2 synthesizes in the background, and so on. This
    collapses time-to-first-word to about one sentence's worth of synthesis.

    Returns ``(time_to_first_audio, total_playback_seconds, first_chunk_synth_s)``.
    ``first_chunk_synth`` is the pure Kokoro synthesis time for chunk 0, so we can
    tell real synth cost apart from scheduling/contention overhead.
    """
    text = voice_session._speakable(text)
    if not text:
        return (0.0, 0.0)
    sentences = _split_sentences(text)
    if not sentences:
        return (0.0, 0.0)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    first_synth = 0.0

    async def _synthesize_all() -> None:
        nonlocal first_synth
        # One chunk at a time (so Kokoro runs serially), handed off eagerly.
        for idx, sentence in enumerate(sentences):
            try:
                t_syn = time.monotonic()
                samples, sr = await asyncio.to_thread(tts.synthesize, sentence)
                path = await asyncio.to_thread(_write_wav, samples, sr)
                dt = time.monotonic() - t_syn
                if idx == 0:
                    first_synth = dt
                log.info("tts chunk %d synth=%.2fs (%d chars)", idx, dt, len(sentence))
                await queue.put(path)
            except Exception:
                log.warning("TTS synth failed for a chunk; skipping", exc_info=True)
        await queue.put(None)  # end-of-stream sentinel

    producer = asyncio.create_task(_synthesize_all())

    t_start = time.monotonic()
    ttfa: float | None = None
    play_total = 0.0
    if vc.is_playing():
        vc.stop()

    try:
        while True:
            path = await queue.get()
            if path is None:
                break
            done = asyncio.Event()

            def _after(err: Exception | None, ev: asyncio.Event = done) -> None:
                if err:
                    log.warning("VC playback error: %s", err)
                loop.call_soon_threadsafe(ev.set)

            t_play = time.monotonic()
            if ttfa is None:
                ttfa = t_play - t_start
            vc.play(discord.FFmpegPCMAudio(path, executable=_FFMPEG), after=_after)
            await done.wait()
            play_total += time.monotonic() - t_play
            try:
                os.remove(path)
            except OSError:
                pass
    finally:
        await producer

    return (ttfa or 0.0, play_total, first_synth)


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


async def _handle_utterance(vc: "voice_recv.VoiceRecvClient", audio: np.ndarray, note) -> bool:
    """Transcribe one utterance, act on it, and speak the reply.

    Returns True if a leave phrase was handled (the caller should then stop).
    Logs a per-stage timing line so we can see where the round-trip time goes.
    """
    dur = audio.size / STT_SR
    if audio.size < int(STT_SR * _MIN_UTTERANCE_S):
        log.info("skipped short utterance (%.1fs)", dur)
        return False

    t0 = time.monotonic()
    text = await asyncio.to_thread(stt.transcribe, audio)
    stt_s = time.monotonic() - t0
    if not text.strip():
        log.info("empty transcription (%.1fs of audio, stt=%.2fs)", dur, stt_s)
        return False
    log.info("VC heard (%.1fs): %s", dur, text)
    await note(f"🗣️ **heard** ({dur:.1f}s): {text}")

    norm = re.sub(r"[^a-z' ]", "", text.lower()).strip()
    if norm in _LEAVE_PHRASES:
        await speak_in_vc(vc, "Goodbye.")  # short, so leaving is quick
        await note("👋 Leaving the channel.")
        await vc.disconnect()
        return True

    t1 = time.monotonic()
    reply = await asyncio.to_thread(agent.handle, text, spoken=True, channel="voice_channel")
    llm_s = time.monotonic() - t1
    log.info("VC reply: %s", reply)
    await note(f"🤖 {reply}")

    ttfa_s, play_s, synth1_s = await speak_in_vc(vc, reply)

    # "think" = time from end of speech to Jarvis's FIRST spoken word (with
    # streaming TTS this is one chunk's synthesis, not the whole reply). synth1 is
    # the pure Kokoro time for that chunk; if tts1st >> synth1, the gap is
    # scheduling/contention rather than synthesis itself.
    think_s = stt_s + llm_s + ttfa_s
    timing = (f"⏱ stt={stt_s:.1f}s llm={llm_s:.1f}s tts1st={ttfa_s:.1f}s "
              f"(synth1={synth1_s:.1f}s) play={play_s:.1f}s | think={think_s:.1f}s")
    log.info(timing)
    await note(f"_{timing}_")
    return False


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

    # Warm the STT (and VAD) models up front so the first utterance isn't slow.
    await _note("_(warming up speech recognition…)_")
    await asyncio.to_thread(stt.warm_up)
    use_vad = await asyncio.to_thread(vad.is_available)
    if use_vad:
        await asyncio.to_thread(vad.warm_up)
    log.info("endpointing: %s", "Silero VAD" if use_vad else "silence timer")

    if greeting:
        await speak_in_vc(vc, greeting)
    await _note("🎧 Listening. Speak any time; I'll show what I hear here.")

    last_diag = 0.0
    try:
        while vc.is_connected():
            await asyncio.sleep(0.3)

            # Every ~5s, log what the receive path is actually getting. This is
            # the fast way to tell "no audio arriving" (frames=0 -> connection/
            # permissions) from "audio arrives but won't decode" (frames>0,
            # pcm=0 -> opus) from "decodes fine but STT drops it".
            now = time.monotonic()
            if now - last_diag >= 5.0:
                last_diag = now
                frames, fpcm, fnouser, nbytes = collector.stats()
                log.info(
                    "voice-recv diag: frames=%d pcm=%d no_user=%d bytes=%d",
                    frames, fpcm, fnouser, nbytes,
                )

            if vc.is_playing():
                continue  # don't process new speech while Jarvis is talking

            # Collect any finished utterances. With Silero VAD we endpoint on real
            # speech (faster + noise-tolerant); otherwise fall back to the timer.
            utterances: list[np.ndarray] = []
            if use_vad:
                min_len = int(config.VAD_MAX_UTTERANCE_S * STT_SR)
                for uid, pcm in collector.snapshot().items():
                    audio = _pcm_to_whisper(pcm)
                    state, cut = await asyncio.to_thread(vad.endpoint, audio)
                    if state == "endpoint":
                        collector.clear(uid)
                        utterances.append(audio[:cut])
                    elif state == "silence" and audio.size >= min_len:
                        collector.clear(uid)  # long stretch with no speech -> drop
            else:
                utterances = [_pcm_to_whisper(pcm)
                              for pcm in collector.drain_ready(config.VOICE_SILENCE_SECONDS)]

            for audio in utterances:
                if await _handle_utterance(vc, audio, _note):
                    return  # a leave phrase was spoken
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("VC conversation loop failed")
    finally:
        try:
            vc.stop_listening()
        except Exception:
            pass
