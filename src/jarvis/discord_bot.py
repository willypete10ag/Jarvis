"""The Discord bot: Jarvis off your desk and onto your phone.

Runs as a single always-on process that does two jobs at once:

1. **Capture** - DM the bot in plain English and it turns your message into task
   actions (via :mod:`jarvis.brain.agent`) and replies to confirm.
2. **Deliver** - a background loop checks for due reminders and DMs them to you,
   so a nudge reaches your phone through the Discord app.

The first person to DM the bot becomes its "owner" (remembered in the DB), which
is who reminders get sent to. Set ``DISCORD_OWNER_ID`` in ``.env`` to pin it.

Run it with:  jarvis discord
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys

from jarvis import config
from jarvis import usage
from jarvis.brain import agent
from jarvis.memory import db
from jarvis.memory import tasks as T

log = logging.getLogger("jarvis.discord")

_OWNER_META_KEY = "discord_owner_id"


def _looks_like_usage_query(low: str) -> bool:
    """True if the user is asking about API usage / free-tier consumption."""
    if "usage" in low:
        return True
    if "how much" in low and any(
        w in low for w in ("used", "left", "tier", "cartesia", "claude", "token", "quota", "spent")
    ):
        return True
    return False

try:
    import discord
    from discord.ext import tasks
except ModuleNotFoundError:  # pragma: no cover - only when dep missing
    discord = None  # type: ignore
    tasks = None  # type: ignore


def _reminder_text(t: T.Task) -> str:
    lines = [f"🔔 **Reminder — #{t.id}: {t.title}**"]
    if t.priority == "high":
        lines.append("Priority: high")
    if t.due_at:
        lines.append(f"Due: {agent._fmt(t.due_at)}")
    if t.next_action:
        lines.append(f"Next: {t.next_action}")
    return "\n".join(lines)


def _build_client() -> "discord.Client":
    intents = discord.Intents.default()
    intents.message_content = True  # to read DM text (privileged; enabled in portal)
    intents.dm_messages = True
    intents.voice_states = True     # to see which voice channel you're in

    client = discord.Client(intents=intents)

    def _owner_id() -> int | None:
        configured = config.DISCORD_OWNER_ID.strip()
        stored = db.get_meta(_OWNER_META_KEY)
        raw = configured or stored
        return int(raw) if raw and raw.isdigit() else None

    @client.event
    async def on_ready() -> None:
        log.info("Logged in as %s (id %s)", client.user, client.user.id)
        if not reminder_loop.is_running():
            reminder_loop.start()

    async def _handle_join(message: "discord.Message") -> None:
        from jarvis import discord_voice

        voice_state = getattr(message.author, "voice", None)
        if voice_state is None or voice_state.channel is None:
            await message.channel.send("You're not in a voice channel — hop in one, then say `join`.")
            return
        if message.guild.voice_client is not None:
            await message.channel.send("I'm already in a voice channel. Say `leave` first.")
            return
        channel = voice_state.channel
        try:
            vc = await channel.connect(cls=discord_voice.voice_recv.VoiceRecvClient)
        except discord.errors.ClientException as e:
            await message.channel.send(f"Couldn't join: {e}")
            return
        except Exception as e:  # missing Connect/Speak permission, etc.
            log.exception("voice join failed")
            await message.channel.send(
                f"Couldn't join **{channel.name}** ({e}). Do I have Connect + Speak permission there?"
            )
            return

        names = [m.display_name for m in channel.members if not m.bot]
        greeting = discord_voice.build_greeting(names)
        await message.channel.send(
            f"🎙️ Joined **{channel.name}**. Talk to me — say `leave` (here or out loud) when you're done."
        )
        client.loop.create_task(
            discord_voice.converse(vc, greeting=greeting, transcript_channel=message.channel)
        )

    async def _handle_leave(message: "discord.Message") -> None:
        vc = message.guild.voice_client
        if vc is None:
            await message.channel.send("I'm not in a voice channel.")
            return
        await vc.disconnect()
        await message.channel.send("👋 Left the voice channel.")

    @client.event
    async def on_message(message: "discord.Message") -> None:
        if message.author == client.user:
            return

        text = message.content.strip()
        low = text.lower()

        # In a server, Jarvis responds when you @mention him. "@Jarvis join" (while
        # you're in a voice channel) makes him join; "@Jarvis leave" disconnects;
        # any other @mention is handled as a task/chat and answered in the channel.
        if message.guild is not None:
            mentioned = client.user in message.mentions
            leave_intent = any(w in low for w in ("leave", "disconnect", "get out", "go away"))
            join_intent = any(w in low for w in ("join", "come", "hop in", "jump in", "get in"))

            if (mentioned and leave_intent) or low in ("leave", "disconnect"):
                await _handle_leave(message)
                return
            if mentioned and join_intent:
                await _handle_join(message)
                return
            if mentioned:
                ask = re.sub(r"<@!?\d+>", "", text).strip()  # drop the mention itself
                if _looks_like_usage_query(ask.lower()):
                    await message.channel.send(f"```\n{usage.format_summary()}\n```")
                    return
                if ask:
                    async with message.channel.typing():
                        reply = await asyncio.to_thread(agent.handle, ask)
                    await message.channel.send(reply[:1900] if reply else "Done.")
            return

        if not isinstance(message.channel, discord.DMChannel):
            return
        if not text:
            return

        # First person to DM becomes the owner, unless one is pinned in config.
        if not config.DISCORD_OWNER_ID.strip() and db.get_meta(_OWNER_META_KEY) is None:
            db.set_meta(_OWNER_META_KEY, str(message.author.id))
            log.info("owner set to %s (%s)", message.author, message.author.id)

        if _looks_like_usage_query(low):
            await message.channel.send(f"```\n{usage.format_summary()}\n```")
            return

        # Task work is blocking (SQLite + a Claude call); keep the event loop
        # responsive by running it in a thread.
        async with message.channel.typing():
            reply = await asyncio.to_thread(agent.handle, text)
        await message.channel.send(reply[:1900] if reply else "Done.")

    @tasks.loop(seconds=config.DISCORD_REMINDER_INTERVAL)
    async def reminder_loop() -> None:
        try:
            due = await asyncio.to_thread(T.due_reminders)
            if not due:
                return
            owner_id = _owner_id()
            if owner_id is None:
                log.warning("%d reminder(s) due but no owner known yet", len(due))
                return
            user = await client.fetch_user(owner_id)
            for t in due:
                await user.send(_reminder_text(t))
                await asyncio.to_thread(T.mark_reminded, t.id)
                log.info("DMed reminder for task #%s", t.id)
        except Exception:
            log.exception("reminder loop tick failed")

    @reminder_loop.before_loop
    async def _before() -> None:
        await client.wait_until_ready()

    return client


def _enable_dave_receive() -> None:
    """Teach discord-ext-voice-recv to decrypt DAVE (E2EE) audio.

    Since 2026-03-02 Discord globally enforces DAVE (MLS end-to-end voice
    encryption). discord.py + davey handle it, so the bot joins and speaks, but
    voice-recv only undoes the *transport* encryption and then feeds the still
    E2E-encrypted opus straight to the decoder -> "OpusError: corrupted stream",
    and Jarvis hears nothing.

    The bot is a full member of the call's MLS group (that's how it can send
    encrypted audio), so its `dave_session` already holds the keys to decrypt
    everyone else's audio -- voice-recv just never calls decrypt(). We bridge the
    two: after voice-recv finishes the transport decryption of each RTP packet,
    run `dave_session.decrypt(sender_id, audio, frame)` to strip the E2EE layer,
    leaving plaintext opus for the decoder. All of this is confined to two small
    monkeypatches on voice-recv's reader; remove once voice-recv ships DAVE.
    """
    try:
        import davey
        from discord.ext.voice_recv import reader as vr_reader

        AudioReader = vr_reader.AudioReader
        PacketDecryptor = vr_reader.PacketDecryptor
        audio_mt = davey.MediaType.audio

        # 1) Give each decryptor a back-reference to its voice client, so the
        #    wrapped decrypt_rtp below can reach the live dave_session + ssrc map.
        _orig_reader_init = AudioReader.__init__

        def _reader_init(self, sink, voice_client, *, after=None):
            _orig_reader_init(self, sink, voice_client, after=after)
            try:
                self.decryptor._dave_vc = voice_client
            except Exception:
                pass

        AudioReader.__init__ = _reader_init

        # 2) After transport decryption, peel off the DAVE/E2EE layer.
        _orig_decryptor_init = PacketDecryptor.__init__

        def _decryptor_init(self, mode, secret_key):
            _orig_decryptor_init(self, mode, secret_key)
            self._dave_vc = None
            _transport_decrypt = self.decrypt_rtp  # per-instance bound method

            def _decrypt_rtp(packet):
                raw = _transport_decrypt(packet)
                vc = getattr(self, "_dave_vc", None)
                if vc is None:
                    return raw
                conn = getattr(vc, "_connection", None)
                ds = getattr(conn, "dave_session", None)
                if ds is None or not getattr(ds, "ready", False):
                    return raw  # DAVE not active -> transport data is plain opus
                if not raw:
                    return raw
                uid = vc._get_id_from_ssrc(packet.ssrc)
                if not uid:
                    return raw
                try:
                    return ds.decrypt(uid, audio_mt, raw)
                except Exception:
                    # Passthrough frames or an unready decryptor for this user:
                    # leave the bytes as-is rather than dropping the packet.
                    return raw

            self.decrypt_rtp = _decrypt_rtp

        PacketDecryptor.__init__ = _decryptor_init

        # 3) Resilience: in voice-recv a single frame that fails opus decode kills
        #    the packet-router thread for good (all later audio is then lost). A
        #    stray undecryptable frame during DAVE setup shouldn't deafen Jarvis
        #    for the rest of the call, so drop the bad frame and keep going.
        from discord.ext.voice_recv import opus as vr_opus

        _orig_decode_packet = vr_opus.PacketDecoder._decode_packet

        def _safe_decode_packet(self, packet):
            try:
                return _orig_decode_packet(self, packet)
            except Exception:
                log.debug("dropping an undecodable voice frame", exc_info=True)
                return packet, b""  # empty pcm -> collector skips it, thread survives

        vr_opus.PacketDecoder._decode_packet = _safe_decode_packet

        log.info("DAVE receive bridge installed (voice-recv will decrypt E2EE audio)")
    except Exception:
        log.warning("could not install DAVE receive bridge; Jarvis may not hear voice", exc_info=True)


def run() -> int:
    """Start the bot. Blocks until interrupted."""
    if discord is None:
        print("discord.py is not installed. Run: pip install discord.py", file=sys.stderr)
        return 1
    if not config.DISCORD_TOKEN:
        print(
            "No DISCORD_TOKEN set. Put it in your .env file as:\n"
            "  DISCORD_TOKEN=your_token_here",
            file=sys.stderr,
        )
        return 1

    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        config.ensure_dirs()
        handlers.append(logging.FileHandler(config.LOG_DIR / "discord.log", encoding="utf-8"))
    except OSError:
        pass  # console logging still works if the file can't be opened
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)

    # Opus must be loaded to *decode* incoming voice (discord.py only auto-loads
    # it lazily for *sending*, which is too late for the receive decoder). Load
    # it up front so Jarvis can hear from the very first frame.
    try:
        if not discord.opus.is_loaded():
            discord.opus._load_default()
        log.info("opus loaded: %s", discord.opus.is_loaded())
    except Exception:
        log.warning("could not load opus; voice receive may not work", exc_info=True)

    _enable_dave_receive()

    db.init_db()

    client = _build_client()
    log.info("starting Discord bot (reminder check every %ss)", config.DISCORD_REMINDER_INTERVAL)
    client.run(config.DISCORD_TOKEN, log_handler=None)
    return 0
