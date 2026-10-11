"""cache 的容量與淘汰驗收 —— 長駐 server 下記憶體必須有界。

過去 `_store` 只增不減:解析過的 session 路徑永久留著(含已刪除者),
長期常駐記憶體只升不降。這裡驗三件事:
- 指紋命中/失效的基本正確性。
- LRU 容量上限:超過 MAX_ENTRIES 時逐出最久未使用者。
- prune_missing:清掉磁碟上已不存在的檔路徑。
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def cache_mod(monkeypatch):
    """每個測試拿到獨立、狀態乾淨的 cache 模組。"""
    from app import cache

    importlib.reload(cache)
    cache.clear()
    yield cache
    cache.clear()


def _write(tmp_path: Path, name: str, content: str = "x") -> Path:
    f = tmp_path / name
    f.write_text(content)
    return f


# ── 基本正確性:同指紋命中、變動後重解析 ──────────────────────
def test_hit_returns_cached_without_reparse(cache_mod, tmp_path):
    f = _write(tmp_path, "a.jsonl", "hello")
    calls = {"n": 0}

    def parser(p):
        calls["n"] += 1
        return p.read_text()

    assert cache_mod.get_or_parse(f, parser) == "hello"
    assert cache_mod.get_or_parse(f, parser) == "hello"
    assert calls["n"] == 1  # 第二次命中,不重解析


def test_changed_file_reparses(cache_mod, tmp_path):
    f = _write(tmp_path, "a.jsonl", "one")
    assert cache_mod.get_or_parse(f, lambda p: p.read_text()) == "one"
    # 改內容(size 變) -> 指紋失效 -> 重解析
    f.write_text("one-two-three")
    assert cache_mod.get_or_parse(f, lambda p: p.read_text()) == "one-two-three"


# ── LRU 容量上限:記憶體有界 ─────────────────────────────────
def test_lru_evicts_oldest_over_cap(cache_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "MAX_ENTRIES", 3)
    files = [_write(tmp_path, f"s{i}.jsonl") for i in range(5)]
    for f in files:
        cache_mod.get_or_parse(f, lambda p: p.read_text())
    assert len(cache_mod._store) == 3
    # 最早的 s0/s1 已被逐出,最新的 s4 仍在
    assert str(files[0]) not in cache_mod._store
    assert str(files[4]) in cache_mod._store


def test_lru_access_refreshes_recency(cache_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "MAX_ENTRIES", 2)
    a = _write(tmp_path, "a.jsonl")
    b = _write(tmp_path, "b.jsonl")
    cache_mod.get_or_parse(a, lambda p: p.read_text())
    cache_mod.get_or_parse(b, lambda p: p.read_text())
    # 再命中 a,使 a 變成最近使用
    cache_mod.get_or_parse(a, lambda p: p.read_text())
    # 加入 c,應逐出最久未用的 b(而非 a)
    c = _write(tmp_path, "c.jsonl")
    cache_mod.get_or_parse(c, lambda p: p.read_text())
    assert str(a) in cache_mod._store
    assert str(b) not in cache_mod._store


def test_zero_cap_means_unbounded(cache_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "MAX_ENTRIES", 0)
    for i in range(20):
        cache_mod.get_or_parse(_write(tmp_path, f"s{i}.jsonl"), lambda p: p.read_text())
    assert len(cache_mod._store) == 20


# ── prune_missing:清掉已刪除的 session ──────────────────────
def test_prune_removes_deleted_files(cache_mod, tmp_path):
    files = [_write(tmp_path, f"s{i}.jsonl") for i in range(4)]
    for f in files:
        cache_mod.get_or_parse(f, lambda p: p.read_text())
    files[0].unlink()
    files[2].unlink()
    removed = cache_mod.prune_missing()
    assert removed == 2
    assert len(cache_mod._store) == 2
    assert all(Path(k).exists() for k in cache_mod._store)


def test_prune_noop_when_all_present(cache_mod, tmp_path):
    for i in range(3):
        cache_mod.get_or_parse(_write(tmp_path, f"s{i}.jsonl"), lambda p: p.read_text())
    assert cache_mod.prune_missing() == 0
    assert len(cache_mod._store) == 3
