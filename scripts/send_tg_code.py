"""Send Telegram auth code to a phone number.
Run from repo root:  pip install telethon  &&  python scripts/send_tg_code.py
Uses .env in repo root for TELEGRAM_API_ID and TELEGRAM_API_HASH.
"""
from __future__ import annotations

import asyncio
import os
import sys

# Project root = parent of scripts/
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_SCRIPT_DIR, "..")
_ENV_PATH = os.path.join(_ROOT, ".env")


def _load_dotenv() -> None:
    if not os.path.isfile(_ENV_PATH):
        return
    with open(_ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


async def main() -> None:
    _load_dotenv()
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        print("ERROR: Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
        sys.exit(1)
    api_id = int(api_id)

    phone = "+79136712134"
    print(f"Sending code request to {phone}...")

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        sent_code = await client.send_code_request(phone)
        type_name = getattr(getattr(sent_code, "type", None), "__class__", None)
        type_name = type_name.__name__ if type_name else "?"
        print(f"OK. Code sent via: {type_name}")
        print("Check Telegram app (notification or login request).")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
