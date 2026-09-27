"""Client for the 'brain' - Anthropic's Claude, via the official SDK.

History: Jarvis originally ran a *local* model (Qwen3-8B on LM Studio/Ollama)
over an OpenAI-compatible API. That was slow, limited, and tied Jarvis to the
GPU box. As of the cloud-brain swap it talks to **Claude** through the
``anthropic`` SDK instead - much smarter, much faster, and no local GPU needed.

Design choices kept from the original:

- **Runtime-swappable model.** The model id comes from ``jarvis.config``
  (``JARVIS_LLM_MODEL``); change it to move between Haiku/Sonnet/Opus without
  touching code. Default is Haiku 4.5: fastest + cheapest, ideal for a
  real-time voice loop.
- **A stable ``chat()`` / ``ChatResult`` interface.** The rest of the codebase
  (agent, voice, cli, bench) calls this module with OpenAI-style ``messages``
  and ``tools`` and reads OpenAI-style ``tool_calls`` off the result. That
  contract is preserved here: the Anthropic-specific translation lives entirely
  inside this file, so nothing else changed in the swap.
- **Thinking is a toggle**, off by default. Extended thinking adds latency, so
  the voice path leaves it off; pass ``thinking=True`` for deliberate one-offs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic

from jarvis import config

# Minimum thinking budget the API accepts, and the headroom we leave above it
# for the actual answer when thinking is switched on (budget must be < max_tokens).
_THINK_BUDGET = 1024
_THINK_HEADROOM = 1024


class BrainError(RuntimeError):
    """Raised when the brain (Claude API) can't be reached or errors out."""


@dataclass
class ChatResult:
    """A single completion, with the answer and its reasoning kept apart.

    ``tool_calls`` mirrors the OpenAI function-call shape the rest of the
    codebase already expects: ``[{"id", "type": "function",
    "function": {"name", "arguments": <json string>}}]``.
    """

    content: str
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_s: float = 0.0

    @property
    def tokens_per_second(self) -> float:
        return self.completion_tokens / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def truncated(self) -> bool:
        """True if generation stopped only because it hit the token ceiling."""
        return self.finish_reason == "length"


# ---------------------------------------------------------------------------
# Client (constructed once, lazily)
# ---------------------------------------------------------------------------
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    """Return a cached Anthropic client, or raise a clear BrainError."""
    global _client
    if _client is not None:
        return _client

    api_key = config.ANTHROPIC_API_KEY
    if not api_key:
        raise BrainError(
            "No ANTHROPIC_API_KEY set. Add a line `ANTHROPIC_API_KEY=sk-ant-...` "
            "to the project's .env file (get a key at https://console.anthropic.com)."
        )

    kwargs: dict[str, Any] = {"api_key": api_key}
    # Allow pointing at a proxy / gateway; ignored when unset or the placeholder.
    base = (config.LLM_BASE_URL or "").strip()
    if base and base != "https://api.anthropic.com":
        kwargs["base_url"] = base

    _client = anthropic.Anthropic(**kwargs)
    return _client


