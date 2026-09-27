# Jarvis — Session Handoff

> **Purpose of this file:** let a fresh Claude session (or the user) resume this
> project with full context. Read this top-to-bottom first. Last updated:
> **2026-09-27**.

---

## 0. ⚡ 2026-09-27 PIVOT — brain is now Claude, not a local model

The local model (Qwen3-8B on LM Studio) was too slow and too limited. **The
brain is now Claude via the Anthropic API** (`anthropic` SDK). Everything below
about LM Studio / Ollama / Qwen / GPU VRAM for the LLM is **historical** — kept
for context, but no longer how the brain works.

- **Model:** default `claude-haiku-4-5` (fastest/cheapest, best for the voice
  loop). Swap via `JARVIS_LLM_MODEL` (`claude-sonnet-5`, `claude-opus-5`) — no
  code change. Billing is **pay-per-token** (console.anthropic.com); a Claude
  Pro/Max subscription does **not** cover the API.
- **What changed:** `src/jarvis/brain/client.py` rewritten to call Claude while
  keeping the same `chat()`/`ChatResult` interface (agent/voice/cli/bench
  untouched). `config.py` now reads `ANTHROPIC_API_KEY` and defaults the model to
  Haiku. `scripts/start-jarvis.ps1` no longer boots LM Studio — it just checks
  the key and starts the bot. `requirements.txt` adds `anthropic>=1.8`.
- **No local GPU needed for the brain.** Only Whisper (STT) + Kokoro (TTS) still
  run locally on CPU. The plan is to eventually host the bot on a small always-on
  box (an OptiPlex / the Pi once upgraded) instead of the gaming PC. Runs on the
  main PC for now.
- **To run:** put `ANTHROPIC_API_KEY=sk-ant-...` in `.env`, then start as usual.
- **Pending live test:** as of this commit the key wasn't in `.env` yet, so the
  swap is verified offline (interface, translation, mocked agent run) but not yet
  against the live API over Discord. First check on next run: `jarvis brain
  --health`, then a voice turn.
- **Latency note:** the old tts1st ~3.4s puzzle (§ below) is now separate from
  the brain. Haiku replies should be sub-second; if time-to-first-word is still
  high, it's the TTS/synth stage, not the LLM. Streaming the LLM into the
  sentence-by-sentence TTS is a possible future optimization (not done yet).

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
  ffmpeg; `@Jarvis leave` or say "leave". User granted the bot Connect+Speak
  perms. Use headphones (echo). **Live two-way VC now WORKING as of 2026-09-26
  — see §6.6.**

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

## 6.6 UPDATE (2026-09-26): Discord two-way voice LIVE + desktop launcher

- **Live VC conversation works end-to-end.** User joined a real voice channel,
  Jarvis heard them, transcribed, replied by voice, and held a back-and-forth.
- **The blocker we had to solve — Discord DAVE (E2EE).** As of **2026-03-02
  Discord globally enforces DAVE** (MLS end-to-end voice encryption). Effects:
  - Must run **discord.py ≥ 2.7 + `davey`**. Older gateways are rejected with WS
    **4006**; advertising no DAVE is rejected with WS **4017**. **Do NOT downgrade
    to 2.5.x** (we tried — 4006). requirements.txt is pinned + commented.
  - discord.py handles connecting + Jarvis *speaking*, but
    **`discord-ext-voice-recv` has no DAVE support**, so inbound audio (Jarvis
    *hearing*) failed opus decode with `OpusError: corrupted stream`.
  - **Fix: a DAVE receive bridge** in `discord_bot.py::_enable_dave_receive()`.
    The bot is a full member of the call's MLS group (that's how it sends
    encrypted audio), so its `dave_session` already holds the keys to decrypt
    everyone else. Three monkeypatches on voice-recv: (1) hand the decryptor a
    ref to the voice client, (2) after transport decryption call
    `dave_session.decrypt(sender_id, MediaType.audio, frame)` to strip the E2EE
    layer → plaintext opus, (3) guard the decoder so one bad frame can't kill the
    packet-router thread. Remove the bridge if/when voice-recv ships native DAVE.
  - Added receive diagnostics + a file log at `logs/discord.log`
    (`voice-recv diag: frames/pcm/...`; `pcm>0` + a "heard" line = working).
- **Desktop launcher** (`scripts/start-jarvis.ps1` + a "Start Jarvis" desktop
  shortcut, Iron-Man icon `scripts/jarvis.ico`): checks LM Studio is up (starts
  it + loads `qwen/qwen3-8b` via the `lms` CLI if not), kills any stale Jarvis
  instance (prevents the 4006 double-session), then runs `jarvis discord`.
- **Voice latency work (2026-09-26/27), see §8 for detail.** Shipped: per-stage
  timing, Silero VAD endpointing, streaming TTS, dropped the 2nd LLM call on task
  turns, smoother chunking. Live `think` ~6-10s. Remaining puzzle below.

## ⏸ WHERE WE STOPPED (2026-09-27 night)

Voice two-way works well; the push now is **latency**. Current live numbers:
`stt≈0.8s` (fine), `llm≈2.7s` chat / was ~5.5s on task turns but **now ~2.7s**
after dropping the 2nd LLM call, `tts1st≈3.4s` (dominant).

