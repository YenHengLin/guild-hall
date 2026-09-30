"""圖片上傳 API —— 前端把剪貼簿/選檔的圖片以 data URL 送來,存檔後回 URL。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import uploads

router = APIRouter(prefix="/api", tags=["uploads"])


class UploadReq(BaseModel):
    data_url: str


@router.post("/uploads")
def upload_image(body: UploadReq) -> dict:
    try:
        url = uploads.save_data_url(body.data_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"url": url}
