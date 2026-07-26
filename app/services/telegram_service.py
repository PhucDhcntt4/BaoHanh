import os
import re
from typing import Any

import requests


class TelegramService:
    def __init__(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")

        if not token:
            raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN trong file .env")

        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN không đúng định dạng "
                "(phải gồm bot_id:dãy_ký_tự_bí_mật)"
            )

        self.base_url = (
            f"https://api.telegram.org/bot{token}"
        )


    def send_message(
        self,
        chat_id: int | str,
        text: str,
    ) -> dict[str, Any]:

        response = requests.post(
            f"{self.base_url}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
            },
            timeout=30,
        )

        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram sendMessage failed: {result}"
            )
        return result

    def send_typing(
        self,
        chat_id: int | str,
    ) -> dict[str, Any]:

        response = requests.post(
            f"{self.base_url}/sendChatAction",
            json={
                "chat_id": chat_id,
                "action": "typing",
            },
            timeout=15,
        )

        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram sendChatAction failed: {result}"
            )

        return result
