# Jarvis

A local, always-on background task agent that runs on your own hardware
(RTX 4070 Ti, 12 GB VRAM). You hand it tasks; it keeps a permanent record,
works them in the background, reminds you so nothing falls through, and reports
to you over Discord. Nothing binding happens without your approval.

## Design principles

- **Free & local.** The brain is a local LLM served by LM Studio (or Ollama)
  over an OpenAI-compatible API. No per-token cost.
- **Your tasks never disappear.** Durable SQLite (WAL) + an append-only event
  log + a human-readable markdown mirror + rolling DB backups.
- **Approve-before-acting.** Jarvis preps everything and asks on Discord before
  anything irreversible.
- **Swappable everything.** Model runtime, model, and voice engines are all
  behind interfaces, so we can change any of them without rewrites.

## Status

| Layer | State |
|-------|-------|
| Durable task memory + CLI | ✅ working |
| Local model runtime (LM Studio) | ✅ working |
| Brain client (LLM + tool-calls) | ✅ working |
| Background worker + reminders | ✅ working |
| Discord bot (capture + notify) | ✅ working |
| Natural-language task capture | ✅ working |
| Voice (STT/TTS, CPU) | ✅ working (desk mic) |
| Voice over Discord ("call" rehearsal) | ✅ working |
| Long-term memory (facts, decisions, records) | ✅ working |
| Telephony (real calls) | deferred (not free) |

## The model

- **Default:** Qwen3-14B @ Q4_K_M, fully GPU-resident (~9 GB weights), KV cache
  quantized to q8 for a larger context window in the same VRAM.
- **Fallback:** Qwen3-8B if we need more headroom (e.g. GPU-side voice).
- Voice models (whisper STT, Piper/Kokoro TTS) run on the **CPU** so the GPU
  stays dedicated to the LLM.

## Usage (task CLI)

```powershell
# from the project root, using the venv
$j = ".\.venv\Scripts\jarvis.exe"

& $j init
& $j add "Book dentist appointment" --priority high --due "2w" --remind "3d"
& $j list
& $j show 1
& $j note 1 "Called, left a voicemail"
& $j done 1
& $j reminders          # what the background worker would fire right now
& $j backup             # manual snapshot of the database

# talk to the local model (the "brain")
& $j brain --health                       # is the LLM server up? list models
& $j brain "Summarize my open tasks" -v   # one-shot prompt (-v: reasoning + tok/s)
& $j brain "Think this through..." --think # enable deeper reasoning mode
& $j bench                                # benchmark the model: latency, tok/s, tool-use

# the always-on background worker (fires reminders + daily backups)
& $j worker --once        # run a single tick now (fires anything due) - great for testing
& $j worker               # loop forever, ticking every 60s (Ctrl+C to stop)
& $j worker --interval 30 # tick faster
& $j autostart install    # run the worker automatically at every logon (Windows)
& $j autostart start      # start it now without logging out
& $j autostart status     # is it installed / running?
& $j autostart remove     # stop launching it at logon
```

Reminders fire as a **native Windows toast** (a pop-up in the corner) and to the
console; each reminder fires exactly once, and survives restarts.

## Talking to Jarvis

- **Discord DM** — message the bot in plain English to capture/list/complete
  tasks; it DMs you reminders (which reach your phone). Run: `jarvis discord`.
- **Discord voice** — in a server text channel, `@Jarvis join` (while you're in
  a voice channel) and he joins, greets whoever's there by name, and holds a
  spoken conversation; `@Jarvis leave` (or say "leave") to disconnect.
- **Desk voice** — `jarvis voice` starts a local mic session (say "stop
  listening" to end).
- **CLI** — `jarvis capture "remind me to call the dentist next Friday"`.

All of these drive the same brain, the same durable task list, and the same memory.

## Memory

Jarvis has two tiers of memory, both in the local database:

- **Permanent** — durable facts it keeps: preferences, contacts, appointment
  outcomes/decisions, and task context. It auto-saves clear facts (and tells
  you), saves anything you explicitly ask it to, and asks first when unsure.
- **Working** — the running conversation, so follow-ups make sense; wiped daily
  so it never piles up.

```powershell
& $j memory                      # list what Jarvis remembers
& $j memory add "gate code is 4821" --category fact --subject "home"
& $j memory forget dentist       # forget by text (or by id)
& $j memory clear-chat           # wipe today's conversation memory now
```

Time inputs accept `30m`, `2h`, `3d`, `1w`, `today 15:00`, `tomorrow`,
`2026-10-01 14:30`.

## Layout

```
src/jarvis/
  config.py            paths + settings (single source of truth)
  timeparse.py         human time strings -> UTC ISO
  cli.py               the task CLI
  memory/
    db.py              SQLite connection + schema (WAL)
    tasks.py           task domain logic + audit log
    mirror.py          markdown mirror + DB backups
data/
  jarvis.db            durable task store (git-ignored)
  notes/tasks.md       human-readable mirror
  backups/             timestamped DB snapshots
```
