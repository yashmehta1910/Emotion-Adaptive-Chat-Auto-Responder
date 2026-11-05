"""Common interface every chat driver implements."""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ChatMessage:
    key: str                 # unique, stable id of the message
    direction: str           # "in" (from the other person) or "out" (sent from this account)
    text: str                # empty for photos, voice notes, etc.
    kind: str = "text"       # text | image | video | audio | document | other
    time: str = ""


class DriverError(Exception):
    """Something went wrong talking to the chat app."""


class ChatDriver:
    name = "base"

    def start(self) -> None:
        """Open the chat app. May return before the user is logged in."""

    def is_alive(self) -> bool:
        return True

    def is_ready(self) -> bool:
        """True once the chat app is usable (logged in)."""
        return True

    def status_hint(self) -> str:
        """Human-readable instruction shown while is_ready() is False."""
        return ""

    def chat_title(self) -> str:
        """Name of the chat that is currently open ('' if none)."""
        return ""

    def read_messages(self, limit: int) -> List[ChatMessage]:
        """The last `limit` messages of the open chat, oldest first."""
        return []

    def send(self, text: str) -> Optional[str]:
        """Send `text` into the open chat. Returns the new message's key if known."""
        raise NotImplementedError

    def close(self) -> None:
        """Release the browser / resources."""
