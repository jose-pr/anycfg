"""The INI format: configparser's dialect, interpolation, errors and writing."""

from __future__ import annotations

import configparser
import logging

import pytest

import yaconfiglib
from yaconfiglib import formats
from yaconfiglib.testing import SECRET, FormatContract

INIFormat = formats.get("ini")


class TestINIContract(FormatContract):
    format = INIFormat
    samples = [
        {"s": {"a": "1"}},
        {"Server": {"host": "h", "path": "100% done"}, "empty": {}},
        {"s": {"multi": "line one\nline two", "unicode": "café"}},
    ]
    document = "[s]\na = 1\n"
    malformed = "{secret} = 1\n"


class TestRegistration:
    def test_names_and_suffixes(self):
        assert INIFormat.name == "ini"
        assert INIFormat.aliases == ("cfg",)
        assert formats.get("CFG") is INIFormat
        assert formats.for_filename("setup.cfg") is INIFormat
        assert formats.for_filename("x.env.cfg") is INIFormat

    def test_capabilities(self):
        assert formats.capabilities(INIFormat) == {"load", "dump"}

    def test_cfg_file_loads_as_ini(self, tmp_path):
        source = tmp_path / "app.cfg"
        source.write_text("[s]\nk = v\n", encoding="utf-8")
        assert yaconfiglib.load(str(source)) == {"s": {"k": "v"}}


class TestDialect:
    def test_sections_of_string_values(self):
        result = yaconfiglib.loads(
            "[section]\nkey = val\nnumber = 42\n[Other]\nx: y\n", format="ini"
        )
        assert result == {
            "section": {"key": "val", "number": "42"},
            "Other": {"x": "y"},
        }

    def test_option_names_lower_cased_sections_kept(self):
        result = yaconfiglib.loads("[MySection]\nMyKey = 1\n", format="ini")
        assert result == {"MySection": {"mykey": "1"}}

    def test_continuation_lines(self):
        result = yaconfiglib.loads("[s]\nk = one\n  two\n", format="ini")
        assert result == {"s": {"k": "one\ntwo"}}

    def test_comments_only_at_line_start(self):
        result = yaconfiglib.loads(
            "# comment\n; also\n[s]\nk = a # not a comment\n", format="ini"
        )
        assert result == {"s": {"k": "a # not a comment"}}

    def test_default_keys_are_inherited_not_returned(self):
        result = yaconfiglib.loads(
            "[DEFAULT]\ntimeout = 30\n[a]\nx = 1\n[b]\ntimeout = 5\n", format="ini"
        )
        assert result == {"a": {"timeout": "30", "x": "1"}, "b": {"timeout": "5"}}

    def test_default_only_file_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = yaconfiglib.loads("[DEFAULT]\ntimeout = 30\n", format="ini")
        assert result == {}
        assert "default_section" in caplog.text

    def test_empty_document(self):
        assert yaconfiglib.loads("", format="ini") == {}


class TestInterpolation:
    def test_basic_is_the_default(self):
        result = yaconfiglib.loads("[s]\na = 1\nb = %(a)s-2\nc = 100%%\n", format="ini")
        assert result["s"] == {"a": "1", "b": "1-2", "c": "100%"}

    @pytest.mark.parametrize("choice", [None, "none", "NONE"])
    def test_none_keeps_values_verbatim(self, choice):
        result = yaconfiglib.loads(
            "[formatter]\nformat = %(levelname)-5.5s [%(name)s]\n"
            "[auth]\npassword = p%ss\nratio = 100%%\n",
            format="ini",
            interpolation=choice,
        )
        assert result["formatter"]["format"] == "%(levelname)-5.5s [%(name)s]"
        assert result["auth"] == {"password": "p%ss", "ratio": "100%%"}

    def test_extended(self):
        result = yaconfiglib.loads(
            "[s]\na = 1\n[t]\nb = ${s:a}-2\n", format="ini", interpolation="extended"
        )
        assert result["t"]["b"] == "1-2"

    def test_interpolation_instance(self):
        result = yaconfiglib.loads(
            "[s]\na = 1\nb = ${a}\n",
            format="ini",
            interpolation=configparser.ExtendedInterpolation(),
        )
        assert result["s"]["b"] == "1"

    def test_invalid_choice_raises(self):
        with pytest.raises(yaconfiglib.ConfigValueError, match="bogus"):
            yaconfiglib.loads("[s]\nk = v\n", format="ini", interpolation="bogus")

    def test_failed_interpolation_is_a_parse_error(self):
        with pytest.raises(yaconfiglib.ParseError, match="interpolation=None"):
            yaconfiglib.loads("[s]\nk = %(missing)s\n", format="ini")


