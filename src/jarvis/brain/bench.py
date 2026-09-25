"""Benchmark local model(s) on the real hardware, so the model choice is settled
by measurement rather than guesswork.

Measures three things that actually matter for Jarvis:

1. **Latency** - round-trip on a tiny generation (how snappy it feels).
2. **Throughput** - sustained tokens/second on a longer generation.
3. **Tool-use** - whether the model reliably emits a valid tool call, which is
   the single most important capability for an agent.

Only models the server currently serves will respond; a requested model that
isn't loaded is reported as unavailable rather than silently skipped. To compare
8B vs 14B, run against each in turn (they don't both fit in VRAM at once).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jarvis.brain import client as brain

# A representative Jarvis tool. If the model is any good at tool-calling, the
# prompt below should make it call this rather than answer in prose.
_ADD_TASK_TOOL: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Add a new task to the user's task list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title of the task"},
                    "due": {
                        "type": "string",
                        "description": "When it is due, e.g. '2026-10-01' or 'next Tuesday'",
                    },
                },
                "required": ["title"],
            },
        },
    }
]

_TOOL_PROMPT = "Please add a task to book a dentist appointment for next Tuesday."


@dataclass
class ModelReport:
    model: str
    available: bool = False
    latency_s: float = 0.0
    throughput_tps: float = 0.0
    throughput_tokens: int = 0
    tool_ok: bool = False
    tool_detail: str = ""
    error: str = ""


def _bench_latency(model: str) -> float:
    """Round-trip time for a trivial generation."""
    res = brain.ask("Reply with exactly: OK", model=model, max_tokens=8, temperature=0)
    return res.elapsed_s


def _bench_throughput(model: str, max_tokens: int) -> tuple[float, int]:
    """Sustained tokens/second over a longer, non-thinking generation.

    The prompt deliberately asks for a long answer so the generation dominates
    the timing; otherwise per-request startup latency skews tok/s downward.
    """
    res = brain.ask(
        "Write roughly 300 words about why keeping a durable, written record of "
        "tasks matters. Do not stop early.",
        model=model,
        max_tokens=max_tokens,
        temperature=0.7,
    )
    return res.tokens_per_second, res.completion_tokens


def _bench_tool(model: str) -> tuple[bool, str]:
    """Check the model emits a well-formed ``add_task`` tool call."""
    res = brain.chat(
        [{"role": "user", "content": _TOOL_PROMPT}],
        model=model,
        tools=_ADD_TASK_TOOL,
        max_tokens=256,
        temperature=0,
    )
    if not res.tool_calls:
        return False, "no tool call emitted (answered in prose)"

    call = res.tool_calls[0]
    fn = call.get("function", {}) or {}
    name = fn.get("name", "")
    if name != "add_task":
        return False, f"called '{name}', expected 'add_task'"

    raw_args = fn.get("arguments", "")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except (json.JSONDecodeError, TypeError):
        return False, f"arguments not valid JSON: {raw_args!r}"

    if not args.get("title"):
        return False, f"missing required 'title' (got {args!r})"

    return True, f"title={args.get('title')!r} due={args.get('due')!r}"


def run(models: list[str], *, max_tokens: int = 300) -> list[ModelReport]:
    """Benchmark each model id, tolerating ones that aren't loaded/available."""
    ok, available = brain.health()
    if not ok:
        # Server is down entirely; report the error against every model.
        return [ModelReport(model=m, available=False, error=str(available)) for m in models]

    reports: list[ModelReport] = []
    for model in models:
        rep = ModelReport(model=model)
        # LM Studio may serve either the base id or a ":N" load-instance id, so
        # match loosely rather than requiring an exact string.
        rep.available = any(model == a or a.startswith(model + ":") for a in available)
        if not rep.available:
            rep.error = "not currently loaded/served"
            reports.append(rep)
            continue
        try:
            rep.latency_s = _bench_latency(model)
            rep.throughput_tps, rep.throughput_tokens = _bench_throughput(model, max_tokens)
            rep.tool_ok, rep.tool_detail = _bench_tool(model)
        except brain.BrainError as e:
            rep.error = str(e)
        reports.append(rep)
    return reports


def format_report(reports: list[ModelReport]) -> str:
    """Render a human-readable benchmark table."""
    lines = ["", "Benchmark results", "=" * 60]
    for r in reports:
        lines.append(f"\nModel: {r.model}")
        if r.error:
            lines.append(f"  UNAVAILABLE: {r.error}")
            continue
        lines.append(f"  latency (tiny gen):   {r.latency_s:.2f} s")
        lines.append(
            f"  throughput:           {r.throughput_tps:.1f} tok/s "
            f"({r.throughput_tokens} tokens)"
        )
        mark = "PASS" if r.tool_ok else "FAIL"
        lines.append(f"  tool-use:             {mark} - {r.tool_detail}")
    lines.append("")
    return "\n".join(lines)
