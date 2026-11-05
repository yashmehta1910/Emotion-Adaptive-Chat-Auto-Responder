"""
Web server: JSON API for the dashboard + the dashboard's static files.

Listens on 127.0.0.1 only. There is no login, because it is meant to run on
your own computer; do not expose it to a network.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, database
from .drivers import mock as demo_chat
from .engine import engine

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init()
    yield
    engine.stop()


app = FastAPI(title="Emotion Responder", lifespan=lifespan)


# --- request models --------------------------------------------------------

class SettingsIn(BaseModel):
    mode: Optional[Literal["dry", "approve", "auto"]] = None
    driver: Optional[Literal["mock", "whatsapp"]] = None
    target_chat: Optional[str] = Field(None, max_length=200)
    style_notes: Optional[str] = Field(None, max_length=1000)
    poll_interval: Optional[float] = Field(None, ge=1, le=60)
    reply_delay: Optional[float] = Field(None, ge=0, le=60)
    history_turns: Optional[int] = Field(None, ge=0, le=20)
    non_text_policy: Optional[Literal["skip", "ack"]] = None
    ack_text: Optional[str] = Field(None, max_length=300)
    self_chat_test: Optional[bool] = None
    reply_to_existing: Optional[bool] = None


class ApproveIn(BaseModel):
    text: Optional[str] = Field(None, max_length=2000)


class DemoIncomingIn(BaseModel):
    text: str = Field("", max_length=1000)
    kind: Literal["text", "image"] = "text"


# --- API -------------------------------------------------------------------

@app.get("/api/state")
def get_state():
    settings = database.get_settings()
    if config.LLM_BACKEND == "stub":
        llm = {"backend": "stub", "label": "Offline demo (keyword rules, not real AI)", "ready": True}
    else:
        llm = {
            "backend": "gemini",
            "label": f"Gemini ({config.GEMINI_MODEL_NAME})",
            "ready": bool(config.GEMINI_API_KEY),
        }
    return {
        "engine": engine.snapshot(),
        "settings": settings,
        "stats": database.stats(),
        "llm": llm,
    }


@app.get("/api/messages")
def get_messages(limit: int = 150):
    return database.list_messages(max(1, min(limit, 500)))


@app.post("/api/start")
def start_bot():
    ok, message = engine.start()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True}


@app.post("/api/stop")
def stop_bot():
    engine.stop()
    return {"ok": True}


@app.post("/api/settings")
def save_settings(body: SettingsIn):
    partial = body.model_dump(exclude_none=True)
    if "driver" in partial and engine.is_running():
        raise HTTPException(status_code=409, detail="Stop the bot before changing where it runs.")
    if "target_chat" in partial:
        partial["target_chat"] = partial["target_chat"].strip()
    return database.update_settings(partial)


@app.post("/api/messages/{message_id}/approve")
def approve_reply(message_id: int, body: ApproveIn):
    row = database.get_message(message_id)
    if not row:
        raise HTTPException(status_code=404, detail="Message not found.")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="This reply is no longer waiting for approval.")
    if not engine.is_running():
        raise HTTPException(status_code=409, detail="Start the bot first, then send the reply.")
    text = (body.text if body.text is not None else row["reply_text"] or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="The reply is empty.")
    database.update_message(message_id, reply_text=text, status="approved")
    return {"ok": True}


@app.post("/api/messages/{message_id}/discard")
def discard_reply(message_id: int):
    row = database.get_message(message_id)
    if not row:
        raise HTTPException(status_code=404, detail="Message not found.")
    if row["status"] not in ("pending", "dry_run"):
        raise HTTPException(status_code=409, detail="This reply can no longer be discarded.")
    database.update_message(message_id, status="discarded")
    return {"ok": True}


@app.post("/api/clear")
def clear_history():
    database.clear_messages()
    return {"ok": True}


# --- demo chat (only meaningful when "Demo chat" is the selected driver) ----

@app.get("/api/demo/chat")
def demo_transcript():
    return demo_chat.transcript()


@app.post("/api/demo/incoming")
def demo_incoming(body: DemoIncomingIn):
    if body.kind == "text" and not body.text.strip():
        raise HTTPException(status_code=400, detail="Type a message first.")
    demo_chat.add_incoming(body.text.strip(), body.kind)
    return {"ok": True}


@app.post("/api/demo/reset")
def demo_reset():
    demo_chat.reset()
    return {"ok": True}


# --- dashboard -------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})