class TestDefaultSection:
    def test_renamed_default_section(self):
        result = yaconfiglib.loads(
            "[common]\nx = 1\n\n[app]\ny = 2\n", format="ini", default_section="common"
        )
        assert result == {"app": {"x": "1", "y": "2"}}

    def test_escape_hatch_loads_default_as_a_section(self):
        result = yaconfiglib.loads(
            "[DEFAULT]\ntimeout = 30\n", format="ini", default_section="__none__"
        )
        assert result == {"DEFAULT": {"timeout": "30"}}

    def test_option_reaches_a_file_load(self, tmp_path):
        source = tmp_path / "cfg.ini"
        source.write_text("[common]\nx = 1\n\n[app]\ny = 2\n", encoding="utf-8")
        result = yaconfiglib.load(str(source), default_section="common")
        assert result["app"]["y"] == "2"

    def test_format_options_through_a_loader(self, tmp_path):
        source = tmp_path / "cfg.ini"
        source.write_text("[s]\nk = 100%\n", encoding="utf-8")
        loader = yaconfiglib.Loader(format_options={"ini": {"interpolation": None}})
        assert loader.load(str(source)) == {"s": {"k": "100%"}}

    def test_unknown_option_is_type_error(self):
        with pytest.raises(TypeError, match="lowercase"):
            yaconfiglib.loads("[s]\n", format="ini", lowercase=True)


class TestErrors:
    def test_missing_section_header(self):
        with pytest.raises(yaconfiglib.ParseError, match=r"\[section\]") as caught:
            yaconfiglib.loads("key = value\n", format="ini")
        assert caught.value.lineno == 1
        assert caught.value.__context__ is None

    def test_duplicate_section(self):
        with pytest.raises(yaconfiglib.ParseError, match="duplicate section") as caught:
            yaconfiglib.loads("[s]\n[s]\n", format="ini")
        assert caught.value.lineno == 2

    def test_duplicate_option(self):
        with pytest.raises(yaconfiglib.ParseError, match="duplicate option") as caught:
            yaconfiglib.loads("[s]\na = 1\nA = 2\n", format="ini")
        assert caught.value.lineno == 3

    def test_unparsable_line(self):
        with pytest.raises(yaconfiglib.ParseError) as caught:
            yaconfiglib.loads(f"[s]\nk = 1\n[broken\n{SECRET}\n", format="ini")
        assert SECRET not in str(caught.value)

    @pytest.mark.parametrize(
        "document",
        [
            "[s]\nk = %({secret})s\n",
            "[s]\nk = %{secret}\n",
            "{secret}\n",
            "[s]\nk = 1\n  [{secret}\n[s]\n",
        ],
        ids=["missing-ref", "bad-syntax", "no-header", "duplicate-section"],
    )
    def test_errors_never_quote_the_line(self, document):
        text = document.replace("{secret}", SECRET)
        with pytest.raises(yaconfiglib.ParseError) as caught:
            yaconfiglib.loads(text, format="ini")
        seen = str(caught.value) + repr(vars(caught.value))
        assert SECRET not in seen
        assert caught.value.__cause__ is None and caught.value.__context__ is None

    def test_parse_error_names_the_full_path(self, tmp_path):
        source = tmp_path / "app.ini"
        source.write_text("key = value\n", encoding="utf-8")
        with pytest.raises(yaconfiglib.ParseError) as caught:
            yaconfiglib.load(str(source))
        assert caught.value.path == str(source)
        assert str(source) in str(caught.value)

    def test_interpolation_error_names_the_file(self, tmp_path):
        nested = tmp_path / "sub" / "a"
        nested.mkdir(parents=True)
        source = nested / "app.ini"
        source.write_text("[s]\nk = %(missing)s\n", encoding="utf-8")
        with pytest.raises(yaconfiglib.ParseError) as caught:
            yaconfiglib.load(str(source))
        assert str(source) in str(caught.value)