# ---------------------------------------------------------------------------
# OpenAI-style  ->  Anthropic-style translation (request)
# ---------------------------------------------------------------------------
def _split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Pull ``system`` turns out into one system string; keep the rest in order.

    Anthropic takes the system prompt as a separate top-level field, and its
    ``messages`` array must start with a ``user`` turn. Leading ``assistant``
    turns (possible when recent-history context is truncated) are dropped so the
    request is always valid.
    """
    system_parts: list[str] = []
    convo: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            if content:
                system_parts.append(str(content))
        elif role in ("user", "assistant"):
            convo.append({"role": role, "content": str(content)})

    # messages must begin with a user turn.
    while convo and convo[0]["role"] != "user":
        convo.pop(0)

    return "\n\n".join(system_parts), convo


def _convert_tools(tools: Optional[list[dict[str, Any]]]) -> Optional[list[dict[str, Any]]]:
    """Translate OpenAI function schemas to Anthropic tool schemas.

    OpenAI:    {"type": "function", "function": {"name", "description", "parameters"}}
    Anthropic: {"name", "description", "input_schema"}

    A prompt-cache breakpoint is placed on the final tool: the tool list is the
    stable head of the request prefix, so caching it trims repeat input cost.
    """
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for t in tools:
        fn = t.get("function", t)  # tolerate an already-flat schema
        converted.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    if converted:
        converted[-1]["cache_control"] = {"type": "ephemeral"}
    return converted


# ---------------------------------------------------------------------------
# Anthropic response  ->  ChatResult (response)
# ---------------------------------------------------------------------------
_STOP_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "refusal",
    "pause_turn": "pause",
}


def _parse_response(resp: Any, model: str, elapsed: float) -> ChatResult:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in resp.content:
        btype = getattr(block, "type", "")
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "thinking":
            reasoning_parts.append(getattr(block, "thinking", "") or "")
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        # agent._dispatch expects a JSON *string* here.
                        "arguments": json.dumps(block.input or {}),
                    },
                }
            )

    usage = getattr(resp, "usage", None)
    return ChatResult(
        content="".join(text_parts).strip(),
        reasoning="\n".join(p for p in reasoning_parts if p).strip(),
        tool_calls=tool_calls,
        finish_reason=_STOP_MAP.get(getattr(resp, "stop_reason", "") or "", getattr(resp, "stop_reason", "") or ""),
        model=getattr(resp, "model", model),
        prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0) if usage else 0,
        completion_tokens=int(getattr(usage, "output_tokens", 0) or 0) if usage else 0,
        elapsed_s=elapsed,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def chat(
    messages: list[dict[str, Any]],
    *,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    tools: Optional[list[dict[str, Any]]] = None,
    thinking: bool = False,
    timeout: Optional[float] = None,
) -> ChatResult:
    """Send a chat completion to Claude and return a parsed :class:`ChatResult`.

    ``messages`` is the OpenAI-style list of ``{"role", "content"}`` dicts
    (``system`` turns are lifted into Claude's system field automatically).
    Pass ``tools`` (OpenAI function schemas) to enable tool calling; any tool
    calls come back on ``ChatResult.tool_calls`` in the same OpenAI shape.
    """
    client = _get_client()
    model = model or config.LLM_MODEL
    system, convo = _split_system(messages)
    if not convo:
        raise BrainError("No user/assistant messages to send to the brain.")

    params: dict[str, Any] = {
        "model": model,
        "messages": convo,
        "max_tokens": max_tokens,
    }
    if system:
        params["system"] = system
    if tools:
        params["tools"] = _convert_tools(tools)
        params["tool_choice"] = {"type": "auto"}

    if thinking:
        # Extended thinking needs headroom above the budget; bump max_tokens if
        # the caller's ceiling is too low to fit both the thought and the answer.
        params["max_tokens"] = max(max_tokens, _THINK_BUDGET + _THINK_HEADROOM)
        params["thinking"] = {"type": "enabled", "budget_tokens": _THINK_BUDGET}
        # Sampling controls aren't allowed alongside extended thinking.
    else:
        params["temperature"] = temperature

    request_timeout = config.LLM_TIMEOUT if timeout is None else timeout

    start = time.perf_counter()
    try:
        resp = client.with_options(timeout=request_timeout).messages.create(**params)
    except anthropic.APIStatusError as e:
        raise BrainError(f"Claude returned HTTP {e.status_code}: {getattr(e, 'message', e)}") from e
    except anthropic.APIError as e:
        raise BrainError(f"Could not reach Claude ({e}).") from e
    except Exception as e:  # never leak a raw SDK error to callers
        raise BrainError(f"Brain call failed ({e}).") from e
    elapsed = time.perf_counter() - start

    return _parse_response(resp, model, elapsed)


def ask(prompt: str, *, system: Optional[str] = None, **kwargs: Any) -> ChatResult:
    """Convenience one-shot: send a single user ``prompt`` (with optional system)."""
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat(messages, **kwargs)


def list_models() -> list[str]:
    """Return the model ids the account can access."""
    client = _get_client()
    try:
        return [m.id for m in client.models.list()]
    except anthropic.APIError as e:
        raise BrainError(f"Could not list models ({e}).") from e


def health() -> tuple[bool, Any]:
    """Check the brain. Returns ``(True, [model ids])`` or ``(False, error str)``."""
    try:
        return True, list_models()
    except BrainError as e:
        return False, str(e)
