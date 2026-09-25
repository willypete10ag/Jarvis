"""Jarvis' "brain": the local LLM it uses to reason, plan, and call tools.

The brain is reached over an OpenAI-compatible HTTP API (LM Studio by default,
Ollama as a spare), so it can be swapped without code changes. See
``jarvis.config`` for the endpoint and model settings.
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
