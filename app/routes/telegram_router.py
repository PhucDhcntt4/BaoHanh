import logging
import os
import threading
import time
import re
import unicodedata
from collections import deque
from typing import Any

from fastapi import ( # type: ignore
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
)

from app.services.AI.base import AIService
from app.services.order_service import (
    OrderService,
    normalize_phone,
)
from app.services.telegram_service import TelegramService
from app.services.warranty_tools import (
    activate_warranty,
    search_order,
)
from app.product_recognition.product_tools import (
    get_product_info
)
from app.product_recognition.image_crop import crop_product_region


router = APIRouter(
    prefix="/api/telegram",
    tags=["Telegram"],
)

logger = logging.getLogger("uvicorn.error")

ai_service: AIService | None = None
telegram_service: TelegramService | None = None
order_service = OrderService()

_state_lock = threading.Lock()
_histories: dict[int, deque[dict[str, str]]] = {}
_processed_updates: deque[int] = deque(maxlen=1000)
_processed_update_set: set[int] = set()
_pending_activations: dict[int, dict[str, Any]] = {}
_shown_product_codes: dict[int, set[str]] = {}
_active_product_codes: dict[int, list[str]] = {}
_PENDING_TTL_SECONDS = 10 * 60


def configure_telegram(
    warranty_agent: AIService,
) -> None:
    global ai_service, telegram_service
    ai_service = warranty_agent
    telegram_service = TelegramService()


