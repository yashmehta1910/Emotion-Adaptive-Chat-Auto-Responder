from .base import ChatDriver, ChatMessage, DriverError


def make_driver(kind: str) -> ChatDriver:
    """Create a driver by name ('mock' or 'whatsapp'). WhatsApp is imported lazily."""
    if kind == "whatsapp":
        from .whatsapp import WhatsAppDriver

        return WhatsAppDriver()
    from .mock import MockDriver

    return MockDriver()


__all__ = ["ChatDriver", "ChatMessage", "DriverError", "make_driver"]
