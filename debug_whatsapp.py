"""
Check what the WhatsApp driver can actually read.

    python debug_whatsapp.py

Opens WhatsApp Web (scan the QR code the first time), then every 3 seconds
prints the name of the open chat and its last messages with direction and kind.
Open a chat in the browser window and compare the output with what you see.
If it prints nothing or the wrong thing, WhatsApp changed its page structure:
the selectors at the top of app/drivers/whatsapp.py are the place to fix.

Press Ctrl+C to quit.
"""

import time

from app.drivers.whatsapp import WhatsAppDriver


def main() -> None:
    driver = WhatsAppDriver()
    driver.start()
    try:
        print("Waiting for WhatsApp Web to be ready (scan the QR code if shown)...")
        while not driver.is_ready():
            print("  ", driver.status_hint())
            time.sleep(2)
        print("Ready. Open a chat in the browser window.\n")
        while True:
            print(f"--- open chat: {driver.chat_title()!r}")
            for m in driver.read_messages(8):
                shown = m.text.replace("\n", " / ") if m.text else f"<{m.kind}>"
                print(f"  [{m.direction:>3}] {m.time:<22} {shown[:80]}")
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        driver.close()


if __name__ == "__main__":
    main()
