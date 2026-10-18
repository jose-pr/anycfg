"""The YAML format: dialect, tags, errors and writing.

Includes resolved through a loader are tested with the loader; here only what
the format does on its own.
"""

from __future__ import annotations

import collections
import datetime
import decimal
import enum
import io
import sys

import pytest

import anycfg
from anycfg import formats
from anycfg.sources import Data, Text
from anycfg.testing import SECRET, FormatContract

YAMLFormat = formats.get("yaml")


@pytest.mark.usefixtures("needs_yaml")
class TestYAMLContract(FormatContract):
    format = YAMLFormat
    samples = [
        {"a": 1},
        {"nested": {"list": [1, 2.5, None, True, "x"]}, "text": "café"},
        [1, [2, 3], {"k": "v"}],
        {"when": datetime.date(2020, 1, 2)},
        {"at": datetime.datetime(2020, 1, 2, 3, 4, 5)},
        {"tags": {1, 2, 3}},
        {"blob": b"\x00\xff"},
        "plain",
        None,
    ]
    document = "a: 1\nb: [x, y]\n"
    malformed = "a: {secret}\n  b: [\n"


class TestRegistration:
    def test_names_and_suffixes(self):
        assert YAMLFormat.name == "yaml"
        assert YAMLFormat.aliases == ("yml",)
        assert formats.get("YML") is YAMLFormat
        assert formats.for_filename("a.yml") is YAMLFormat
        assert formats.for_filename("A.YAML") is YAMLFormat

    def test_capabilities(self):
        assert formats.capabilities(YAMLFormat) == {"load", "load_all", "dump"}


@pytest.mark.usefixtures("needs_yaml")
class TestDialect:
    def test_yaml_1_1_scalars(self):
        result = anycfg.loads(
            "a: yes\nb: off\nc: 0755\nd: 1.10\ne: '0755'\nf: 'yes'\n", format="yaml"
        )
        assert result == {
            "a": True,
            "b": False,
            "c": 493,
            "d": 1.1,
            "e": "0755",
            "f": "yes",
        }

    def test_dates_load_as_dates(self):
        result = anycfg.loads("d: 2020-01-02\nt: 2020-01-02 03:04:05\n", format="yaml")
        assert result == {
            "d": datetime.date(2020, 1, 2),
            "t": datetime.datetime(2020, 1, 2, 3, 4, 5),
        }

    def test_empty_document_is_none(self):
        assert anycfg.loads("", format="yaml") is None

    def test_duplicate_keys_raise(self):
        with pytest.raises(anycfg.ParseError, match="duplicate key") as caught:
            anycfg.loads("a: 1\nb: 2\na: 3\n", format="yaml")
        assert caught.value.lineno == 3

    def test_nested_duplicate_keys_raise(self):
        with pytest.raises(anycfg.ParseError, match="duplicate key"):
            anycfg.loads("db:\n  host: a\n  host: b\n", format="yaml")

    def test_duplicate_keys_in_flow_mapping_raise(self):
        with pytest.raises(anycfg.ParseError, match="duplicate key"):
            anycfg.loads("a: {x: 1, x: 2}\n", format="yaml")

    def test_merge_key_may_be_overridden(self):
        result = anycfg.loads(
            "base: &base {x: 1, y: 2}\nchild:\n  <<: *base\n  x: 3\n", format="yaml"
        )
        assert result == {"base": {"x": 1, "y": 2}, "child": {"x": 3, "y": 2}}

    def test_two_merge_keys_are_not_duplicates(self):
        result = anycfg.loads(
            "a: &a {x: 1}\nb: &b {y: 2}\nc:\n  <<: *a\n  <<: *b\n", format="yaml"
        )
        assert result["c"] == {"x": 1, "y": 2}

    def test_explicit_duplicate_beside_merge_key_raises(self):
        with pytest.raises(anycfg.ParseError, match="duplicate key"):
            anycfg.loads("a: &a {x: 1}\nc:\n  <<: *a\n  x: 2\n  x: 3\n", format="yaml")

    def test_unhashable_key_is_parse_error(self):
        with pytest.raises(anycfg.ParseError):
            anycfg.loads("? [a, b]\n: 1\n", format="yaml")

    def test_python_tags_are_refused(self):
        with pytest.raises(anycfg.ParseError):
            anycfg.loads("a: !!python/object/apply:os.getcwd []\n", format="yaml")

    def test_unknown_tag_is_parse_error(self):
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.loads("a: !custom x\n", format="yaml")
        assert (caught.value.lineno, caught.value.colno) == (1, 4)


