"""Sources that are not files: `Env`, `Data` and `Text`."""

from __future__ import annotations

import copy
import os

import pytest

from anycfg import (
    DELETE,
    ConfigValueError,
    Loader,
    ParseError,
    UnsupportedFormatError,
    sources,
)
from anycfg.sources import _env


def _env_load(environ, prefix="", **options):
    return Loader().load(sources.Env(prefix, environ=environ, **options))


# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------


class TestEnv:
    def test_prefix_selects_and_is_removed(self):
        environ = {"APP_HOST": "h", "APP_PORT": "80", "OTHER": "ignored"}
        assert _env_load(environ, "APP_") == {"HOST": "h", "PORT": "80"}

    def test_no_prefix_reads_every_variable(self):
        environ = {"A": "1", "B": "2"}
        assert _env_load(environ) == {"A": "1", "B": "2"}

    def test_reads_os_environ_by_default(self, monkeypatch):
        monkeypatch.setenv("YACLTEST_ENV_ONE", "1")
        result = Loader().load(sources.Env("YACLTEST_ENV_"))
        assert result == {"ONE": "1"}

    def test_keys_keep_their_case(self):
        assert _env_load({"APP_MixedCase": "v"}, "APP_") == {"MixedCase": "v"}

    def test_lowercase_folds_keys(self):
        environ = {"APP_HOST": "h", "APP_DB__PORT": "1"}
        assert _env_load(environ, "APP_", lowercase=True) == {
            "host": "h",
            "db__port": "1",
        }
        assert _env_load(environ, "APP_", lowercase=True, nested="__") == {
            "host": "h",
            "db": {"port": "1"},
        }

    def test_nested_splits_on_the_delimiter(self):
        environ = {
            "APP_DB__HOST": "localhost",
            "APP_DB__PORT": "5432",
            "APP_FEATURES__CACHE": "true",
            "OTHER_DB__HOST": "ignored",
        }
        assert _env_load(environ, "APP_", nested="__") == {
            "DB": {"HOST": "localhost", "PORT": "5432"},
            "FEATURES": {"CACHE": "true"},
        }

    def test_nested_several_levels(self):
        environ = {"APP_A__B__C": "1", "APP_A__D": "2", "APP_E": "3"}
        assert _env_load(environ, "APP_", nested="__") == {
            "A": {"B": {"C": "1"}, "D": "2"},
            "E": "3",
        }

    def test_without_nested_the_delimiter_is_part_of_the_key(self):
        assert _env_load({"APP_DB__PORT": "1"}, "APP_") == {"DB__PORT": "1"}

    def test_values_stay_strings(self):
        environ = {
            "APP_PORT": "5432",
            "APP_DEBUG": "true",
            "APP_EMPTY": "null",
            "APP_ITEMS": '["a", 2]',
            "APP_DB__POOL": "5",
        }
        result = _env_load(environ, "APP_", nested="__")
        assert result == {
            "PORT": "5432",
            "DEBUG": "true",
            "EMPTY": "null",
            "ITEMS": '["a", 2]',
            "DB": {"POOL": "5"},
        }

    @pytest.mark.parametrize(
        "environ",
        [
            {"ZZ_DB": "sqlite", "ZZ_DB__PORT": "5432"},
            {"ZZ_DB__PORT": "5432", "ZZ_DB": "sqlite"},
        ],
        ids=["value_then_nested", "nested_then_value"],
    )
    def test_collision_names_both_variables(self, environ):
        with pytest.raises(ConfigValueError) as caught:
            _env_load(environ, "ZZ_", nested="__")
        message = str(caught.value)
        assert "'ZZ_DB'" in message
        assert "'ZZ_DB__PORT'" in message

    @pytest.mark.parametrize(
        "environ",
        [
            {"ZZ_A__B": "1", "ZZ_A__B__C": "2"},
            {"ZZ_A__B__C": "2", "ZZ_A__B": "1"},
        ],
        ids=["value_then_nested", "nested_then_value"],
    )
    def test_deep_collision_names_both_variables(self, environ):
        with pytest.raises(ConfigValueError) as caught:
            _env_load(environ, "ZZ_", nested="__")
        message = str(caught.value)
        assert "'ZZ_A__B'" in message
        assert "'ZZ_A__B__C'" in message

    def test_no_collision_is_possible_without_nested(self):
        environ = {"ZZ_DB": "sqlite", "ZZ_DB__PORT": "5432"}
        assert _env_load(environ, "ZZ_") == {"DB": "sqlite", "DB__PORT": "5432"}

    def test_variable_equal_to_the_prefix_is_skipped(self):
        environ = {"ZZ_": "dropped", "ZZ_KEEP": "k"}
        assert _env_load(environ, "ZZ_") == {"KEEP": "k"}
        assert _env_load(environ, "ZZ_", nested="__") == {"KEEP": "k"}

    def test_empty_segments_are_dropped(self):
        environ = {"ZZ_A____B": "1", "ZZ___": "dropped"}
        assert _env_load(environ, "ZZ_", nested="__") == {"A": {"B": "1"}}

    def test_prefix_case_insensitive_when_the_flag_is_set(self, monkeypatch):
        monkeypatch.setattr(_env, "_KEYS_CASE_INSENSITIVE", True)
        assert _env_load({"ZZENV_LOWER": "x"}, "zzenv_") == {"LOWER": "x"}

    def test_prefix_case_sensitive_when_the_flag_is_clear(self, monkeypatch):
        monkeypatch.setattr(_env, "_KEYS_CASE_INSENSITIVE", False)
        assert _env_load({"ZZENV_LOWER": "x"}, "zzenv_") == {}
        assert _env_load({"ZZENV_LOWER": "x"}, "ZZENV_") == {"LOWER": "x"}

    def test_prefix_matching_follows_the_platform(self):
        assert _env._KEYS_CASE_INSENSITIVE is (os.name == "nt")

    def test_environ_mapping_is_not_modified(self):
        environ = {"APP_DB__PORT": "1"}
        before = dict(environ)
        _env_load(environ, "APP_", nested="__")
        assert environ == before

    def test_layers_over_a_file_like_any_document(self):
        defaults = {"DB": {"HOST": "localhost", "PORT": "5432"}}
        env = sources.Env("APP_", nested="__", environ={"APP_DB__PORT": "6543"})
        assert Loader(merge="deep").load(defaults, env) == {
            "DB": {"HOST": "localhost", "PORT": "6543"}
        }

    def test_name_and_flags(self):
        source = sources.Env("APP_")
        assert source.name == "env:APP_"
        assert source.executes is False
        assert "APP_" in repr(source)

    def test_hydration_gives_types(self):
        import dataclasses

        @dataclasses.dataclass
        class Settings:
            PORT: int
            DEBUG: bool

        environ = {"APP_PORT": "8080", "APP_DEBUG": "yes"}
        result = Loader().load(sources.Env("APP_", environ=environ), into=Settings)
        assert result == Settings(PORT=8080, DEBUG=True)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


