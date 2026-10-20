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


class TestDefaultConfigs:
    @pytest.fixture
    def dirs(self, windows, monkeypatch, tmp_path):
        system, user = tmp_path / "ProgramData", tmp_path / "Roaming"
        system.mkdir()
        user.mkdir()
        monkeypatch.setenv("PROGRAMDATA", str(system))
        monkeypatch.setenv("APPDATA", str(user))
        return system, user

    def test_both_layers_user_last(self, dirs):
        system, user = dirs
        (system / "app.toml").write_text('a = 1\nb = "system"\n')
        (user / "app.toml").write_text('b = "user"\n')
        layers = anycfg.default_configs("app.toml")
        assert layers == [system / "app.toml", user / "app.toml"]
        assert anycfg.Loader().load(*layers) == {"a": 1, "b": "user"}

    @pytest.mark.parametrize("present", ["system", "user"])
    def test_an_absent_layer_is_none(self, dirs, present):
        system, user = dirs
        here = system if present == "system" else user
        (here / "app.toml").write_text("a = 1\n")
        layers = anycfg.default_configs("app.toml")
        assert layers == (
            [here / "app.toml", None]
            if present == "system"
            else [None, here / "app.toml"]
        )
        assert anycfg.Loader().load(*layers) == {"a": 1}

    def test_neither_present_loads_the_default(self, dirs):
        assert anycfg.default_configs("app.toml") == [None, None]
        assert anycfg.Loader().load(*anycfg.default_configs("app.toml")) is None
        assert (
            anycfg.Loader().load(*anycfg.default_configs("app.toml"), default={}) == {}
        )

    def test_a_directory_is_not_a_layer(self, dirs):
        system, _ = dirs
        (system / "app.toml").mkdir()
        assert anycfg.default_configs("app.toml") == [None, None]

    def test_subdirectories_in_the_name(self, dirs):
        _, user = dirs
        (user / "app").mkdir()
        (user / "app" / "app.toml").write_text("a = 1\n")
        assert anycfg.default_configs(pathlib.PurePath("app", "app.toml")) == [
            None,
            user / "app" / "app.toml",
        ]

    @pytest.mark.parametrize("name", [None, ""])
    def test_needs_a_name(self, name):
        with pytest.raises(ValueError, match="file name"):
            anycfg.default_configs(name)
