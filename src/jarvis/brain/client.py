"""Client for the local LLM 'brain', served over an OpenAI-compatible API.

Design choices that mirror the memory core:

- **Standard library only** (``urllib`` + ``json``). No ``openai``/``requests``
  dependency, so the brain can never break from a bad third-party package.
- **Runtime-swappable.** The endpoint and model come from ``jarvis.config``;
  point them at LM Studio, Ollama, or anything else that speaks the protocol.
- **Thinking is a toggle.** Qwen3 is a hybrid reasoning model that, left to its
  own devices, emits a verbose ``<think>...</think>`` block before answering.
  That is great for hard problems and wasteful for routine agent work, so the
  default here is *non-thinking* (fast, direct); pass ``thinking=True`` when you
  want it to deliberate. Either way the reasoning is separated from the answer.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from jarvis import config

# Qwen3 soft switches: these tokens, placed in the prompt, turn its internal
# reasoning on or off. Verified against the running LM Studio server.
_THINK = "/think"
_NO_THINK = "/no_think"

# Matches a complete inline reasoning block, if the server didn't already split
# it into a separate ``reasoning_content`` field.
_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


class BrainError(RuntimeError):
    """Raised when the brain (local LLM server) can't be reached or errors out."""


@dataclass
class ChatResult:
    """A single completion, with the answer and its reasoning kept apart."""

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
# HTTP plumbing
# ---------------------------------------------------------------------------
def _request(
    path: str,
    *,
    method: str = "GET",
    payload: Optional[dict[str, Any]] = None,
    timeout: Optional[float] = None,
) -> dict[str, Any]:
    """Make one JSON request to the configured endpoint, or raise BrainError."""
    timeout = config.LLM_TIMEOUT if timeout is None else timeout
    url = config.LLM_BASE_URL.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {config.LLM_API_KEY}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # server responded, but with an error
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise BrainError(f"Brain returned HTTP {e.code} for {path}: {body[:500]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:  # couldn't connect
        raise BrainError(
            f"Could not reach the brain at {config.LLM_BASE_URL} ({e}). "
            "Is LM Studio running with the server started "
            "(Developer tab -> Start Server)?"
        ) from e


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def _split_reasoning(content: str) -> tuple[str, str]:
    """Separate any inline ``<think>`` reasoning from the visible answer.

    Returns ``(answer, reasoning)``. Handles the truncated case where an opening
    ``<think>`` never got a closing tag (the model ran out of tokens mid-thought).
    """
    if "<think>" not in content:
        return content, ""

    blocks = _THINK_BLOCK_RE.findall(content)
    if blocks:
        answer = _THINK_BLOCK_RE.sub("", content)
        return answer, "\n".join(b.strip() for b in blocks)

    # Unclosed block: everything after the tag is (incomplete) reasoning.
    answer, _, reasoning = content.partition("<think>")
    return answer, reasoning


def _apply_thinking(messages: list[dict[str, Any]], thinking: bool) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` with the Qwen3 think/no_think switch set.

    The flag is appended to the last user turn (where Qwen3 reads it most
    reliably); if there is no user turn, it becomes a system directive.
    """
    flag = _THINK if thinking else _NO_THINK
    msgs = [dict(m) for m in messages]
    for m in reversed(msgs):
        if m.get("role") == "user":
            content = str(m.get("content", ""))
            if _THINK not in content and _NO_THINK not in content:
                m["content"] = f"{content} {flag}".strip()
            break
    else:
        msgs.insert(0, {"role": "system", "content": flag})
    return msgs


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
    """Send a chat completion to the brain and return a parsed :class:`ChatResult`.

    ``messages`` is the OpenAI-style list of ``{"role", "content"}`` dicts.
    Pass ``tools`` (OpenAI function schemas) to enable tool calling; any tool
    calls the model makes come back on ``ChatResult.tool_calls``.
    """
    model = model or config.LLM_MODEL
    payload: dict[str, Any] = {
        "model": model,
        "messages": _apply_thinking(messages, thinking),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    start = time.perf_counter()
    data = _request("/chat/completions", method="POST", payload=payload, timeout=timeout)
    elapsed = time.perf_counter() - start

    try:
        choice = data["choices"][0]
    except (KeyError, IndexError) as e:
        raise BrainError(f"Malformed response from brain: {data}") from e

    msg = choice.get("message", {}) or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    if not reasoning:  # some servers leave reasoning inline instead
        content, reasoning = _split_reasoning(content)

    usage = data.get("usage", {}) or {}
    return ChatResult(
        content=content.strip(),
        reasoning=reasoning.strip(),
        tool_calls=list(msg.get("tool_calls") or []),
        finish_reason=choice.get("finish_reason", ""),
        model=data.get("model", model),
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        elapsed_s=elapsed,
    )


def ask(prompt: str, *, system: Optional[str] = None, **kwargs: Any) -> ChatResult:
    """Convenience one-shot: send a single user ``prompt`` (with optional system)."""
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat(messages, **kwargs)


def list_models() -> list[str]:
    """Return the model ids the server currently exposes."""
    data = _request("/models")
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


def health() -> tuple[bool, Any]:
    """Check the brain. Returns ``(True, [model ids])`` or ``(False, error str)``."""
    try:
        return True, list_models()
    except BrainError as e:
        return False, str(e)