class TestData:
    def test_mapping_source_is_python_data(self):
        assert Loader().load({"a": 1}) == {"a": 1}

    def test_data_source_takes_a_list(self):
        # A bare list is a list of sources; Data makes it a document.
        assert Loader().load(sources.Data([1, 2])) == [1, 2]

    def test_result_is_a_deep_copy(self):
        data = {"a": {"b": [1, {"c": 2}]}}
        before = copy.deepcopy(data)
        for result in (Loader().load(data), Loader().load(sources.Data(data))):
            assert result == before
            assert result["a"] is not data["a"]
            result["a"]["b"].append(3)
            result["a"]["b"][1]["c"] = 9
            assert data == before

    @pytest.mark.parametrize("merge", ["simple", "substitute", "deep"])
    def test_merge_never_modifies_the_caller_data(self, merge):
        base = {"a": {"b": [1, {"c": 2}]}, "keep": "x"}
        over = {"a": {"b": [3], "d": 4}, "keep": None}
        before = (copy.deepcopy(base), copy.deepcopy(over))
        Loader(merge=merge).load(base, over)
        assert (base, over) == before

    def test_interpolation_never_modifies_the_caller_data(self, needs_jinja2):
        data = {"name": "world", "greeting": "hello {{ name }}"}
        before = copy.deepcopy(data)
        result = Loader(interpolate=True).load(data)
        assert result["greeting"] == "hello world"
        assert data == before

    def test_delete_marker_in_data_removes_a_key(self):
        result = Loader(merge="deep").load({"a": 1, "b": 2}, {"b": DELETE})
        assert result == {"a": 1}

    def test_delete_marker_alone_is_pruned(self):
        assert Loader().load({"a": 1, "b": DELETE}) == {"a": 1}

    def test_name_is_the_hash_key(self):
        result = Loader(collect="hash").load(
            sources.Data({"a": 1}, name="defaults.yaml"),
            sources.Data({"b": 2}, name="local.yaml"),
        )
        assert result == {"defaults": {"a": 1}, "local": {"b": 2}}

    def test_name_and_repr(self):
        source = sources.Data({}, name="defaults")
        assert source.name == "defaults"
        assert "defaults" in repr(source)
        assert sources.Data({}).name == ""
        assert source.executes is False


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


