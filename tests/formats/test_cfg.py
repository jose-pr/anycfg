"""The ``cfg`` format: a ``.cfg`` file names its own format, else it is TOML."""

from __future__ import annotations

import pytest

import anycfg
from anycfg import formats
from anycfg.testing import FormatContract
from _extras import needs_toml_writer, needs_yaml

CfgFormat = formats.get("cfg")


@needs_toml_writer
class TestCfgContract(FormatContract):
    format = CfgFormat
    samples = [{"a": 1}, {"nested": {"b": [1, 2]}, "text": "café"}]
    document = "a = 1\n"
    malformed = 'password = "{secret}" ='


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestRegistration:
    def test_claims_the_suffix(self):
        assert CfgFormat.name == "cfg"
        assert CfgFormat.suffixes == (".cfg",)
        assert formats.for_filename("setup.cfg") is CfgFormat
        assert formats.capabilities(CfgFormat) == {"load", "load_all", "dump"}


class TestReading:
    def test_a_file_naming_nothing_is_toml(self, tmp_path):
        source = _write(tmp_path / "app.cfg", 'name = "app"\n[db]\nport = 5432\n')
        assert anycfg.load(source) == {"name": "app", "db": {"port": 5432}}

    def test_a_shebang_names_the_format_and_is_dropped(self, tmp_path):
        ini = _write(tmp_path / "a.cfg", "#!ini\n[s]\nk = v\n")
        json = _write(tmp_path / "b.cfg", '#!/usr/bin/env json\n{"k": 1}\n')
        assert anycfg.load(ini) == {"s": {"k": "v"}}
        assert anycfg.load(json) == {"k": 1}

    def test_hash_bang_cfg_is_toml_too(self, tmp_path):
        assert anycfg.load(_write(tmp_path / "a.cfg", "#!cfg\nk = 1\n")) == {"k": 1}

    @needs_yaml
    def test_a_yaml_directive(self, tmp_path):
        source = _write(tmp_path / "a.cfg", "%YAML 1.1\n---\nk: yes\n")
        assert anycfg.load(source) == {"k": True}

    @needs_yaml
    def test_every_document_of_a_named_yaml_stream(self, tmp_path):
        source = _write(tmp_path / "a.cfg", "#!yaml\nk: 1\n---\nk: 2\n")
        assert list(anycfg.load_all(source)) == [{"k": 1}, {"k": 2}]

    def test_a_format_nobody_registers(self, tmp_path):
        with pytest.raises(anycfg.UnsupportedFormatError, match="nope"):
            anycfg.load(_write(tmp_path / "a.cfg", "#!nope\nk = 1\n"))

    def test_toml_that_does_not_parse_is_a_parse_error(self, tmp_path):
        with pytest.raises(anycfg.ParseError):
            anycfg.load(_write(tmp_path / "a.cfg", "[s]\nk = v\n"))

    @needs_yaml
    def test_as_an_include(self, tmp_path):
        _write(tmp_path / "db.cfg", "port = 1\n")
        _write(tmp_path / "app.yaml", "db: !include db.cfg\n")
        assert anycfg.load(str(tmp_path / "app.yaml")) == {"db": {"port": 1}}

    def test_an_explicit_format_wins(self, tmp_path):
        source = _write(tmp_path / "a.cfg", "[s]\nk = v\n")
        assert anycfg.load(source, format="ini") == {"s": {"k": "v"}}


@needs_toml_writer
class TestWriting:
    def test_dump_writes_toml_and_round_trips(self, tmp_path):
        target = tmp_path / "out.cfg"
        anycfg.dump({"db": {"port": 1}}, str(target))
        assert target.read_text(encoding="utf-8").startswith("[db]")
        assert anycfg.load(str(target)) == {"db": {"port": 1}}
