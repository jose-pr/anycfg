"""The platform's standard configuration directories."""

from __future__ import annotations

import pathlib

import pytest

import anycfg


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    for var in ("APPDATA", "PROGRAMDATA", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.fixture
def posix(monkeypatch, home):
    monkeypatch.setattr("sys.platform", "linux")
    return home


@pytest.fixture
def windows(monkeypatch, home):
    monkeypatch.setattr("sys.platform", "win32")
    return home


class TestUserConfigPath:
    def test_posix_defaults_to_dot_config(self, posix):
        assert anycfg.user_config_path() == posix / ".config"

    def test_posix_honours_xdg_config_home(self, posix, monkeypatch, tmp_path):
        xdg = str(tmp_path / "xdg")
        monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
        assert anycfg.user_config_path("app.toml") == pathlib.Path(xdg, "app.toml")

    @pytest.mark.parametrize("value", ["", "relative/dir"])
    def test_posix_ignores_an_empty_or_relative_xdg(self, posix, monkeypatch, value):
        monkeypatch.setenv("XDG_CONFIG_HOME", value)
        assert anycfg.user_config_path() == posix / ".config"

    def test_macos_uses_the_xdg_rule(self, home, monkeypatch):
        monkeypatch.setattr("sys.platform", "darwin")
        assert anycfg.user_config_path() == home / ".config"

    def test_windows_uses_appdata(self, windows, monkeypatch, tmp_path):
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        assert anycfg.user_config_path("app.cfg") == tmp_path / "Roaming" / "app.cfg"

    def test_windows_without_appdata(self, windows):
        assert anycfg.user_config_path() == windows / "AppData" / "Roaming"

    def test_windows_ignores_xdg(self, windows, monkeypatch, tmp_path):
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert anycfg.user_config_path() == tmp_path / "Roaming"


class TestSystemConfigPath:
    def test_posix_is_etc(self, posix):
        assert anycfg.system_config_path() == pathlib.Path("/etc")
        assert anycfg.system_config_path("app/app.toml") == pathlib.Path(
            "/etc/app/app.toml"
        )

    def test_windows_uses_programdata(self, windows, monkeypatch, tmp_path):
        monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "PD"))
        assert anycfg.system_config_path("app.toml") == tmp_path / "PD" / "app.toml"

    def test_windows_without_programdata(self, windows):
        assert anycfg.system_config_path() == pathlib.Path("C:\\ProgramData")


@pytest.mark.parametrize("func", [anycfg.user_config_path, anycfg.system_config_path])
def test_name_accepts_a_path_and_creates_nothing(home, func):
    base = func()
    assert func(pathlib.PurePath("a", "b.toml")) == base / "a" / "b.toml"
    assert func("") == base
    assert not (home / ".config").exists()
    assert not (home / "AppData").exists()


def test_layering_skips_an_absent_layer(posix, tmp_path):
    system = tmp_path / "etc" / "app.toml"
    system.parent.mkdir()
    system.write_text('a = 1\nb = "system"\n')
    user = anycfg.user_config_path("app.toml")
    user.parent.mkdir()
    user.write_text('b = "user"\n')
    layers = [system, user, anycfg.user_config_path("absent.toml")]
    config = anycfg.Loader().load(*(p for p in layers if p.is_file()))
    assert config == {"a": 1, "b": "user"}
