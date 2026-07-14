"""記憶體快取 —— 依 jsonl 檔的 mtime + size 判斷是否需重新解析。

stock-predictor 的大 session 可達數 MB,每次 API 呼叫都整檔重解析會很慢。
這裡以 (mtime, size) 當作版本指紋,只在檔案變動時重跑解析器。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

# key: 檔案路徑字串 -> (fingerprint, 解析結果)
_store: dict[str, tuple[tuple[float, int], Any]] = {}


def _fingerprint(path: Path) -> tuple[float, int]:
    st = path.stat()
    return (st.st_mtime, st.st_size)


def get_or_parse(path: Path, parser: Callable[[Path], Any]) -> Any:
    """回傳快取結果;若檔案自上次解析後有變動則重新解析。"""
    key = str(path)
    fp = _fingerprint(path)
    cached = _store.get(key)
    if cached is not None and cached[0] == fp:
        return cached[1]
    result = parser(path)
    _store[key] = (fp, result)
    return result


def clear() -> None:
    _store.clear()
