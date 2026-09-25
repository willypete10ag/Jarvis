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
from jarvis.brain import agent
from jarvis.memory import db
from jarvis.memory import tasks as T

log = logging.getLogger("jarvis.discord")

_OWNER_META_KEY = "discord_owner_id"

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

        # Task work is blocking (SQLite + a local LLM call); keep the event loop
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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    db.init_db()

    client = _build_client()
    log.info("starting Discord bot (reminder check every %ss)", config.DISCORD_REMINDER_INTERVAL)
    client.run(config.DISCORD_TOKEN, log_handler=None)
    return 0
