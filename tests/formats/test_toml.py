"""The TOML format: dialect, errors and writing."""

from __future__ import annotations

import datetime
import pickle
import sys
import types

import pytest

import anycfg
from anycfg import formats
from anycfg.testing import FormatContract

TOMLFormat = formats.get("toml")


@pytest.mark.usefixtures("needs_toml", "needs_toml_writer")
class TestTOMLContract(FormatContract):
    format = TOMLFormat
    samples = [
        {"a": 1},
        {"table": {"x": "y", "list": [1, 2, 3]}, "text": "café"},
        {"f": 1.5, "b": True, "mixed": [1, "two", 3.0]},
        {"d": datetime.date(1979, 5, 27), "t": datetime.time(7, 32)},
        {"at": datetime.datetime(1979, 5, 27, 7, 32, tzinfo=datetime.timezone.utc)},
        {"rows": [{"n": 1}, {"n": 2}]},
    ]
    document = 'title = "x"\n[owner]\nname = "y"\n'
    malformed = 'a = "{secret}\n'


class TestRegistration:
    def test_name_and_suffix(self):
        assert TOMLFormat.name == "toml"
        assert TOMLFormat.suffixes == (".toml",)
        assert formats.for_filename("pyproject.TOML") is TOMLFormat

    def test_capabilities(self):
        assert formats.capabilities(TOMLFormat) == {"load", "dump"}