@pytest.mark.usefixtures("needs_yaml")
class TestDelete:
    def test_direct_format_call_returns_the_marker(self):
        result = YAMLFormat().loads("a: !delete\nb: 1\n")
        assert result["a"] is anycfg.DELETE
        assert result["b"] == 1

    def test_markers_are_pruned_from_a_single_document(self):
        assert anycfg.loads("a: !delete\nb: 1\n", format="yaml") == {"b": 1}

    @pytest.mark.parametrize("merge", ["simple", "substitute", "deep"])
    def test_delete_removes_a_key_from_an_earlier_layer(self, merge):
        loader = anycfg.Loader(merge=merge)
        result = loader.load(
            Data({"a": 1, "b": 2}), Text("a: !delete\n", format="yaml")
        )
        assert result == {"b": 2}

    def test_delete_removes_a_nested_key(self):
        loader = anycfg.Loader(merge="deep")
        result = loader.load(
            Data({"db": {"host": "h", "port": 1}}),
            Text("db:\n  port: !delete\n", format="yaml"),
        )
        assert result == {"db": {"host": "h"}}


@pytest.mark.usefixtures("needs_yaml")
class TestMultipleDocuments:
    STREAM = "a: 1\n---\nb: 2\n---\n- 3\n"

    def test_loads_all_yields_each_document(self):
        assert list(anycfg.loads_all(self.STREAM, format="yaml")) == [
            {"a": 1},
            {"b": 2},
            [3],
        ]

    def test_loads_all_of_empty_text_yields_nothing(self):
        assert list(anycfg.loads_all("", format="yaml")) == []

    def test_loads_refuses_a_stream(self):
        with pytest.raises(anycfg.ParseError, match="single document"):
            anycfg.loads(self.STREAM, format="yaml")

    def test_load_refuses_a_stream_file(self, tmp_path):
        source = tmp_path / "multi.yaml"
        source.write_text(self.STREAM, encoding="utf-8")
        with pytest.raises(anycfg.ParseError):
            anycfg.load(str(source))

    def test_load_all_reads_a_stream_file(self, tmp_path):
        source = tmp_path / "multi.yaml"
        source.write_text(self.STREAM, encoding="utf-8")
        assert list(anycfg.load_all(str(source))) == [{"a": 1}, {"b": 2}, [3]]

    def test_error_in_a_later_document_is_a_parse_error(self):
        with pytest.raises(anycfg.ParseError, match="duplicate key") as caught:
            list(anycfg.loads_all("a: 1\n---\nb: 1\nb: 2\n", format="yaml"))
        assert caught.value.lineno == 4

    def test_each_document_is_its_own_anchor_scope(self):
        with pytest.raises(anycfg.ParseError, match="alias"):
            list(anycfg.loads_all("a: &x 1\n---\nb: *x\n", format="yaml"))


@pytest.mark.usefixtures("needs_yaml")
class TestErrors:
    def test_parse_error_carries_position(self):
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.loads("a: [1\nb: 2\n", format="yaml")
        error = caught.value
        assert (error.lineno, error.colno) == (2, 2)
        assert "flow sequence" in error.msg
        assert error.__cause__ is None and error.__context__ is None

    def test_parse_error_names_the_file(self, tmp_path):
        source = tmp_path / "bad.yaml"
        source.write_text("b: [unclosed\n", encoding="utf-8")
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.load(str(source))
        assert caught.value.path == str(source)
        assert str(source) in str(caught.value)

    def test_reader_error_names_the_file(self, tmp_path):
        source = tmp_path / "bell.yaml"
        source.write_bytes(b"a: \x07\n")
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.load(str(source))
        assert "bell.yaml" in str(caught.value)
        assert caught.value.__context__ is None

    def test_deep_nesting_is_a_parse_error(self):
        depth = 20000
        with pytest.raises(anycfg.ParseError):
            anycfg.loads("[" * depth + "]" * depth, format="yaml")

    @pytest.mark.parametrize(
        "document",
        [
            pytest.param("a: 2020-13-45\n", id="implicit-timestamp"),
            pytest.param("a: !!timestamp {secret}\n", id="timestamp-tag"),
            pytest.param("a: !!int {secret}\n", id="int-tag"),
            pytest.param("a: !!float {secret}\n", id="float-tag"),
            pytest.param("a: !!bool {secret}\n", id="bool-tag"),
        ],
    )
    def test_bad_typed_scalar_is_a_parse_error(self, document):
        text = document.replace("{secret}", SECRET)
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.loads(text, format="yaml")
        assert SECRET not in str(caught.value)
        assert caught.value.__context__ is None

    @pytest.mark.parametrize(
        "document",
        [
            pytest.param("a: !{secret} x\n", id="unknown-tag"),
            pytest.param("a: *{secret}\n", id="undefined-alias"),
        ],
    )
    def test_names_from_the_document_may_appear_in_errors(self, document):
        # A tag or an alias is structure, and naming it is what makes the
        # error useful; values are what must never appear.
        text = document.replace("{secret}", SECRET)
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.loads(text, format="yaml")
        assert caught.value.lineno == 1
        assert caught.value.__context__ is None


