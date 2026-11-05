"""
AI layer.

GeminiLLM: ONE request per incoming message that both judges the sender's
emotion and writes the reply in the message's own language and script
(English, Hindi, Gujarati, Hinglish...).

StubLLM: a tiny offline keyword demo so the dashboard can be shown without an
API key. It is NOT real AI and the dashboard labels it as such.

Message text and recent chat history ARE sent to Google when GeminiLLM is used.
"""

import json
import logging
import re
import time
from typing import Dict, List, Optional

from . import config

logger = logging.getLogger(__name__)

EMOTIONS = ["joy", "sadness", "anger", "fear", "disgust", "surprise", "neutral"]

EMOTION_GUIDANCE = {
    "joy": "The sender seems happy or excited. Match their energy, be warm and "
           "celebratory, keep it light.",
    "sadness": "The sender seems sad or down. Be gentle, slow down, and validate "
               "their feelings before offering any suggestions. Do not sound "
               "dismissive or overly cheerful.",
    "anger": "The sender seems frustrated or angry. Stay calm, acknowledge their "
             "frustration without being defensive, and keep sentences short and "
             "non-confrontational.",
    "fear": "The sender seems anxious or worried. Be reassuring and steady, do not "
            "minimise their concern, and keep the reply grounded and calm.",
    "disgust": "The sender seems put off by something. Acknowledge their reaction "
               "without judgment and keep the tone measured.",
    "surprise": "The sender seems surprised. Match their curiosity or shock, and ask "
                "a natural follow-up if it fits.",
    "neutral": "The sender's tone is neutral. Respond naturally and conversationally.",
}

SYSTEM_INSTRUCTION = """You are a thoughtful, emotionally intelligent person replying to a chat \
message on behalf of the user. Sound like a genuine, empathetic human: natural, concise, and \
appropriate for a casual chat app (not a formal essay).

Rules:
- Keep replies short and natural, the way a real person texts (usually 1-3 sentences).
- Never mention that you are an AI, a bot, or that you are analysing emotions.
- Never explain your reasoning inside the reply; just write the message.
- Avoid hashtags and excessive emojis (at most one, only if it fits naturally).
- Use the recent conversation only for context; do not repeat it back.
- Never agree to meet, send money, share personal details, or make promises on the \
user's behalf. If asked for any of these, reply warmly but noncommittally.
"""


class QuotaExhaustedError(Exception):
    """The daily free-tier quota is used up; retrying is pointless."""


class LLMUnavailableError(Exception):
    """The AI backend cannot be used (for example: no API key)."""


def format_history(chat_history: List[Dict[str, str]]) -> str:
    if not chat_history:
        return "(no earlier messages)"
    return "\n".join(f"{t.get('sender', 'them')}: {t.get('message', '')}" for t in chat_history)


def build_prompt(user_message: str, chat_history: List[Dict[str, str]], style_notes: str = "") -> str:
    guidance = "\n".join(f"- {name}: {text}" for name, text in EMOTION_GUIDANCE.items())
    style_block = (
        f"\nStyle notes from the user (follow these when writing the reply): {style_notes.strip()}\n"
        if style_notes and style_notes.strip() else ""
    )
    return f"""Recent conversation (most recent last; "me" is the user, "them" is the other person):
{format_history(chat_history)}

New incoming message from them: {json.dumps(user_message, ensure_ascii=False)}

Step 1 - Judge the sender's emotional state from the message itself. It may be English, Hindi, \
Gujarati, or mixed (for example Hinglish or Gujarati written in Roman letters). Choose exactly one \
of: {", ".join(EMOTIONS)}. Use "neutral" for ordinary everyday talk such as plans, schedules, \
logistics and simple questions. Do not invent strong emotions the message does not clearly show.

Step 2 - Write the next reply to send back. Reply in the SAME language and script the message is \
written in (if it is Roman-script Gujarati or Hinglish, reply the same way). Adapt your tone to \
the emotion you chose, using this guidance:
{guidance}
{style_block}
Respond with JSON only, in exactly this shape:
{{"emotion": "<one of the seven words>", "reply": "<the message to send>"}}"""


