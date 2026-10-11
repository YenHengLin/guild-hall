"""training._extract_json 的邊界驗收。

回歸重點:舊版用貪心 `\\{.*\\}`,輸出裡有多個 JSON 片段或前後說明文字時,
會從第一個 `{` 吃到最後一個 `}` 抓到整段非法字串,`json.loads` 失敗後靜默
`return None`——進化提案就此無聲消失。改用 `raw_decode()` 逐個 `{` 定位,應能
從雜訊中撈出第一個完整的 JSON 物件。
"""
from __future__ import annotations

from app.training import _extract_json


def test_plain_object():
    assert _extract_json('{"route": "tool"}') == {"route": "tool"}


def test_fenced_object():
    text = '```json\n{"route": "memory", "name": "a"}\n```'
    assert _extract_json(text) == {"route": "memory", "name": "a"}


def test_multiple_fragments_does_not_silently_drop():
    # 舊版貪心正規式會抓到 `{"x":1} in code, result: {...}` 整段 → 非法 → None
    text = 'Found {"x": 1} in code, result: {"route": "skill", "name": "foo"}'
    out = _extract_json(text)
    assert out is not None
    assert out == {"x": 1}  # 回傳第一個完整物件,不再整段丟棄


def test_nested_object():
    text = 'note: {"route": "skill", "meta": {"a": 1, "b": [1, 2]}} done'
    assert _extract_json(text) == {"route": "skill", "meta": {"a": 1, "b": [1, 2]}}


def test_leading_non_object_json_is_skipped():
    # 先出現陣列(非 dict)應被跳過,撈出後面的物件
    text = '[1, 2, 3] then {"route": "script"}'
    assert _extract_json(text) == {"route": "script"}


def test_no_json_returns_none():
    assert _extract_json("no json here") is None


def test_empty_string_returns_none():
    assert _extract_json("") is None