@pytest.mark.usefixtures("needs_yaml")
class TestIncludeTagsStayPrivate:
    def test_stock_safe_loader_has_no_anycfg_tags(self):
        import yaml

        anycfg.loads("a: 1\n", format="yaml")
        for tag in ("!include", "!load", "!delete"):
            assert tag not in yaml.SafeLoader.yaml_constructors

    def test_plain_safe_load_never_resolves_include(self, tmp_path):
        import yaml

        target = tmp_path / "t.yaml"
        target.write_text("a: 1\n", encoding="utf-8")
        anycfg.loads("a: 1\n", format="yaml")
        with pytest.raises(yaml.constructor.ConstructorError):
            yaml.safe_load(f"x: !include '{target.as_posix()}'\n")

    @pytest.mark.parametrize("tag", ["!include", "!load"])
    def test_direct_format_call_refuses_include(self, tmp_path, tag):
        target = tmp_path / "t.yaml"
        target.write_text("a: 1\n", encoding="utf-8")
        document = f"x: {tag} '{target.as_posix()}'\n"
        with pytest.raises(anycfg.ParseError, match="Loader") as caught:
            YAMLFormat().loads(document)
        assert (caught.value.lineno, caught.value.colno) == (1, 4)
        assert isinstance(caught.value, anycfg.ConfigError)

    def test_dumps_leaves_pyyaml_dumpers_alone(self):
        import yaml

        dumpers = (yaml.Dumper, yaml.SafeDumper)
        before = [
            (dict(d.yaml_representers), dict(d.yaml_multi_representers))
            for d in dumpers
        ]
        anycfg.dumps(
            {"a": collections.OrderedDict(b=1), "t": (1,), "c": _Colour.RED},
            format="yaml",
        )
        after = [
            (dict(d.yaml_representers), dict(d.yaml_multi_representers))
            for d in dumpers
        ]
        assert after == before
        assert dict not in yaml.SafeDumper.yaml_multi_representers


class _Colour(enum.Enum):
    RED = "red"


class _Level(enum.IntEnum):
    HIGH = 3


