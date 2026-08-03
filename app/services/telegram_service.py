import logging
import os
import re
import time
from typing import Any

import requests


logger = logging.getLogger("uvicorn.error")


class TelegramService:
    @staticmethod
    def _plain_text(text: str) -> str:
        """Loại bỏ Markdown cơ bản vì Telegram đang gửi plain text."""

        cleaned = re.sub(
            r"\*\*(.+?)\*\*",
            r"\1",
            str(text),
            flags=re.DOTALL,
        )
        cleaned = re.sub(
            r"__(.+?)__",
            r"\1",
            cleaned,
            flags=re.DOTALL,
        )
        cleaned = re.sub(
            r"(?m)^\s*\*\s+",
            "• ",
            cleaned,
        )
        return cleaned

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

        self.file_base_url = (
            f"https://api.telegram.org/file/bot{token}"
        )

    def send_message(
        self,
        chat_id: int | str,
        text: str,
    ) -> dict[str, Any]:
        text = self._plain_text(text)
        started_at = time.perf_counter()
        try:
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

            logger.info(
                "TELEGRAM SENT type=message chat_id=%s "
                "success=true time=%.3fs",
                chat_id,
                time.perf_counter() - started_at,
            )
            return result
        except Exception:
            logger.exception(
                "TELEGRAM SENT type=message chat_id=%s "
                "success=false time=%.3fs",
                chat_id,
                time.perf_counter() - started_at,
            )
            raise

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

    def download_file(
        self,
        file_id: str,
    ) -> tuple[bytes, str]:

        response = requests.get(
            f"{self.base_url}/getFile",
            params={"file_id": file_id},
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            raise RuntimeError("Không lấy được hình ảnh")

        file_path = result["result"]["file_path"]

        file_response = requests.get(
            f"{self.file_base_url}/{file_path}",
            timeout=30,
        )

        file_response.raise_for_status()

        if len(file_response.content) > 20 * 1024 * 1024:
            raise RuntimeError("Ảnh vượt quá giới hạn 20 MB")

        return file_response.content, file_path

    def send_photo(
        self,
        chat_id: int | str,
        photo_url: str,
        caption: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "photo": photo_url,
        }

        if caption:
            payload["caption"] = caption[:1024]

        started_at = time.perf_counter()
        response = requests.post(
            f"{self.base_url}/sendPhoto",
            json=payload,
            timeout=30,
        )

        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram sendPhoto failed: {result}"
            )

        logger.info(
            "TELEGRAM SENT type=photo chat_id=%s "
            "success=true time=%.3fs",
            chat_id,
            time.perf_counter() - started_at,
        )
        return result

    def send_media_group(
        self,
        chat_id: int | str,
        photo_urls: list[str],
    ) -> list[dict[str, Any]]:
        unique_urls = list (
            dict.fromkeys(
                url for url in photo_urls if url
            )
        )

        if len(unique_urls) < 2:
            if unique_urls:
                result = self.send_photo(
                    chat_id=chat_id,
                    photo_url=unique_urls[0],
                )
                return [result]
            return []

        media = [
            {
                "type": "photo",
                "media": photo_url,
            }

            for photo_url in unique_urls[:10]
        ]

        started_at = time.perf_counter()
        response = requests.post(
            f"{self.base_url}/sendMediaGroup",
            json={
                "chat_id": chat_id,
                "media": media,
            },
            timeout=60,
        )

        try:
            result = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Telegram sendMediaGroup returned invalid JSON"
            ) from exc

        if not response.ok or not result.get("ok"):
            raise RuntimeError(
                "Telegram sendMediaGroup failed: "
                f"{result.get('description', 'unknown error')}"
            )

        logger.info(
            "TELEGRAM SENT type=album chat_id=%s images=%s "
            "success=true time=%.3fs",
            chat_id,
            len(media),
            time.perf_counter() - started_at,
        )
        return result.get("result", [])
