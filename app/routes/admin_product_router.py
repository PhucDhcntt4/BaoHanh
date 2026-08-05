import io
import re
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from openpyxl import load_workbook  # type: ignore
from pydantic import BaseModel

from app.services.product_sync_service import product_sync_manager


router = APIRouter(prefix="/admin/products", tags=["Product Admin"])
PAGE_PATH = Path(__file__).resolve().parent.parent / "static" / "product_admin.html"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SKU_HEADERS = {"sku", "ma san pham", "mã sản phẩm", "product code", "product_code"}


class SkuImportRequest(BaseModel):
    skus: list[str]


def _start_job(skus: list[str], background_tasks: BackgroundTasks) -> dict:
    try:
        job = product_sync_manager.create(skus)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    background_tasks.add_task(product_sync_manager.run, job.id)
    return job.public()


def _normalize_header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _read_excel_skus(content: bytes) -> list[str]:
    try:
        workbook = load_workbook(
            filename=io.BytesIO(content),
            read_only=True,
            data_only=True,
        )
    except Exception as error:
        raise ValueError("File Excel không hợp lệ hoặc bị hỏng.") from error

    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)
    header_row = next(rows, None)
    if not header_row:
        raise ValueError("File Excel không có dữ liệu.")

    headers = [_normalize_header(value) for value in header_row]
    sku_index = next(
        (index for index, header in enumerate(headers) if header in SKU_HEADERS),
        None,
    )
    if sku_index is None:
        raise ValueError(
            "Excel phải có cột SKU, Mã sản phẩm hoặc Product Code."
        )

    skus: list[str] = []
    for row in rows:
        if sku_index >= len(row):
            continue
        value = row[sku_index]
        if value is not None and str(value).strip():
            skus.append(str(value).strip())

    if not skus:
        raise ValueError("Cột SKU không có mã sản phẩm.")
    return skus


@router.get("", response_class=HTMLResponse)
def product_admin_page() -> HTMLResponse:
    return HTMLResponse(
        PAGE_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/import-skus")
def import_skus(
    data: SkuImportRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    return _start_job(data.skus, background_tasks)


@router.post("/api/import-excel")
async def import_excel(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict:
    suffix = Path(file.filename or "").suffix.casefold()
    if suffix != ".xlsx":
        raise HTTPException(status_code=415, detail="Chỉ hỗ trợ file .xlsx.")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File vượt quá 5 MB.")

    try:
        skus = _read_excel_skus(content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _start_job(skus, background_tasks)


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = product_sync_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ.")
    return job
