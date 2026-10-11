"""記憶體快取 —— 依 jsonl 檔的 mtime + size 判斷是否需重新解析。

stock-predictor 的大 session 可達數 MB,每次 API 呼叫都整檔重解析會很慢。
這裡以 (mtime, size) 當作版本指紋,只在檔案變動時重跑解析器。

長駐 server 下,若只增不減會隨 session 檔累積吃記憶體(含已刪除的 session),
故採兩道防線:
- LRU 容量上限(`MAX_ENTRIES`):超過時逐出最久未使用者,保證記憶體有界。
- `prune_missing()`:scanner 每輪掃描後呼叫,清掉磁碟上已不存在的檔路徑。
上限可用環境變數 GUILD_CACHE_MAX 覆寫(0 或負數表示不限)。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from . import config


def _max_entries() -> int:
    raw = config.get("GUILD_CACHE_MAX", "512")
    try:
        return int(raw)
    except ValueError:
        return 512


MAX_ENTRIES = _max_entries()

# key: 檔案路徑字串 -> (fingerprint, 解析結果);OrderedDict 保序以支援 LRU 逐出。
_store: "OrderedDict[str, tuple[tuple[float, int], Any]]" = OrderedDict()
# uvicorn 把同步路由丟到 threadpool 跑,多請求會並發讀寫這個快取。所有對 _store 的
# 讀寫/迭代都必須在鎖內,否則一邊迭代一邊被改會噴 "OrderedDict mutated during iteration"。
_lock = threading.Lock()


def _fingerprint(path: Path) -> tuple[float, int]:
    st = path.stat()
    return (st.st_mtime, st.st_size)


def get_or_parse(path: Path, parser: Callable[[Path], Any]) -> Any:
    """回傳快取結果;若檔案自上次解析後有變動則重新解析。

    命中或寫入時把該 key 移到尾端(最近使用);超過上限時逐出頭端(最久未用)。
    """
    key = str(path)
    fp = _fingerprint(path)
    with _lock:
        cached = _store.get(key)
        if cached is not None and cached[0] == fp:
            _store.move_to_end(key)
            return cached[1]
    # 解析是慢 I/O,放在鎖外以免拖垮並發;可能有兩個執行緒同時解同一檔(可接受,
    # 結果一致),最後寫回時以後到者為準。
    result = parser(path)
    with _lock:
        _store[key] = (fp, result)
        _store.move_to_end(key)
        if MAX_ENTRIES > 0:
            while len(_store) > MAX_ENTRIES:
                _store.popitem(last=False)
    return result


def prune_missing() -> int:
    """清掉磁碟上已不存在的檔路徑,回傳清掉的筆數。

    scanner 每輪掃描後呼叫,避免已刪除的 session 永久佔記憶體。
    """
    # 先在鎖內拍一份 key 快照,再到鎖外做 exists() 的磁碟 I/O(慢、且會釋放 GIL),
    # 最後回鎖內刪除。全程不直接迭代 _store 本體,避免迭代中被並發改動而爆掉。
    with _lock:
        keys = list(_store.keys())
    gone = [key for key in keys if not Path(key).exists()]
    with _lock:
        for key in gone:
            _store.pop(key, None)  # 期間可能已被逐出,pop 容許不存在
    return len(gone)


def clear() -> None:
    with _lock:
        _store.clear()
