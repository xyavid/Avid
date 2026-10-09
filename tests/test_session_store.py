"""会话目录的解析（环境变量 > 设置文件 > 默认）与设置文件的读写纪律。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avid.security import userdirs


@pytest.fixture
def home(tmp_path, monkeypatch):
    """AVID_HOME 指向临时目录（conftest 已设，这里显式拿回路径）。"""
    return userdirs.avid_home()


def test_default_store_sits_under_the_avid_home(home):
    assert userdirs.sessions_dir() == home / "sessions"
    assert userdirs.sessions_dir_source() == "default"
    assert userdirs.default_sessions_dir() == home / "sessions"


def test_settings_file_wins_over_the_default(home, tmp_path):
    custom = tmp_path / "elsewhere" / "sessions"
    userdirs.write_settings({"sessions_dir": str(custom)})

    assert userdirs.sessions_dir() == custom
    assert userdirs.sessions_dir_source() == "settings"
    # 默认值仍报默认值：界面要能显示「回到哪」。
    assert userdirs.default_sessions_dir() == home / "sessions"
    assert userdirs.sessions_dir() != userdirs.default_sessions_dir()


def test_env_wins_over_the_settings_file(home, tmp_path, monkeypatch):
    monkeypatch.setenv(userdirs.SESSIONS_DIR_ENV, str(tmp_path / "from-env"))
    userdirs.write_settings({"sessions_dir": str(tmp_path / "from-file")})

    assert userdirs.sessions_dir() == tmp_path / "from-env"
    assert userdirs.sessions_dir_source() == "env"


def test_blank_values_fall_through(monkeypatch):
    monkeypatch.setenv(userdirs.SESSIONS_DIR_ENV, "   ")
    userdirs.write_settings({"sessions_dir": "   "})

    assert userdirs.sessions_dir_source() == "default"


def test_paths_expand_user(monkeypatch):
    monkeypatch.delenv(userdirs.SESSIONS_DIR_ENV, raising=False)
    userdirs.write_settings({"sessions_dir": "~/some-sessions"})

    assert userdirs.sessions_dir() == Path("~/some-sessions").expanduser()


def test_missing_or_corrupt_settings_read_as_empty(home):
    assert userdirs.read_settings() == {}

    userdirs.settings_path().parent.mkdir(parents=True, exist_ok=True)
    userdirs.settings_path().write_text("{ 不是 JSON", encoding="utf-8")
    assert userdirs.read_settings() == {}
    assert userdirs.sessions_dir() == home / "sessions"

    userdirs.settings_path().write_text('["列表不算设置"]', encoding="utf-8")
    assert userdirs.read_settings() == {}


def test_write_settings_preserves_sibling_keys_and_removes_on_none(home):
    userdirs.write_settings({"sessions_dir": "/tmp/one", "other": 7})
    assert userdirs.read_settings() == {"version": 1, "sessions_dir": "/tmp/one", "other": 7}

    userdirs.write_settings({"sessions_dir": None})
    assert userdirs.read_settings() == {"version": 1, "other": 7}
    assert userdirs.sessions_dir() == home / "sessions"
    assert json.loads(userdirs.settings_path().read_text(encoding="utf-8"))["other"] == 7


def test_write_settings_leaves_no_temp_file(home):
    userdirs.write_settings({"sessions_dir": "/tmp/two"})

    leftovers = [p.name for p in userdirs.settings_path().parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_write_settings_merges_only_the_given_keys(home):
    userdirs.write_settings({"sessions_dir": "/tmp/three"})
    userdirs.write_settings({})

    assert userdirs.read_settings()["sessions_dir"] == "/tmp/three"
