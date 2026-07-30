import logging
import os
import threading
import time
import re
from collections import deque
from typing import Any

from fastapi import ( # type: ignore
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
)

from app.product_recognition.handler import ProductImageHandler
from app.product_recognition.image_intent_service import (
    ImageIntentService,
)
from app.services.gemini_service import GeminiService
from app.services.image_extraction_service import (
    ImageExtractionService,
)
from app.services.telegram_service import TelegramService
from app.services.warranty_tools import (
    activate_warranty,
    search_order,
)
from app.product_recognition.product_tools import (
    get_product_info
)


router = APIRouter(
    prefix="/api/telegram",
    tags=["Telegram"],
)

logger = logging.getLogger(__name__)

gemini_service: GeminiService | None = None
telegram_service: TelegramService | None = None
image_extraction_service: ImageExtractionService | None = None
image_intent_service: ImageIntentService | None = None
product_image_handler: ProductImageHandler | None = None

_state_lock = threading.Lock()
_histories: dict[int, deque[dict[str, str]]] = {}
_processed_updates: deque[int] = deque(maxlen=1000)
_processed_update_set: set[int] = set()
_pending_activations: dict[int, dict[str, Any]] = {}
_shown_product_codes: dict[int, set[str]] = {}
_PENDING_TTL_SECONDS = 10 * 60


def configure_telegram(
    warranty_agent: GeminiService,
) -> None:
    global gemini_service, telegram_service, image_extraction_service
    global image_intent_service, product_image_handler
    gemini_service = warranty_agent
    telegram_service = TelegramService()
    image_extraction_service = ImageExtractionService(
        client=warranty_agent.client,
        model=warranty_agent.model,
    )
    image_intent_service = ImageIntentService(
        client=warranty_agent.client,
        model=warranty_agent.model,
    )
    product_image_handler = ProductImageHandler(
        client=warranty_agent.client,
        model=warranty_agent.model,
    )


def telegram_ready() -> bool:
    return (
        gemini_service is not None
        and telegram_service is not None
        and image_extraction_service is not None
        and image_intent_service is not None
        and product_image_handler is not None
    )


def _save_pending_activation(
    chat_id: int,
    phone: str,
    order_code: str,
) -> None:
    with _state_lock:
        _pending_activations[chat_id] = {
            "phone": phone,
            "order_code": order_code,
            "created_at": time.monotonic(),
        }


def _take_pending_activation(
    chat_id: int,
) -> dict[str, Any] | None:
    with _state_lock:
        pending = _pending_activations.pop(chat_id, None)

    if not pending:
        return None

    age = time.monotonic() - pending["created_at"]

    if age > _PENDING_TTL_SECONDS:
        return None

    return pending


def _peek_pending_activation(
    chat_id: int,
) -> dict[str, Any] | None:
    with _state_lock:
        pending = _pending_activations.get(chat_id)

        if not pending:
            return None

        age = time.monotonic() - pending["created_at"]

        if age > _PENDING_TTL_SECONDS:
            _pending_activations.pop(chat_id, None)
            return None

        return dict(pending)


def _cancel_pending_activation(chat_id: int) -> bool:
    with _state_lock:
        return _pending_activations.pop(chat_id, None) is not None


def _mask_phone(phone: str) -> str:
    if len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-3:]}"


def _agent_reply(
    chat_id: int,
    event: str,
    fallback: str,
) -> str:
    if gemini_service is None:
        return fallback

    try:
        return gemini_service.compose_reply(
            event=event,
            history=_get_history(chat_id),
        )
    except Exception:
        logger.exception(
            "Không thể tạo câu trả lời AI chat_id=%s",
            chat_id,
        )
        return fallback


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

