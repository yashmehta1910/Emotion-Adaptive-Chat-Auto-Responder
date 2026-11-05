"""
Built-in demo chat.

The transcript lives in memory at module level, so the dashboard's "Try it"
panel (which plays the friend) and the bot (which plays you) share it, and it
survives stopping and starting the bot.
"""

import threading
from typing import Dict, List, Optional

from .base import ChatDriver, ChatMessage

_lock = threading.Lock()
_messages: List[ChatMessage] = []
_counter = 0
CHAT_TITLE = "Demo chat"


def add_message(direction: str, text: str, kind: str = "text") -> str:
    """Append a message to the demo chat and return its key."""
    global _counter
    with _lock:
        _counter += 1
        key = f"mock_{_counter}"
        _messages.append(ChatMessage(key=key, direction=direction, text=text, kind=kind))
        return key


def add_incoming(text: str, kind: str = "text") -> str:
    return add_message("in", text, kind)


def transcript() -> List[Dict[str, str]]:
    with _lock:
        return [
            {"key": m.key, "direction": m.direction, "text": m.text, "kind": m.kind}
            for m in _messages
        ]


def reset() -> None:
    with _lock:
        _messages.clear()


class MockDriver(ChatDriver):
    name = "mock"

    def chat_title(self) -> str:
        return CHAT_TITLE

    def read_messages(self, limit: int) -> List[ChatMessage]:
        with _lock:
            return list(_messages[-limit:])

    def send(self, text: str) -> Optional[str]:
        return add_message("out", text)
