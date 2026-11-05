"""
Application configuration, loaded from environment variables / a .env file.

Nothing here exits the process on a missing value: the dashboard shows what is
missing instead, so a new user can see the app and fix the configuration.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)).strip())
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)).strip())
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- AI (Gemini) -----------------------------------------------------------
GEMINI_API_KEY = _str("GEMINI_API_KEY")
GEMINI_MODEL_NAME = _str("GEMINI_MODEL_NAME", "gemini-3.5-flash-lite")
# Minimum seconds between Gemini calls (keeps you under free-tier per-minute limits).
GEMINI_MIN_INTERVAL_SECONDS = _float("GEMINI_MIN_INTERVAL_SECONDS", 5.0)
GEMINI_TIMEOUT_SECONDS = _int("GEMINI_TIMEOUT_SECONDS", 60)
# "gemini" = real AI.  "stub" = offline keyword demo (no key needed, not real AI).
LLM_BACKEND = _str("LLM_BACKEND", "gemini").lower()

# --- Chat driver -----------------------------------------------------------
# "mock" = built-in demo chat inside the dashboard.  "whatsapp" = WhatsApp Web.
DEFAULT_DRIVER = _str("DRIVER", "mock").lower()
# Optional: use an installed browser instead of Playwright's bundled Chromium.
#   BROWSER_CHANNEL=chrome  or  msedge
#   BROWSER_EXECUTABLE_PATH=C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe
BROWSER_CHANNEL = _str("BROWSER_CHANNEL")
BROWSER_EXECUTABLE_PATH = _str("BROWSER_EXECUTABLE_PATH")
HEADLESS = _bool("HEADLESS", False)

# --- Server ----------------------------------------------------------------
HOST = _str("HOST", "127.0.0.1")
PORT = _int("PORT", 8000)

# --- Storage ---------------------------------------------------------------
DATA_DIR = Path(_str("DATA_DIR") or (BASE_DIR / "data"))
DB_PATH = DATA_DIR / "app.db"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