@pytest.mark.usefixtures("needs_yaml")
class TestDumps:
    def test_keeps_insertion_order(self):
        assert (
            anycfg.dumps({"zeta": 1, "alpha": 2}, format="yaml")
            == "zeta: 1\nalpha: 2\n"
        )

    def test_writes_non_ascii_as_is(self):
        assert anycfg.dumps({"a": "café"}, format="yaml") == "a: café\n"

    def test_block_style_by_default(self):
        assert anycfg.dumps({"a": [1, 2]}, format="yaml") == "a:\n- 1\n- 2\n"

    def test_pyyaml_defaults_can_be_restored(self):
        out = anycfg.dumps(
            {"b": 1, "a": "é"}, format="yaml", sort_keys=True, allow_unicode=False
        )
        assert out == 'a: "\\xE9"\nb: 1\n'

    def test_layout_options(self):
        out = anycfg.dumps(
            {"a": {"b": [1, 2]}},
            format="yaml",
            default_flow_style=True,
            explicit_start=True,
            explicit_end=True,
        )
        assert out.startswith("---")
        assert out.rstrip().endswith("...")
        assert anycfg.loads(out, format="yaml") == {"a": {"b": [1, 2]}}

    def test_indent_and_width(self):
        out = anycfg.dumps({"a": {"b": 1}}, format="yaml", indent=4, width=40)
        assert out == "a:\n    b: 1\n"

    def test_unknown_dump_option_is_type_error(self):
        with pytest.raises(TypeError, match="encoding"):
            anycfg.dumps({"a": 1}, format="yaml", encoding="utf-8")

    @pytest.mark.parametrize(
        "value",
        [
            collections.OrderedDict([("b", 1), ("a", 2)]),
            collections.defaultdict(list, {"a": [1]}),
            anycfg.DotAccessibleDict({"a": {"b": 1}}),
        ],
        ids=["OrderedDict", "defaultdict", "DotAccessibleDict"],
    )
    def test_mapping_subclasses_as_plain_mappings(self, value):
        out = anycfg.dumps({"v": value}, format="yaml")
        assert "python/" not in out and "!!" not in out
        assert anycfg.loads(out, format="yaml") == {"v": dict(value)}

    def test_list_subclass_as_plain_list(self):
        class Items(list):
            pass

        out = anycfg.dumps({"v": Items([1, 2])}, format="yaml")
        assert "python/" not in out
        assert anycfg.loads(out, format="yaml") == {"v": [1, 2]}

    def test_tuple_is_a_plain_sequence(self):
        out = anycfg.dumps({"t": (1, (2, 3))}, format="yaml")
        assert "python/tuple" not in out
        assert anycfg.loads(out, format="yaml") == {"t": [1, [2, 3]]}

    def test_enum_member_as_its_value(self):
        out = anycfg.dumps({"c": _Colour.RED, "l": [_Level.HIGH]}, format="yaml")
        assert anycfg.loads(out, format="yaml") == {"c": "red", "l": [3]}

    def test_set_round_trips(self):
        out = anycfg.dumps({"s": {1, 2}}, format="yaml")
        assert anycfg.loads(out, format="yaml") == {"s": {1, 2}}

    def test_dates_round_trip(self):
        data = {
            "d": datetime.date(2020, 1, 2),
            "t": datetime.datetime(2020, 1, 2, 3, 4, 5),
        }
        out = anycfg.dumps(data, format="yaml")
        assert anycfg.loads(out, format="yaml") == data

    def test_loaded_config_round_trips(self):
        original = "a: 1\nb:\n  c: 2\n"
        loader = anycfg.Loader(wrap=True)
        config = loader.load(Text(original, format="yaml"))
        assert isinstance(config, anycfg.DotAccessibleDict)
        out = anycfg.dumps(config, format="yaml")
        assert "python/" not in out
        assert anycfg.loads(out, format="yaml") == {"a": 1, "b": {"c": 2}}

    @pytest.mark.parametrize(
        "value",
        [object(), decimal.Decimal("1.1"), 1 + 2j, (x for x in [])],
        ids=["object", "Decimal", "complex", "generator"],
    )
    def test_unrepresentable_raises_config_type_error(self, value):
        # The format itself, without dehydrate: `dumps()` writes a Decimal as text.
        with pytest.raises(anycfg.ConfigTypeError) as caught:
            formats.get("yaml")().dumps({"v": value})
        assert caught.value.__cause__ is None

    def test_unrepresentable_leaves_an_existing_file(self, tmp_path):
        target = tmp_path / "out.yaml"
        target.write_text("old\n", encoding="utf-8")
        with pytest.raises(anycfg.ConfigTypeError):
            anycfg.dump({"g": (x for x in [])}, str(target))
        assert target.read_text(encoding="utf-8") == "old\n"

    @pytest.mark.parametrize("name", ["out.yaml", "out.yml"])
    def test_dump_file_round_trip(self, tmp_path, name):
        target = tmp_path / name
        data = {"key": "val", "list": [1, 2], "nested": {"ok": True}}
        anycfg.dump(data, str(target))
        assert b"\r\n" not in target.read_bytes()
        assert anycfg.load(str(target)) == data

    @pytest.mark.parametrize("codec", ["utf-8", "utf-16"])
    def test_dump_path_uses_encoding(self, tmp_path, codec):
        import yaml

        target = tmp_path / "out.yaml"
        anycfg.dump({"a": "café"}, str(target), encoding=codec)
        assert yaml.safe_load(target.read_text(encoding=codec)) == {"a": "café"}
        assert anycfg.load(str(target), encoding=codec) == {"a": "café"}

    def test_dump_binary_stream(self):
        buffer = io.BytesIO()
        anycfg.dump({"a": "café"}, buffer, format="yaml")
        assert buffer.getvalue() == "a: café\n".encode("utf-8")


