import logging
import os
import threading
from collections import deque
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
)

from app.services.gemini_service import GeminiService
from app.services.telegram_service import TelegramService


router = APIRouter(
    prefix="/api/telegram",
    tags=["Telegram"],
)

logger = logging.getLogger(__name__)

gemini_service: GeminiService | None = None
telegram_service: TelegramService | None = None

_state_lock = threading.Lock()
_histories: dict[int, deque[dict[str, str]]] = {}
_processed_updates: deque[int] = deque(maxlen=1000)
_processed_update_set: set[int] = set()


def configure_telegram(
    warranty_agent: GeminiService,
) -> None:
    global gemini_service, telegram_service
    gemini_service = warranty_agent
    telegram_service = TelegramService()


def telegram_ready() -> bool:
    return (
        gemini_service is not None
        and telegram_service is not None
    )


def _remember_update(update_id: Any) -> bool:
    if not isinstance(update_id, int):
        return True

    with _state_lock:
        if update_id in _processed_update_set:
            return False

        if len(_processed_updates) == _processed_updates.maxlen:
            oldest = _processed_updates[0]
            _processed_update_set.discard(oldest)

        _processed_updates.append(update_id)
        _processed_update_set.add(update_id)
        return True


def _get_history(chat_id: int) -> list[dict[str, str]]:
    with _state_lock:
        return list(_histories.get(chat_id, ()))


def _append_history(
    chat_id: int,
    user_text: str,
    model_text: str,
) -> None:
    with _state_lock:
        history = _histories.setdefault(
            chat_id,
            deque(maxlen=10),
        )
        history.append({"role": "user", "text": user_text})
        history.append({"role": "model", "text": model_text})


def extract_message(
    payload: dict[str, Any],
) -> tuple[int | None, str | None]:

    message = payload.get("message")

    if not isinstance(message, dict):
        return None, None

    chat = message.get("chat") or {}
    text = message.get("text")

    chat_id = chat.get("id")

    if chat_id is None or not text:
        return None, None

    return int(chat_id), str(text).strip()


def process_message(
    chat_id: int,
    text: str,
) -> None:
    try:
        if not telegram_ready():
            raise RuntimeError("Telegram service chưa sẵn sàng")

        assert telegram_service is not None
        assert gemini_service is not None

        telegram_service.send_typing(chat_id)

        result = gemini_service.chat(
            message=text,
            customer_id=f"telegram:{chat_id}",
            history=_get_history(chat_id),
        )

        reply = result.get("reply")

        if not reply:
            reply = (
                "Dạ hiện tại em chưa thể xử lý yêu cầu. "
                "Anh/chị vui lòng thử lại sau ạ."
            )

        telegram_service.send_message(
            chat_id=chat_id,
            text=reply,
        )
        _append_history(chat_id, text, reply)

    except Exception:
        logger.exception(
            "TELEGRAM MESSAGE ERROR chat_id=%s",
            chat_id,
        )

        try:
            telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    "Dạ hệ thống đang gặp chút gián đoạn. "
                    "Anh/chị vui lòng thử lại sau ít phút ạ."
                ),
            )
        except Exception:
            logger.exception(
                "Không thể gửi thông báo lỗi về Telegram"
            )


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any],
):
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    )

    if secret and received_secret != secret:
        raise HTTPException(
            status_code=403,
            detail="Telegram webhook secret không hợp lệ",
        )

    if not telegram_ready():
        raise HTTPException(
            status_code=503,
            detail="Telegram service chưa sẵn sàng",
        )

    if not _remember_update(payload.get("update_id")):
        return {"status": "duplicate"}

    chat_id, text = extract_message(payload)

    if chat_id is not None and text:
        background_tasks.add_task(
            process_message,
            chat_id,
            text,
        )

    return {
        "status": "received",
    }
