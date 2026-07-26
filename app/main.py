from contextlib import asynccontextmanager
import logging

from dotenv import load_dotenv # type: ignore
from fastapi import FastAPI, HTTPException

from app.models import (
    WarrantyMessageRequest,
    WarrantyMessageResponse,
)
from app.services.gemini_service import GeminiService


load_dotenv()

from app.routes.telegram_router import (  # noqa: E402
    configure_telegram,
    router as telegram_router,
    telegram_ready,
)

logger = logging.getLogger(__name__)


gemini_service: GeminiService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_service

    try:
        gemini_service = GeminiService()
        logger.info("Gemini Warranty Agent is ready")

        try:
            configure_telegram(gemini_service)
            logger.info("Telegram Bot is ready")
        except Exception as error:
            logger.exception(
                "Telegram Bot initialization failed: %s",
                error,
            )

    except Exception as error:
        gemini_service = None
        logger.exception(
            "Gemini Agent initialization failed: %s",
            error,
        )

    yield


app = FastAPI(
    title="Warranty Agent",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(telegram_router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "gemini_ready": gemini_service is not None,
        "telegram_ready": telegram_ready(),
    }


@app.post(
    "/api/warranty/message",
    response_model=WarrantyMessageResponse,
)
def handle_warranty_message(
    data: WarrantyMessageRequest,
):
    if gemini_service is None:
        raise HTTPException(
            status_code=503,
            detail="Gemini Agent chưa sẵn sàng",
        )

    try:
        history = [
            item.model_dump()
            for item in data.history
        ]

        result = gemini_service.chat(
            message=data.message,
            customer_id=data.customer_id,
            history=history,
        )

        return WarrantyMessageResponse(
            status="completed",
            message=result["reply"],
        )

    except Exception as error:
        logger.exception("Warranty Agent request failed: %s", error)

        raise HTTPException(
            status_code=500,
            detail="Agent chưa thể xử lý yêu cầu lúc này",
        ) from error