**The open puzzle:** warm Kokoro synth benchmarks at **0.63s** on this machine at
any thread count, yet live `tts1st` is ~3.4s. So the cost is scheduling/contention,
not synthesis compute or threads. **Prime suspect:** voice-recv continuously
DAVE-decrypts + opus-decodes inbound mic audio in its router thread, starving the
synth while Jarvis is composing/speaking.

**FIRST THING NEXT SESSION:** get a fresh `⏱` line — it now prints
`tts1st=.. (synth1=..)`. `synth1` is the *pure* Kokoro time for chunk 0.
  - `synth1` small (~0.7s) but `tts1st` ~3s  → contention/scheduling. Fix ideas:
    pause/park inbound listening while Jarvis speaks, run synth off the contended
    path, or a dedicated executor; consider `vc.stop_listening()` during playback.
  - `synth1` also ~3s  → synthesis really is slow under load → faster/GPU TTS.
Config toggles: `JARVIS_VOICE_FAST=0` restores natural (slower) phrasing;
`JARVIS_VAD=0` restores the silence timer. Chunk caps + VAD tunables in config.py.

**Open correctness items (deferred by user, revisit after speed):**
- VAD over-capture → Whisper hallucination ("You. You. You." on a long silent
  tail; one turn captured 10.3s → stt 4.5s). Tune VAD thresholds / trailing trim.
- Natural-language "leave" only matches exact phrases; "can you leave now" etc.
  don't trigger. Detect leave *intent* like the text @mention path does.
- Task add/remove correctness: a "remove water my plants" turn read the list
  instead; duplicate task entries appeared. Needs a look.

## What's left toward the vision
- **Booking engine** — a task Jarvis actively works and reports status on
  (active → needs-confirmation → done/failed), pushing updates to Discord. The
  task states already exist; the outcome memory category is ready. Autonomy
  granularity to be decided here.
- **Telephony** — real PSTN calls; deferred (cost). Discord VC is the free
  rehearsal.
- **Voice echo cancellation (AEC)** — so Jarvis doesn't hear his own TTS through
  the mic without headphones. Partial mitigation exists (VC loop ignores audio
  while playing; Discord's own Echo Cancellation can be toggled on). Real AEC
  (WebRTC/speexdsp, post-playback cooldown) is future work; desk-mic path has
  none yet. Requested by user 2026-09-25.
- **Activation / lifecycle** (user 2026-09-25) — wants resource-aware runtime:
  on when there are active tasks, checks in via Discord, goes idle when done;
  ideally woken by a Discord message. Recommended design: keep the lightweight
  **bot always on** (Task Scheduler at logon → always reachable), **JIT-load the
  LLM on demand and unload on idle** (frees VRAM), and have the **worker sleep**
  when idle rather than kill the process. Self-kill is possible but makes him
  unreachable. A second always-on "waker" bot is only needed for zero idle
  footprint. Prefer idle-over-terminate. Details in the activation-lifecycle
  memory note.

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

**CURRENT PRIORITY (2026-09-26): cut voice latency (~5s/turn).** Progress + levers:
- ✅ **Instrumented** — each turn logs `⏱ stt=… llm=… tts=… play=… | think=…`
  (to `logs/discord.log` and the transcript channel). `think` = end-of-speech →
  Jarvis starts talking (the number to minimize). Use it to confirm the bottleneck.
- ✅ **Silero VAD endpointing** (`voice/vad.py`) replaced the fixed 1.2s silence
  timer: endpoints on real speech after ~0.5s trailing silence (≈0.7s/turn faster)
  and rejects non-speech noise. ONNX via existing onnxruntime, no PyTorch; ~2 MB
  model auto-downloads to `data/models/`. Falls back to the timer if unavailable
  (`JARVIS_VAD=0` to force). Tunables in `config.py` (`VAD_*`).
- ✅ **Streaming TTS + short first chunk** (2026-09-27) — replies are split into
  small clause-level chunks (`_split_sentences`, cap ~60 chars) and pipelined:
  Jarvis starts speaking chunk 1 (~1.5-1.8s, mostly Kokoro's fixed per-call
  overhead) while the rest synthesize during playback. `tts1st` in the timing
  line is time-to-first-word. Cut it from up to 7.5s to ~1.5s.
- ✅ **Dropped the 2nd LLM call on voice task turns** (2026-09-27) — spoken tool
  confirmations are now built deterministically (`_spoken_confirmation`: strips
  IDs/markdown/emoji, list lines -> commas, 24h -> "4 pm") instead of a second
  model round-trip. Saves ~2.5-3.5s per task turn. Reversible: `JARVIS_VOICE_FAST=0`
  restores the natural-language phrasing pass (`_natural_spoken`).
- **NEXT levers if still too slow:** stream the LLM tokens straight into the TTS
  pipeline (overlap `llm` with `tts`); reasoning is already off; `base.en` STT is
  fine (~0.8s). Kokoro's ~1.5s fixed synth overhead is now the first-word floor —
  a lighter/faster TTS or GPU TTS would be the next big lever if needed.

Original backlog (mostly done):
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
