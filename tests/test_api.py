"""The one-document API: ``load``, ``loads``, ``load_all``, ``loads_all``, ``dump``, ``dumps``."""

from __future__ import annotations

import dataclasses
import decimal
import io
import os
import pathlib

import pytest

import anycfg
from anycfg import (
    ConfigError,
    ConfigTypeError,
    ConfigValueError,
    ParseError,
    UnsupportedFormatError,
)
from anycfg.formats import Format
from anycfg.sources import Data, Text

from _extras import needs_toml, needs_toml_writer, needs_yaml

LF = chr(10)
CR = chr(13)
BOM = chr(0xFEFF)


class _ReadOnly(Format):
    """A format that can read but not write; never registered."""

    name = "readonlytest"
    suffixes = (".readonlytest",)

    def loads(self, data, /, *, context=None, **options):
        return {"text": data}


def _write(path, text, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


# ---------------------------------------------------------------------------
# load: what a source is, and how its format is chosen
# ---------------------------------------------------------------------------


class TestLoadFormatSelection:
    def test_format_from_suffix(self, tmp_path):
        source = _write(tmp_path / "conf.json", '{"foo": "bar"}')
        assert anycfg.load(str(source)) == {"foo": "bar"}

    @pytest.mark.parametrize("suffix", [".JSON", ".Json"])
    def test_suffix_is_case_insensitive(self, tmp_path, suffix):
        source = _write(tmp_path / ("conf" + suffix), '{"a": 1}')
        assert anycfg.load(str(source)) == {"a": 1}

    def test_unclaimed_name_raises_before_the_file_is_opened(self, tmp_path):
        # The file does not exist: an open attempt would be FileNotFoundError.
        with pytest.raises(UnsupportedFormatError) as caught:
            anycfg.load(str(tmp_path / "missing.unknown"))
        assert "json" in str(caught.value)  # lists the registered names

    def test_unclaimed_existing_file_is_not_read(self, tmp_path, monkeypatch):
        source = _write(tmp_path / "notes.txt", '{"a": 1}')
        opened = []
        real_open = open

        def spy(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr("builtins.open", spy)
        monkeypatch.setattr(io, "open", spy)
        with pytest.raises(UnsupportedFormatError):
            anycfg.load(str(source))
        assert not [name for name in opened if name.endswith("notes.txt")]

    def test_format_overrides_suffix(self, tmp_path):
        source = _write(tmp_path / "notes.txt", '{"a": 1}')
        assert anycfg.load(str(source), format="json") == {"a": 1}

    def test_format_class(self, tmp_path):
        source = _write(tmp_path / "notes.txt", '{"a": 1}')
        json_format = anycfg.formats.get("json")
        assert anycfg.load(str(source), format=json_format) == {"a": 1}

    def test_format_name_is_case_insensitive(self, tmp_path):
        source = _write(tmp_path / "notes.txt", '{"a": 1}')
        assert anycfg.load(str(source), format="JSON") == {"a": 1}

    def test_unknown_format_name(self, tmp_path):
        source = _write(tmp_path / "a.json", "{}")
        with pytest.raises(UnsupportedFormatError, match="registered"):
            anycfg.load(str(source), format="nope")

    def test_format_of_wrong_type_is_type_error(self, tmp_path):
        source = _write(tmp_path / "a.json", "{}")
        with pytest.raises(TypeError):
            anycfg.load(str(source), format=5)

    def test_unregistered_format_class(self, tmp_path):
        source = _write(tmp_path / "a.txt", "hello")
        assert anycfg.load(str(source), format=_ReadOnly) == {"text": "hello"}


class TestLoadSources:
    def test_str_is_always_a_location(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Content passed by mistake is looked up as a file name, never parsed.
        with pytest.raises(UnsupportedFormatError):
            anycfg.load("a: 1")
        with pytest.raises(OSError):
            anycfg.load('{"a": 1}', format="json")

    def test_pathlib_path(self, tmp_path):
        source = _write(tmp_path / "a.json", '{"a": 1}')
        assert anycfg.load(source) == {"a": 1}

    def test_other_pathlike(self, tmp_path):
        source = _write(tmp_path / "a.json", '{"a": 1}')

        class FsPath:
            def __fspath__(self):
                return str(source)

        assert anycfg.load(FsPath()) == {"a": 1}

    def test_pathapi_local_path(self, tmp_path):
        from pathapi import LocalPath

        source = _write(tmp_path / "a.json", '{"a": 1}')
        assert anycfg.load(LocalPath(str(source))) == {"a": 1}

    def test_pathapi_mempath(self):
        from pathapi.mempath import MemPath

        source = MemPath("mem.json")
        source.write_bytes(b'{"m": 1}')
        assert anycfg.load(source) == {"m": 1}

    def test_text_stream_with_name_selects_format(self, tmp_path):
        source = _write(tmp_path / "a.json", '{"n": 1e5}')
        with open(source, encoding="utf-8") as handle:
            assert anycfg.load(handle) == {"n": 100000.0}

    def test_binary_stream_with_name(self, tmp_path):
        source = _write(tmp_path / "a.json", '{"a": "café"}')
        with open(source, "rb") as handle:
            assert anycfg.load(handle) == {"a": "café"}

    def test_unnamed_stream_needs_format(self):
        with pytest.raises(UnsupportedFormatError):
            anycfg.load(io.StringIO('{"a": 1}'))
        assert anycfg.load(io.StringIO('{"a": 1}'), format="json") == {"a": 1}

    def test_unnamed_bytes_stream_with_format(self):
        assert anycfg.load(io.BytesIO(b'{"a": 1}'), format="json") == {"a": 1}

    def test_source_object(self):
        assert anycfg.load(Text('{"a": 1}', format="json")) == {"a": 1}
        assert anycfg.load(Data({"d": [1]})) == {"d": [1]}

    def test_none_is_type_error(self):
        with pytest.raises(TypeError, match="load"):
            anycfg.load(None)

    def test_empty_path_is_value_error(self):
        with pytest.raises(ValueError, match="load"):
            anycfg.load("")

    @pytest.mark.parametrize("data", [b"", b"a.json", bytearray(b"a.json")])
    def test_bytes_is_type_error(self, data):
        with pytest.raises(TypeError, match="loads"):
            anycfg.load(data)

    def test_load_all_checks_its_argument_eagerly(self):
        with pytest.raises(TypeError):
            anycfg.load_all(None)
        with pytest.raises(ValueError):
            anycfg.load_all("")

    def test_missing_file_is_os_error_not_wrapped(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            anycfg.load(str(tmp_path / "missing.json"))


class TestLoadOptions:
    def test_options_reach_the_format(self, tmp_path):
        source = _write(tmp_path / "a.json", '{"rate": 1.5}')
        result = anycfg.load(str(source), parse_float=decimal.Decimal)
        assert result["rate"] == decimal.Decimal("1.5")

    def test_ini_option(self, tmp_path):
        source = _write(tmp_path / "a.ini", "[common]\nx = 1\n\n[app]\ny = 2\n")
        result = anycfg.load(str(source), default_section="common")
        assert result == {"app": {"x": "1", "y": "2"}}

    def test_unknown_option_is_type_error(self, tmp_path):
        source = _write(tmp_path / "a.json", "{}")
        with pytest.raises(TypeError, match="nope"):
            anycfg.load(str(source), nope=1)

    def test_unknown_option_names_the_accepted_ones(self):
        with pytest.raises(TypeError, match="parse_float"):
            anycfg.loads("{}", format="json", nope=1)

    def test_loader_settings_are_not_options(self):
        # A one-document call takes no merge or interpolation settings.
        with pytest.raises(TypeError):
            anycfg.loads("{}", format="json", merge="deep")
        with pytest.raises(TypeError):
            anycfg.loads("{}", format="json", interpolate=True)

    def test_into_hydrates(self, tmp_path):
        @dataclasses.dataclass
        class Server:
            host: str
            port: int

        source = _write(tmp_path / "a.json", '{"host": "h", "port": 80, "x": 1}')
        result = anycfg.load(str(source), into=Server)
        assert result == Server("h", 80)
        assert anycfg.loads(
            '{"host": "h", "port": 81}', format="json", into=Server
        ) == Server("h", 81)

    def test_into_with_none_document_raises(self):
        @dataclasses.dataclass
        class Server:
            host: str = "h"

        with pytest.raises(ConfigTypeError):
            anycfg.loads("null", format="json", into=Server)


# ---------------------------------------------------------------------------
# loads: content, encodings, decoding errors
# ---------------------------------------------------------------------------


class TestLoads:
    def test_a_document_that_names_no_format_needs_format(self):
        with pytest.raises(anycfg.UnsupportedFormatError, match="format="):
            anycfg.loads('{"a": 1}')

    def test_str(self):
        assert anycfg.loads('{"hello": "world"}', format="json") == {"hello": "world"}

    def test_bytes_and_bytearray(self):
        assert anycfg.loads(b'{"a": 1}', format="json") == {"a": 1}
        assert anycfg.loads(bytearray(b'{"a": 1}'), format="json") == {"a": 1}

    @pytest.mark.parametrize("bad", [None, 42, ["{}"], io.StringIO("{}")])
    def test_other_types_are_type_error(self, bad):
        with pytest.raises(TypeError, match="str or bytes"):
            anycfg.loads(bad, format="json")

    @pytest.mark.parametrize(
        "codec", ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-32", "cp1252"]
    )
    def test_bytes_in_any_codec(self, codec):
        data = '{"a": "café"}'.encode(codec)
        assert anycfg.loads(data, format="json", encoding=codec) == {"a": "café"}

    def test_one_utf8_bom_is_dropped(self):
        data = BOM.encode("utf-8") + b'{"a": 1}'
        assert anycfg.loads(data, format="json") == {"a": 1}

    def test_only_one_bom_is_dropped(self):
        data = (BOM + BOM).encode("utf-8") + b'{"a": 1}'
        with pytest.raises(ParseError):
            anycfg.loads(data, format="json")

    def test_bom_in_a_file_is_dropped(self, tmp_path):
        source = tmp_path / "bom.json"
        source.write_bytes(BOM.encode("utf-8") + b'{"a": 1}')
        assert anycfg.load(str(source)) == {"a": 1}

    def test_strict_decoding_names_the_byte_offset(self):
        data = b'{"a": "caf' + bytes([0xE9]) + b'"}'
        with pytest.raises(ParseError, match="byte 10") as caught:
            anycfg.loads(data, format="json")
        error = caught.value
        assert "utf-8" in str(error)
        # The decode error holds the document's bytes; it is not kept.
        assert error.__cause__ is None and error.__context__ is None

    def test_decoding_error_in_a_file_names_the_file(self, tmp_path):
        source = tmp_path / "latin.json"
        source.write_bytes(b'{"a": "caf' + bytes([0xE9]) + b'"}')
        with pytest.raises(ParseError) as caught:
            anycfg.load(str(source))
        assert caught.value.path == str(source)
        assert "latin.json" in str(caught.value)
        assert anycfg.load(str(source), encoding="cp1252") == {"a": "café"}

    def test_unknown_encoding(self):
        with pytest.raises(ConfigValueError, match="encoding"):
            anycfg.loads(b"{}", format="json", encoding="no-such-codec")

    def test_parse_error_is_a_config_error_and_value_error(self):
        with pytest.raises(ParseError) as caught:
            anycfg.loads('{"a": }', format="json")
        assert isinstance(caught.value, ConfigError)
        assert isinstance(caught.value, ValueError)

    @needs_yaml
    def test_yaml_default_document_is_not_json(self):
        # Each format parses its own dialect: 1e5 is a string in YAML 1.1.
        assert anycfg.loads('{"n": 1e5}', format="yaml") == {"n": "1e5"}
        assert anycfg.loads('{"n": 1e5}', format="json") == {"n": 100000.0}

    def test_marker_line_is_content(self):
        # A "#!" first line names nothing: it is part of the document.
        with pytest.raises(ParseError):
            anycfg.loads('#!c.json\n{"a": 1}', format="json")


@needs_yaml
class TestMultiDocument:
    STREAM = "a: 1\n---\nb: 2\n"

    def test_load_refuses_a_stream(self, tmp_path):
        source = _write(tmp_path / "m.yaml", self.STREAM)
        with pytest.raises(ParseError):
            anycfg.load(str(source))

    def test_loads_refuses_a_stream(self):
        with pytest.raises(ParseError):
            anycfg.loads(self.STREAM, format="yaml")

    def test_load_all_yields_each(self, tmp_path):
        source = _write(tmp_path / "m.yaml", self.STREAM)
        assert list(anycfg.load_all(str(source))) == [{"a": 1}, {"b": 2}]

    def test_loads_all_yields_each(self):
        documents = anycfg.loads_all(self.STREAM, format="yaml")
        assert list(documents) == [{"a": 1}, {"b": 2}]

    def test_loads_all_is_lazy(self):
        documents = anycfg.loads_all(self.STREAM, format="yaml")
        assert next(documents) == {"a": 1}

    def test_single_document_format_yields_one(self):
        assert list(anycfg.loads_all('{"a": 1}', format="json")) == [{"a": 1}]

    def test_loads_all_needs_a_format_or_a_declared_one(self):
        with pytest.raises(anycfg.UnsupportedFormatError):
            anycfg.loads_all(self.STREAM)


# ---------------------------------------------------------------------------
# dumps
# ---------------------------------------------------------------------------


class TestDumps:
    def test_format_is_required(self):
        with pytest.raises(TypeError, match="format"):
            anycfg.dumps({"a": 1})

    def test_json(self):
        assert anycfg.loads(
            anycfg.dumps({"a": [1, 2]}, format="json"), format="json"
        ) == {"a": [1, 2]}

    def test_options_reach_the_format(self):
        assert anycfg.dumps({"b": 1, "a": 2}, format="json", indent=None) == (
            '{"b": 1, "a": 2}'
        )
        assert anycfg.dumps({"b": 1, "a": 2}, format="json", sort_keys=True) == (
            '{\n  "a": 2,\n  "b": 1\n}'
        )

    def test_unknown_option_is_type_error(self):
        with pytest.raises(TypeError, match="encoding"):
            anycfg.dumps({"a": 1}, format="json", encoding="utf-8")

    def test_format_that_cannot_write(self):
        with pytest.raises(UnsupportedFormatError, match="cannot write"):
            anycfg.dumps({"a": 1}, format=_ReadOnly)

    def test_unknown_format(self):
        with pytest.raises(UnsupportedFormatError):
            anycfg.dumps({"a": 1}, format="nope")

    def test_unrepresentable_value(self):
        with pytest.raises(ConfigTypeError):
            anycfg.dumps({"a": object()}, format="json")
        with pytest.raises(ConfigValueError):
            anycfg.dumps({"a": float("nan")}, format="json")

    @needs_yaml
    def test_yaml_keeps_order_and_unicode(self):
        assert anycfg.dumps({"zeta": 1, "alpha": "café"}, format="yaml") == (
            "zeta: 1\nalpha: café\n"
        )

    @needs_yaml
    def test_yaml_dumps_a_loaded_wrapped_config(self, tmp_path):
        source = _write(tmp_path / "in.yaml", "a: 1\nb:\n  c: 2\n")
        config = anycfg.Loader(wrap=True).load(str(source))
        out = anycfg.dumps(config, format="yaml")
        assert "python/" not in out
        assert anycfg.loads(out, format="yaml") == {"a": 1, "b": {"c": 2}}

    @needs_yaml
    def test_yaml_tuple_is_a_sequence(self):
        out = anycfg.dumps({"t": (1, 2)}, format="yaml")
        assert anycfg.loads(out, format="yaml") == {"t": [1, 2]}

    @needs_yaml
    def test_yaml_does_not_touch_global_dumpers(self):
        import yaml

        anycfg.dumps({"a": 1}, format="yaml")
        assert dict not in yaml.Dumper.yaml_multi_representers
        assert dict not in yaml.SafeDumper.yaml_multi_representers

    @needs_yaml
    def test_yaml_other_python_objects_raise(self):
        with pytest.raises(ConfigTypeError):
            anycfg.dumps({"x": 1 + 2j}, format="yaml")

    def test_decimal_writes_as_its_text(self):
        assert anycfg.dumps({"x": decimal.Decimal("1.10")}, format="yaml") == (
            "x: '1.10'\n"
        )


# ---------------------------------------------------------------------------
# dump: target, format from the target's name, what gets written
# ---------------------------------------------------------------------------


class TestDumpTargets:
    def test_str_path(self, tmp_path):
        target = tmp_path / "out.json"
        anycfg.dump({"a": 1}, str(target))
        assert anycfg.load(str(target)) == {"a": 1}

    def test_pathlib_path(self, tmp_path):
        target = tmp_path / "out.json"
        anycfg.dump({"a": 1}, target)
        assert anycfg.load(target) == {"a": 1}

    def test_other_pathlike(self, tmp_path):
        target = tmp_path / "out.json"

        class FsPath:
            def __fspath__(self):
                return str(target)

        anycfg.dump({"a": 1}, FsPath())
        assert anycfg.load(target) == {"a": 1}

    def test_pathapi_local_path(self, tmp_path):
        from pathapi import LocalPath

        target = LocalPath(str(tmp_path / "out.json"))
        anycfg.dump({"a": 1}, target)
        assert anycfg.load(target) == {"a": 1}

    def test_pathapi_mempath(self):
        from pathapi.mempath import MemPath

        target = MemPath("dumped.json")
        anycfg.dump({"a": "café"}, target)
        assert target.read_bytes() == '{\n  "a": "café"\n}\n'.encode("utf-8")
        assert anycfg.load(target) == {"a": "café"}

    def test_mempath_encoding(self):
        from pathapi.mempath import MemPath

        target = MemPath("dumped.json")
        anycfg.dump({"a": "café"}, target, encoding="utf-16")
        assert target.read_bytes().decode("utf-16") == '{\n  "a": "café"\n}\n'

    def test_text_stream_with_name(self, tmp_path):
        target = tmp_path / "out.json"
        with open(target, "w", encoding="utf-8", newline="") as handle:
            anycfg.dump({"a": 1}, handle)
        assert anycfg.load(target) == {"a": 1}

    def test_text_stream_keeps_its_own_codec(self, tmp_path):
        target = tmp_path / "out.json"
        with open(target, "w", encoding="utf-16", newline="") as handle:
            anycfg.dump({"a": "café"}, handle, encoding="ascii")
        assert anycfg.load(target, encoding="utf-16") == {"a": "café"}

    def test_binary_stream_with_name(self, tmp_path):
        target = tmp_path / "out.json"
        with open(target, "wb") as handle:
            anycfg.dump({"a": "café"}, handle)
        assert target.read_bytes() == '{\n  "a": "café"\n}\n'.encode("utf-8")

    def test_binary_stream_uses_encoding(self):
        buffer = io.BytesIO()
        anycfg.dump({"a": "café"}, buffer, format="json", encoding="utf-16")
        assert anycfg.load(
            io.BytesIO(buffer.getvalue()), format="json", encoding="utf-16"
        ) == {"a": "café"}

    def test_gzip_stream(self, tmp_path):
        import gzip

        target = tmp_path / "out.json.gz"
        with gzip.open(target, "wb") as handle:
            anycfg.dump({"a": 1}, handle, format="json")
        with gzip.open(target, "rb") as handle:
            assert handle.read() == b'{\n  "a": 1\n}\n'

    def test_unnamed_stream_with_format(self):
        buffer = io.StringIO()
        anycfg.dump({"a": 1}, buffer, format="json")
        assert buffer.getvalue() == '{\n  "a": 1\n}\n'

    @pytest.mark.parametrize(
        "target",
        [
            pytest.param(io.StringIO, id="stringio"),
            pytest.param(io.BytesIO, id="bytesio"),
        ],
    )
    def test_no_name_and_no_format_raises(self, target):
        with pytest.raises(UnsupportedFormatError, match="format="):
            anycfg.dump({"a": 1}, target())

    def test_pseudo_name_is_not_a_file_name(self):
        class Stdout(io.StringIO):
            name = "<stdout>"

        with pytest.raises(UnsupportedFormatError):
            anycfg.dump({"a": 1}, Stdout())

    def test_unclaimed_suffix_raises_and_creates_nothing(self, tmp_path):
        target = tmp_path / "out.unknown"
        with pytest.raises(UnsupportedFormatError):
            anycfg.dump({"a": 1}, str(target))
        assert not target.exists()

    def test_format_overrides_the_suffix(self, tmp_path):
        target = tmp_path / "out.txt"
        anycfg.dump({"a": 1}, str(target), format="json")
        assert anycfg.load(str(target), format="json") == {"a": 1}

    def test_options_reach_the_format(self, tmp_path):
        target = tmp_path / "out.json"
        anycfg.dump({"a": 1}, str(target), indent=None)
        assert target.read_bytes() == b'{"a": 1}\n'

    def test_unknown_option_is_type_error(self, tmp_path):
        target = tmp_path / "out.json"
        with pytest.raises(TypeError):
            anycfg.dump({"a": 1}, str(target), nope=1)
        assert not target.exists()

    def test_format_that_cannot_write(self, tmp_path):
        target = tmp_path / "out.readonlytest"
        with pytest.raises(UnsupportedFormatError):
            anycfg.dump({"a": 1}, str(target), format=_ReadOnly)
        assert not target.exists()

    def test_unwritable_target_type(self):
        with pytest.raises((TypeError, UnsupportedFormatError)):
            anycfg.dump({"a": 1}, 42)
        with pytest.raises(TypeError):
            anycfg.dump({"a": 1}, 42, format="json")


class TestDumpOutput:
    def test_serialized_before_the_target_is_opened(self, tmp_path):
        target = tmp_path / "out.json"
        target.write_bytes(b"old\n")
        with pytest.raises(ConfigTypeError):
            anycfg.dump({"g": object()}, str(target))
        assert target.read_bytes() == b"old\n"

    def test_stream_untouched_on_failure(self):
        buffer = io.StringIO("old")
        with pytest.raises(ConfigTypeError):
            anycfg.dump({"g": object()}, buffer, format="json")
        assert buffer.getvalue() == "old"

    def test_path_is_written_with_lf(self, tmp_path):
        target = tmp_path / "out.json"
        anycfg.dump({"a": 1, "b": [1, 2]}, str(target))
        data = target.read_bytes()
        assert CR.encode("ascii") not in data
        assert data.count(LF.encode("ascii")) > 1

    def test_trailing_newline_is_ensured(self, tmp_path):
        # JSON's own text has no trailing newline.
        assert not anycfg.dumps({"a": 1}, format="json").endswith(LF)
        target = tmp_path / "out.json"
        anycfg.dump({"a": 1}, str(target))
        data = target.read_bytes()
        assert data.endswith(b"}\n") and not data.endswith(b"\n\n")

    @needs_yaml
    def test_trailing_newline_is_not_doubled(self, tmp_path):
        target = tmp_path / "out.yaml"
        anycfg.dump({"a": 1}, str(target))
        assert target.read_bytes() == b"a: 1\n"

    @pytest.mark.parametrize("codec", ["utf-8", "utf-16", "cp1252"])
    def test_path_uses_encoding(self, tmp_path, codec):
        target = tmp_path / "out.json"
        anycfg.dump({"a": "café"}, str(target), encoding=codec)
        assert anycfg.load(str(target), encoding=codec) == {"a": "café"}

    def test_path_default_encoding_is_utf8(self, tmp_path):
        target = tmp_path / "out.json"
        anycfg.dump({"a": "日本"}, str(target))
        assert "日本".encode("utf-8") in target.read_bytes()


# ---------------------------------------------------------------------------
# Round trips: dump then load gives the value back
# ---------------------------------------------------------------------------


_ROUND_TRIPS = [
    pytest.param(
        "out.json", {"a": 1, "b": [True, None, 1.5], "c": {"d": "é"}}, id="json"
    ),
    pytest.param(
        "out.yaml",
        {"a": 1, "b": [True, None, 1.5], "c": {"d": "é"}, "e": "yes"},
        id="yaml",
        marks=needs_yaml,
    ),
    pytest.param("out.yml", {"a": {"b": [1, 2]}}, id="yml", marks=needs_yaml),
    pytest.param(
        "out.toml",
        {"title": "x", "db": {"port": 5432, "hosts": ["a", "b"]}},
        id="toml",
        marks=[needs_toml, needs_toml_writer],
    ),
    pytest.param(
        "out.ini",
        {"server": {"host": "h", "port": "80", "pct": "100%"}},
        id="ini",
    ),
    pytest.param("out.cfg", {"s": {"k": "v"}}, id="cfg"),
    pytest.param(
        "app.env",
        {"KEY": "value", "SPACED": "a b # not a comment", "Q": 'say "hi"\nbye'},
        id="dotenv",
    ),
    pytest.param(".env", {"A": "1"}, id="dotenv_bare"),
]


class TestRoundTrip:
    @pytest.mark.parametrize("name, value", _ROUND_TRIPS)
    def test_dump_then_load(self, tmp_path, name, value):
        target = tmp_path / name
        anycfg.dump(value, str(target))
        assert anycfg.load(str(target)) == value

    @pytest.mark.parametrize("name, value", _ROUND_TRIPS)
    def test_dumps_then_loads(self, name, value):
        fmt = anycfg.formats.for_filename(name)
        text = anycfg.dumps(value, format=fmt.name)
        assert anycfg.loads(text, format=fmt.name) == value

    @needs_yaml
    def test_loaded_config_round_trips_through_a_file(self, tmp_path):
        source = _write(tmp_path / "in.yaml", "a: 1\nb:\n  c: 2\n")
        target = tmp_path / "out.yaml"
        anycfg.dump(anycfg.load(str(source)), str(target))
        assert anycfg.load(str(target)) == {"a": 1, "b": {"c": 2}}

    def test_cross_format_by_suffix(self, tmp_path):
        source = _write(tmp_path / "in.json", '{"s": {"k": "v"}}')
        target = tmp_path / "out.ini"
        anycfg.dump(anycfg.load(str(source)), str(target))
        assert anycfg.load(str(target)) == {"s": {"k": "v"}}

    def test_dump_to_pathlike_round_trips(self, tmp_path):
        target = pathlib.Path(tmp_path) / "nested.json"
        value = {"x": {"y": [1, {"z": None}]}}
        anycfg.dump(value, target)
        assert anycfg.load(os.fspath(target)) == value


class TestLoadReadsOneLocation:
    def test_a_glob_matching_nothing(self, tmp_path):
        with pytest.raises(anycfg.ConfigValueError, match="gave none"):
            anycfg.load(str(tmp_path / "*.json"))

    def test_a_glob_matching_several(self, tmp_path):
        for name in ("a", "b"):
            (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
        with pytest.raises(anycfg.ConfigValueError, match="more than one"):
            anycfg.load(str(tmp_path / "*.json"))

    def test_a_glob_matching_one(self, tmp_path):
        (tmp_path / "a.json").write_text('{"a": 1}', encoding="utf-8")
        assert anycfg.load(str(tmp_path / "*.json")) == {"a": 1}

    @pytest.mark.parametrize("sources", [["a.json", "b.json"], ("a.json",)])
    def test_a_list_is_not_a_location(self, sources):
        with pytest.raises(TypeError, match=r"unpack it: load\(\*sources\)"):
            anycfg.load(sources)


@dataclasses.dataclass
class _Server:
    host: str
    port: int


class TestLoadSeveralSources:
    @pytest.fixture
    def layers(self, tmp_path):
        base = tmp_path / "base.json"
        base.write_text('{"server": {"host": "a", "port": 1}, "x": [1]}')
        local = tmp_path / "local.json"
        local.write_text('{"server": {"port": 2}, "x": [2]}')
        return base, local

    def test_later_sources_win_key_by_key(self, layers):
        assert anycfg.load(*layers) == {"server": {"port": 2}, "x": [2]}

    def test_merge_deep(self, layers):
        assert anycfg.load(*layers, merge="deep") == {
            "server": {"host": "a", "port": 2},
            "x": [1, 2],
        }

    @pytest.mark.parametrize("merge", [None, "simple", "substitute", "deep"])
    def test_merged_as_the_loader_merges(self, layers, merge):
        loader = anycfg.Loader() if merge is None else anycfg.Loader(merge=merge)
        assert anycfg.load(*layers, merge=merge) == loader.load(*layers)

    def test_none_is_an_absent_layer(self, layers):
        base, _local = layers
        assert anycfg.load(None, base, None) == anycfg.load(base)

    def test_every_layer_absent_gives_the_default(self):
        assert anycfg.load(None, None, default={}) == {}
        assert anycfg.load(None, default=None) is None

    def test_every_layer_absent_without_a_default(self):
        with pytest.raises(TypeError, match="default="):
            anycfg.load(None, None)

    def test_no_source(self):
        with pytest.raises(TypeError, match="at least one source"):
            anycfg.load()

    def test_a_default_is_not_merged(self, layers):
        base, _local = layers
        assert anycfg.load(base, default={"y": 1}) == anycfg.load(base)

    def test_into_builds_the_merged_result(self, tmp_path):
        (tmp_path / "a.json").write_text('{"host": "h", "port": "1"}')
        (tmp_path / "b.json").write_text('{"port": "2"}')
        got = anycfg.load(tmp_path / "a.json", tmp_path / "b.json", into=_Server)
        assert got == _Server("h", 2)

    def test_format_applies_to_every_source(self, tmp_path):
        (tmp_path / "a.txt").write_text('{"a": 1}')
        (tmp_path / "b.txt").write_text('{"b": 2}')
        assert anycfg.load(tmp_path / "a.txt", tmp_path / "b.txt", format="json") == {
            "a": 1,
            "b": 2,
        }

    def test_mixed_source_kinds(self, layers):
        base, _local = layers
        got = anycfg.load(base, Data({"x": [3]}), Text('{"z": 1}', format="json"))
        assert got["x"] == [3] and got["z"] == 1

    def test_each_source_still_gives_one_document(self, tmp_path, layers):
        base, _local = layers
        with pytest.raises(ConfigValueError, match="per source.*gave none"):
            anycfg.load(base, str(tmp_path / "*.yaml"))

    def test_a_bad_source_is_refused_before_reading(self, layers):
        base, _local = layers
        with pytest.raises(TypeError, match="loads"):
            anycfg.load(base, b"{}")

    def test_default_configs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "system"))
        monkeypatch.setenv("APPDATA", str(tmp_path / "user"))
        assert anycfg.load(*anycfg.default_configs("app.json"), default={}) == {}
        (tmp_path / "user").mkdir()
        (tmp_path / "user" / "app.json").write_text('{"a": 1}')
        assert anycfg.load(*anycfg.default_configs("app.json")) == {"a": 1}


@dataclasses.dataclass
class _Doc:
    a: int


class TestLoadAllInto:
    @needs_yaml
    def test_load_all_and_loads_all_build_each_document(self, tmp_path):
        text = "a: '1'\n---\na: 2\n"
        assert list(anycfg.loads_all(text, format="yaml", into=_Doc)) == [
            _Doc(1),
            _Doc(2),
        ]
        (tmp_path / "s.yaml").write_text(text, encoding="utf-8")
        assert list(anycfg.load_all(str(tmp_path / "s.yaml"), into=_Doc)) == [
            _Doc(1),
            _Doc(2),
        ]


class TestDeclaredFormat:
    """``loads`` without ``format=``: the document names its own, or it is an error."""

    def test_shebang_names_the_format_and_is_dropped(self):
        assert anycfg.loads('#!json\n{"a": 1}') == {"a": 1}
        assert anycfg.loads('#!/usr/bin/env json\n{"a": 2}') == {"a": 2}

    def test_bytes_and_a_byte_order_mark(self):
        assert anycfg.loads(b'\xef\xbb\xbf#!json\n{"a": 3}') == {"a": 3}

    @needs_yaml
    def test_an_alias_and_yaml_directive(self):
        assert anycfg.loads("#!yml\na: 1\n") == {"a": 1}
        assert anycfg.loads("%YAML 1.1\n---\na: 2\n") == {"a": 2}
        assert list(anycfg.loads_all("#!yaml\na: 1\n---\na: 2\n")) == [
            {"a": 1},
            {"a": 2},
        ]

    def test_nothing_is_guessed_from_content(self):
        for text in ('{"a": 1}', "[s]\nk = v\n", "A=1\n", "#!nope\n{}"):
            with pytest.raises(anycfg.UnsupportedFormatError):
                anycfg.loads(text)

    def test_an_explicit_format_wins_and_keeps_the_text(self):
        # With format= nothing is claimed or removed: a #! line is document text.
        assert anycfg.loads("#!yaml\nA=1\n", format="dotenv") == {"A": "1"}

    def test_text_source_without_format(self):
        assert anycfg.Loader().load(Text('#!json\n{"a": 4}')) == {"a": 4}

    def test_a_format_can_claim_its_own_marker(self):
        class Magic(Format):
            name = "magicfmt"

            @classmethod
            def claims_document(cls, data):
                text = data.decode() if isinstance(data, bytes) else data
                return text.startswith("MAGIC1") or super().claims_document(data)

            def loads(self, data, /, *, context=None, **options):
                return {"body": data.split("\n", 1)[1]}

        anycfg.formats.register_format(Magic)
        try:
            assert anycfg.loads("MAGIC1\nhello") == {"body": "hello"}
            assert anycfg.loads(b"#!magicfmt\nx\ny") == {"body": "y"}
        finally:
            anycfg.formats.unregister_format("magicfmt")
