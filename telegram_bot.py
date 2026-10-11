"""HelpLine Houston on Telegram: the same answers as the SMS line, with no carrier approval needed.

Run it next to the server (in its own terminal):
    python telegram_bot.py

It asks Telegram for new messages (long polling), so it needs no public URL or tunnel.
Set TELEGRAM_BOT_TOKEN in .env (get one from @BotFather in Telegram).
"""

import os
import pathlib
import time

import httpx
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).resolve().parent / ".env")

import main  # noqa: E402  (main reads .env too, so load it first)

API = "https://api.telegram.org/bot{token}/{method}"
POLL_SECONDS = 30      # how long one getUpdates call waits for new messages
RETRY_SECONDS = 5      # pause after a network error


def call(client, token, method, **params):
    """Call one Telegram Bot API method and return its result (raises on an error)."""
    response = client.post(API.format(token=token, method=method), json=params,
                           timeout=POLL_SECONDS + 10)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or "Telegram error")
    return data["result"]


def answer(message):
    """The reply text for one Telegram message, or None if there is nothing to answer."""
    chat_id = message["chat"]["id"]
    session_id = f"telegram:{chat_id}"   # like a phone number for SMS; never logged
    location = message.get("location")
    if location:
        # The person shared their location: use it instead of asking for a zip.
        result = main.handle_ask("sms", session_id, "", lat=location["latitude"],
                                 lng=location["longitude"])
        return result["reply"]
    text = message.get("text")
    if not text:
        return None
    if text.strip() == "/start":
        text = "hi"
    return main.handle_ask("sms", session_id, text)["reply"]


def run():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env first (get one from @BotFather).")
    offset = None
    with httpx.Client() as client:
        me = call(client, token, "getMe")
        print(f"HelpLine Houston is live on Telegram as @{me['username']}")
        while True:
            try:
                updates = call(client, token, "getUpdates", offset=offset, timeout=POLL_SECONDS,
                               allowed_updates=["message"])
            except Exception as problem:
                print(f"Telegram poll failed: {problem}")
                time.sleep(RETRY_SECONDS)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue
                reply = answer(message)
                if reply:
                    try:
                        call(client, token, "sendMessage", chat_id=message["chat"]["id"], text=reply)
                    except Exception as problem:
                        print(f"Telegram send failed: {problem}")


if __name__ == "__main__":
    run()