def _send_product_photos(
    chat_id: int,
    reply: str,
    user_message: str = "",
    force: bool = False,
) -> None:
    if telegram_service is None:
        return

    # Lấy các từ có khả năng là mã sản phẩm trong câu trả lời.
    possible_codes = re.findall(
        r"\b[A-Za-z0-9]{3,20}\b",
        reply,
    )

    sent_codes: set[str] = set()
    sent_count = 0
    resend_checked = False
    resend_requested = False

    for possible_code in possible_codes:
        result = get_product_info(possible_code)

        if result.get("status") != "product_found":
            continue

        product = result.get("product") or {}
        product_code = str(
            product.get("product_code") or ""
        ).upper()

        photo_urls = product.get("image_urls") or []

        if not photo_urls and product.get("featured_image"):
            photo_urls = [product["featured_image"]]

        photo_urls = list(dict.fromkeys(photo_urls))[:4]


        if (
            not product_code
            or not photo_urls
            or product_code in sent_codes
        ):
            continue

        with _state_lock:
            already_shown = product_code in _shown_product_codes.get(
                chat_id,
                set(),
            )

        if already_shown and not force:
            if not resend_checked:
                resend_checked = True
                if gemini_service is not None and user_message:
                    try:
                        resend_requested = (
                            gemini_service.classify_product_image_request(
                                user_message
                            )
                        )
                    except Exception:
                        logger.exception(
                            "KhĂ´ng thá»ƒ phĂ¢n loáº¡i yĂªu cáº§u gá»­i láº¡i áº£nh "
                            "chat_id=%s",
                            chat_id,
                        )

            if not resend_requested:
                continue

        product_name = (
            product.get("product_name")
            or "Sản phẩm Đông Hải"
        )

        prices = product.get("prices") or []
        colors = product.get("colors") or []

        caption_lines = [
            str(product_name),
            f"Mã sản phẩm: {product_code}",
        ]

        if prices:
            formatted_price = (
                f"{prices[0]:,.0f}"
                .replace(",", ".")
            )
            caption_lines.append(
                f"Giá: {formatted_price}đ"
            )

        if colors:
            caption_lines.append(
                f"Màu: {', '.join(colors)}"
            )

        try:
            telegram_service.send_media_group(
                chat_id=chat_id,
                photo_urls=[
                    str(photo_url)
                    for photo_url in photo_urls
                ],
            )
        except Exception:
            logger.exception(
                "Không thể gửi album ảnh sản phẩm code=%s chat_id=%s",
                product_code,
                chat_id,
            )
            continue

        sent_codes.add(product_code)
        sent_count += 1
        with _state_lock:
            _shown_product_codes.setdefault(
                chat_id,
                set(),
            ).add(product_code)

        # Tránh gửi quá nhiều ảnh cùng lúc.
        if sent_count >= 3:
            break

def extract_message(
    payload: dict[str, Any],
) -> tuple[int | None, str | None, str | None]:

    message = payload.get("message")

    if not isinstance(message, dict):
        return None, None, None

    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    text = message.get("text")
    caption = message.get("caption")
    photos = message.get("photo") or []

    file_id = None

    if photos:
        file_id = photos[-1].get("file_id")

    if chat_id is None:
        return None, None, None

    return (
        int(chat_id),
        str(text or caption or "").strip() or None,
        file_id,
    )

