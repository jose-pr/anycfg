"""The JSON format: dialect, errors, options and writing."""

from __future__ import annotations

import decimal
import io
import json

import pytest

import yaconfiglib
from yaconfiglib import formats
from yaconfiglib.testing import SECRET, FormatContract

JSONFormat = formats.get("json")


class TestJSONContract(FormatContract):
    format = JSONFormat
    samples = [
        {"a": 1},
        {"nested": {"b": [1, 2.5, None, True, False]}, "text": "café"},
        [1, "two", {"three": 3}],
        "a string",
        0,
        None,
    ]
    document = '{"a": 1, "b": [true, null]}'
    malformed = '{"a": "{secret}",, }'


class TestRegistration:
    def test_name_and_suffix(self):
        assert JSONFormat.name == "json"
        assert JSONFormat.suffixes == (".json",)
        assert formats.for_filename("conf.JSON") is JSONFormat

    def test_capabilities(self):
        assert formats.capabilities(JSONFormat) == {"load", "dump"}


class TestParsing:
    def test_scalars_and_containers(self):
        assert yaconfiglib.loads(
            '{"i": 1, "f": 1.5, "e": 1e5, "t": true, "n": null, "l": [1]}',
            format="json",
        ) == {"i": 1, "f": 1.5, "e": 100000.0, "t": True, "n": None, "l": [1]}

    def test_int_and_float_stay_distinct(self):
        result = yaconfiglib.loads('{"i": 1, "f": 1.0}', format="json")
        assert type(result["i"]) is int
        assert type(result["f"]) is float

    def test_duplicate_keys_last_wins(self):
        assert yaconfiglib.loads('{"a": 1, "a": 2}', format="json") == {"a": 2}

    def test_top_level_scalar(self):
        assert yaconfiglib.loads("42", format="json") == 42

    @pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_constants_refused(self, constant):
        with pytest.raises(yaconfiglib.ParseError):
            yaconfiglib.loads('{"a": %s}' % constant, format="json")

    def test_loads_all_yields_the_one_document(self):
        assert list(yaconfiglib.loads_all('{"a": 1}', format="json")) == [{"a": 1}]

    def test_deeply_nested_document_parses_or_is_a_parse_error(self):
        # Whether json recurses that deep depends on the interpreter and the
        # platform (3.14 on Linux and macOS parses it); a RecursionError never
        # escapes either way.
        depth = 100000
        try:
            result = yaconfiglib.loads("[" * depth + "]" * depth, format="json")
        except yaconfiglib.ParseError as error:
            assert "nested too deeply" in str(error)
        else:
            assert isinstance(result, list)


class TestErrors:
    def test_parse_error_carries_position(self):
        with pytest.raises(yaconfiglib.ParseError) as caught:
            yaconfiglib.loads('{\n  "a": }\n', format="json")
        error = caught.value
        assert (error.lineno, error.colno) == (2, 8)
        assert error.msg == "Expecting value"
        assert isinstance(error, ValueError)
        assert not isinstance(error, json.JSONDecodeError)

    def test_parse_error_names_the_file(self, tmp_path):
        source = tmp_path / "bad.json"
        source.write_text('{"a": }\n', encoding="utf-8")
        with pytest.raises(yaconfiglib.ParseError) as caught:
            yaconfiglib.load(str(source))
        assert caught.value.path == str(source)
        assert str(caught.value).startswith(f"{source}:1:7: ")

    def test_direct_format_call_has_no_path(self):
        with pytest.raises(yaconfiglib.ParseError) as caught:
            JSONFormat().loads("[1,")
        assert caught.value.path is None
        assert caught.value.__context__ is None

    def test_hook_failure_is_a_parse_error_without_chain(self):
        def bad_int(text):
            raise ValueError("refused " + text)

        with pytest.raises(yaconfiglib.ParseError) as caught:
            yaconfiglib.loads('{"a": 1}', format="json", parse_int=bad_int)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

    def test_secret_value_never_in_error(self):
        text = '{"password": "%s" "next": 1}' % SECRET
        with pytest.raises(yaconfiglib.ParseError) as caught:
            yaconfiglib.loads(text, format="json")
        assert SECRET not in str(caught.value)
        assert SECRET not in repr(vars(caught.value))


