"""The dotenv format: dispatch, the assignment grammar, errors and writing."""

from __future__ import annotations

import logging

import pytest

import _extras
import anycfg
from anycfg import formats
from anycfg.testing import SECRET, FormatContract

DotenvFormat = formats.get("dotenv")
BACKSLASH = chr(92)


class TestDotenvContract(FormatContract):
    format = DotenvFormat
    samples = [
        {"A": "1"},
        {"KEY": 'say "hi"\nnext\tline\r' + BACKSLASH, "lower.key-x": "", "U": "café"},
        {"HASH": "a # not a comment", "SQ": "it's"},
    ]
    document = "A=1\nexport B=two\n"
    malformed = 'KEY="{secret}\n'


class TestDispatch:
    @pytest.mark.parametrize(
        "name",
        [
            ".env",
            ".env.local",
            ".env.production",
            ".env.development.local",
            "secrets.env",
            "SECRETS.ENV",
            "conf/.env.prod",
        ],
    )
    def test_claimed_by_dotenv(self, name):
        assert formats.for_filename(name) is DotenvFormat

    @pytest.mark.parametrize(
        "name, expected",
        [
            pytest.param("app.env.yaml", "yaml", marks=_extras.needs_yaml),
            pytest.param(".env.yaml", "yaml", marks=_extras.needs_yaml),
            ("x.env.json", "json"),
            (".env.json", "json"),
            ("x.env.toml", "toml"),
            ("x.env.ini", "ini"),
            ("x.env.cfg", "ini"),
        ],
    )
    def test_another_formats_suffix_wins(self, name, expected):
        assert formats.for_filename(name).name == expected

    def test_claims_name(self):
        assert DotenvFormat.claims_name(".env.staging")
        assert not DotenvFormat.claims_name("app.env.staging")
        assert not DotenvFormat.claims_name("env.staging")

    def test_staged_file_loads(self, tmp_path):
        (tmp_path / ".env.production").write_text("A=1\n", encoding="utf-8")
        assert anycfg.load(str(tmp_path / ".env.production")) == {"A": "1"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_env_yaml_file_loads_as_yaml(self, tmp_path):
        source = tmp_path / "app.env.yaml"
        source.write_text("db:\n  host: h\n  port: 5432\n", encoding="utf-8")
        assert anycfg.load(str(source)) == {"db": {"host": "h", "port": 5432}}

    def test_capabilities(self):
        assert formats.capabilities(DotenvFormat) == {"load", "dump"}


def _loads(text, **options):
    return anycfg.loads(text, format="dotenv", **options)


class TestGrammar:
    def test_assignments_comments_and_quotes(self):
        assert _loads(
            "DB_HOST=127.0.0.1 # local database\n"
            "export DB_PORT=5432\n"
            "# Comment\n"
            'DB_PASS="secret # preserved"\n'
            "DB_TOKEN='abc#123'\n"
            "DB_UNQUOTED_TOKEN=abc#123\n"
        ) == {
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
            "DB_PASS": "secret # preserved",
            "DB_TOKEN": "abc#123",
            "DB_UNQUOTED_TOKEN": "abc#123",
        }

    def test_keys_keep_their_case(self):
        assert _loads("Mixed_Case=1\nlower=2\n") == {"Mixed_Case": "1", "lower": "2"}

    def test_lowercase_option(self):
        assert _loads("DB_HOST=h\nDB_PORT=1\n", lowercase=True) == {
            "db_host": "h",
            "db_port": "1",
        }

    def test_spaces_around_equals_dropped(self):
        assert _loads("  A  =  value  \nexport   B = 2\n") == {"A": "value", "B": "2"}

    def test_empty_values(self):
        assert _loads("A=\nB=''\nC=\"\"\n") == {"A": "", "B": "", "C": ""}

    def test_later_duplicate_replaces(self):
        assert _loads("A=1\nA=2\n") == {"A": "2"}

    def test_dotted_and_dashed_keys(self):
        assert _loads("DOTTED.KEY=1\nDASH-KEY=2\n") == {
            "DOTTED.KEY": "1",
            "DASH-KEY": "2",
        }

    def test_multiline_double_quoted_value(self):
        assert _loads(
            'KEY="-----BEGIN KEY-----\nline2\n-----END KEY-----"\nPORT=1\n'
        ) == {
            "KEY": "-----BEGIN KEY-----\nline2\n-----END KEY-----",
            "PORT": "1",
        }

    def test_multiline_single_quoted_value(self):
        assert _loads("KEY='a\nb'\nPORT=1\n") == {"KEY": "a\nb", "PORT": "1"}

    def test_double_quoted_escapes_decoded(self):
        body = "".join(
            (
                'NL="a', BACKSLASH, 'nb"\n',
                'CR="a', BACKSLASH, 'rb"\n',
                'TAB="a', BACKSLASH, 'tb"\n',
                'QUOTE="say ', BACKSLASH, '"hi', BACKSLASH, '""\n',
                'BACK="C:', BACKSLASH, BACKSLASH, 'tmp"\n',
            )
        )  # fmt: skip
        assert _loads(body) == {
            "NL": "a\nb",
            "CR": "a\rb",
            "TAB": "a\tb",
            "QUOTE": 'say "hi"',
            "BACK": "C:" + BACKSLASH + "tmp",
        }

    def test_unknown_escape_kept(self):
        assert _loads('KEY="a' + BACKSLASH + 'qb"\n') == {"KEY": "a" + BACKSLASH + "qb"}

    def test_single_quoted_value_is_raw(self):
        assert _loads("KEY='it" + BACKSLASH + "'s'\n") == {
            "KEY": "it" + BACKSLASH + "'s"
        }
        assert _loads("P='C:" + BACKSLASH + "new'\n") == {"P": "C:" + BACKSLASH + "new"}

    def test_comment_after_closing_quote(self):
        assert _loads('A="x" # trailing\n') == {"A": "x"}

    def test_hash_without_preceding_space_is_data(self):
        assert _loads("A=abc#123\nB= #comment\n") == {"A": "abc#123", "B": ""}

    def test_hash_right_after_equals_is_data(self):
        assert _loads("B=#start\n") == {"B": "#start"}

    def test_apostrophe_in_unquoted_value_strips_comment(self):
        assert _loads("APOS=it's here # comment\n") == {"APOS": "it's here"}

    def test_crlf_and_cr_line_endings(self):
        assert _loads("A=1\r\nB=2\rC=3\r\n") == {"A": "1", "B": "2", "C": "3"}

    def test_unicode_line_separators_stay_in_value(self):
        separators = [
            "\x0b",
            "\x0c",
            "\x1c",
            "\x1d",
            "\x1e",
            "\x85",
            " ",
            " ",
        ]
        lines = ["K{}=x{}y".format(index, sep) for index, sep in enumerate(separators)]
        lines.append('D="q r"')
        lines.append("S='q r'")
        lines.append("C=3")
        result = anycfg.loads(
            ("\n".join(lines) + "\n").encode("utf-8"), format="dotenv"
        )
        expected = {
            "K{}".format(index): "x{}y".format(sep)
            for index, sep in enumerate(separators)
        }
        expected["D"] = "q r"
        expected["S"] = "q r"
        expected["C"] = "3"
        assert result == expected

    def test_latin1_nel_byte_stays_in_value(self, tmp_path):
        (tmp_path / "c.env").write_bytes(b"MSG=wait\x85done\nPORT=1\n")
        result = anycfg.load(str(tmp_path / "c.env"), encoding="latin-1")
        assert result == {"MSG": "wait\x85done", "PORT": "1"}


class TestRejectedLines:
    def test_bare_key_line_warns_and_is_skipped(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _loads(f"{SECRET}\nAFTER=ok\n")
        assert result == {"AFTER": "ok"}
        assert "line 1" in caplog.text
        assert SECRET not in caplog.text

    def test_text_after_closing_quote_warns_and_skips(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _loads("S='it's here'\nAFTER=ok\n")
        assert result == {"AFTER": "ok"}
        assert "line 1" in caplog.text

    def test_warning_counts_lines_of_multiline_values(self, caplog):
        with caplog.at_level(logging.WARNING):
            _loads('A="one\ntwo"\nBROKEN\n')
        assert "line 3" in caplog.text

    def test_strict_raises_on_unparseable_line(self):
        with pytest.raises(anycfg.ParseError) as caught:
            _loads(f"A=1\n{SECRET}\nAFTER=ok\n", strict=True)
        assert caught.value.lineno == 2
        assert SECRET not in str(caught.value)

    def test_strict_accepts_comments_and_blank_lines(self):
        assert _loads("# only a comment\n\n", strict=True) == {}

    def test_unterminated_quote_always_raises(self):
        with pytest.raises(anycfg.ParseError) as caught:
            _loads(f'OK=1\nKEY="{SECRET}\nPORT=1\n')
        error = caught.value
        assert error.lineno == 2
        assert "KEY" in str(error)
        assert SECRET not in str(error)

    def test_unterminated_quote_names_the_file(self, tmp_path):
        source = tmp_path / "c.env"
        source.write_text('KEY="never closed\n', encoding="utf-8")
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.load(str(source))
        assert caught.value.path == str(source)
        assert str(caught.value).startswith(f"{source}:1: ")

    def test_unknown_option_is_type_error(self):
        with pytest.raises(TypeError, match="interpolation"):
            _loads("A=1\n", interpolation=None)

    def test_format_options_through_a_loader(self, tmp_path):
        source = tmp_path / "c.env"
        source.write_text("NOEQ\n", encoding="utf-8")
        loader = anycfg.Loader(format_options={"dotenv": {"strict": True}})
        with pytest.raises(anycfg.ParseError):
            loader.load(str(source))


class TestDumps:
    def test_double_quoted_and_escaped(self):
        out = anycfg.dumps(
            {"A": "plain", "B": 'q"' + BACKSLASH + "\n\t\r"}, format="dotenv"
        )
        assert out == (
            'A="plain"\n'
            'B="q' + BACKSLASH + '"' + BACKSLASH * 2 + BACKSLASH + "n"
            + BACKSLASH + "t" + BACKSLASH + 'r"\n'
        )  # fmt: skip

    def test_round_trip_keeps_case(self):
        data = {"Mixed": "1", "lower": "two words # hash"}
        assert _loads(anycfg.dumps(data, format="dotenv")) == data

    @pytest.mark.parametrize(
        "value",
        [["A"], {"A": 1}, {"A": None}, {"A": {"B": "1"}}, {"1A": "x"}, {"A B": "x"}],
        ids=["list", "int", "none", "nested", "digit-key", "space-key"],
    )
    def test_flat_string_mapping_only(self, value):
        with pytest.raises(anycfg.ConfigTypeError):
            anycfg.dumps(value, format="dotenv")

    def test_no_dump_options(self):
        with pytest.raises(TypeError, match="lowercase"):
            anycfg.dumps({"A": "1"}, format="dotenv", lowercase=True)

    def test_dump_file_round_trip(self, tmp_path):
        target = tmp_path / "out.env"
        data = {"A": "1", "MULTI": "x\ny"}
        anycfg.dump(data, str(target))
        assert anycfg.load(str(target)) == data


class TestEncoding:
    def test_bom_file_loads(self, tmp_path):
        source = tmp_path / ".env"
        source.write_bytes(b"\xef\xbb\xbfDB_HOST=h\nDB_PORT=5432\n")
        assert anycfg.load(str(source)) == {"DB_HOST": "h", "DB_PORT": "5432"}

    def test_invalid_utf8_is_a_parse_error(self, tmp_path):
        source = tmp_path / ".env"
        source.write_bytes(b"A=caf\xe9\n")
        with pytest.raises(anycfg.ParseError, match="byte 5"):
            anycfg.load(str(source))


class TestCoerce:
    def test_unquoted_values_only(self):
        text = 'PORT=80\nQUOTED="80"\nDEBUG=true\nWORD=yes\n'
        assert anycfg.loads(text, format="dotenv", coerce=True) == {
            "PORT": 80,
            "QUOTED": "80",
            "DEBUG": True,
            "WORD": "yes",
        }

    def test_strings_by_default(self):
        assert anycfg.loads("PORT=80\n", format="dotenv") == {"PORT": "80"}
