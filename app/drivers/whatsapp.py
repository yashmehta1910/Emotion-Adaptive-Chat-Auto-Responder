"""
WhatsApp Web driver (Playwright).

Instead of reading pixels, this reads WhatsApp Web's page structure:
  * every message element carries a data-id that starts with "true_" (sent from
    this account) or "false_" (received) -> reliable sender detection and a
    unique id per message,
  * photos / voice notes / videos are recognised by their icons, so they can be
    skipped instead of being misread.

IMPORTANT: WhatsApp Web changes its page structure from time to time. All
selectors live in SELECTORS and JS_EXTRACT below, so a change is a small edit.
Run `python debug_whatsapp.py` to see exactly what the driver can read.

Automating WhatsApp Web is against WhatsApp's Terms of Service; use it only on
your own account, for testing, and accept the risk.

All Playwright calls must happen on ONE thread (the bot thread).
"""

import logging
import time
from typing import List, Optional

from .. import config
from .base import ChatDriver, ChatMessage, DriverError

logger = logging.getLogger(__name__)

WHATSAPP_URL = "https://web.whatsapp.com"

SELECTORS = {
    "chat_list": "#pane-side",                                  # present once logged in
    "qr": "canvas[aria-label*='QR'], div[data-ref]",            # login QR code
    "compose": "#main footer div[contenteditable='true']",      # message box of the open chat
}

JS_CHAT_TITLE = r"""
() => {
  const header = document.querySelector('#main header');
  if (!header) return '';
  const el = header.querySelector('span[dir="auto"][title]') || header.querySelector('span[dir="auto"]');
  if (!el) return '';
  return (el.getAttribute('title') || el.textContent || '').trim();
}
"""

JS_EXTRACT = r"""
(limit) => {
  const main = document.querySelector('#main');
  if (!main) return [];

  // Text of an element, keeping emoji (WhatsApp renders them as <img alt="...">).
  const textOf = (root) => {
    let out = '';
    const walk = (n) => {
      if (n.nodeType === 3) { out += n.data; return; }
      if (n.nodeType !== 1) return;
      if (n.tagName === 'IMG') { out += n.getAttribute('alt') || ''; return; }
      if (n.tagName === 'BR') { out += '\n'; return; }
      for (const c of n.childNodes) walk(c);
    };
    walk(root);
    return out.trim();
  };

  const nodes = Array.from(main.querySelectorAll('[data-id]'))
    .filter(n => /^(true|false)_/.test(n.getAttribute('data-id') || ''));
  const seen = new Set();
  const result = [];
  for (const n of nodes) {
    const id = n.getAttribute('data-id');
    if (seen.has(id)) continue;
    seen.add(id);
    const direction = id.startsWith('true_') ? 'out' : 'in';

    // Message text: skip quoted-reply previews and nested duplicates.
    const spans = Array.from(n.querySelectorAll('span.selectable-text')).filter(
      s => !s.closest('[aria-label*="uoted"]') && !(s.parentElement && s.parentElement.closest('span.selectable-text'))
    );
    const text = spans.map(textOf).filter(Boolean).join('\n');

    const metaEl = n.querySelector('[data-pre-plain-text]');
    const meta = metaEl ? (metaEl.getAttribute('data-pre-plain-text') || '') : '';
    const m = meta.match(/^\[([^\]]*)\]/);

    let kind = 'text';
    if (!text) {
      if (n.querySelector('[data-icon="audio-play"], [data-icon="ptt-status"], audio')) kind = 'audio';
      else if (n.querySelector('[data-icon="media-play"], video')) kind = 'video';
      else if (n.querySelector('[data-icon^="document"], [data-icon="doc"]')) kind = 'document';
      else if (n.querySelector('img[src^="blob:"], [data-icon="image"], img[src*="mmg.whatsapp.net"]')) kind = 'image';
      else kind = 'other';
    }
    result.push({ key: id, direction, text, kind, time: m ? m[1] : '' });
  }
  return result.slice(-limit);
}
"""


class WhatsAppDriver(ChatDriver):
    name = "whatsapp"

    def __init__(self) -> None:
        self._pw = None
        self._ctx = None
        self._page = None

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise DriverError(
                "Playwright is not installed. Run: pip install playwright  and  "
                "python -m playwright install chromium"
            ) from exc

        config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        kwargs = dict(
            user_data_dir=str(config.BROWSER_PROFILE_DIR),  # keeps you logged in between runs
            headless=config.HEADLESS,
            no_viewport=True,
        )
        if config.BROWSER_EXECUTABLE_PATH:
            kwargs["executable_path"] = config.BROWSER_EXECUTABLE_PATH
        elif config.BROWSER_CHANNEL:
            kwargs["channel"] = config.BROWSER_CHANNEL
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:  # noqa: BLE001
            self.close()
            raise DriverError(
                f"Could not open the browser: {exc}. If it says the browser is missing, run: "
                "python -m playwright install chromium"
            ) from exc
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._page.goto(WHATSAPP_URL, wait_until="domcontentloaded", timeout=60000)

    def is_alive(self) -> bool:
        try:
            return self._page is not None and not self._page.is_closed()
        except Exception:  # noqa: BLE001
            return False

    def is_ready(self) -> bool:
        try:
            return self._page.locator(SELECTORS["chat_list"]).count() > 0
        except Exception:  # noqa: BLE001
            return False

    def status_hint(self) -> str:
        try:
            if self._page.locator(SELECTORS["qr"]).count() > 0:
                return ("Scan the QR code in the browser window: WhatsApp on your phone > "
                        "Linked devices > Link a device.")
        except Exception:  # noqa: BLE001
            pass
        return "Loading WhatsApp Web..."

    def close(self) -> None:
        for closer in (
            lambda: self._ctx and self._ctx.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
        self._ctx = self._pw = self._page = None

    # --- reading -----------------------------------------------------------

    def chat_title(self) -> str:
        try:
            return self._page.evaluate(JS_CHAT_TITLE) or ""
        except Exception as exc:  # noqa: BLE001
            raise DriverError(f"Could not read the chat name: {exc}") from exc

    def read_messages(self, limit: int) -> List[ChatMessage]:
        try:
            raw = self._page.evaluate(JS_EXTRACT, int(limit))
        except Exception as exc:  # noqa: BLE001
            raise DriverError(f"Could not read messages: {exc}") from exc
        return [
            ChatMessage(
                key=item["key"], direction=item["direction"], text=item["text"],
                kind=item["kind"], time=item.get("time", ""),
            )
            for item in raw
        ]

    # --- sending -----------------------------------------------------------

    def send(self, text: str) -> Optional[str]:
        if not text.strip():
            raise DriverError("Refusing to send an empty message.")
        before = {m.key for m in self.read_messages(30)}
        try:
            box = self._page.locator(SELECTORS["compose"]).last
            box.click(timeout=5000)
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line:
                    self._page.keyboard.insert_text(line)
                if i < len(lines) - 1:
                    self._page.keyboard.press("Shift+Enter")
            self._page.keyboard.press("Enter")
        except Exception as exc:  # noqa: BLE001
            raise DriverError(f"Could not type into the message box: {exc}") from exc

        # Confirm WhatsApp really shows the new outgoing message.
        deadline = time.time() + 8
        while time.time() < deadline:
            for msg in self.read_messages(10):
                if msg.direction == "out" and msg.key not in before:
                    return msg.key
            time.sleep(0.4)
        raise DriverError(
            "The reply was typed but WhatsApp did not show it as sent. "
            "Check the browser window; the page layout may have changed."
        )