class TestText:
    def test_parses_in_the_named_format(self):
        assert Loader().load(sources.Text('{"a": 1}', format="json")) == {"a": 1}

    def test_bytes_decoded_with_the_load_encoding(self):
        data = '{"a": "caf\xe9"}'.encode("cp1252")
        source = sources.Text(data, format="json")
        assert Loader(encoding="cp1252").load(source) == {"a": "caf\xe9"}
        assert Loader().load(source, encoding="cp1252") == {"a": "caf\xe9"}

    def test_bytes_decoded_strictly(self):
        data = b'{"a": "caf' + bytes([233]) + b'"}'
        with pytest.raises(ParseError) as caught:
            Loader().load(sources.Text(data, format="json", name="inline.json"))
        assert "byte 10" in str(caught.value)

    def test_bytearray_accepted(self):
        source = sources.Text(bytearray(b'{"a": 1}'), format="json")
        assert Loader().load(source) == {"a": 1}

    def test_byte_order_mark_dropped(self):
        source = sources.Text('﻿{"a": 1}'.encode("utf-8"), format="json")
        assert Loader().load(source) == {"a": 1}

    @pytest.mark.parametrize("value", [5, None, {"a": 1}, ["a"]])
    def test_non_text_is_a_type_error(self, value):
        with pytest.raises(TypeError):
            sources.Text(value, format="json")

    def test_name_appears_in_a_parse_error(self):
        with pytest.raises(ParseError) as caught:
            Loader().load(sources.Text("{", format="json", name="inline.json"))
        assert caught.value.path == "inline.json"

    def test_name_is_the_hash_key(self):
        result = Loader(collect="hash").load(
            sources.Text('{"a": 1}', format="json", name="one.json"),
            sources.Text('{"b": 2}', format="json", name="two.json"),
        )
        assert result == {"one": {"a": 1}, "two": {"b": 2}}

    def test_unnamed_text_cannot_be_hashed(self):
        with pytest.raises(ConfigValueError):
            Loader(collect="hash").load(sources.Text("{}", format="json"))

    def test_unknown_format_is_refused(self):
        with pytest.raises(UnsupportedFormatError):
            Loader().load(sources.Text("{}", format="nosuchformat"))

    def test_caller_named_format_is_not_limited_to_document_formats(self):
        # The caller chose the format, so formats= (what documents may select)
        # does not apply to it.
        source = sources.Text("[s]\nk = v\n", format="ini")
        assert Loader(formats=["json"]).load(source) == {"s": {"k": "v"}}

    def test_relative_include_resolves_against_base_dir(self, tmp_path, needs_yaml):
        (tmp_path / "sub.yaml").write_text("inner: 1\n", encoding="utf-8")
        source = sources.Text("x: !include sub.yaml\n", format="yaml")
        assert Loader(base_dir=tmp_path).load(source) == {"x": {"inner": 1}}

    def test_layers_with_files(self, tmp_path):
        (tmp_path / "base.json").write_text('{"a": 1, "b": 1}', encoding="utf-8")
        result = Loader(base_dir=tmp_path).load(
            "base.json", sources.Text('{"b": 2}', format="json")
        )
        assert result == {"a": 1, "b": 2}

    def test_name_and_repr(self):
        source = sources.Text("{}", format="json", name="inline")
        assert source.name == "inline"
        assert "json" in repr(source)
        assert source.executes is False


class TestCoerce:
    ENVIRON = {"A_PORT": "80", "A_X": "yes", "A_N": "null", "A_E": "", "A_R": "0.5"}

    def test_strings_by_default(self):
        result = Loader().load(sources.Env("A_", environ=self.ENVIRON))
        assert result["PORT"] == "80"

    def test_core_schema(self):
        result = Loader().load(sources.Env("A_", coerce=True, environ=self.ENVIRON))
        assert result == {"PORT": 80, "X": "yes", "N": None, "E": "", "R": 0.5}

    def test_a_callable(self):
        result = Loader().load(
            sources.Env("A_", coerce=str.upper, environ={"A_K": "v"})
        )
        assert result == {"K": "V"}

    def test_anything_else(self):
        with pytest.raises(TypeError, match="coerce="):
            sources.Env("A_", coerce="yes")
