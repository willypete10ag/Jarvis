# Jarvis — Session Handoff

> **Purpose of this file:** let a fresh Claude session (or the user) resume this
> project with full context. Read this top-to-bottom first. Last updated:
> **2026-09-24**.

---

## 1. What we're building

A **local, always-on, free "Jarvis"** background agent that runs on the user's
own PC. The user hands it tasks; it keeps a **permanent record**, works them in
the background, **reminds** the user so nothing is forgotten, and reports over
**Discord** (so notifications reach the user's phone). **Approve-before-acting**:
nothing irreversible happens without the user's sign-off.

The user's headline ambition is eventually having Jarvis **make phone calls to
book appointments** (e.g. a dentist). See the important constraint on that in §4.

---

## 2. Hardware (the constraint that drives every decision)

| Component | Spec | Notes |
|-----------|------|-------|
| GPU | **NVIDIA RTX 4070 Ti, 12 GB VRAM** | ~10 GB *free* in practice; baseline ~1.6–2 GB used by desktop/Chrome/Discord/**Wallpaper Engine** (a persistent GPU draw that can be paused to reclaim VRAM). |
| CPU | **i7-13700KF**, 16C/24T | Strong — comfortably runs voice models (STT/TTS) on CPU. |
| RAM | **32 GB** (~18 GB free) | Headroom for CPU offload + orchestration. |
| OS | **Windows 10 Pro** | PowerShell primary shell. Task Scheduler = "run at logon". |

**VRAM planning budget: ~9–10 GB**, because the desktop baseline fluctuates as
the user actually uses the machine. Plan for "fits with room to spare," not
"fits exactly."

---

## 3. Key decisions already made (don't re-litigate these)

- **Model runtime: LM Studio** (primary), the way the user's friend "Robinson"
  (aka Alex/AlexGeddylfson) described it — LM Studio runs the model + serves an
  OpenAI-compatible API; Jarvis and any other program talk to that API.
  **Ollama is also installed** as a spare (scripted pulls / fallback). Both
  expose an OpenAI-compatible endpoint, so the runtime is swappable:
  - LM Studio → `http://localhost:1234/v1`
  - Ollama → `http://localhost:11434/v1`
- **Model: Qwen3-14B @ Q4_K_M** (fully GPU-resident, ~9 GB weights), with the
  **KV cache quantized to q8** to get a bigger context window in the same VRAM.
  - **Fallback: Qwen3-8B @ Q4_K_M** if we need headroom (e.g. GPU-side voice).
  - Rationale settled with the user: 27B/32B/30B-class models **do not fit**
    (weights alone ~17 GB+; KV-cache tuning can't save a model whose *weights*
    overflow). 14B fits AND leaves room for a usable context; 8B is the safe
    fallback. Decision loop agreed with the user:
    **14B → if context tight, quantize KV → still tight? → 8B → feels dumb? → back to 14B.**
  - Why not Gemma (the friend floated "27b"/"9b" = Gemma family): Gemma is
    weaker/finicky at structured tool-calling; Jarvis is an agent, so tool-call
    reliability > prose. Qwen3 / gpt-oss are stronger here.
- **Optional "heavy brain": Qwen3-30B-A3B** (MoE, ~3B active) for *background-only*
  deep reasoning where slower-but-smarter is fine (it spills ~8 GB to RAM, so
  NOT for real-time voice). Loaded on demand, not during calls.
- **Voice models run on CPU** (whisper STT + Piper/Kokoro TTS) so the GPU stays
  dedicated to the LLM. Note: whisper/Piper *can* run on GPU but that risks OOM
  when stacked with the LLM — the friend confirmed this from experience.
- **Autonomy: approve-before-acting** for anything irreversible.

---

## 4. Phone calls — the deferred piece (IMPORTANT)

The user requires the project stay **100% free**. That takes **real phone calls
to arbitrary businesses OFF the table for now** — there's no free/sustainable way
to reach the PSTN (telephony providers cost ~cents/min; trial credits only call
*pre-verified* numbers). This is **deferred, not cancelled** — revisit only if
the user later accepts a small per-minute cost.

**Free substitute for testing the voice pipeline** (agreed as the path):
1. Desk mic + speakers — talk to Jarvis locally.
2. **Discord voice channel** — Jarvis joins a voice channel; user talks to it
   from the Discord phone app. Feels like a "call," costs nothing. This is where
   the user's "call my cell and book a fake appointment" rehearsal lives.
3. *(deferred, paid)* real PSTN call.

---

## 5. What's BUILT and WORKING ✅

Project root: **`C:\Coding_Projects\jarvis`** (git initialized; venv at `.venv`).

**Durable task memory + CLI** — pure Python **standard library** (no deps, so it
can't break from a bad package). Tested end-to-end. Three durability guarantees:
1. **SQLite (WAL mode)** — crash/power-loss resilient → `data/jarvis.db`
2. **Append-only event log** (`task_events` table) — every change recorded
   forever, never overwritten.
3. **Markdown mirror** (`data/notes/tasks.md`, auto-written on every change,
   readable with no app) + **rolling DB backups** (`data/backups/`, keep 30).

**CLI commands** (via `.venv\Scripts\jarvis.exe` or `python -m jarvis`):
`init, add, list, show, note, next, status, done, cancel, remind, due,
reminders, backup`. Human time parsing: `30m`, `2h`, `3d`, `1w`,
`today 15:00`, `tomorrow`, `2026-10-01 14:30`.

Two real tasks already seeded: **#1 Book dentist appointment**, **#2 Update Plex
server**.

### File layout
```
src/jarvis/
  config.py          paths + settings (single source of truth; JARVIS_DB env override)
  timeparse.py       human time strings -> UTC ISO
  cli.py             the task CLI (argparse subcommands)
  __main__.py        enables `python -m jarvis`
  memory/
    db.py            SQLite connection + schema (WAL, schema_version=1)
    tasks.py         Task dataclass + domain logic + audit logging
    mirror.py        markdown mirror + DB backups
data/
  jarvis.db          durable store (git-ignored)
  notes/tasks.md     human-readable mirror
  backups/           timestamped .db snapshots
pyproject.toml       src-layout, editable install, console_script `jarvis`
requirements.txt     future deps (commented; core needs none)
README.md            project overview
```

### Quick verify (sanity check on resume)
```powershell
$j = "C:\Coding_Projects\jarvis\.venv\Scripts\jarvis.exe"
& $j list
& $j show 1
```

---

## 6. What's INSTALLED but not yet configured

- **Ollama** — installed (user signed into its cloud catalog; harmless). Spare.
- **LM Studio v0.4.25** — installed via winget (`ElementLabs.LMStudio`).
  **NOT yet launched/configured.** The `lms` CLI bootstraps on first app launch
  (`%USERPROFILE%\.lmstudio\bin\lms.exe`).
- Also present on machine: `python 3.13`, `git`, `node`, `ffmpeg`, `winget`.

---

## 6.5 BIG UPDATE (2026-09-24 session cont.)

Since the runtime went live we shipped, in order, each tested + pushed:
- **Brain client** (`brain/`) — LLM chat + tool-calling + benchmark. Daily
  driver = **Qwen3-8B** (14B kept as unloaded fallback).
- **Background worker** (`worker.py`, `notify.py`, `autostart.py`) — timer loop
  fires due reminders as Windows toasts + console, daily DB backup, `autostart`
  installs a logon task.
- **Discord bot** (`discord_bot.py`) — DM it in plain English to capture tasks;
  it DMs reminders to you (owner learned from first DM or `DISCORD_OWNER_ID`).
  Bot = **J.A.R.V.I.S.#7340**, logs in fine. Token in `.env` (git-ignored).
- **NL capture agent** (`brain/agent.py`) — shared by Discord + `jarvis capture`.
- **Reliable dates** (`timeparse.py` + `parsedatetime`) — "next Friday" etc.
  resolved in code, not by the model.
- **Voice** (`voice/`) — `jarvis voice` = desk-mic session: Whisper STT (CPU) →
  agent → **Kokoro** TTS (CPU, user disliked Piper). Models in `data/models/`
  (git-ignored). STT↔TTS round-trip verified.
- **Voice polish** — spoken replies are natural (2nd LLM call phrases the
  outcome; lists read aloud), with pre-rendered filler phrases ("one moment")
  played while it thinks (user wants quality over speed, fillers cover the gap).
- **Discord voice** (`discord_voice.py`) — `@Jarvis join` (from a server text
  channel while in a VC) → bot joins via `discord-ext-voice-recv` + PyNaCl,
  greets people by name, listens (48k→16k → Whisper), replies with Kokoro via
  ffmpeg; `@Jarvis leave` or say "leave". Receive→STT path verified with
  simulated audio; **live VC untested (needs a human in a channel)**. User
  granted the bot Connect+Speak perms. Use headphones (echo).

Live voice (desk + Discord VC) is the user's to test.

- **MEMORY layer** (`memory/recall.py`, schema v2) — two tiers agreed with the
  user: **permanent** memory (`memories` table: preference|contact|outcome|
  task_context|fact) and **working** memory (`working_memory`, conversation
  context, auto-wiped daily). Saving policy: auto-save clear facts + tell the
  user, save on explicit request, ASK when ambiguous. Agent injects known facts
  into its prompt and includes recent turns for follow-ups. Tools: remember,
  forget. CLI: `jarvis memory [list|add|forget|clear-chat]`. Verified: save,
  recall, cross-turn context, auto-save of a preference, ambiguous not saved.
  **Autonomy decision (act-vs-ask on booking choices) DEFERRED** — default
  always-ask, revisit when the booking engine is built.

All on GitHub (`willypete10ag/Jarvis`, private).

## What's left toward the vision
- **Booking engine** — a task Jarvis actively works and reports status on
  (active → needs-confirmation → done/failed), pushing updates to Discord. The
  task states already exist; the outcome memory category is ready. Autonomy
  granularity to be decided here.
- **Telephony** — real PSTN calls; deferred (cost). Discord VC is the free
  rehearsal.

## 7. WHERE WE STOPPED

**Runtime is live and the brain client is built.** Both models are downloaded.
We loaded 14B first (fit, but only ~750 MiB VRAM free — too tight for an
always-on agent on a gaming rig), then **switched the daily driver to Qwen3-8B**
(3.68 GB free, ~2x speed, full 16k context, tool-use verified). 14B is kept
installed-but-unloaded as the benchmark baseline / fallback (0 VRAM when idle).

The **brain client** (`src/jarvis/brain/`) is built, tested against the live
server, committed, and pushed. `jarvis brain` and `jarvis bench` both work;
tool-calling PASSes on the 8B.

**GitHub:** repo is live at https://github.com/willypete10ag/Jarvis (private).
Local `main` tracks `origin/main`. Two commits pushed. Claude handles git ops
(the user is new to git) — commit + push after each finished piece.

Next up: the **background worker** (§8 step 4).

---

## 8. NEXT STEPS (in order)

1. ✅ **DONE** — Both models downloaded; 8B loaded and serving on `:1234/v1`.
2. ✅ **DONE** — VRAM measured (8B: 8.3 GB used / 3.68 GB free).
3. ✅ **DONE** — Brain client built (`src/jarvis/brain/`), configurable endpoint
   in `config.py`, tool-calling + thinking toggle + benchmark. 8B is the daily
   driver. *(Optional later: run `jarvis bench` against 14B for a full head-to-head.)*
4. **Background worker** — a loop (launched by Windows Task Scheduler at logon)
   that ticks on a timer, fires due reminders (`tasks.due_reminders()` →
   `mark_reminded()`), and works active tasks. Daily auto-backup via
   `mirror.backup_db()`.
5. **Discord bot** (`discord.py`) — capture tasks from DMs, send reminders +
   progress + approval prompts. **User will need to create a Discord bot token**
   (Claude will walk them through the Developer Portal steps). Store the token in
   a `.env` (already git-ignored) — never commit it.
6. **Voice (later):** whisper (STT) + Piper/Kokoro (TTS) on CPU → then Discord
   voice channel for the "call" rehearsal.
7. **Telephony:** deferred (see §4).

---

## 9. Open items / loose ends

- **Git: DONE.** Repo initialized, committed, and pushed to
  https://github.com/willypete10ag/Jarvis (private). Branch renamed
  `master`→`main`. Attribution footer for commits:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- `.env` for secrets (Discord token, etc.) is git-ignored but not yet created.
- **14B not deleted** — kept as unloaded fallback / benchmark baseline (0 VRAM
  idle; re-downloading it is slow, so not worth reclaiming 9 GB of disk).

---

## 10. Tone / working-relationship notes

- User is technically capable, hands-on, and interrogates recommendations
  (good — explain the *why*, back claims with numbers, offer to measure rather
  than assert). The friend "Robinson" is a real, knowledgeable collaborator whose
  advice largely aligns with the plan; don't dismiss him.
- The user briefly asked for a "Joker" speaking style, then dropped it — **speak
  normally** unless they ask again.
- User's email (for attribution/identity only): willorderapizza@gmail.com.