class TestDumps:
    def test_dumps_sections(self):
        out = yaconfiglib.dumps({"s": {"a": "1"}, "t": {"b": "x"}}, format="ini")
        assert out == "[s]\na = 1\n\n[t]\nb = x\n\n"

    def test_percent_escaped_with_default_interpolation(self):
        out = yaconfiglib.dumps({"s": {"a": "100%"}}, format="ini")
        assert "a = 100%%" in out
        assert yaconfiglib.loads(out, format="ini") == {"s": {"a": "100%"}}

    def test_percent_verbatim_without_interpolation(self):
        out = yaconfiglib.dumps({"s": {"a": "100%"}}, format="ini", interpolation=None)
        assert "a = 100%\n" in out
        assert yaconfiglib.loads(out, format="ini", interpolation=None) == {
            "s": {"a": "100%"}
        }

    @pytest.mark.parametrize(
        "value",
        [
            ["s"],
            {"s": "not a mapping"},
            {1: {"a": "1"}},
            {"s": {"Upper": "1"}},
            {"s": {1: "1"}},
            {"s": {"a": 1}},
            {"s": {"a": None}},
        ],
        ids=[
            "not-mapping",
            "section-not-mapping",
            "section-name",
            "upper-option",
            "int-option",
            "int-value",
            "none-value",
        ],
    )
    def test_only_what_loads_returns(self, value):
        with pytest.raises(yaconfiglib.ConfigTypeError):
            yaconfiglib.dumps(value, format="ini")

    def test_unknown_dump_option_is_type_error(self):
        with pytest.raises(TypeError, match="default_section"):
            yaconfiglib.dumps({"s": {}}, format="ini", default_section="x")

    def test_dump_file_round_trip(self, tmp_path):
        target = tmp_path / "out.ini"
        data = {"db": {"host": "h", "port": "5432"}, "Web": {"ratio": "50%"}}
        yaconfiglib.dump(data, str(target))
        assert b"\r\n" not in target.read_bytes()
        assert yaconfiglib.load(str(target)) == data


class TestEncoding:
    def test_bom_file_loads(self, tmp_path):
        source = tmp_path / "c.ini"
        source.write_bytes(b"\xef\xbb\xbf[s]\nk = 1\n")
        assert yaconfiglib.load(str(source)) == {"s": {"k": "1"}}

    def test_latin1_file(self, tmp_path):
        source = tmp_path / "c.ini"
        source.write_bytes(b"[s]\nk = caf\xe9\n")
        assert yaconfiglib.load(str(source), encoding="latin-1") == {"s": {"k": "café"}}


class TestDumpsRoundTrips:
    @pytest.mark.parametrize(
        ("interpolation", "value"),
        [("basic", "50% off"), ("extended", "cost $5 and 50%"), ("none", "%($x)")],
    )
    def test_escapes_for_the_interpolation_it_will_be_read_with(
        self, interpolation, value
    ):
        data = {"s": {"k": value}}
        text = yaconfiglib.dumps(data, format="ini", interpolation=interpolation)
        assert (
            yaconfiglib.loads(text, format="ini", interpolation=interpolation) == data
        )

    @pytest.mark.parametrize(
        "data",
        [
            {"s": {"a=b": "v"}},
            {"s": {"a:b": "v"}},
            {"s": {"k": "  padded"}},
            {"s": {"k": "line\n# comment"}},
            {"DEFAULT": {"k": "v"}},
        ],
    )
    def test_what_would_not_load_back_raises(self, data):
        with pytest.raises(yaconfiglib.ConfigValueError) as caught:
            yaconfiglib.dumps(data, format="ini")
        assert "padded" not in str(caught.value)

    def test_a_custom_interpolation_cannot_be_escaped_for(self):
        with pytest.raises(yaconfiglib.ConfigValueError, match="custom"):
            yaconfiglib.dumps(
                {"s": {"k": "v"}},
                format="ini",
                interpolation=configparser.Interpolation(),
            )

    def test_multi_line_value_round_trips(self):
        data = {"s": {"k": "one\ntwo"}}
        text = yaconfiglib.dumps(data, format="ini")
        assert yaconfiglib.loads(text, format="ini") == data
