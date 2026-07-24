from contextlib import asynccontextmanager

from dotenv import load_dotenv # type: ignore
from fastapi import FastAPI, HTTPException

from app.models import (
    WarrantyMessageRequest,
    WarrantyMessageResponse,
)
from app.services.gemini_service import GeminiService


load_dotenv()


gemini_service: GeminiService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_service

    try:
        gemini_service = GeminiService()
        print("Gemini Warranty Agent đã sẵn sàng")

    except Exception as error:
        gemini_service = None
        print(f"Không thể khởi tạo Gemini Agent: {error}")

    yield


app = FastAPI(
    title="Warranty Agent",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "gemini_ready": gemini_service is not None,
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
        print(f"WARRANTY AGENT ERROR: {error}")

        raise HTTPException(
            status_code=500,
            detail="Agent chưa thể xử lý yêu cầu lúc này",
        ) from error