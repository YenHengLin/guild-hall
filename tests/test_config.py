"""設定載入:環境變數 > config.yaml > 預設值。"""
from __future__ import annotations

import os

from app import config


def test_env_beats_yaml_beats_default(monkeypatch):
    monkeypatch.setattr(config, "_FILE", {"dispatch_rounds": 9})
    monkeypatch.delenv("GUILD_DISPATCH_ROUNDS", raising=False)
    assert config.get("GUILD_DISPATCH_ROUNDS", "6") == "9"
    monkeypatch.setenv("GUILD_DISPATCH_ROUNDS", "3")
    assert config.get("GUILD_DISPATCH_ROUNDS", "6") == "3"
    assert config.get("GUILD_NOT_SET_ANYWHERE", "dflt") == "dflt"


def test_yaml_bool_maps_to_flag(monkeypatch):
    monkeypatch.setattr(config, "_FILE", {"acceptance": False, "git_commit": True})
    monkeypatch.delenv("GUILD_ACCEPTANCE", raising=False)
    monkeypatch.delenv("GUILD_GIT_COMMIT", raising=False)
    assert config.get("GUILD_ACCEPTANCE", "1") == "0"
    assert config.get("GUILD_GIT_COMMIT", "0") == "1"


def test_tilde_expands_and_lists(monkeypatch):
    monkeypatch.setattr(config, "_FILE", {"projects_dir": "~/projects",
                                          "extra_write_roots": ["~/a", "D:/b"]})
    monkeypatch.delenv("GUILD_PROJECTS_DIR", raising=False)
    monkeypatch.delenv("GUILD_EXTRA_WRITE_ROOTS", raising=False)
    assert config.get("GUILD_PROJECTS_DIR") == os.path.expanduser("~/projects")
    assert config.get_list("GUILD_EXTRA_WRITE_ROOTS") == [os.path.expanduser("~/a"), "D:/b"]


def test_example_config_parses():
    import yaml
    data = yaml.safe_load((config.ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    assert data["dispatch_parallel"] == 2 and data["wiki_dir"] == ""