def telegram_ready() -> bool:
    return (
        ai_service is not None
        and telegram_service is not None
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
    return phone


def _masked_phone_matches(
    full_phone: str,
    masked_phone: str,
) -> bool:
    normalized_full = normalize_phone(full_phone)
    normalized_masked = (
        masked_phone.replace("x", "*").replace("X", "*")
    )

    if not normalized_full or "*" not in normalized_masked:
        return False

    prefix, _, suffix = normalized_masked.partition("*")
    suffix = suffix.lstrip("*")

    if not prefix or not suffix:
        return False

    return (
        normalized_full.startswith(prefix)
        and normalized_full.endswith(suffix)
        and len(normalized_full) >= len(prefix) + len(suffix)
    )


def _agent_reply(
    chat_id: int,
    event: str,
    fallback: str,
) -> str:
    if ai_service is None:
        return fallback

    try:
        return ai_service.compose_reply(
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


def _normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize(
        "NFD", str(value or "").casefold()
    )
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")


def _remember_active_products(
    chat_id: int,
    product_codes: list[str],
) -> None:
    normalized_codes = list(dict.fromkeys(
        str(code).strip().upper()
        for code in product_codes
        if str(code).strip()
    ))
    if normalized_codes:
        with _state_lock:
            _active_product_codes[chat_id] = normalized_codes[:3]


def _active_products(chat_id: int) -> list[str]:
    with _state_lock:
        return list(_active_product_codes.get(chat_id, ()))


def _requested_color_images(
    user_message: str,
    images_by_color: dict[str, list[str]],
) -> tuple[str | None, list[str] | None]:
    """Return the requested catalog color and its images."""
    message = _normalize_text(user_message)
    if not message or not images_by_color:
        return None, None

    alias_groups = (
        {"kem", "trang kem", "be", "beige"},
        {"den", "black"},
        {"trang", "white"},
        {"nau", "brown"},
        {"do", "red"},
        {"xanh", "blue", "green"},
        {"vang", "yellow", "gold"},
        {"xam", "ghi", "gray", "grey"},
        {"hong", "pink"},
    )
    def contains_phrase(phrase: str, text: str) -> bool:
        return bool(re.search(
            rf"(?<!\w){re.escape(phrase)}(?!\w)",
            text,
        ))

    requested_aliases: set[str] = set()
    for aliases in alias_groups:
        if any(contains_phrase(alias, message) for alias in aliases):
            requested_aliases.update(aliases)

    candidates: list[tuple[int, str, list[str]]] = []
    for color, urls in images_by_color.items():
        normalized_color = _normalize_text(color).strip()
        if not normalized_color or not urls:
            continue
        if contains_phrase(normalized_color, message):
            candidates.append(
                (100 + len(normalized_color), str(color), urls)
            )
        elif requested_aliases and any(
            alias in normalized_color
            for alias in requested_aliases
        ):
            candidates.append(
                (50 + len(normalized_color), str(color), urls)
            )

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, selected_color, selected_urls = candidates[0]
        return selected_color, selected_urls

    # Empty string means a color was requested but has no matching album.
    return ("", []) if requested_aliases else (None, None)

def _send_product_photos(
    chat_id: int,
    reply: str,
    user_message: str = "",
    force: bool = False,
    product_codes: list[str] | None = None,
) -> None:
    if telegram_service is None:
        return

    # Lấy các từ có khả năng là mã sản phẩm trong câu trả lời.
    possible_codes = list(product_codes or []) + re.findall(
        r"\b[A-Za-z0-9]{3,20}\b",
        reply,
    )
    possible_codes.extend(_active_products(chat_id))
    possible_codes = list(dict.fromkeys(
        str(code).strip().upper()
        for code in possible_codes
        if str(code).strip()
    ))

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
        images_by_color = (
            product.get("image_urls_by_color") or {}
        )

        # Nếu khách yêu cầu một màu cụ thể, ưu tiên album của đúng
        # Shopify Product màu đó thay vì album mặc định của mã mẫu.
        selected_color, selected_urls = _requested_color_images(
            f"{user_message}\n{reply}",
            images_by_color,
        )
        if selected_urls:
            photo_urls = selected_urls
        elif selected_color == "":
            logger.info(
                "PRODUCT IMAGE COLOR NOT FOUND chat_id=%s code=%s "
                "message=%r available_colors=%s",
                chat_id,
                product_code,
                user_message,
                list(images_by_color),
            )
            continue

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
                if ai_service is not None and user_message:
                    try:
                        resend_requested = (
                            ai_service.classify_product_image_request(
                                (
                                    f"Tin nhắn khách: {user_message}\n"
                                    f"Phản hồi dự định của trợ lý: {reply}"
                                )
                            )
                        )
                        logger.info(
                            "PRODUCT IMAGE REQUEST chat_id=%s code=%s "
                            "requested=%s",
                            chat_id,
                            product_code,
                            resend_requested,
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
        _remember_active_products(chat_id, [product_code])
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
    started_at = time.perf_counter()
    ai_seconds = 0.0
    status = "completed"

    try:
        if not telegram_ready():
            raise RuntimeError("Telegram service chưa sẵn sàng")

        assert telegram_service is not None
        assert ai_service is not None

        pending = _peek_pending_activation(chat_id)
        confirmation_intent = "unknown"
        if pending:
            ai_started_at = time.perf_counter()
            confirmation_intent = (
                ai_service.classify_confirmation_intent(text)
            )
            ai_seconds += time.perf_counter() - ai_started_at

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

        ai_started_at = time.perf_counter()
        result = ai_service.chat(
            message=text,
            customer_id=f"telegram:{chat_id}",
            history=_get_history(chat_id),
        )
        ai_seconds += time.perf_counter() - ai_started_at

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
        status = "error"
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

    finally:
        logger.info(
            "BOT RESPONSE chat_id=%s status=%s "
            "provider=%s model=%s ai=%.3fs total=%.3fs",
            chat_id,
            status,
            (
                ai_service.provider_name
                if ai_service is not None
                else "unknown"
            ),
            (
                ai_service.model
                if ai_service is not None
                else "unknown"
            ),
            ai_seconds,
            time.perf_counter() - started_at,
        )


def process_image(
    chat_id: int,
    file_id: str,
    caption: str | None = None,
) -> None:
    started_at = time.perf_counter()
    download_seconds = 0.0
    ai_seconds = 0.0
    status = "completed"

    try:
        if not telegram_ready():
            raise RuntimeError("Telegram service chưa sẵn sàng")

        assert telegram_service is not None
        assert ai_service is not None

        telegram_service.send_typing(chat_id)
        download_started_at = time.perf_counter()
        image_bytes, file_path = telegram_service.download_file(
            file_id
        )
        download_seconds = (
            time.perf_counter() - download_started_at
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

        ai_started_at = time.perf_counter()
        intent_result = ai_service.classify_image_intent(
            image_bytes=image_bytes,
            mime_type=mime_type,
            caption=caption,
        )
        ai_seconds += time.perf_counter() - ai_started_at
        image_intent = intent_result.get(
            "intent",
            "unknown",
        )
        product_type = intent_result.get(
            "product_type",
            "unknown",
        )
        bounding_box = intent_result.get("bounding_box")
        status = image_intent
        logger.info(
            "IMAGE CLASSIFIED chat_id=%s intent=%s product_type=%s bbox=%s",
            chat_id,
            image_intent,
            product_type,
            bounding_box,
        )

        if image_intent == "product_lookup":
            recognition_bytes, recognition_mime, crop_applied = (
                crop_product_region(image_bytes, bounding_box)
            )
            if not crop_applied:
                recognition_bytes = image_bytes
                recognition_mime = mime_type
            logger.info(
                "PRODUCT IMAGE CROP chat_id=%s applied=%s "
                "original_bytes=%s recognition_bytes=%s",
                chat_id,
                crop_applied,
                len(image_bytes),
                len(recognition_bytes),
            )
            if product_type == "unknown" and crop_applied:
                ai_started_at = time.perf_counter()
                refined_intent = ai_service.classify_image_intent(
                    image_bytes=recognition_bytes,
                    mime_type=recognition_mime,
                    caption=caption,
                )
                ai_seconds += time.perf_counter() - ai_started_at
                refined_product_type = refined_intent.get(
                    "product_type", "unknown"
                )
                if (
                    refined_intent.get("intent") == "product_lookup"
                    and refined_product_type != "unknown"
                ):
                    product_type = refined_product_type
                logger.info(
                    "IMAGE RECLASSIFIED chat_id=%s product_type=%s",
                    chat_id,
                    product_type,
                )
            ai_started_at = time.perf_counter()
            product_result = ai_service.handle_product_image(
                image_bytes=recognition_bytes,
                mime_type=recognition_mime,
                product_type=product_type,
            )
            ai_seconds += time.perf_counter() - ai_started_at
            reply = product_result["reply"]
            telegram_service.send_message(chat_id, reply)
            product_codes = product_result.get(
                "product_codes",
                [],
            )
            logger.info(
                "PRODUCT RECOGNITION chat_id=%s product_type=%s "
                "codes=%s",
                chat_id,
                product_type,
                product_codes,
            )
            if product_codes:
                _remember_active_products(chat_id, product_codes)
                _send_product_photos(
                    chat_id=chat_id,
                    reply=" ".join(
                        str(product_code)
                        for product_code in product_codes
                    ),
                    user_message=caption or "",
                    force=True,
                    product_codes=product_codes,
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

        ai_started_at = time.perf_counter()
        extracted = ai_service.extract_order_from_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        ai_seconds += time.perf_counter() - ai_started_at
        phone = extracted.get("phone")
        masked_phone = extracted.get("masked_phone")
        order_code = extracted.get("order_code")
        phone_confident = (
            extracted.get("phone_confident") is True
        )
        masked_phone_confident = (
            extracted.get("masked_phone_confident") is True
        )
        order_code_confident = (
            extracted.get("order_code_confident") is True
        )
        confident = (
            phone_confident
            and order_code_confident
        )

        if (
            not phone
            and masked_phone
            and masked_phone_confident
            and order_code
            and order_code_confident
        ):
            matched_order = order_service.get_by_order_code(
                order_code
            )
            stored_phone = (
                str(matched_order.get("phone") or "")
                if matched_order
                else ""
            )

            if (
                not matched_order
                or not _masked_phone_matches(
                    stored_phone,
                    masked_phone,
                )
            ):
                telegram_service.send_message(
                    chat_id,
                    (
                        f"Dạ em đọc được mã đơn {order_code} và số điện "
                        f"thoại {masked_phone}, nhưng thông tin chưa khớp "
                        "với đơn hàng. Anh/chị vui lòng kiểm tra lại ạ."
                    ),
                )
                return

            if matched_order.get("warranty_status") == "activated":
                telegram_service.send_message(
                    chat_id,
                    (
                        f"Dạ em đã xác minh mã đơn {order_code} với số "
                        f"điện thoại {masked_phone}. Đơn này đã được kích "
                        "hoạt bảo hành trước đó rồi ạ."
                    ),
                )
                return

            _save_pending_activation(
                chat_id=chat_id,
                phone=stored_phone,
                order_code=order_code,
            )
            telegram_service.send_message(
                chat_id,
                (
                    f"Dạ em đã xác minh được mã đơn {order_code} và số "
                    f"điện thoại {masked_phone}. Anh/chị trả lời XÁC NHẬN "
                    "để kích hoạt bảo hành hoặc HỦY để dừng ạ. 😊"
                ),
            )
            return

        if (
            order_code
            and order_code_confident
            and (not phone or not phone_confident)
        ):
            reply = (
                f"Dạ em đã đọc được mã đơn {order_code}. Trên ảnh chưa "
                "có số điện thoại đặt hàng, anh/chị gửi số điện thoại "
                "giúp em để em xác minh và kích hoạt bảo hành nhé. 😊"
            )
            telegram_service.send_message(chat_id, reply)
            _append_history(
                chat_id,
                (
                    "Khách gửi ảnh kích hoạt bảo hành. Hệ thống đã đọc "
                    f"chắc chắn mã đơn {order_code}, nhưng ảnh không có "
                    "số điện thoại."
                ),
                reply,
            )
            return

        if (
            phone
            and phone_confident
            and (not order_code or not order_code_confident)
        ):
            reply = (
                f"Dạ em đã đọc được số điện thoại {phone}, nhưng chưa "
                "đọc rõ mã đơn. Anh/chị gửi thêm mã đơn hoặc ảnh có mã "
                "đơn rõ hơn giúp em nhé. 😊"
            )
            telegram_service.send_message(chat_id, reply)
            _append_history(
                chat_id,
                (
                    "Khách gửi ảnh kích hoạt bảo hành. Hệ thống đã đọc "
                    f"chắc chắn số điện thoại {phone}, nhưng chưa đọc "
                    "được mã đơn."
                ),
                reply,
            )
            return

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
                    f"số điện thoại {_mask_phone(phone)}. "
                    "Cần yêu cầu khách xác nhận kích hoạt hoặc hủy; "
                    "chưa được nói đã kích hoạt."
                ),
                fallback,
            ),
        )

    except Exception:
        status = "error"
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

    finally:
        logger.info(
            "BOT IMAGE RESPONSE chat_id=%s status=%s "
            "provider=%s model=%s download=%.3fs "
            "ai=%.3fs total=%.3fs",
            chat_id,
            status,
            (
                ai_service.provider_name
                if ai_service is not None
                else "unknown"
            ),
            (
                ai_service.model
                if ai_service is not None
                else "unknown"
            ),
            download_seconds,
            ai_seconds,
            time.perf_counter() - started_at,
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
