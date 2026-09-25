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
| Local model runtime (LM Studio) | ⏳ next |
| Background worker + reminders | ⏳ |
| Discord bot (capture + notify) | ⏳ |
| Voice (STT/TTS, CPU) | later |
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
