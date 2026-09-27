"""Jarvis' "brain": Claude, which it uses to reason, plan, and call tools.

Reached through the official ``anthropic`` SDK (default model
``claude-haiku-4-5``). The model is swappable without code changes via
``JARVIS_LLM_MODEL``. See ``jarvis.config`` for model and credential settings.
"""

from jarvis.brain.client import (
    BrainError,
    ChatResult,
    ask,
    chat,
    health,
    list_models,
)

__all__ = [
    "BrainError",
    "ChatResult",
    "ask",
    "chat",
    "health",
    "list_models",
]