class TestOptions:
    def test_parse_float(self):
        result = yaconfiglib.loads(
            '{"rate": 1.5}', format="json", parse_float=decimal.Decimal
        )
        assert result["rate"] == decimal.Decimal("1.5")

    def test_parse_int(self):
        assert yaconfiglib.loads('{"n": 7}', format="json", parse_int=str) == {"n": "7"}

    def test_object_pairs_hook(self):
        result = yaconfiglib.loads(
            '{"b": 1, "a": 2}', format="json", object_pairs_hook=list
        )
        assert result == [("b", 1), ("a", 2)]

    def test_object_hook(self):
        result = yaconfiglib.loads(
            '{"a": {"b": 1}}', format="json", object_hook=lambda d: sorted(d)
        )
        assert result == ["a"]

    def test_options_reach_a_file_load(self, tmp_path):
        source = tmp_path / "c.json"
        source.write_text('{"rate": 0.1}', encoding="utf-8")
        result = yaconfiglib.load(str(source), parse_float=decimal.Decimal)
        assert result == {"rate": decimal.Decimal("0.1")}

    def test_format_options_through_a_loader(self, tmp_path):
        source = tmp_path / "c.json"
        source.write_text('{"rate": 0.1}', encoding="utf-8")
        loader = yaconfiglib.Loader(
            format_options={"json": {"parse_float": decimal.Decimal}}
        )
        assert loader.load(str(source)) == {"rate": decimal.Decimal("0.1")}

    def test_unknown_load_option_is_type_error(self):
        with pytest.raises(TypeError, match="strict"):
            yaconfiglib.loads("{}", format="json", strict=True)

    def test_unknown_dump_option_is_type_error(self):
        with pytest.raises(TypeError, match="encoding"):
            yaconfiglib.dumps({}, format="json", encoding="utf-8")


class TestDumps:
    def test_defaults_indent_two_unescaped_insertion_order(self):
        out = yaconfiglib.dumps({"z": "café", "a": [1]}, format="json")
        assert out == '{\n  "z": "café",\n  "a": [\n    1\n  ]\n}'

    def test_dump_options(self):
        out = yaconfiglib.dumps(
            {"b": "é", "a": 1},
            format="json",
            indent=None,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        assert out == '{"a":1,"b":"\\u00e9"}'

    def test_default_hook(self):
        out = yaconfiglib.dumps(
            {"d": decimal.Decimal("1.5")}, format="json", indent=None, default=str
        )
        assert out == '{"d": "1.5"}'

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_floats_refused(self, value):
        with pytest.raises(yaconfiglib.ConfigValueError):
            yaconfiglib.dumps({"a": value}, format="json")

    def test_unserializable_is_config_type_error(self):
        with pytest.raises(yaconfiglib.ConfigTypeError):
            yaconfiglib.dumps({"a": {1, 2}}, format="json")

    def test_circular_reference_is_config_value_error(self):
        data = {}
        data["self"] = data
        with pytest.raises(yaconfiglib.ConfigValueError):
            yaconfiglib.dumps(data, format="json")

    def test_tuple_written_as_array(self):
        assert yaconfiglib.loads(
            yaconfiglib.dumps({"t": (1, 2)}, format="json"), format="json"
        ) == {"t": [1, 2]}

    def test_dump_file_round_trip(self, tmp_path):
        target = tmp_path / "out.json"
        data = {"name": "café", "items": [1, 2], "nested": {"ok": True}}
        yaconfiglib.dump(data, str(target))
        assert target.read_bytes().endswith(b"}\n")
        assert yaconfiglib.load(str(target)) == data

    def test_dump_failure_leaves_the_file(self, tmp_path):
        target = tmp_path / "out.json"
        target.write_text("old\n", encoding="utf-8")
        with pytest.raises(yaconfiglib.ConfigTypeError):
            yaconfiglib.dump({"a": object()}, str(target))
        assert target.read_text(encoding="utf-8") == "old\n"

    def test_dump_to_text_stream(self):
        buffer = io.StringIO()
        yaconfiglib.dump({"a": 1}, buffer, format="json")
        assert json.loads(buffer.getvalue()) == {"a": 1}


class TestEncoding:
    def test_bom_file_loads(self, tmp_path):
        source = tmp_path / "c.json"
        source.write_bytes(b"\xef\xbb\xbf" + b'{"k": 1}')
        assert yaconfiglib.load(str(source)) == {"k": 1}

    @pytest.mark.parametrize("codec", ["utf-16", "utf-16-le", "utf-32", "latin-1"])
    def test_bytes_in_other_codecs(self, codec):
        data = '{"a": "café"}'.encode(codec)
        assert yaconfiglib.loads(data, format="json", encoding=codec) == {"a": "café"}

    def test_invalid_utf8_is_parse_error_naming_offset(self):
        with pytest.raises(yaconfiglib.ParseError, match="byte 7"):
            yaconfiglib.loads(b'{"a": "\xff"}', format="json")

    def test_bytearray(self):
        assert yaconfiglib.loads(bytearray(b'{"a": 1}'), format="json") == {"a": 1}
