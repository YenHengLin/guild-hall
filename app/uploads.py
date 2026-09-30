"""委託附圖儲存 —— 把使用者貼上的圖片存到磁碟,供前端顯示與派遣冒險者 Read。

為什麼存檔而非塞進 DB / prompt:`claude -p` 走 stdin 是純文字,無法直接餵圖;
但 Claude Code 本身能用 Read 工具讀圖片檔。所以把圖存成實體檔、在 prompt 注入
其絕對路徑,冒險者就能自己讀圖(派遣工具白名單已含 Read)。
"""

from __future__ import annotations

import base64
import re
import uuid
from datetime import date
from pathlib import Path

# 存在專案根目錄下 uploads/(與 guild.db 同層),已在 .gitignore 忽略
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"

# data URL:data:image/png;base64,xxxx
_DATA_URL_RE = re.compile(r"^data:image/(?P<ext>[a-zA-Z0-9.+-]+);base64,(?P<b64>.+)$",
                          re.DOTALL)
_EXT_FIX = {"jpeg": "jpg", "svg+xml": "svg"}
_ALLOWED = {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"}
MAX_BYTES = 12 * 1024 * 1024  # 單張上限 12MB,擋住異常大的貼上


def save_data_url(data_url: str) -> str:
    """存一張 base64 data URL 圖片,回傳可直接當 <img src> 與 DB 儲存的 URL 路徑。

    回傳形如 `/uploads/202609/ab12cd.png`。非法格式 / 過大會丟 ValueError。
    """
    m = _DATA_URL_RE.match((data_url or "").strip())
    if not m:
        raise ValueError("不是合法的圖片 data URL")
    ext = m.group("ext").lower()
    ext = _EXT_FIX.get(ext, ext)
    if ext not in _ALLOWED:
        raise ValueError(f"不支援的圖片格式:{ext}")
    try:
        raw = base64.b64decode(m.group("b64"), validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise ValueError(f"圖片解碼失敗:{exc}") from exc
    if len(raw) > MAX_BYTES:
        raise ValueError(f"圖片過大(>{MAX_BYTES // 1024 // 1024}MB)")

    sub = date.today().strftime("%Y%m")
    folder = UPLOAD_DIR / sub
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex[:12]}.{ext}"
    (folder / name).write_bytes(raw)
    return f"/uploads/{sub}/{name}"


def abs_path(url: str) -> Path | None:
    """把 `/uploads/...` URL 還原成磁碟絕對路徑(供派遣 prompt 注入)。防目錄穿越。"""
    if not url or not url.startswith("/uploads/"):
        return None
    rel = url[len("/uploads/"):].lstrip("/")
    p = (UPLOAD_DIR / rel).resolve()
    if UPLOAD_DIR.resolve() not in p.parents:
        return None
    return p if p.exists() else None
