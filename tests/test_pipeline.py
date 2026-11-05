"""
Offline end-to-end test: demo chat + offline stub AI (no API key, no internet).

    python -m tests.test_pipeline
"""

import os
import shutil
import sys
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="emotion_test_")
os.environ["DATA_DIR"] = _tmp
os.environ["LLM_BACKEND"] = "stub"
os.environ["DRIVER"] = "mock"

from app import database  # noqa: E402
from app.drivers import mock as demo  # noqa: E402
from app.engine import BotEngine  # noqa: E402


def wait_for(predicate, timeout=15.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("Timed out waiting for condition")


def rows():
    return database.list_messages(500)


def row_for(text):
    for r in rows():
        if r["incoming_text"] == text:
            return r
    return None


def main() -> None:
    database.init()
    demo.reset()
    database.update_settings({"mode": "auto", "poll_interval": 1.0, "reply_delay": 0.0})
    # poll_interval is clamped to >=0.5 inside the engine; 1s keeps the test quick.

    engine = BotEngine()

    # --- 1. a message that is already there at start is NOT answered ------
    demo.add_incoming("old message from before the bot started")
    ok, msg = engine.start()
    assert ok, msg
    wait_for(lambda: engine.snapshot()["status"] == "running")
    wait_for(lambda: row_for("old message from before the bot started"))
    assert row_for("old message from before the bot started")["status"] == "skipped"
    print("ok 1: existing message skipped at startup")

    # --- 2. auto mode: reply is sent, emotion detected --------------------
    demo.add_incoming("aaje bahu maja aavi, so happy")
    r = wait_for(lambda: (x := row_for("aaje bahu maja aavi, so happy")) and x["status"] == "sent" and x)
    assert r["emotion"] == "joy", r
    assert any(m["direction"] == "out" for m in demo.transcript())
    print("ok 2: auto mode sent a reply, emotion =", r["emotion"])

    # --- 3. the bot does not answer its own reply -------------------------
    n_before = len(rows())
    time.sleep(3.5)
    assert len(rows()) == n_before, "bot reacted to its own reply"
    print("ok 3: no loop on its own reply")

    # --- 4. a photo is skipped, not misread --------------------------------
    demo.add_incoming("", kind="image")
    wait_for(lambda: any(x["kind"] == "image" and x["status"] == "skipped" for x in rows()))
    print("ok 4: photo skipped")

    # --- 5. approve mode holds the reply until approved --------------------
    database.update_settings({"mode": "approve"})
    demo.add_incoming("I am so worried about my exam tomorrow")
    r = wait_for(lambda: (x := row_for("I am so worried about my exam tomorrow")) and x["status"] == "pending" and x)
    assert r["emotion"] == "fear"
    outs_before = sum(1 for m in demo.transcript() if m["direction"] == "out")
    time.sleep(2.0)
    assert sum(1 for m in demo.transcript() if m["direction"] == "out") == outs_before
    database.update_message(r["id"], reply_text="Edited reply from me", status="approved")
    wait_for(lambda: database.get_message(r["id"])["status"] == "sent")
    assert demo.transcript()[-1]["text"] == "Edited reply from me"
    print("ok 5: approve mode: held, edited, then sent")

    # --- 6. practice mode writes a draft but sends nothing -----------------
    database.update_settings({"mode": "dry"})
    demo.add_incoming("This is so annoying, I hate it")
    r = wait_for(lambda: (x := row_for("This is so annoying, I hate it")) and x["status"] == "dry_run" and x)
    assert r["emotion"] == "anger"
    assert demo.transcript()[-1]["direction"] == "in"
    print("ok 6: practice mode drafted without sending")

    # --- 7. wrong target chat: nothing happens -----------------------------
    database.update_settings({"mode": "auto", "target_chat": "Somebody Else"})
    demo.add_incoming("hello are you there")
    time.sleep(3.0)
    assert row_for("hello are you there") is None
    assert "Somebody Else" in engine.snapshot()["detail"]
    database.update_settings({"target_chat": ""})
    wait_for(lambda: (x := row_for("hello are you there")) and x["status"] == "sent")
    print("ok 7: target-chat guard works")

    # --- 8. stop, then restart: messages that arrived while stopped are skipped
    engine.stop()
    wait_for(lambda: not engine.is_running())
    assert engine.snapshot()["status"] == "stopped"
    demo.add_incoming("sent while the bot was off")
    ok, msg = engine.start()
    assert ok, msg
    wait_for(lambda: (x := row_for("sent while the bot was off")) and x["status"] == "skipped")
    engine.stop()
    wait_for(lambda: not engine.is_running())
    print("ok 8: stop/restart skips what arrived while stopped")

    # --- 9. self-chat test mode: own message treated as incoming, bot reply ignored
    demo.reset()
    database.clear_messages()
    database.update_settings({"mode": "auto", "self_chat_test": True})
    ok, msg = engine.start()
    assert ok, msg
    wait_for(lambda: engine.snapshot()["status"] == "running")
    time.sleep(1.5)
    demo.add_message("out", "I am so happy today")  # typed by the user into "Message yourself"
    wait_for(lambda: (x := row_for("I am so happy today")) and x["status"] == "sent")
    n = len(rows())
    time.sleep(3.5)
    assert len(rows()) == n, "self-chat mode looped on the bot's own reply"
    engine.stop()
    wait_for(lambda: not engine.is_running())
    print("ok 9: self-chat test mode works without looping")

    print("\nALL PIPELINE TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    finally:
        database.close()
        shutil.rmtree(_tmp, ignore_errors=True)
    sys.exit(0)