@pytest.mark.usefixtures("needs_yaml")
class TestEncoding:
    def test_bom_file_loads(self, tmp_path):
        source = tmp_path / "c.yaml"
        source.write_bytes(b"\xef\xbb\xbfk: 1\n")
        assert anycfg.load(str(source)) == {"k": 1}

    @pytest.mark.parametrize("codec", ["utf-8-sig", "utf-16", "utf-16-le", "utf-32"])
    def test_bytes_in_any_codec(self, codec):
        data = "a: café\n".encode(codec)
        assert anycfg.loads(data, format="yaml", encoding=codec) == {"a": "café"}

    def test_utf16_file_without_encoding_is_parse_error(self, tmp_path):
        source = tmp_path / "wide.yaml"
        source.write_bytes("a: 1\n".encode("utf-16"))
        with pytest.raises(anycfg.ParseError) as caught:
            anycfg.load(str(source))
        assert "wide.yaml" in str(caught.value)
        assert "utf-8" in str(caught.value)


class TestMissingPyYAML:
    """``yaml`` absent: the format stays listed and says which extra to install."""

    @pytest.fixture
    def no_yaml(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "yaml", None)

    def test_still_listed(self, no_yaml):
        assert "yaml" in formats.names()
        assert formats.for_filename("a.yaml") is YAMLFormat

    def test_loads_names_the_extra(self, no_yaml):
        with pytest.raises(anycfg.MissingDependencyError) as caught:
            anycfg.loads("a: 1\n", format="yaml")
        error = caught.value
        assert "anycfg[yaml]" in str(error)
        assert isinstance(error, ImportError)
        assert isinstance(error, anycfg.UnsupportedFormatError)

    def test_load_file_names_the_extra(self, no_yaml, tmp_path):
        source = tmp_path / "cfg.yaml"
        source.write_text("a: 1\n", encoding="utf-8")
        with pytest.raises(anycfg.MissingDependencyError, match=r"\[yaml\]"):
            anycfg.load(str(source))

    def test_dumps_names_the_extra(self, no_yaml):
        with pytest.raises(anycfg.MissingDependencyError, match=r"\[yaml\]"):
            anycfg.dumps({"a": 1}, format="yaml")

    def test_other_formats_unaffected(self, no_yaml):
        assert anycfg.loads('{"a": 1}', format="json") == {"a": 1}


@pytest.mark.usefixtures("needs_yaml")
class TestDialect:
    DOC = (
        "a: yes\nb: 0755\nc: 0o755\nd: 2024-01-02\ne: TRUE\nf: ~\ng: .inf\n"
        "h: 1.10\nj: on\nk: 1_000\nm: 0x1F\nn: 1:30\n"
    )

    def test_default_is_yaml_1_1(self):
        doc = anycfg.loads(self.DOC, format="yaml")
        assert (doc["a"], doc["b"], doc["c"], doc["j"], doc["n"]) == (
            True,
            493,
            "0o755",
            True,
            90,
        )
        assert doc["d"] == datetime.date(2024, 1, 2)

    def test_1_2_core_schema(self):
        doc = anycfg.loads(self.DOC, format="yaml", dialect="1.2")
        assert doc == {
            "a": "yes",
            "b": 755,
            "c": 493,
            "d": "2024-01-02",
            "e": True,
            "f": None,
            "g": float("inf"),
            "h": 1.1,
            "j": "on",
            "k": "1_000",
            "m": 31,
            "n": "1:30",
        }

    def test_merge_keys_still_work(self):
        doc = anycfg.loads(
            "b: &b {x: 1}\nc:\n  <<: *b\n  y: 2\n", format="yaml", dialect="1.2"
        )
        assert doc["c"] == {"x": 1, "y": 2}

    def test_unknown_dialect(self):
        with pytest.raises(anycfg.ConfigValueError, match="dialect"):
            anycfg.loads("a: 1", format="yaml", dialect="2")

    def test_reaches_includes_and_the_loader_setting(self, tmp_path):
        (tmp_path / "inc.yaml").write_text("v: no\n", encoding="utf-8")
        (tmp_path / "app.yaml").write_text("x: !include inc.yaml\n", encoding="utf-8")
        assert anycfg.load(str(tmp_path / "app.yaml"), dialect="1.2") == {
            "x": {"v": "no"}
        }
        loader = anycfg.Loader(
            base_dir=tmp_path, format_options={"yaml": {"dialect": "1.2"}}
        )
        assert loader.load("app.yaml") == {"x": {"v": "no"}}
