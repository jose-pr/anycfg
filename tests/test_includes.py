"""YAML ``!include``/``!load``: forms, resolution, options, cycles, context and reach."""

from __future__ import annotations

import logging
import sys

import pytest

import anycfg
from anycfg import (
    BudgetExceededError,
    CommandsDisabledError,
    ConfigError,
    ConfigTypeError,
    ConfigValueError,
    LoadContext,
    Loader,
    ParseError,
    Policy,
    PolicyError,
    UnsupportedFormatError,
)
from anycfg import formats as formats_registry
from anycfg.formats import Format
from anycfg.sources import Text

from _extras import needs_jinja2, needs_toml

pytestmark = pytest.mark.usefixtures("needs_yaml")

LOGGER = "anycfg"


def _write(path, text, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


def _quote(text):
    """*text* as a single-quoted YAML scalar: backslashes stay literal."""
    return "'" + text.replace("'", "''") + "'"


def _emitter(tmp_path, body, name="emit.py"):
    """A command line running a Python script with this interpreter."""
    script = _write(tmp_path / name, body)
    return f'"{sys.executable}" "{script}"'


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


class TestForms:
    def test_scalar(self, tmp_path):
        _write(tmp_path / "db.yaml", "host: h\n")
        _write(tmp_path / "app.yaml", "db: !include db.yaml\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"db": {"host": "h"}}

    def test_load_tag_is_the_same(self, tmp_path):
        _write(tmp_path / "db.yaml", "host: h\n")
        _write(tmp_path / "app.yaml", "db: !load db.yaml\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"db": {"host": "h"}}

    def test_sequence_merged_in_order(self, tmp_path):
        _write(tmp_path / "base.yaml", "a: 1\nshared: base\n")
        _write(tmp_path / "local.yaml", "b: 2\nshared: local\n")
        _write(tmp_path / "app.yaml", "all: !include [base.yaml, local.yaml]\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {
            "all": {"a": 1, "b": 2, "shared": "local"}
        }

    def test_mapping(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(tmp_path / "app.yaml", "opt: !include {pathname: x.yaml}\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"opt": {"a": 1}}

    def test_mapping_with_a_list_of_paths(self, tmp_path):
        _write(tmp_path / "a.yaml", "a: 1\n")
        _write(tmp_path / "b.yaml", "b: 2\n")
        _write(tmp_path / "app.yaml", "o: !include {pathname: [a.yaml, b.yaml]}\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"o": {"a": 1, "b": 2}}

    def test_block_mapping(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(
            tmp_path / "app.yaml", "opt: !include\n  pathname: x.yaml\n  default: {}\n"
        )
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"opt": {"a": 1}}

    def test_mapping_without_pathname(self, tmp_path):
        _write(tmp_path / "app.yaml", "child: !include {encoding: utf-8}\n")
        with pytest.raises(ParseError, match="pathname") as caught:
            anycfg.load(str(tmp_path / "app.yaml"))
        assert caught.value.lineno == 1

    def test_empty_sequence(self, tmp_path):
        _write(tmp_path / "app.yaml", "child: !include []\n")
        with pytest.raises(ParseError, match="at least one"):
            anycfg.load(str(tmp_path / "app.yaml"))

    def test_include_at_the_top_level(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(tmp_path / "app.yaml", "!include x.yaml\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"a": 1}

    def test_include_inside_a_list(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(tmp_path / "app.yaml", "items:\n  - !include x.yaml\n  - plain\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"items": [{"a": 1}, "plain"]}

    def test_same_file_included_twice_is_not_a_cycle(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(tmp_path / "app.yaml", "p: !include x.yaml\nq: !include x.yaml\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {
            "p": {"a": 1},
            "q": {"a": 1},
        }

    def test_without_a_loader(self):
        # A direct format call has no loader to resolve an include through.
        with pytest.raises(ParseError, match="Loader"):
            formats_registry.get("yaml")().loads("x: !include a.yaml\n")
        with pytest.raises(ConfigValueError):
            LoadContext().include("a.yaml")

    def test_stock_safe_loader_never_resolves_include(self, tmp_path):
        import yaml

        target = _write(tmp_path / "t.yaml", "a: 1\n")
        anycfg.loads("a: 1\n", format="yaml")
        assert "!include" not in yaml.SafeLoader.yaml_constructors
        with pytest.raises(yaml.constructor.ConstructorError):
            yaml.safe_load(f"x: !include {_quote(target.as_posix())}")

    def test_refused_when_the_policy_disallows_includes(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(tmp_path / "app.yaml", "x: !include x.yaml\n")
        with pytest.raises(PolicyError):
            anycfg.load(str(tmp_path / "app.yaml"), policy=Policy(allow_includes=False))


# ---------------------------------------------------------------------------
# Resolution: relative to the including file
# ---------------------------------------------------------------------------


class TestResolution:
    @staticmethod
    def _tree(tmp_path):
        """conf/app.yaml including db.json, and a decoy db.json in the CWD."""
        conf = tmp_path / "conf"
        _write(conf / "db.json", '{"host": "from-conf"}')
        _write(conf / "app.yaml", "db: !include db.json\n")
        elsewhere = tmp_path / "elsewhere"
        _write(elsewhere / "db.json", '{"host": "from-cwd"}')
        return conf, elsewhere

    def test_relative_to_the_including_file(self, tmp_path, monkeypatch):
        conf, elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(elsewhere)
        assert anycfg.load(str(conf / "app.yaml")) == {"db": {"host": "from-conf"}}
        assert Loader(base_dir=tmp_path).load("conf/app.yaml") == {
            "db": {"host": "from-conf"}
        }

    def test_every_depth(self, tmp_path, monkeypatch):
        conf = tmp_path / "conf"
        _write(conf / "leaf.json", '{"z": 999}')  # decoy next to app.yaml
        _write(conf / "sub" / "leaf.json", '{"z": 1}')
        _write(conf / "sub" / "mid.yaml", "y: !include leaf.json\n")
        _write(conf / "app.yaml", "x: !include sub/mid.yaml\n")
        monkeypatch.chdir(tmp_path)
        assert Loader(base_dir=conf).load("app.yaml") == {"x": {"y": {"z": 1}}}

    def test_same_relative_name_at_each_depth_is_not_a_cycle(self, tmp_path):
        conf = tmp_path / "conf"
        _write(conf / "a.yaml", "x: !include x/a.yaml\n")
        _write(conf / "x" / "a.yaml", "y: !include x/a.yaml\n")
        _write(conf / "x" / "x" / "a.yaml", "z: 1\n")
        assert Loader(base_dir=conf).load("a.yaml") == {"x": {"y": {"z": 1}}}

    @pytest.mark.parametrize(
        "include, expected",
        [
            ('o: !include ["one.yaml", "two.yaml"]\n', {"a": 1, "b": 2}),
            ('o: !include {pathname: "one.yaml"}\n', {"a": 1}),
            ('o: !include {pathname: ["one.yaml", "two.yaml"]}\n', {"a": 1, "b": 2}),
        ],
    )
    def test_every_form_resolves_relative(
        self, tmp_path, monkeypatch, include, expected
    ):
        conf = tmp_path / "conf"
        _write(conf / "one.yaml", "a: 1\n")
        _write(conf / "two.yaml", "b: 2\n")
        _write(conf / "app.yaml", include)
        monkeypatch.chdir(tmp_path)
        assert Loader(base_dir=tmp_path).load(str(conf / "app.yaml")) == {"o": expected}

    def test_parent_directory_reference(self, tmp_path):
        _write(tmp_path / "shared" / "db.json", '{"host": "shared"}')
        _write(tmp_path / "conf" / "app.yaml", "db: !include ../shared/db.json\n")
        assert anycfg.load(str(tmp_path / "conf" / "app.yaml")) == {
            "db": {"host": "shared"}
        }

    def test_absolute_include_path(self, tmp_path, monkeypatch):
        conf, elsewhere = self._tree(tmp_path)
        other = _write(tmp_path / "other.json", '{"host": "from-other"}')
        _write(conf / "app.yaml", f"db: !include {_quote(str(other))}\n")
        monkeypatch.chdir(elsewhere)
        assert anycfg.load(str(conf / "app.yaml")) == {"db": {"host": "from-other"}}

    def test_relative_base_dir(self, tmp_path, monkeypatch):
        conf, _elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert Loader(base_dir="conf").load("app.yaml") == {"db": {"host": "from-conf"}}

    def test_in_memory_content_resolves_against_base_dir(self, tmp_path, monkeypatch):
        conf, elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(elsewhere)
        result = Loader(base_dir=conf).load(
            Text("d: !include db.json\n", format="yaml", name="mem.yaml")
        )
        assert result == {"d": {"host": "from-conf"}}

    def test_mempath_document_resolves_against_base_dir(self, tmp_path, monkeypatch):
        from pathapi.mempath import MemPath

        conf, elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(elsewhere)
        document = MemPath("memdir/app.yaml")
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_bytes(b"d: !include db.json\n")
        assert Loader(base_dir=conf).load(document) == {"d": {"host": "from-conf"}}

    def test_loads_resolves_against_the_working_directory(self, tmp_path, monkeypatch):
        conf, _elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(conf)
        assert anycfg.loads("d: !include db.json\n", format="yaml") == {
            "d": {"host": "from-conf"}
        }

    def test_in_memory_content_without_base_dir_uses_the_cwd(
        self, tmp_path, monkeypatch
    ):
        _conf, elsewhere = self._tree(tmp_path)
        monkeypatch.chdir(elsewhere)
        assert Loader().load(Text("d: !include db.json\n", format="yaml")) == {
            "d": {"host": "from-cwd"}
        }


# ---------------------------------------------------------------------------
# Mapping options
# ---------------------------------------------------------------------------


class TestOptions:
    def test_default_when_nothing_matches(self, tmp_path):
        _write(
            tmp_path / "app.yaml",
            "opt: !include {pathname: 'none-*.yaml', default: {fallback: true}}\n",
        )
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"opt": {"fallback": True}}

    def test_default_is_not_merged(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(
            tmp_path / "app.yaml", "opt: !include {pathname: x.yaml, default: {d: 1}}\n"
        )
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"opt": {"a": 1}}

    def test_no_match_without_default_is_none(self, tmp_path):
        _write(tmp_path / "app.yaml", "opt: !include 'none-*.yaml'\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"opt": None}

    def test_missing_file_without_glob_raises(self, tmp_path):
        _write(
            tmp_path / "app.yaml", "opt: !include {pathname: none.yaml, default: {}}\n"
        )
        with pytest.raises(FileNotFoundError):
            anycfg.load(str(tmp_path / "app.yaml"))

    def test_format(self, tmp_path):
        _write(tmp_path / "x.txt", '{"k": 2}')
        _write(tmp_path / "app.yaml", "t: !include {pathname: x.txt, format: json}\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"t": {"k": 2}}

    def test_unknown_format(self, tmp_path):
        _write(tmp_path / "x.txt", "{}")
        _write(tmp_path / "app.yaml", "t: !include {pathname: x.txt, format: nope}\n")
        with pytest.raises(UnsupportedFormatError):
            anycfg.load(str(tmp_path / "app.yaml"))

    def test_merge_name(self, tmp_path):
        _write(tmp_path / "a.yaml", "db: {host: h, port: 1}\n")
        _write(tmp_path / "b.yaml", "db: {port: 2}\n")
        _write(
            tmp_path / "app.yaml",
            "simple: !include [a.yaml, b.yaml]\n"
            "deep: !include {pathname: [a.yaml, b.yaml], merge: deep}\n",
        )
        assert anycfg.load(str(tmp_path / "app.yaml")) == {
            "simple": {"db": {"port": 2}},
            "deep": {"db": {"host": "h", "port": 2}},
        }

    def test_loader_merge_is_the_default(self, tmp_path):
        _write(tmp_path / "a.yaml", "db: {host: h, port: 1}\n")
        _write(tmp_path / "b.yaml", "db: {port: 2}\n")
        _write(tmp_path / "app.yaml", "all: !include [a.yaml, b.yaml]\n")
        assert Loader(merge="deep").load(str(tmp_path / "app.yaml")) == {
            "all": {"db": {"host": "h", "port": 2}}
        }

    def test_loader_merge_applies_to_an_include_glob(self, tmp_path):
        _write(tmp_path / "conf.d" / "a.yaml", "db: {host: a, port: 1}\n")
        _write(tmp_path / "conf.d" / "b.yaml", "db: {port: 2}\n")
        _write(tmp_path / "app.yaml", 'conf: !include "conf.d/*.yaml"\n')
        assert Loader(merge="deep").load(str(tmp_path / "app.yaml")) == {
            "conf": {"db": {"host": "a", "port": 2}}
        }

    def test_merge_must_be_a_name(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(tmp_path / "app.yaml", "o: !include {pathname: x.yaml, merge: [deep]}\n")
        with pytest.raises(ConfigTypeError, match="merge"):
            anycfg.load(str(tmp_path / "app.yaml"))

    def test_unknown_merge_name(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(tmp_path / "app.yaml", "o: !include {pathname: x.yaml, merge: nope}\n")
        with pytest.raises(ValueError, match="nope"):
            anycfg.load(str(tmp_path / "app.yaml"))

    def test_merge_options(self, tmp_path):
        _write(tmp_path / "a.yaml", "items:\n  - {name: api, enabled: false}\n")
        _write(tmp_path / "b.yaml", "items:\n  - {name: api, enabled: true}\n")
        _write(
            tmp_path / "app.yaml",
            "plain: !include {pathname: [a.yaml, b.yaml], merge: deep}\n"
            "merged: !include {pathname: [a.yaml, b.yaml], merge: deep,"
            " merge_options: {mergelists: true}}\n",
        )
        result = anycfg.load(str(tmp_path / "app.yaml"))
        assert result["merged"] == {"items": [{"name": "api", "enabled": True}]}
        assert result["plain"] == {
            "items": [
                {"name": "api", "enabled": False},
                {"name": "api", "enabled": True},
            ]
        }

    def test_merge_options_must_be_a_mapping(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(
            tmp_path / "app.yaml",
            "o: !include {pathname: x.yaml, merge_options: [1]}\n",
        )
        with pytest.raises(ConfigTypeError):
            anycfg.load(str(tmp_path / "app.yaml"))

    def test_flatten(self, tmp_path):
        _write(tmp_path / "parts" / "a.yaml", "x: {a: 1}\ny: {b: 2}\n")
        _write(
            tmp_path / "app.yaml",
            "o: !include {pathname: parts/a.yaml, flatten: true}\n",
        )
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"o": {"a": 1, "b": 2}}

    def test_flatten_non_mapping_member(self, tmp_path):
        _write(tmp_path / "a.yaml", "x: 1\n")
        _write(tmp_path / "app.yaml", "o: !include {pathname: a.yaml, flatten: true}\n")
        with pytest.raises(ConfigTypeError):
            anycfg.load(str(tmp_path / "app.yaml"))

    def test_recursive(self, tmp_path):
        _write(tmp_path / "sub" / "y.yaml", "e: 1\n")
        _write(tmp_path / "sub" / "deep" / "x.yaml", "d: 1\n")
        _write(
            tmp_path / "app.yaml",
            "flat: !include 'sub/**/*.yaml'\n"
            "rec: !include {pathname: 'sub/**/*.yaml', recursive: true}\n",
        )
        assert anycfg.load(str(tmp_path / "app.yaml")) == {
            "flat": {"d": 1},
            "rec": {"d": 1, "e": 1},
        }

    def test_loader_recursive_is_the_default(self, tmp_path):
        _write(tmp_path / "sub" / "y.yaml", "e: 1\n")
        _write(tmp_path / "sub" / "deep" / "x.yaml", "d: 1\n")
        _write(tmp_path / "app.yaml", "rec: !include 'sub/**/*.yaml'\n")
        assert Loader(recursive=True).load(str(tmp_path / "app.yaml")) == {
            "rec": {"d": 1, "e": 1}
        }

    @pytest.mark.parametrize(
        "key, value",
        [
            ("allow_commands", "true"),
            ("sandbox", "false"),
            ("policy", "{}"),
            ("key_factory", "unlink"),
            ("transform", "value.a"),
            ("loader", "command"),
            ("on_error", "true"),
            ("base_dir", "/"),
            ("interpolate", "true"),
        ],
    )
    def test_other_keys_are_dropped_with_a_warning(self, tmp_path, caplog, key, value):
        victim = _write(tmp_path / "victim.yaml", "a: 1\n")
        _write(
            tmp_path / "app.yaml",
            f"x: !include {{pathname: victim.yaml, {key}: {value}}}\n",
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = anycfg.load(str(tmp_path / "app.yaml"))
        assert result == {"x": {"a": 1}}
        assert victim.exists()
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert len(warnings) == 1 and key in warnings[0]

    def test_allowed_keys_are_not_warned(self, tmp_path, caplog):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(
            tmp_path / "app.yaml",
            "x: !include {pathname: x.yaml, encoding: utf-8, default: {}, flatten: false,"
            " merge: deep, merge_options: {}, recursive: false, format: yaml}\n",
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert anycfg.load(str(tmp_path / "app.yaml")) == {"x": {"a": 1}}
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_a_dropped_key_cannot_enable_commands(self, tmp_path):
        marker = tmp_path / "ran.marker"
        command = _emitter(
            tmp_path,
            f"import pathlib\npathlib.Path({str(marker)!r}).touch()\nprint('{{}}')\n",
        )
        _write(
            tmp_path / "app.yaml",
            f"x: !include {{pathname: {_quote('cmd+json://' + command)},"
            " allow_commands: true}\n",
        )
        with pytest.raises(CommandsDisabledError):
            anycfg.load(str(tmp_path / "app.yaml"))
        assert not marker.exists()


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


class TestEncoding:
    CAFE = "café"

    @classmethod
    def _chain(cls, tmp_path, codec="cp1252"):
        """main.yaml -> sub.yaml -> leaf.yaml, all written in *codec*."""
        _write(tmp_path / "leaf.yaml", f"c: {cls.CAFE}\n", codec)
        _write(tmp_path / "sub.yaml", "b: !include leaf.yaml\n", codec)
        _write(tmp_path / "main.yaml", "a: !include sub.yaml\n", codec)
        return {"a": {"b": {"c": cls.CAFE}}}

    @pytest.mark.parametrize("entry_point", ["loader_call", "module_load", "load_all"])
    def test_per_call_encoding_reaches_every_depth(self, tmp_path, entry_point):
        expected = self._chain(tmp_path)
        main = str(tmp_path / "main.yaml")
        if entry_point == "loader_call":
            result = Loader().load(main, encoding="cp1252")
        elif entry_point == "module_load":
            result = anycfg.load(main, encoding="cp1252")
        else:
            (result,) = Loader().load_all(main, encoding="cp1252")
        assert result == expected

    def test_instance_encoding_reaches_includes(self, tmp_path):
        expected = self._chain(tmp_path)
        assert Loader(encoding="cp1252").load(str(tmp_path / "main.yaml")) == expected

    def test_per_call_encoding_overrides_the_instance(self, tmp_path):
        _write(tmp_path / "sub.yaml", f"b: {self.CAFE}\n")
        _write(tmp_path / "main.yaml", "a: !include sub.yaml\n")
        result = Loader(encoding="cp1252").load(
            str(tmp_path / "main.yaml"), encoding="utf-8"
        )
        assert result == {"a": {"b": self.CAFE}}

    def test_without_the_right_encoding_decoding_fails(self, tmp_path):
        self._chain(tmp_path)
        _write(tmp_path / "main.yaml", "a: !include sub.yaml\n")
        with pytest.raises(ParseError, match="leaf.yaml"):
            anycfg.load(str(tmp_path / "main.yaml"))

    def test_include_encoding_is_inherited_by_its_includes(self, tmp_path):
        _write(tmp_path / "leaf.yaml", f"c: {self.CAFE}\n", "cp1252")
        _write(tmp_path / "sub.yaml", "b: !include leaf.yaml\n", "cp1252")
        _write(
            tmp_path / "main.yaml",
            "a: !include {pathname: sub.yaml, encoding: cp1252}\n",
        )
        assert anycfg.load(str(tmp_path / "main.yaml")) == {
            "a": {"b": {"c": self.CAFE}}
        }

    def test_include_encoding_applies_to_its_target_only(self, tmp_path):
        _write(tmp_path / "mid.yaml", "b: 1\n", "utf-16")
        _write(tmp_path / "other.yaml", f"o: {self.CAFE}\n", "cp1252")
        _write(
            tmp_path / "main.yaml",
            "a: !include {pathname: mid.yaml, encoding: utf-16}\nb: !include other.yaml\n",
            "cp1252",
        )
        assert Loader().load(str(tmp_path / "main.yaml"), encoding="cp1252") == {
            "a": {"b": 1},
            "b": {"o": self.CAFE},
        }

    def test_null_encoding_inherits(self, tmp_path):
        self._chain(tmp_path)
        _write(
            tmp_path / "main.yaml",
            "a: !include {pathname: sub.yaml, encoding: null}\n",
            "cp1252",
        )
        assert Loader().load(str(tmp_path / "main.yaml"), encoding="cp1252") == {
            "a": {"b": {"c": self.CAFE}}
        }

    def test_sequence_form(self, tmp_path):
        _write(tmp_path / "a.yaml", f"x: {self.CAFE}\n", "cp1252")
        _write(tmp_path / "b.yaml", f"y: {self.CAFE}\n", "cp1252")
        _write(tmp_path / "main.yaml", "s: !include [a.yaml, b.yaml]\n")
        assert Loader().load(str(tmp_path / "main.yaml"), encoding="cp1252") == {
            "s": {"x": self.CAFE, "y": self.CAFE}
        }

    def test_bom_in_an_included_file(self, tmp_path):
        (tmp_path / "sub.yaml").write_bytes(chr(0xFEFF).encode("utf-8") + b"b: 1\n")
        _write(tmp_path / "main.yaml", "a: !include sub.yaml\n")
        assert anycfg.load(str(tmp_path / "main.yaml")) == {"a": {"b": 1}}


# ---------------------------------------------------------------------------
# Globs
# ---------------------------------------------------------------------------


class TestGlobs:
    def test_expands_next_to_the_including_file(self, tmp_path, monkeypatch):
        conf = tmp_path / "conf"
        _write(conf / "parts" / "p1.yaml", "a: 1\n")
        _write(conf / "parts" / "p2.yaml", "b: 2\n")
        _write(tmp_path / "parts" / "decoy.yaml", "decoy: 1\n")
        _write(conf / "app.yaml", 'all: !include "parts/*.yaml"\n')
        monkeypatch.chdir(tmp_path)
        assert Loader(base_dir=tmp_path).load(str(conf / "app.yaml")) == {
            "all": {"a": 1, "b": 2}
        }

    def test_matches_merge_in_sorted_order(self, tmp_path):
        _write(tmp_path / "parts" / "20.yaml", "v: second\n")
        _write(tmp_path / "parts" / "10.yaml", "v: first\n")
        _write(tmp_path / "app.yaml", 'v: !include "parts/*.yaml"\n')
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"v": {"v": "second"}}

    def test_glob_of_mixed_formats(self, tmp_path):
        _write(tmp_path / "parts" / "a.json", '{"a": 1}')
        _write(tmp_path / "parts" / "b.yaml", "b: 2\n")
        _write(tmp_path / "parts" / "c.env", "C=3\n")
        _write(tmp_path / "app.yaml", 'all: !include "parts/*"\n')
        assert anycfg.load(str(tmp_path / "app.yaml")) == {
            "all": {"a": 1, "b": 2, "C": "3"}
        }

    def test_including_file_matched_by_its_own_glob_is_a_cycle(self, tmp_path):
        _write(tmp_path / "a.yaml", "a: 1\n")
        _write(tmp_path / "app.yaml", 'all: !include "*.yaml"\n')
        with pytest.raises(ConfigValueError, match="include cycle"):
            anycfg.load(str(tmp_path / "app.yaml"))


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------


class TestCycles:
    def test_direct(self, tmp_path):
        _write(tmp_path / "self.yaml", "x: !include self.yaml\n")
        with pytest.raises(ConfigValueError, match="include cycle"):
            anycfg.load(str(tmp_path / "self.yaml"))

    def test_indirect_names_the_chain(self, tmp_path):
        _write(tmp_path / "a.yaml", "x: !include b.yaml\n")
        _write(tmp_path / "b.yaml", "y: !include a.yaml\n")
        with pytest.raises(ConfigValueError) as caught:
            Loader(base_dir=tmp_path).load("a.yaml")
        error = caught.value
        assert isinstance(error, ValueError)
        message = str(error)
        assert message.startswith("include cycle: ")
        chain = message[len("include cycle: ") :].split(" [")[0].split(" -> ")
        assert [name.replace("\\", "/").rsplit("/", 1)[-1] for name in chain] == [
            "a.yaml",
            "b.yaml",
            "a.yaml",
        ]

    def test_via_parent_directory_spelling(self, tmp_path):
        _write(tmp_path / "conf" / "self.yaml", "x: !include ../conf/self.yaml\n")
        with pytest.raises(ConfigValueError, match="include cycle"):
            anycfg.load(str(tmp_path / "conf" / "self.yaml"))

    def test_via_dot_spelling(self, tmp_path):
        _write(tmp_path / "a.yaml", "x: !include ./b.yaml\n")
        _write(tmp_path / "b.yaml", "y: !include ./sub/../a.yaml\n")
        with pytest.raises(ConfigValueError, match="include cycle"):
            anycfg.load(str(tmp_path / "a.yaml"))

    def test_three_files(self, tmp_path):
        _write(tmp_path / "a.yaml", "x: !include b.yaml\n")
        _write(tmp_path / "b.yaml", "y: !include c.yaml\n")
        _write(tmp_path / "c.yaml", "z: !include a.yaml\n")
        with pytest.raises(ConfigValueError, match="include cycle"):
            anycfg.load(str(tmp_path / "a.yaml"))

    def test_include_budget(self, tmp_path):
        _write(tmp_path / "x.yaml", "a: 1\n")
        _write(tmp_path / "app.yaml", "p: !include x.yaml\nq: !include x.yaml\n")
        with pytest.raises(BudgetExceededError, match="max_includes"):
            anycfg.load(str(tmp_path / "app.yaml"), policy=Policy(max_includes=1))

    def test_depth_budget(self, tmp_path):
        _write(tmp_path / "a.yaml", "x: !include b.yaml\n")
        _write(tmp_path / "b.yaml", "y: !include c.yaml\n")
        _write(tmp_path / "c.yaml", "z: 1\n")
        assert anycfg.load(str(tmp_path / "a.yaml"), policy=Policy(max_depth=3))
        with pytest.raises(BudgetExceededError, match="max_depth"):
            anycfg.load(str(tmp_path / "a.yaml"), policy=Policy(max_depth=2))


# ---------------------------------------------------------------------------
# Error context
# ---------------------------------------------------------------------------


class TestErrorContext:
    def test_names_the_child_and_the_include_site(self, tmp_path):
        parent = _write(tmp_path / "parent.yaml", "a: 1\nchild: !include child.yaml\n")
        child = _write(tmp_path / "child.yaml", "b: [unclosed\n")
        with pytest.raises(ParseError) as caught:
            anycfg.load(str(parent))
        error = caught.value
        assert error.path == str(child)
        message = str(error)
        assert f"included from {parent}, line 2" in message

    def test_non_yaml_child_keeps_its_type(self, tmp_path):
        parent = _write(tmp_path / "parent.yaml", "secrets: !include secrets.json\n")
        _write(tmp_path / "secrets.json", '{"a": }')
        with pytest.raises(ParseError) as caught:
            anycfg.load(str(parent))
        error = caught.value
        assert type(error) is ParseError
        assert error.config_source.endswith("secrets.json")
        assert error.config_frames[0].kind == "include"
        assert error.config_frames[0].source == str(parent)
        assert error.config_frames[0].line == 1
        assert f"included from {parent}, line 1" in str(error)

    def test_nested_frames_innermost_first_rendered_once(self, tmp_path):
        top = _write(tmp_path / "top.yaml", "mid: !include mid.yaml\n")
        mid = _write(tmp_path / "mid.yaml", "x: 1\nleaf: !include leaf.yaml\n")
        _write(tmp_path / "leaf.yaml", "b: [unclosed\n")
        with pytest.raises(ParseError) as caught:
            anycfg.load(str(top))
        error = caught.value
        assert [(f.kind, f.source, f.line) for f in error.config_frames] == [
            ("include", str(mid), 2),
            ("include", str(top), 1),
        ]
        message = str(error)
        suffix = message[message.rindex(" [") :]
        assert suffix.count("mid.yaml") == 1
        assert suffix.count("top.yaml") == 1
        # The innermost file is the ParseError's own path, not repeated.
        assert "leaf.yaml" not in suffix
        assert suffix.index("mid.yaml") < suffix.index("top.yaml")

    def test_missing_include_names_the_including_file(self, tmp_path):
        parent = _write(tmp_path / "parent.yaml", "missing: !include nope/x.yaml\n")
        with pytest.raises(FileNotFoundError) as caught:
            anycfg.load(str(parent))
        assert "parent.yaml" in str(caught.value)

    def test_in_memory_include_site(self, tmp_path):
        _write(tmp_path / "bad.json", '{"a": }')
        with pytest.raises(ParseError) as caught:
            Loader(base_dir=tmp_path).load(
                Text("a: 1\nb: !include bad.json\n", format="yaml", name="mem")
            )
        assert caught.value.config_frames[0].line == 2

    def test_error_identity_is_preserved_through_levels(self, tmp_path):
        _write(tmp_path / "top.yaml", "mid: !include mid.yaml\n")
        _write(tmp_path / "mid.yaml", "leaf: !include leaf.json\n")
        _write(tmp_path / "leaf.json", "{bad")
        seen = []

        def record(event):
            seen.append(event.error)
            return False

        with pytest.raises(ParseError) as caught:
            Loader(on_error=record).load(str(tmp_path / "top.yaml"))
        assert len(seen) == 3
        assert all(error is caught.value for error in seen)


# ---------------------------------------------------------------------------
# Scope: anchors and interpolation
# ---------------------------------------------------------------------------


class TestScope:
    def test_each_included_document_is_its_own_anchor_scope(self, tmp_path):
        _write(tmp_path / "app.yaml", "base: &b {x: 1}\nchild: !include child.yaml\n")
        _write(tmp_path / "child.yaml", "y: *b\n")
        with pytest.raises(ParseError, match="alias"):
            anycfg.load(str(tmp_path / "app.yaml"))

    def test_anchors_inside_an_include_work(self, tmp_path):
        _write(tmp_path / "child.yaml", "a: &x {k: 1}\nb: *x\n")
        _write(tmp_path / "app.yaml", "c: !include child.yaml\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {
            "c": {"a": {"k": 1}, "b": {"k": 1}}
        }

    def test_included_templates_stay_raw_without_interpolate(self, tmp_path):
        _write(tmp_path / "svc.yaml", 'url: "http://{{ host }}/api"\n')
        _write(tmp_path / "app.yaml", "host: example.com\nsvc: !include svc.yaml\n")
        assert anycfg.load(str(tmp_path / "app.yaml"))["svc"] == {
            "url": "http://{{ host }}/api"
        }

    @needs_jinja2
    def test_include_sees_the_merged_document_scope(self, tmp_path):
        _write(tmp_path / "svc.yaml", 'url: "http://{{ host }}/api"\n')
        _write(tmp_path / "app.yaml", "host: example.com\nsvc: !include svc.yaml\n")
        result = Loader(interpolate=True).load(str(tmp_path / "app.yaml"))
        assert result["svc"]["url"] == "http://example.com/api"

    @needs_jinja2
    def test_parent_reference_under_strict(self, tmp_path):
        # Rendered only as part of the merged document, where the name exists.
        _write(tmp_path / "svc.yaml", 'url: "http://{{ host }}/api"\n')
        _write(tmp_path / "app.yaml", "host: example.com\nsvc: !include svc.yaml\n")
        loader = Loader(interpolate=True, policy=Policy(strict=True))
        assert loader.load(str(tmp_path / "app.yaml"))["svc"]["url"] == (
            "http://example.com/api"
        )

    @needs_jinja2
    def test_included_value_is_rendered_once(self, tmp_path):
        _write(tmp_path / "inc.yaml", "lit: \"{{ '{{ x }}' }}\"\n")
        _write(tmp_path / "app.yaml", "x: SURPRISE\ninc: !include inc.yaml\n")
        result = Loader(interpolate=True).load(str(tmp_path / "app.yaml"))
        assert result["inc"]["lit"] == "{{ x }}"

    def test_delete_marker_in_an_include(self, tmp_path):
        _write(tmp_path / "over.yaml", "a: !delete\n")
        _write(tmp_path / "app.yaml", "x: !include over.yaml\n")
        result = Loader(merge="deep").load(
            {"x": {"a": 1, "b": 2}}, str(tmp_path / "app.yaml")
        )
        assert result == {"x": {"b": 2}}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class TestCommands:
    def test_include_of_command_output(self, tmp_path):
        command = _emitter(
            tmp_path, "import json\nprint(json.dumps({'name': 'my-app'}))\n"
        )
        _write(
            tmp_path / "app.yaml",
            f"config:\n  app: !include {_quote('cmd+json://' + command)}\n",
        )
        result = Loader(policy=Policy.trusted()).load(str(tmp_path / "app.yaml"))
        assert result == {"config": {"app": {"name": "my-app"}}}

    def test_command_include_refused_by_default(self, tmp_path):
        marker = tmp_path / "ran.marker"
        command = _emitter(
            tmp_path,
            f"import pathlib\npathlib.Path({str(marker)!r}).touch()\nprint('{{}}')\n",
        )
        _write(
            tmp_path / "app.yaml", f"x: !include {_quote('cmd+json://' + command)}\n"
        )
        with pytest.raises(CommandsDisabledError):
            anycfg.load(str(tmp_path / "app.yaml"))
        with pytest.raises(CommandsDisabledError):
            Loader(policy=Policy.trusted()).load(
                str(tmp_path / "app.yaml"), policy=Policy()
            )
        assert not marker.exists()

    def test_include_inside_command_output_uses_base_dir(self, tmp_path, monkeypatch):
        _write(tmp_path / "conf" / "db.json", '{"host": "from-conf"}')
        _write(tmp_path / "elsewhere" / "db.json", '{"host": "from-cwd"}')
        monkeypatch.chdir(tmp_path / "elsewhere")
        command = _emitter(tmp_path, "print('d: !include db.json')\n")
        result = Loader(base_dir=tmp_path / "conf", policy=Policy.trusted()).load(
            "cmd+yaml://" + command
        )
        assert result == {"d": {"host": "from-conf"}}

    def test_command_output_encoding_reaches_its_includes(self, tmp_path):
        _write(tmp_path / "leaf.yaml", "c: café\n", "cp1252")
        command = _emitter(tmp_path, "print('x: !include leaf.yaml')\n")
        _write(
            tmp_path / "main.yaml", f"o: !include {_quote('cmd+yaml://' + command)}\n"
        )
        result = Loader(base_dir=tmp_path, policy=Policy.trusted()).load(
            str(tmp_path / "main.yaml"), encoding="cp1252"
        )
        assert result == {"o": {"x": {"c": "café"}}}

    def test_script_include_is_never_run_without_allow_commands(self, tmp_path):
        marker = tmp_path / "ran.marker"
        if sys.platform == "win32":
            _write(
                tmp_path / "gen.json.cmd", f'@type nul > "{marker}"\r\n@echo {{}}\r\n'
            )
            name = "gen.json.cmd"
        else:
            _write(tmp_path / "gen.json.sh", f"touch '{marker}'\necho '{{}}'\n")
            name = "gen.json.sh"
        _write(tmp_path / "app.yaml", f"s: !include {name}\n")
        with pytest.raises(ConfigError):
            anycfg.load(str(tmp_path / "app.yaml"))
        assert not marker.exists()


# ---------------------------------------------------------------------------
# Formats a document may reach
# ---------------------------------------------------------------------------


class TestNonYamlIncludes:
    def test_json_ini_dotenv(self, tmp_path):
        _write(tmp_path / "x.json", '{"k": 1}')
        _write(tmp_path / "x.ini", "[s]\nk = v\n")
        _write(tmp_path / "x.env", "K=v\n")
        _write(tmp_path / "x.cfg", "[c]\nk = w\n")
        _write(
            tmp_path / "app.yaml",
            "j: !include x.json\ni: !include x.ini\ne: !include x.env\n"
            "c: !include x.cfg\n",
        )
        assert anycfg.load(str(tmp_path / "app.yaml")) == {
            "j": {"k": 1},
            "i": {"s": {"k": "v"}},
            "e": {"K": "v"},
            "c": {"c": {"k": "w"}},
        }

    @needs_toml
    def test_toml(self, tmp_path):
        _write(tmp_path / "db.toml", 'host = "from-toml"\n')
        _write(tmp_path / "app.yaml", "db: !include db.toml\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"db": {"host": "from-toml"}}

    def test_env_yaml_name_is_yaml(self, tmp_path):
        _write(tmp_path / "app.env.yaml", "a: 1\n")
        _write(tmp_path / "main.yaml", "inc: !include app.env.yaml\n")
        assert anycfg.load(str(tmp_path / "main.yaml")) == {"inc": {"a": 1}}

    def test_dotenv_stage_name(self, tmp_path):
        _write(tmp_path / ".env.production", "MODE=prod\n")
        _write(tmp_path / "main.yaml", "env: !include .env.production\n")
        assert anycfg.load(str(tmp_path / "main.yaml")) == {"env": {"MODE": "prod"}}

    def test_unclaimed_name(self, tmp_path):
        _write(tmp_path / "notes.txt", "x")
        _write(tmp_path / "main.yaml", "n: !include notes.txt\n")
        with pytest.raises(UnsupportedFormatError):
            anycfg.load(str(tmp_path / "main.yaml"))


class _EntryPointFormat(Format):
    """Stands in for a format an installed distribution provides."""

    name = "yacltestep"
    suffixes = (".yacltestep",)

    def loads(self, data, /, *, context=None, **options):
        return {"ep": data.strip()}


@pytest.fixture
def entry_point_format(monkeypatch):
    """Register `_EntryPointFormat` the way an entry point would be."""

    class Point:
        name = "yacltestep"

        def load(self):
            return _EntryPointFormat

    formats_registry.names()  # the real built-ins and entry points first
    monkeypatch.setattr(formats_registry, "_entry_points", lambda: [Point()])
    monkeypatch.setitem(formats_registry._state, "entry_points", False)
    assert "yacltestep" in formats_registry.names()
    yield _EntryPointFormat
    formats_registry.unregister_format("yacltestep")


class TestDocumentFormatReach:
    def test_formats_limits_includes(self, tmp_path):
        _write(tmp_path / "a.json", "{}")
        _write(tmp_path / "app.yaml", "x: !include a.json\n")
        with pytest.raises(UnsupportedFormatError, match="formats="):
            Loader(formats=["yaml"]).load(str(tmp_path / "app.yaml"))
        assert Loader(formats=["yaml", "json"]).load(str(tmp_path / "app.yaml")) == {
            "x": {}
        }

    def test_formats_limits_the_format_key(self, tmp_path):
        _write(tmp_path / "a.txt", "{}")
        _write(tmp_path / "app.yaml", "x: !include {pathname: a.txt, format: json}\n")
        with pytest.raises(UnsupportedFormatError):
            Loader(formats=["yaml"]).load(str(tmp_path / "app.yaml"))

    def test_formats_by_alias(self, tmp_path):
        _write(tmp_path / "a.yml", "a: 1\n")
        _write(tmp_path / "app.yaml", "x: !include a.yml\n")
        assert Loader(formats=["yml"]).load(str(tmp_path / "app.yaml")) == {
            "x": {"a": 1}
        }

    def test_formats_limits_a_command_marker_line(self, tmp_path):
        command = _emitter(tmp_path, "print('#!json')\nprint('{\"a\": 1}')\n")
        _write(tmp_path / "app.yaml", f"x: !include {_quote('cmd://' + command)}\n")
        loader = Loader(policy=Policy.trusted())
        assert loader.load(str(tmp_path / "app.yaml")) == {"x": {"a": 1}}
        with pytest.raises(UnsupportedFormatError):
            Loader(policy=Policy.trusted(), formats=["yaml"]).load(
                str(tmp_path / "app.yaml")
            )

    def test_entry_point_format_is_refused_for_documents(
        self, tmp_path, entry_point_format
    ):
        _write(tmp_path / "x.yacltestep", "plugin data\n")
        _write(tmp_path / "app.yaml", "x: !include x.yacltestep\n")
        assert "yacltestep" not in formats_registry.document_formats()
        # The caller may read it directly...
        assert Loader().load(str(tmp_path / "x.yacltestep")) == {"ep": "plugin data"}
        # ...but a document may not select it, by name or by format: key.
        with pytest.raises(UnsupportedFormatError, match="yacltestep"):
            Loader().load(str(tmp_path / "app.yaml"))
        _write(
            tmp_path / "app2.yaml",
            "x: !include {pathname: x.txt, format: yacltestep}\n",
        )
        _write(tmp_path / "x.txt", "plugin data\n")
        with pytest.raises(UnsupportedFormatError):
            Loader().load(str(tmp_path / "app2.yaml"))

    def test_entry_point_format_named_in_formats(self, tmp_path, entry_point_format):
        _write(tmp_path / "x.yacltestep", "plugin data\n")
        _write(tmp_path / "app.yaml", "x: !include x.yacltestep\n")
        loader = Loader(formats=["yaml", "yacltestep"])
        assert loader.load(str(tmp_path / "app.yaml")) == {"x": {"ep": "plugin data"}}

    def test_code_registered_format_is_reachable(self, tmp_path):
        class Registered(Format):
            name = "yacltestreg"
            suffixes = (".yacltestreg",)

            def loads(self, data, /, *, context=None, **options):
                return {"reg": data.strip()}

        formats_registry.register_format(Registered)
        try:
            _write(tmp_path / "x.yacltestreg", "data\n")
            _write(tmp_path / "app.yaml", "x: !include x.yacltestreg\n")
            assert Loader().load(str(tmp_path / "app.yaml")) == {"x": {"reg": "data"}}
        finally:
            formats_registry.unregister_format("yacltestreg")


class TestMalformedIncludes:
    """A bad value in an include is the document's error: a ConfigError."""

    @pytest.mark.parametrize(
        "include",
        [
            "!include {pathname: 5}",
            "!include [1, 2]",
            "!include {pathname: [a.yaml, 3]}",
            "!include {pathname: a.yaml, encoding: 5}",
            "!include {pathname: a.yaml, flatten: 'false'}",
            "!include {pathname: a.yaml, recursive: 1}",
            "!include {pathname: a.yaml, merge_options: [1]}",
            "!include {pathname: a.yaml, merge: 1}",
        ],
    )
    def test_raises_a_config_error(self, tmp_path, include):
        (tmp_path / "a.yaml").write_text("k: 1\n", encoding="utf-8")
        (tmp_path / "app.yaml").write_text(f"x: {include}\n", encoding="utf-8")
        with pytest.raises(anycfg.ConfigError):
            anycfg.Loader(base_dir=tmp_path).load("app.yaml")
