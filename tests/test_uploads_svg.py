"""回歸測試:SVG 可內嵌 <script>,經 StaticFiles 原樣掛出後直接開圖網址會在面板
同源執行指令碼(儲存型 XSS)。確保 svg 不在上傳白名單,並保留一般圖片格式。
"""

import base64

import pytest

import app.uploads as u

# 1x1 透明 PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMEAYETAhjmAAAAAElFTkSuQmCC"
)


def _data_url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def test_svg_rejected():
    """含 <script> 的 SVG 必須被擋下,不得存檔。"""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(document.cookie)</script></svg>'
    with pytest.raises(ValueError):
        u.save_data_url(_data_url("image/svg+xml", svg))


def test_svg_not_in_allowlist():
    assert "svg" not in u._ALLOWED


def test_png_still_accepted(tmp_path, monkeypatch):
    url = u.save_data_url(_data_url("image/png", _PNG))
    assert url.endswith(".png")
    p = u.abs_path(url)
    assert p is not None and p.exists()
    p.unlink()