@pytest.mark.usefixtures("needs_toml")
class TestDialect:
    def test_tables_and_dotted_keys(self):
        result = anycfg.loads(
            '[section]\nkey = "hello"\na.b = 1\n[[rows]]\nn = 1\n[[rows]]\nn = 2\n',
            format="toml",
        )
        assert result == {
            "section": {"key": "hello", "a": {"b": 1}},
            "rows": [{"n": 1}, {"n": 2}],
        }

    def test_heterogeneous_array(self):
        assert anycfg.loads('a = [1, "two", 3.0]\n', format="toml") == {
            "a": [1, "two", 3.0]
        }

    def test_lowercase_z_datetime_is_utc_aware(self):
        value = anycfg.loads("d = 1979-05-27t07:32:00z\n", format="toml")["d"]
        assert value == datetime.datetime(
            1979, 5, 27, 7, 32, tzinfo=datetime.timezone.utc
        )
        assert value.tzinfo is not None

    def test_local_date_and_time(self):
        result = anycfg.loads(
            "d = 1979-05-27\nt = 07:32:00\nl = 1979-05-27T07:32:00\n", format="toml"
        )
        assert result == {
            "d": datetime.date(1979, 5, 27),
            "t": datetime.time(7, 32),
            "l": datetime.datetime(1979, 5, 27, 7, 32),
        }

    def test_multiline_basic_string_ending_quotes(self):
        assert anycfg.loads('s = """a""""\n', format="toml") == {"s": 'a"'}

    def test_offset_datetime_pickles(self):
        value = anycfg.loads("d = 1979-05-27T00:32:00-08:00\n", format="toml")["d"]
        assert pickle.loads(pickle.dumps(value)) == value

    def test_duplicate_keys_raise(self):
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.loads("a = 1\na = 2\n", format="toml")
        assert caught.value.lineno == 2

    def test_there_is_no_null(self):
        with pytest.raises(anycfg.ParseError):
            anycfg.loads("a = null\n", format="toml")

    def test_empty_document_is_an_empty_table(self):
        assert anycfg.loads("", format="toml") == {}

    def test_never_uses_the_toml_package(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("the toml package must not be used")

        fake = types.ModuleType("toml")
        fake.loads = refuse
        fake.load = refuse
        fake.dumps = refuse
        monkeypatch.setitem(sys.modules, "toml", fake)
        assert anycfg.loads("a = 1\n", format="toml") == {"a": 1}


@pytest.mark.usefixtures("needs_toml")
class TestErrors:
    def test_parse_error_carries_position(self):
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.loads("ok = 1\na = =\n", format="toml")
        error = caught.value
        assert (error.lineno, error.colno) == (2, 5)
        assert error.msg == "Invalid value"
        assert error.__cause__ is None and error.__context__ is None

    def test_message_has_no_position_suffix(self):
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.loads("a = =\n", format="toml")
        assert "(at line" not in caught.value.msg

    def test_parse_error_names_the_file(self, tmp_path):
        source = tmp_path / "bad.toml"
        source.write_text("a = =\n", encoding="utf-8")
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.load(str(source))
        assert caught.value.path == str(source)
        assert str(caught.value).startswith(f"{source}:1:5: ")

    def test_deep_nesting_is_a_parse_error(self):
        depth = 100000
        with pytest.raises(anycfg.ParseError):
            anycfg.loads("a = " + "[" * depth + "]" * depth, format="toml")


@pytest.mark.usefixtures("needs_toml", "needs_toml_writer")
class TestDumps:
    def test_dumps_a_table(self):
        out = anycfg.dumps({"a": 1, "t": {"b": "x"}}, format="toml")
        assert anycfg.loads(out, format="toml") == {"a": 1, "t": {"b": "x"}}

    @pytest.mark.parametrize("value", [[1, 2], "text", 3])
    def test_document_must_be_a_table(self, value):
        with pytest.raises(anycfg.ConfigTypeError, match="table"):
            anycfg.dumps(value, format="toml")

    def test_none_is_refused(self):
        with pytest.raises(anycfg.ConfigTypeError) as caught:
            anycfg.dumps({"a": {"b": None}}, format="toml")
        assert caught.value.__cause__ is None

    def test_no_dump_options(self):
        with pytest.raises(TypeError, match="indent"):
            anycfg.dumps({"a": 1}, format="toml", indent=2)

    def test_dump_file_round_trip(self, tmp_path):
        target = tmp_path / "out.toml"
        data = {"title": "café", "owner": {"n": [1, 2]}}
        anycfg.dump(data, str(target))
        assert anycfg.load(str(target)) == data

    def test_dump_failure_leaves_the_file(self, tmp_path):
        target = tmp_path / "out.toml"
        target.write_text("old = 1\n", encoding="utf-8")
        with pytest.raises(anycfg.ConfigTypeError):
            anycfg.dump({"a": None}, str(target))
        assert target.read_text(encoding="utf-8") == "old = 1\n"


@pytest.mark.usefixtures("needs_toml")
class TestEncoding:
    def test_bom_file_loads(self, tmp_path):
        source = tmp_path / "c.toml"
        source.write_bytes(b"\xef\xbb\xbfk = 1\n")
        assert anycfg.load(str(source)) == {"k": 1}

    @pytest.mark.parametrize("codec", ["utf-16", "latin-1"])
    def test_bytes_in_other_codecs(self, codec):
        data = 'a = "café"\n'.encode(codec)
        assert anycfg.loads(data, format="toml", encoding=codec) == {"a": "café"}


class TestMissingDependencies:
    def test_reader_missing_names_the_extra(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", None)
        assert "toml" in formats.names()
        with pytest.raises(anycfg.MissingDependencyError) as caught:
            anycfg.loads("a = 1\n", format="toml")
        # tomli is a dependency below 3.11, so only a broken install gets here.
        assert "tomli" in str(caught.value)
        assert isinstance(caught.value, ImportError)

    def test_reader_missing_on_a_file_load(self, monkeypatch, tmp_path):
        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", None)
        source = tmp_path / "cfg.toml"
        source.write_text("a = 1\n", encoding="utf-8")
        with pytest.raises(anycfg.MissingDependencyError, match="tomli"):
            anycfg.load(str(source))

    @pytest.mark.usefixtures("needs_toml")
    def test_writer_missing_names_the_extra(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tomli_w", None)
        with pytest.raises(anycfg.MissingDependencyError, match=r"\[toml\]"):
            anycfg.dumps({"a": 1}, format="toml")
        # Reading needs only the reader.
        assert anycfg.loads("a = 1\n", format="toml") == {"a": 1}