def parse_json_reply(text: str) -> Optional[Dict[str, str]]:
    """Parse Gemini's JSON answer, tolerating code fences and stray text."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        return None
    reply = str(data.get("reply", "")).strip()
    emotion = str(data.get("emotion", "neutral")).strip().lower()
    if not reply:
        return None
    if emotion not in EMOTION_GUIDANCE:
        emotion = "neutral"
    return {"emotion": emotion, "reply": reply}


class GeminiLLM:
    name = "gemini"

    def __init__(self, max_retries: int = 2):
        if not config.GEMINI_API_KEY:
            raise LLMUnavailableError(
                "GEMINI_API_KEY is empty. Add your key to the .env file and restart the app."
            )
        import google.generativeai as genai

        genai.configure(api_key=config.GEMINI_API_KEY)
        self._genai = genai
        self.model_name = config.GEMINI_MODEL_NAME
        self.max_retries = max_retries
        self._last_call = 0.0
        try:
            self._model = genai.GenerativeModel(
                model_name=self.model_name, system_instruction=SYSTEM_INSTRUCTION
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMUnavailableError(
                f"Could not initialise Gemini model '{self.model_name}': {exc}"
            ) from exc

    def _generate(self, prompt: str) -> Optional[str]:
        from google.api_core import exceptions as gexc

        gen_config = self._genai.types.GenerationConfig(
            temperature=0.8,
            # Generous: "thinking" models spend part of this budget on reasoning.
            max_output_tokens=2048,
            response_mime_type="application/json",
        )
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            wait = config.GEMINI_MIN_INTERVAL_SECONDS - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                response = self._model.generate_content(
                    prompt,
                    request_options={"timeout": config.GEMINI_TIMEOUT_SECONDS},
                    generation_config=gen_config,
                )
                text = (response.text or "").strip()
                finish = ""
                try:
                    finish = str(response.candidates[0].finish_reason)
                except Exception:  # noqa: BLE001
                    pass
                if text and ("MAX_TOKENS" in finish or finish.strip() == "2"):
                    logger.warning("Gemini reply was cut off; not using it.")
                elif text:
                    return text
            except gexc.ResourceExhausted as exc:
                last_error = exc
                match = re.search(r"retry in ([\d.]+)s", str(exc))
                delay = float(match.group(1)) + 1.0 if match else 15.0
                if "PerDay" in str(exc) or delay > 120:
                    raise QuotaExhaustedError(
                        f"Daily Gemini quota used up for model '{self.model_name}' "
                        f"(resets in about {delay / 3600:.1f} hours). Switch GEMINI_MODEL_NAME "
                        "to another model, wait, or enable billing."
                    ) from exc
                logger.warning("Gemini rate limit; waiting %.0fs (attempt %d).", delay, attempt + 1)
                time.sleep(delay)
            except gexc.DeadlineExceeded as exc:
                last_error = exc
                logger.warning("Gemini request timed out (attempt %d).", attempt + 1)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.error("Gemini call failed (attempt %d): %s", attempt + 1, exc)
            time.sleep(1)
        logger.error("All Gemini retries failed: %s", last_error)
        return None

    def analyze_and_reply(
        self,
        user_message: str,
        chat_history: Optional[List[Dict[str, str]]] = None,
        style_notes: str = "",
    ) -> Optional[Dict[str, str]]:
        prompt = build_prompt(user_message, chat_history or [], style_notes)
        for _ in range(2):  # one retry if the output cannot be parsed
            text = self._generate(prompt)
            if text is None:
                return None
            parsed = parse_json_reply(text)
            if parsed:
                return parsed
            logger.warning("Could not parse Gemini output: %r", text[:200])
        return None


class StubLLM:
    """Offline keyword demo. Not real AI."""

    name = "stub"
    model_name = "offline demo"

    KEYWORDS = {
        "joy": ["happy", "great", "awesome", "yay", "love", "maja", "khush", "congrat", "excited"],
        "sadness": ["sad", "cry", "miss you", "lonely", "dukhi", "upset", "depressed", "hurt"],
        "anger": ["angry", "hate", "furious", "gussa", "annoyed", "stupid", "fed up"],
        "fear": ["worried", "scared", "afraid", "tension", "nervous", "anxious", "exam", "darr"],
        "disgust": ["gross", "disgusting", "yuck", "ew "],
        "surprise": ["wow", "omg", "unexpected", "can't believe", "no way", "shocked"],
    }
    REPLIES = {
        "joy": "That's wonderful to hear! Tell me more.",
        "sadness": "I'm sorry you're going through this. I'm here if you want to talk.",
        "anger": "That sounds really frustrating. I get why you're upset.",
        "fear": "Try to breathe; you've got this. What's worrying you most?",
        "disgust": "Ugh, I can see why that bothered you.",
        "surprise": "Whoa, really? How did that happen?",
        "neutral": "Okay, got it. Thanks for letting me know.",
    }

    def analyze_and_reply(self, user_message, chat_history=None, style_notes=""):
        low = user_message.lower()
        emotion = "neutral"
        for name, words in self.KEYWORDS.items():
            if any(w in low for w in words):
                emotion = name
                break
        return {"emotion": emotion, "reply": self.REPLIES[emotion]}


def create_llm():
    if config.LLM_BACKEND == "stub":
        return StubLLM()
    return GeminiLLM()
