"""
The bot engine: a background thread that watches the open chat and, for each
new incoming message, asks the AI to judge the emotion and write a reply, then
acts on it according to the selected mode:

    dry      write the reply and show it, but send nothing   ("Practice")
    approve  hold the reply until the user clicks Send         ("Ask me first")
    auto     send the reply after a short delay                ("Send automatically")

Settings are re-read on every check, so changes in the dashboard apply live.
All chat-driver calls happen on this one thread (required by Playwright).
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from . import database
from .drivers import ChatMessage, DriverError, make_driver
from .llm import LLMUnavailableError, QuotaExhaustedError, create_llm

logger = logging.getLogger(__name__)

_KIND_PHRASE = {
    "image": "photos", "video": "videos", "audio": "voice notes",
    "document": "documents", "other": "this kind of message",
}


class BotEngine:
    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._state: Dict[str, Any] = {
            "status": "stopped", "detail": "", "last_error": "", "chat_title": "",
            "driver": "", "llm": "", "started_at": None,
        }
        self._driver_kind = "mock"
        self._reset_runtime()

    # --- public API --------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        with self._state_lock:
            snap = dict(self._state)
        snap["running"] = bool(self._thread and self._thread.is_alive())
        return snap

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> Tuple[bool, str]:
        if self.is_running():
            return False, "The bot is already running."
        settings = database.get_settings()
        if (settings["driver"] == "whatsapp" and settings["mode"] in ("approve", "auto")
                and not settings["target_chat"].strip()):
            return False, ("Enter the name of the chat to watch before using 'Ask me first' "
                           "or 'Send automatically' with WhatsApp.")
        try:
            llm = create_llm()
        except LLMUnavailableError as exc:
            return False, str(exc)

        self._stop.clear()
        self._reset_runtime()
        self._driver_kind = settings["driver"]
        self._set(status="starting", detail="", last_error="", chat_title="",
                  driver=settings["driver"], llm=f"{llm.name} ({getattr(llm, 'model_name', '')})",
                  started_at=time.time())
        self._thread = threading.Thread(
            target=self._run, args=(llm, settings["driver"]), daemon=True, name="bot-engine"
        )
        self._thread.start()
        return True, "Started"

    def stop(self) -> None:
        if self.is_running():
            self._stop.set()
            self._set(status="stopping", detail="")

    # --- internals ---------------------------------------------------------

    def _reset_runtime(self) -> None:
        self._own_keys: set = set()
        self._own_texts: deque = deque(maxlen=30)
        self._baseline_done = False

    def _set(self, **fields: Any) -> None:
        with self._state_lock:
            self._state.update(fields)

    def _run(self, llm, driver_kind: str) -> None:
        driver = None
        final_status, final_detail = "stopped", ""
        try:
            driver = make_driver(driver_kind)
            self._set(status="starting",
                      detail="Opening WhatsApp Web..." if driver_kind == "whatsapp" else "")
            driver.start()

            while not self._stop.is_set():
                if not driver.is_alive():
                    raise DriverError("The browser window was closed.")
                if driver.is_ready():
                    break
                self._set(status="waiting_login", detail=driver.status_hint())
                self._stop.wait(1.0)

            if not self._stop.is_set():
                self._set(status="running", detail="")
                errors = 0
                while not self._stop.is_set():
                    settings = database.get_settings()
                    try:
                        if not driver.is_alive():
                            raise DriverError("The browser window was closed.")
                        self._tick(driver, llm, settings)
                        errors = 0
                    except QuotaExhaustedError as exc:
                        final_status, final_detail = "error", str(exc)
                        break
                    except DriverError as exc:
                        errors += 1
                        logger.warning("Driver problem (%d/5): %s", errors, exc)
                        self._set(last_error=str(exc))
                        if errors >= 5:
                            final_status, final_detail = "error", str(exc)
                            break
                    self._stop.wait(max(0.5, float(settings["poll_interval"])))
        except Exception as exc:  # noqa: BLE001
            logger.exception("The bot stopped because of an unexpected error")
            final_status, final_detail = "error", str(exc)
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception:  # noqa: BLE001
                    pass
            self._set(status=final_status, detail=final_detail, chat_title="")
            if final_detail:
                self._set(last_error=final_detail)

    def _tick(self, driver, llm, s: Dict[str, Any]) -> None:
        chat = driver.chat_title()
        self._set(chat_title=chat)

        # Replies the user approved in the dashboard.
        for row in database.list_by_status("approved"):
            self._send_approved(driver, row, chat)

        if not chat:
            self._set(detail="Open the chat you want the bot to watch in the browser window.")
            return
        target = s["target_chat"].strip()
        if target and target.lower() not in chat.lower():
            self._set(detail=f"Waiting: the open chat is '{chat}', not '{target}'.")
            return
        self._set(detail="")

        first_check = not self._baseline_done
        self._baseline_done = True

        msgs = driver.read_messages(int(s["history_turns"]) + 2)
        if not msgs:
            return
        last = msgs[-1]

        if last.direction == "out":
            # Only react to your own messages in "Message yourself" test mode,
            # and never to messages this bot sent.
            if not s["self_chat_test"]:
                return
            if last.key in self._own_keys or last.text.strip() in self._own_texts:
                return

        if database.has_key(chat, last.key):
            return

        if first_check and not s["reply_to_existing"]:
            database.add_message(
                chat=chat, msg_key=last.key, incoming_text=last.text, kind=last.kind,
                status="skipped", error="Already in the chat when the bot started.",
            )
            return

        self._handle(driver, llm, s, chat, last, msgs[:-1])

    def _handle(self, driver, llm, s, chat: str, msg: ChatMessage, earlier: List[ChatMessage]) -> None:
        # Photos, voice notes, etc.: the bot cannot read them.
        if msg.kind != "text" or not msg.text.strip():
            ack = s["ack_text"].strip()
            if s["non_text_policy"] == "ack" and ack:
                row_id = database.add_message(
                    chat=chat, msg_key=msg.key, incoming_text="", kind=msg.kind,
                    emotion="neutral", reply_text=ack, status="thinking",
                )
                self._dispatch(driver, s, row_id, chat, ack)
            else:
                phrase = _KIND_PHRASE.get(msg.kind, "this kind of message")
                database.add_message(
                    chat=chat, msg_key=msg.key, incoming_text="", kind=msg.kind, status="skipped",
                    error=f"The bot can't read {phrase}, so it skipped this one.",
                )
            return

        history = [
            {"sender": "me" if m.direction == "out" else "them", "message": m.text}
            for m in earlier
            if m.kind == "text" and m.text.strip()
        ][-int(s["history_turns"]):] if int(s["history_turns"]) > 0 else []

        row_id = database.add_message(
            chat=chat, msg_key=msg.key, incoming_text=msg.text, kind="text", status="thinking"
        )
        started = time.time()
        try:
            result = llm.analyze_and_reply(msg.text, history, s["style_notes"])
        except QuotaExhaustedError as exc:
            database.update_message(row_id, status="failed", error=str(exc))
            raise
        if not result:
            database.update_message(
                row_id, status="failed",
                error="The AI did not return a reply. See the terminal for details.",
            )
            return
        database.update_message(
            row_id, emotion=result["emotion"], reply_text=result["reply"],
            latency_ms=int((time.time() - started) * 1000),
        )
        self._dispatch(driver, s, row_id, chat, result["reply"])

    def _dispatch(self, driver, s: Dict[str, Any], row_id: int, chat: str, reply: str) -> None:
        mode = s["mode"]
        if self._driver_kind == "whatsapp" and mode != "dry" and not s["target_chat"].strip():
            mode = "dry"  # safety: never send on WhatsApp without a named target chat
            self._set(detail="Practice only: enter the chat name to watch to allow sending.")
        database.update_message(row_id, mode=mode)

        if mode == "dry":
            database.update_message(row_id, status="dry_run")
        elif mode == "approve":
            database.update_message(row_id, status="pending")
        else:
            if self._stop.wait(max(0.0, float(s["reply_delay"]))):
                database.update_message(row_id, status="discarded", error="Stopped before sending.")
                return
            self._send(driver, row_id, reply)

    def _send_approved(self, driver, row: Dict[str, Any], chat: str) -> None:
        if row["chat"] != chat:
            database.update_message(
                row["id"], status="failed",
                error="You switched chats before this reply was sent, so it was not sent.",
            )
            return
        self._send(driver, row["id"], row["reply_text"] or "")

    def _send(self, driver, row_id: int, text: str) -> None:
        try:
            key = driver.send(text)
        except DriverError as exc:
            logger.error("Sending failed: %s", exc)
            database.update_message(row_id, status="failed", error=str(exc))
            return
        self._own_texts.append(text.strip())
        if key:
            self._own_keys.add(key)
        database.update_message(row_id, status="sent")


engine = BotEngine()
