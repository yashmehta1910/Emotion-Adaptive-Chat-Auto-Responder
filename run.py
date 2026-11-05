"""
Start the Emotion Responder dashboard.

    python run.py

Then open http://127.0.0.1:8000 (a browser tab opens automatically).
"""

import logging
import threading
import webbrowser

import uvicorn

from app import config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    url = f"http://{config.HOST}:{config.PORT}"
    print(f"\n  Emotion Responder dashboard: {url}\n  Press Ctrl+C here to quit.\n")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.server:app", host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
