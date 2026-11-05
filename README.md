# Emotion-Adaptive Chat Auto-Responder

A chat auto-responder with a web dashboard. It watches a chat, works out how the
other person is feeling, and writes a reply in the **same language, script and tone**.
You decide whether it only practises, asks you first, or sends on its own.

![Dashboard](docs/dashboard.png)

## Features

- **Multilingual emotion detection.** Gemini judges one of seven emotions (joy, sadness,
  anger, fear, disgust, surprise, neutral) and writes the reply in the message's own
  language, including Hinglish and Gujarati typed in English letters.
- **Three safety modes.** *Practice* (nothing is sent), *Ask me first* (edit and approve
  each reply), *Send automatically*.
- **No screen coordinates.** The WhatsApp driver reads WhatsApp Web's page structure
  with Playwright, so it knows who sent each message, skips photos and voice notes, and
  never answers its own replies.
- **Live dashboard.** Mood strip, conversation log, approval area, emotion mix and
  settings, in a pastel theme with light and dark modes.
- **Built-in demo chat**, so the whole system can be shown without WhatsApp.
- **Robust by design.** Rate-limit spacing, retries, a clean stop when the daily quota
  runs out, a target-chat guard, and an offline stub AI for testing.

## How it works

```
 Dashboard (browser)  <-- JSON API -->  FastAPI server  --  SQLite (messages, settings)
 app/static/*                           app/server.py             data/app.db
                                              |
                                        Bot engine (thread)  app/engine.py
                                        /               \
                          Chat driver                     AI (Gemini)
                 app/drivers/whatsapp.py (Playwright)     app/llm.py
                 app/drivers/mock.py (demo chat)
```

For each new incoming message the engine makes **one** Gemini call that returns the
emotion and the reply as JSON, then acts on it according to the selected mode.

## Tech stack

Python 3 · FastAPI · Uvicorn · Playwright · Google Gemini API · SQLite · HTML/CSS/JavaScript

## Getting started

Requires Python 3.10+ (developed on 3.13) and a free
[Gemini API key](https://aistudio.google.com/app/apikey).

```bash
git clone <your-repo-url>
cd emotion_auto_responder_web

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
python -m playwright install chromium

copy env.example .env          # Windows  (macOS / Linux: cp env.example .env)
# open .env and set GEMINI_API_KEY

python run.py
```

The dashboard opens at <http://127.0.0.1:8000>.

No API key yet? Set `LLM_BACKEND=stub` in `.env` to try the dashboard with simple
offline keyword rules (the dashboard labels it as not real AI).

If Gemini returns a 404 or quota error, run `python list_models.py` and set
`GEMINI_MODEL_NAME` in `.env` to a model your key can use.

## Usage

**Demo chat**
1. Click **Start bot**.
2. In **Try it**, type a message as your friend and press Send.
3. Watch the emotion tag, the mood strip and the drafted reply appear.
4. Switch to **Ask me first** to edit, send or discard replies.

**WhatsApp Web**
1. Set **Where it runs** to *WhatsApp Web* and enter the exact chat name in **Chat to watch**.
2. Keep the mode on **Practice** and click **Start bot**.
3. Scan the QR code in the browser window that opens (first time only; the login is kept
   in `data/browser_profile`).
4. Open the named chat in that window and check the drafted replies.
5. Move to **Ask me first**, and later **Send automatically**, when you trust it.

To test in "Message yourself", tick *Treat my own messages as incoming* under More options.
Run `python debug_whatsapp.py` to see what the driver can read from the open chat.

## Settings

| Setting | What it does |
|---|---|
| Practice / Ask me first / Send automatically | What happens to each written reply. Changes apply immediately. |
| Where it runs | Demo chat or WhatsApp Web. Change it while the bot is stopped. |
| Chat to watch | The bot acts only while a chat with this name is open. Required to send on WhatsApp. |
| How you text | Style notes for the AI, e.g. "short, warm, mix Hinglish and English". |
| Check every / Wait before sending | Polling interval and the pause before an automatic send. |
| Photos and voice notes | Skip them, or reply with a fixed message. |
| Also answer the message already there | Off by default: the bot ignores what was in the chat at start. |

## Project structure

```
run.py                     start the dashboard
debug_whatsapp.py          show what the WhatsApp driver can read
list_models.py             list the Gemini models your key can use
env.example                copy to .env
requirements.txt
app/
  server.py                FastAPI routes + static files
  engine.py                the bot loop and the three reply modes
  llm.py                   Gemini call (emotion + reply in one request), offline stub
  database.py              SQLite: message log and settings
  config.py                values from .env
  drivers/
    whatsapp.py            WhatsApp Web via Playwright
    mock.py                the built-in demo chat
    base.py                the interface drivers implement
  static/                  index.html, style.css, app.js, favicon.svg
tests/
  test_pipeline.py             engine end to end, offline
  test_whatsapp_extraction.py  page-reading code against a WhatsApp-like page
docs/
  dashboard.png
```

## Tests

```bash
python -m tests.test_pipeline
python -m tests.test_whatsapp_extraction
```

They run offline with the stub AI: startup skipping, all three modes, photos, the
chat-name guard, restart, and "Message yourself" mode without reply loops, plus the
page-reading code against a synthetic WhatsApp-like page.

**Not covered:** the live WhatsApp Web site. WhatsApp can change its page at any time. If
nothing is read, run `python debug_whatsapp.py` and adjust `SELECTORS` and `JS_EXTRACT`
in `app/drivers/whatsapp.py`.

## Limits and responsible use

- **WhatsApp's terms.** Automating WhatsApp Web is against its Terms of Service and can
  get an account restricted. Use your own account, for testing only.
- **Disclosure.** Replies come from an AI; consider telling the people you chat with.
- **Privacy.** With Gemini, message text and recent history are sent to Google. The
  dashboard listens on your computer only and has no login: do not expose it to a network.
- **Not understood:** photos, stickers and voice notes (skipped or answered with a fixed message).
- **One chat at a time.** Do not switch chats while the bot runs.
- **Quota.** The free tier has per-minute and per-day limits; the bot stops cleanly when they run out.
- **Noncommittal replies.** The AI is told never to agree to meet, send money or share
  personal details for you.

## Troubleshooting

| Problem | Fix |
|---|---|
| "No Gemini API key found" | Put `GEMINI_API_KEY` in `.env` and restart `run.py`. |
| 404 for the model | Set `GEMINI_MODEL_NAME` to another model your key can use. |
| Daily quota message | Use another model, wait for the reset, or enable billing. |
| "Could not open the browser" | Run `python -m playwright install chromium`. |
| Stuck on "Waiting for WhatsApp login" | Scan the QR code in the browser window the bot opened. |
| "Waiting: the open chat is X, not Y" | Open the chat you named, or fix the name in settings. |
| Nothing is read from WhatsApp | Run `python debug_whatsapp.py`. |
| Port already in use | Change `PORT` in `.env`. |

## Author

Yash Kumar Mehta, MCA, Chandigarh University.

## License

Add a license of your choice (for example MIT) before publishing.