def process_message(
    chat_id: int,
    text: str,
) -> None:
    try:
        if not telegram_ready():
            raise RuntimeError("Telegram service chưa sẵn sàng")

        assert telegram_service is not None
        assert gemini_service is not None

        pending = _peek_pending_activation(chat_id)
        confirmation_intent = (
            gemini_service.classify_confirmation_intent(text)
            if pending
            else "unknown"
        )

        if confirmation_intent == "cancel":
            cancelled = _cancel_pending_activation(chat_id)
            event = (
                "Khách đã hủy yêu cầu kích hoạt bảo hành đang chờ."
                if cancelled
                else "Khách yêu cầu hủy nhưng không có yêu cầu nào đang chờ."
            )
            fallback = (
                "Dạ em đã hủy yêu cầu kích hoạt bảo hành ạ."
                if cancelled
                else "Dạ hiện không có yêu cầu nào đang chờ hủy ạ."
            )
            reply = _agent_reply(
                chat_id,
                event,
                fallback,
            )
            telegram_service.send_message(chat_id, reply)
            return

        if confirmation_intent == "confirm":
            pending = _take_pending_activation(chat_id)

            if not pending:
                fallback = (
                        "Dạ yêu cầu xác nhận không tồn tại hoặc đã "
                        "hết hạn. Anh/chị vui lòng gửi lại ảnh ạ."
                )
                reply = _agent_reply(
                    chat_id,
                    (
                        "Khách xác nhận nhưng yêu cầu đọc ảnh không "
                        "tồn tại hoặc đã hết hạn."
                    ),
                    fallback,
                )
                telegram_service.send_message(chat_id, reply)
                return

            result = activate_warranty(
                order_code=pending["order_code"],
                phone=pending["phone"],
                customer_id=f"telegram:{chat_id}",
            )
            status = result.get("status")

            if status == "activated":
                event = (
                    f"Đã kích hoạt bảo hành thành công cho đơn "
                    f"{pending['order_code']}."
                )
                fallback = (
                    f"Dạ đơn {pending['order_code']} đã được kích "
                    "hoạt bảo hành thành công ạ."
                )
            elif status == "already_activated":
                event = (
                    f"Đơn {pending['order_code']} đã được kích hoạt "
                    "bảo hành trước đó."
                )
                fallback = (
                    f"Dạ đơn {pending['order_code']} đã được kích "
                    "hoạt bảo hành trước đó ạ."
                )
            elif status == "order_not_eligible":
                event = (
                    f"Đơn {pending['order_code']} chưa đủ điều kiện "
                    "kích hoạt bảo hành."
                )
                fallback = (
                    f"Dạ đơn {pending['order_code']} hiện chưa đủ "
                    "điều kiện kích hoạt bảo hành ạ."
                )
            else:
                event = (
                    f"Không thể kích hoạt bảo hành cho đơn "
                    f"{pending['order_code']} theo kết quả hệ thống."
                )
                fallback = (
                    "Dạ em chưa thể kích hoạt theo thông tin trong "
                    "ảnh. Anh/chị vui lòng kiểm tra và thử lại ạ."
                )

            reply = _agent_reply(
                chat_id,
                event,
                fallback,
            )
            telegram_service.send_message(chat_id, reply)
            return

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
        _send_product_photos(
            chat_id=chat_id,
            reply=reply,
            user_message=text,
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


def process_image(
    chat_id: int,
    file_id: str,
    caption: str | None = None,
) -> None:
    try:
        if not telegram_ready():
            raise RuntimeError("Telegram service chưa sẵn sàng")

        assert telegram_service is not None
        assert image_extraction_service is not None
        assert image_intent_service is not None
        assert product_image_handler is not None

        telegram_service.send_typing(chat_id)
        image_bytes, file_path = telegram_service.download_file(
            file_id
        )

        extension = os.path.splitext(file_path)[1].lower()
        mime_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }
        mime_type = mime_types.get(extension)

        if not mime_type:
            telegram_service.send_message(
                chat_id,
                "Dạ ảnh cần có định dạng JPG, PNG hoặc WEBP ạ.",
            )
            return

        image_intent = image_intent_service.classify(
            image_bytes=image_bytes,
            mime_type=mime_type,
            caption=caption,
        )

        if image_intent == "product_lookup":
            product_result = product_image_handler.handle(
                image_bytes=image_bytes,
                mime_type=mime_type,
            )
            reply = product_result["reply"]
            telegram_service.send_message(chat_id, reply)
            product_codes = product_result.get(
                "product_codes",
                [],
            )
            if product_codes:
                _send_product_photos(
                    chat_id=chat_id,
                    reply=" ".join(
                        str(product_code)
                        for product_code in product_codes
                    ),
                    user_message=caption or "",
                    force=True,
                )
                _append_history(
                    chat_id,
                    (
                        "Khách đã gửi ảnh sản phẩm. Hệ thống đã "
                        "xác minh mã sản phẩm phù hợp: "
                        f"{', '.join(product_codes)}."
                    ),
                    reply,
                )
            return

        if image_intent == "unknown":
            telegram_service.send_message(
                chat_id,
                (
                    "Dạ em chưa xác định được mục đích của ảnh. "
                    "Anh/chị muốn nhận diện sản phẩm hay kích hoạt "
                    "bảo hành từ ảnh này ạ?"
                ),
            )
            return

        extracted = image_extraction_service.extract(
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        phone = extracted.get("phone")
        order_code = extracted.get("order_code")
        confident = (
            extracted.get("phone_confident") is True
            and extracted.get("order_code_confident") is True
        )

        if not phone or not order_code or not confident:
            fallback = (
                "Dạ em chưa đọc rõ số điện thoại hoặc mã đơn. "
                "Anh/chị vui lòng gửi ảnh rõ và đầy đủ hơn ạ."
            )
            telegram_service.send_message(
                chat_id,
                _agent_reply(
                    chat_id,
                    (
                        "Không đọc chắc chắn được đầy đủ số điện "
                        "thoại và mã đơn từ ảnh khách gửi."
                    ),
                    fallback,
                ),
            )
            return

        search_result = search_order(
            phone=phone,
            order_code=order_code,
        )

        if (
            not search_result.get("success")
            or search_result.get("count") != 1
        ):
            fallback = (
                "Dạ thông tin trong ảnh chưa khớp với đơn hàng. "
                "Anh/chị vui lòng kiểm tra và gửi lại ảnh ạ."
            )
            telegram_service.send_message(
                chat_id,
                _agent_reply(
                    chat_id,
                    (
                        "Số điện thoại và mã đơn đọc từ ảnh không "
                        "khớp duy nhất một đơn hàng."
                    ),
                    fallback,
                ),
            )
            return

        order = search_result["orders"][0]

        if order.get("warranty_status") == "activated":
            fallback = (
                f"Dạ đơn {order_code} đã được kích hoạt trước đó ạ."
            )
            telegram_service.send_message(
                chat_id,
                _agent_reply(
                    chat_id,
                    (
                        f"Đơn {order_code} đã được kích hoạt bảo "
                        "hành trước đó."
                    ),
                    fallback,
                ),
            )
            return

        _save_pending_activation(
            chat_id=chat_id,
            phone=phone,
            order_code=order_code,
        )
        fallback = (
                f"Dạ em đọc được mã đơn {order_code}, số điện thoại "
                f"{_mask_phone(phone)}. Anh/chị trả lời XÁC NHẬN "
                "để kích hoạt hoặc HỦY để dừng ạ."
        )
        telegram_service.send_message(
            chat_id,
            _agent_reply(
                chat_id,
                (
                    f"Đã đọc và xác minh được đơn {order_code}, "
                    f"số điện thoại đã che {_mask_phone(phone)}. "
                    "Cần yêu cầu khách xác nhận kích hoạt hoặc hủy; "
                    "chưa được nói đã kích hoạt."
                ),
                fallback,
            ),
        )

    except Exception:
        logger.exception(
            "TELEGRAM IMAGE ERROR chat_id=%s",
            chat_id,
        )
        if telegram_service is not None:
            try:
                telegram_service.send_message(
                    chat_id,
                    (
                        "Dạ em chưa thể xử lý ảnh lúc này. "
                        "Anh/chị vui lòng thử lại sau ạ."
                    ),
                )
            except Exception:
                logger.exception(
                    "Không thể gửi lỗi xử lý ảnh về Telegram"
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

    chat_id, text, file_id = extract_message(payload)

    if chat_id is not None and file_id:
        background_tasks.add_task(
            process_image,
            chat_id,
            file_id,
            text,
        )
    elif chat_id is not None and text:
        background_tasks.add_task(
            process_message,
            chat_id,
            text,
        )

    return {
        "status": "received",
    }
