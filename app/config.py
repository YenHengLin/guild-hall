"""設定載入 —— 優先序:環境變數 > config.yaml > 程式內預設值。

config.yaml 放在專案根目錄(或用環境變數 GUILD_CONFIG 指定路徑),已被 .gitignore,
每台機器各自一份;範本見 config.example.yaml。

yaml 的 key = 環境變數名去掉 `GUILD_` 前綴、轉小寫,例如:
    GUILD_WIKI_DIR       ↔  wiki_dir
    GUILD_DISPATCH_MODEL ↔  dispatch_model
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("guild.config")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("GUILD_CONFIG", str(ROOT / "config.yaml")))


def _load() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml  # 只有真的有 config.yaml 才需要 PyYAML
    except ImportError:
        log.warning("找到 %s 但沒裝 PyYAML(pip install pyyaml),先忽略設定檔", CONFIG_PATH)
        return {}
    try:
        data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — 設定檔壞掉不該讓整個後端起不來
        log.error("讀取 %s 失敗,改用預設值:%s", CONFIG_PATH, exc)
        return {}
    return {str(k).lower(): v for k, v in data.items()} if isinstance(data, dict) else {}


_FILE = _load()


def _yaml_key(env_key: str) -> str:
    k = env_key.lower()
    return k[len("guild_"):] if k.startswith("guild_") else k


def get(env_key: str, default: str = "") -> str:
    """取一個設定值(一律回字串,與 os.environ.get 同介面,呼叫端自行 int()/=="1")。"""
    if env_key in os.environ:
        return _expand(os.environ[env_key])
    v = _FILE.get(_yaml_key(env_key))
    if v is None:
        return default
    if isinstance(v, bool):
        return "1" if v else "0"
    return _expand(str(v))


def _expand(s: str) -> str:
    """路徑值可寫 ~ 開頭(~/projects),展開成家目錄。"""
    return os.path.expanduser(s) if s.startswith("~") else s


def get_list(key: str) -> list[str]:
    """取 yaml 裡的清單設定(環境變數則以 ; 分隔)。"""
    if key in os.environ:
        return [_expand(s) for s in os.environ[key].split(";") if s.strip()]
    v = _FILE.get(_yaml_key(key))
    if isinstance(v, list):
        return [_expand(str(x)) for x in v if x]
    return [_expand(str(v))] if v else []
