"""
Checks the WhatsApp page-reading JavaScript against a SYNTHETIC page that mimics
WhatsApp Web's structure (data-id prefixes, selectable-text spans, icons).

    python -m tests.test_whatsapp_extraction

This proves the extraction logic is sound for that structure. It does NOT prove
WhatsApp's live page still has that structure; use `python debug_whatsapp.py`
for that.
"""

import os
import sys

from app.drivers.whatsapp import JS_CHAT_TITLE, JS_EXTRACT

FIXTURE = """
<!doctype html><html><body>
<div id="pane-side">chat list</div>
<div id="main">
  <header><div><span dir="auto" title="Test Friend">Test Friend</span>
    <span dir="auto">last seen today</span></div></header>
  <div role="application">

    <div role="row"><div>Today</div></div>

    <div role="row"><div data-id="false_111@c.us_A1" class="message-in">
      <div class="copyable-text" data-pre-plain-text="[8:10 PM, 10/26/2026] Test Friend: ">
        <span dir="ltr" class="selectable-text copyable-text"><span>kal exam che, bahu tension che</span></span>
      </div></div></div>

    <div role="row"><div data-id="true_111@c.us_B2" class="message-out">
      <div class="copyable-text" data-pre-plain-text="[8:11 PM, 10/26/2026] Me: ">
        <span dir="ltr" class="selectable-text copyable-text"><span>Tension na rakh <img alt="😊" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></span></span>
      </div></div></div>

    <div role="row"><div data-id="false_111@c.us_C3" class="message-in">
      <div><img src="blob:https://web.whatsapp.com/abc-123"></div></div></div>

    <div role="row"><div data-id="false_111@c.us_D4" class="message-in">
      <div><span data-icon="audio-play"></span></div></div></div>

    <div role="row"><div data-id="false_111@c.us_E5" class="message-in">
      <div aria-label="Quoted message"><span class="selectable-text"><span>old quoted text</span></span></div>
      <div class="copyable-text" data-pre-plain-text="[8:15 PM, 10/26/2026] Test Friend: ">
        <span dir="ltr" class="selectable-text copyable-text"><span>line one</span><br><span>line two</span></span>
      </div></div></div>

    <div role="row"><div data-id="false_111@c.us_F6" class="message-in">
      <div><span data-icon="media-play"></span></div></div></div>

    <div role="row"><div data-id="false_111@c.us_G7" class="message-in">
      <div data-id="false_111@c.us_G7">
      <div class="copyable-text" data-pre-plain-text="[8:20 PM, 10/26/2026] Test Friend: ">
        <span class="selectable-text copyable-text"><span>duplicate id test</span></span>
      </div></div></div></div>

  </div>
  <footer><div contenteditable="true" data-tab="10"></div></footer>
</div>
</body></html>
"""


def main() -> None:
    from playwright.sync_api import sync_playwright

    exe = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
    with sync_playwright() as p:
        kwargs = {"executable_path": exe} if os.path.exists(exe) else {}
        browser = p.chromium.launch(**kwargs)
        page = browser.new_page()
        page.set_content(FIXTURE)

        title = page.evaluate(JS_CHAT_TITLE)
        assert title == "Test Friend", title

        msgs = page.evaluate(JS_EXTRACT, 50)
        by_key = {m["key"]: m for m in msgs}

        assert len(msgs) == 7, [m["key"] for m in msgs]
        assert by_key["false_111@c.us_A1"] == {
            "key": "false_111@c.us_A1", "direction": "in", "kind": "text",
            "text": "kal exam che, bahu tension che", "time": "8:10 PM, 10/26/2026",
        }, by_key["false_111@c.us_A1"]
        assert by_key["true_111@c.us_B2"]["direction"] == "out"
        assert by_key["true_111@c.us_B2"]["text"] == "Tension na rakh 😊"
        assert by_key["false_111@c.us_C3"]["kind"] == "image"
        assert by_key["false_111@c.us_D4"]["kind"] == "audio"
        assert by_key["false_111@c.us_E5"]["text"] == "line one\nline two", by_key["false_111@c.us_E5"]
        assert by_key["false_111@c.us_F6"]["kind"] == "video"
        assert by_key["false_111@c.us_G7"]["text"] == "duplicate id test"
        assert [m["key"] for m in page.evaluate(JS_EXTRACT, 2)] == [
            "false_111@c.us_F6", "false_111@c.us_G7"
        ]
        browser.close()
    print("ALL EXTRACTION TESTS PASSED (synthetic WhatsApp-like page)")


if __name__ == "__main__":
    main()
    sys.exit(0)
