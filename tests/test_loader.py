"""`Loader`: settings, per-call overrides, combining, finishing, error handling, sources."""

from __future__ import annotations

import dataclasses
import decimal
import io
import json
import logging
import os
import pathlib
import sys

import pytest

import anycfg
from anycfg import (
    BudgetExceededError,
    CommandsDisabledError,
    ConfigTypeError,
    ConfigValueError,
    DotAccessibleDict,
    LoadEvent,
    Loader,
    ParseError,
    Policy,
    UnsupportedFormatError,
)
from anycfg.sources import Data, Env, Text

from _extras import needs_toml, needs_yaml

LF = chr(10)
LOGGER = "anycfg._loader"


def _write(path, text, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


def _json(path, data):
    return _write(path, json.dumps(data))


def _recorder(seen, answer=False):
    def predicate(event):
        seen.append(event)
        return answer

    return predicate


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self):
        loader = Loader()
        assert loader.policy == Policy()
        assert loader.base_dir is None
        assert "Loader(" in repr(loader)

    def test_unknown_merge_name_is_value_error(self):
        with pytest.raises(ValueError, match="nope"):
            Loader(merge="nope")

    def test_non_callable_merge_is_type_error(self):
        with pytest.raises(TypeError):
            Loader(merge=5)

    @pytest.mark.parametrize("collect", ["merge", "LIST", ""])
    def test_unknown_collect_is_value_error(self, collect):
        with pytest.raises(ValueError, match="collect"):
            Loader(collect=collect)

    @pytest.mark.parametrize("on_error", ["yes", 1, None])
    def test_on_error_must_be_bool_or_callable(self, on_error):
        with pytest.raises(TypeError, match="on_error"):
            Loader(on_error=on_error)

    @pytest.mark.parametrize("policy", [{}, "trusted", True])
    def test_policy_must_be_a_policy(self, policy):
        with pytest.raises(TypeError, match="Policy"):
            Loader(policy=policy)

    def test_format_options_for_an_unknown_format(self):
        with pytest.raises(UnsupportedFormatError, match="nope"):
            Loader(format_options={"nope": {}})

    def test_formats_naming_an_unknown_format(self):
        with pytest.raises(UnsupportedFormatError):
            Loader(formats=["json", "nope"])

    def test_base_dir_of_wrong_type(self):
        with pytest.raises(TypeError, match="base_dir"):
            Loader(base_dir=5)

    @pytest.mark.parametrize(
        "keyword", ["log_level", "allow_commands", "sandbox", "ignore_error", "loader"]
    )
    def test_unknown_constructor_keyword(self, keyword):
        with pytest.raises(TypeError):
            Loader(**{keyword: True})

    def test_construction_leaves_logging_alone(self):
        loggers = [logging.getLogger(name) for name in ("anycfg", LOGGER)]
        before = [logger.level for logger in loggers]
        Loader()
        Loader(base_dir="anywhere", on_error=True)
        assert [logger.level for logger in loggers] == before

    def test_construction_emits_no_warning(self):
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            Loader()
        assert caught == []


# ---------------------------------------------------------------------------
# Per-call overrides
# ---------------------------------------------------------------------------


class TestOverrides:
    def _layers(self, tmp_path):
        _json(tmp_path / "a.json", {"db": {"host": "h", "port": 1}})
        _json(tmp_path / "b.json", {"db": {"port": 2}})
        return Loader(base_dir=tmp_path)

    def test_override_applies_to_that_call_only(self, tmp_path):
        loader = self._layers(tmp_path)
        assert loader.load("a.json", "b.json", merge="deep") == {
            "db": {"host": "h", "port": 2}
        }
        assert loader.load("a.json", "b.json") == {"db": {"port": 2}}

    def test_merge_options_override_does_not_leak(self, tmp_path):
        _write(tmp_path / "a.json", '{"items": [{"name": "api", "on": 0}]}')
        _write(tmp_path / "b.json", '{"items": [{"name": "api", "on": 1}]}')
        loader = Loader(base_dir=tmp_path, merge="deep")
        merged = loader.load("a.json", "b.json", merge_options={"mergelists": True})
        assert merged == {"items": [{"name": "api", "on": 1}]}
        assert loader.load("a.json", "b.json") == {
            "items": [{"name": "api", "on": 0}, {"name": "api", "on": 1}]
        }

    @pytest.mark.parametrize(
        "name, value",
        [
            ("policy", Policy.trusted()),
            ("encoding", "utf-8"),
            ("merge", "deep"),
            ("merge_options", {}),
            ("collect", "list"),
            ("key", lambda name, doc: name),
            ("recursive", True),
            ("interpolate", False),
            ("inject_env", False),
            ("wrap", None),
            ("on_error", False),
            ("format_options", {"json": {}}),
        ],
    )
    def test_every_documented_override_is_accepted(self, tmp_path, name, value):
        _json(tmp_path / "a.json", {"a": 1})
        loader = Loader(base_dir=tmp_path)
        assert loader.load("a.json", **{name: value}) is not None
        assert list(loader.load_all("a.json", **{name: value}))

    @pytest.mark.parametrize(
        "name, value",
        [
            ("nope", 1),
            ("parse_float", decimal.Decimal),
            ("base_dir", "."),
            ("bound_loops", False),
            ("formats", ["json"]),
            ("allow_commands", True),
        ],
    )
    def test_unknown_keyword_is_type_error(self, tmp_path, name, value):
        _json(tmp_path / "a.json", {"a": 1})
        loader = Loader(base_dir=tmp_path)
        with pytest.raises(TypeError, match=name):
            loader.load("a.json", **{name: value})
        with pytest.raises(TypeError, match=name):
            list(loader.load_all("a.json", **{name: value}))

    def test_format_option_keyword_points_at_format_options(self, tmp_path):
        _json(tmp_path / "a.json", {"a": 1})
        with pytest.raises(TypeError, match="format_options"):
            Loader(base_dir=tmp_path).load("a.json", parse_float=decimal.Decimal)

    def test_overrides_are_validated(self, tmp_path):
        _json(tmp_path / "a.json", {"a": 1})
        loader = Loader(base_dir=tmp_path)
        with pytest.raises(ValueError):
            loader.load("a.json", collect="nope")
        with pytest.raises(ValueError):
            loader.load("a.json", merge="nope")
        with pytest.raises(TypeError):
            loader.load("a.json", policy={})

    def test_override_policy_applies(self):
        loader = Loader(policy=Policy.trusted())
        with pytest.raises(CommandsDisabledError):
            loader.load('cmd+json://python -c "print(1)"', policy=Policy())

    def test_load_all_override(self, tmp_path):
        _json(tmp_path / "a.json", {"a": {"b": 1}})
        loader = Loader(base_dir=tmp_path)
        (document,) = loader.load_all("a.json", wrap=True)
        assert isinstance(document, DotAccessibleDict)
        (document,) = loader.load_all("a.json")
        assert type(document) is dict


# ---------------------------------------------------------------------------
# Combining: merge strategies and collect modes
# ---------------------------------------------------------------------------


class TestMergeStrategies:
    def _layers(self, tmp_path):
        _json(
            tmp_path / "a.json", {"x": 1, "db": {"host": "h", "port": 1}, "l": [1, 2]}
        )
        _json(tmp_path / "b.json", {"y": 2, "db": {"port": 2}, "l": [2, 3]})
        return Loader(base_dir=tmp_path)

    def test_simple_is_the_default(self, tmp_path):
        loader = self._layers(tmp_path)
        assert loader.load("a.json", "b.json") == {
            "x": 1,
            "y": 2,
            "db": {"port": 2},
            "l": [2, 3],
        }
        assert loader.load("a.json", "b.json", merge="simple") == loader.load(
            "a.json", "b.json"
        )

    def test_substitute(self, tmp_path):
        loader = self._layers(tmp_path)
        assert loader.load("a.json", "b.json", merge="substitute") == {
            "x": 1,
            "y": 2,
            "db": {"host": "h", "port": 2},
            "l": [2, 3],
        }

    def test_deep(self, tmp_path):
        loader = self._layers(tmp_path)
        assert loader.load("a.json", "b.json", merge="deep") == {
            "x": 1,
            "y": 2,
            "db": {"host": "h", "port": 2},
            "l": [1, 2, 3],
        }

    def test_merge_names_are_resolved_at_construction(self, tmp_path):
        self._layers(tmp_path)
        assert Loader(base_dir=tmp_path, merge="deep").load("a.json", "b.json")[
            "l"
        ] == [
            1,
            2,
            3,
        ]

    def test_top_level_list_documents_replace_under_simple(self, tmp_path):
        _write(tmp_path / "a.json", '["a", "b", "c"]')
        _write(tmp_path / "b.json", '["x"]')
        assert Loader(base_dir=tmp_path).load("a.json", "b.json") == ["x"]

    def test_callable_strategy(self, tmp_path):
        self._layers(tmp_path)
        calls = []

        def keep_first(a, b, *, memo=None, **options):
            calls.append((a, b, options))
            return a

        _json(tmp_path / "c.json", {"z": 3})
        result = Loader(base_dir=tmp_path, merge=keep_first).load(
            "a.json", "b.json", "c.json"
        )
        assert result["x"] == 1 and "y" not in result
        # The first document seeds the result; the strategy runs once per later one.
        assert len(calls) == 2
        assert calls[1][1] == {"z": 3}

    def test_merge_options_reach_the_strategy(self, tmp_path):
        self._layers(tmp_path)
        seen = []

        def record(a, b, *, memo=None, **options):
            seen.append(options)
            return b

        Loader(base_dir=tmp_path, merge=record, merge_options={"flag": 1}).load(
            "a.json", "b.json"
        )
        assert seen == [{"flag": 1}]

    def test_deep_mergelists(self, tmp_path):
        _write(tmp_path / "a.json", '{"items": [{"name": "api", "enabled": false}]}')
        _write(tmp_path / "b.json", '{"items": [{"name": "api", "enabled": true}]}')
        loader = Loader(
            base_dir=tmp_path, merge="deep", merge_options={"mergelists": True}
        )
        assert loader.load("a.json", "b.json") == {
            "items": [{"name": "api", "enabled": True}]
        }

    def test_inputs_are_not_modified(self):
        base = {"db": {"host": "h"}}
        override = {"db": {"port": 1}}
        result = Loader(merge="deep").load(base, override)
        assert result == {"db": {"host": "h", "port": 1}}
        assert base == {"db": {"host": "h"}} and override == {"db": {"port": 1}}

    def test_single_source_is_not_merged(self):
        def boom(a, b, **options):
            raise AssertionError("merge called")

        assert Loader(merge=boom).load({"a": 1}) == {"a": 1}


class TestCollect:
    def _files(self, tmp_path):
        _json(tmp_path / "a.json", {"v": 1})
        _json(tmp_path / "b.json", {"v": 2})
        return Loader(base_dir=tmp_path)

    def test_last(self, tmp_path):
        loader = self._files(tmp_path)
        assert loader.load("a.json", "b.json", collect="last") == {"v": 2}

    def test_list_keeps_order(self, tmp_path):
        loader = self._files(tmp_path)
        assert loader.load("b.json", "a.json", collect="list") == [{"v": 2}, {"v": 1}]

    def test_list_of_one(self, tmp_path):
        loader = self._files(tmp_path)
        assert loader.load("a.json", collect="list") == [{"v": 1}]

    def test_hash_keys_on_the_file_stem(self, tmp_path):
        loader = self._files(tmp_path)
        assert loader.load("a.json", "b.json", collect="hash") == {
            "a": {"v": 1},
            "b": {"v": 2},
        }

    def test_hash_with_key_callable(self, tmp_path):
        loader = self._files(tmp_path)
        seen = []

        def key(name, document):
            seen.append((pathlib.PurePath(name).name, document))
            return pathlib.PurePath(name).name

        result = loader.load("a.json", "b.json", collect="hash", key=key)
        assert set(result) == {"a.json", "b.json"}
        assert seen == [("a.json", {"v": 1}), ("b.json", {"v": 2})]

    def test_hash_repeated_key_warns_and_keeps_the_later(self, tmp_path, caplog):
        for name in ("api", "billing", "web"):
            _json(tmp_path / "services" / name / "config.json", {"service": name})
        loader = Loader(base_dir=tmp_path, collect="hash")
        with caplog.at_level(logging.WARNING, logger="anycfg"):
            result = loader.load("services/*/config.json")
        assert result == {"config": {"service": "web"}}
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2
        assert all("config" in r.getMessage() for r in warnings)

    def test_hash_parent_directory_key_keeps_every_source(self, tmp_path, caplog):
        for name in ("api", "billing", "web"):
            _json(tmp_path / "services" / name / "config.json", {"service": name})
        loader = Loader(
            base_dir=tmp_path,
            collect="hash",
            key=lambda name, document: pathlib.PurePath(name).parent.name,
        )
        with caplog.at_level(logging.WARNING, logger="anycfg"):
            result = loader.load("services/*/config.json")
        assert result == {
            "api": {"service": "api"},
            "billing": {"service": "billing"},
            "web": {"service": "web"},
        }
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_hash_unnamed_source_raises(self):
        with pytest.raises(ConfigValueError, match="hash"):
            Loader(collect="hash").load(Text('{"a": 1}', format="json"))

    def test_hash_named_text(self):
        result = Loader(collect="hash").load(
            Text('{"a": 1}', format="json", name="defaults.json")
        )
        assert result == {"defaults": {"a": 1}}

    def test_hash_named_data(self):
        result = Loader(collect="hash").load(Data({"a": 1}, name="builtin"))
        assert result == {"builtin": {"a": 1}}

    def test_collect_ignores_the_merge_strategy(self, tmp_path):
        loader = self._files(tmp_path)

        def boom(a, b, **options):
            raise AssertionError("merge called")

        assert loader.load("a.json", "b.json", collect="list", merge=boom) == [
            {"v": 1},
            {"v": 2},
        ]


# ---------------------------------------------------------------------------
# load() arguments: default, flatten, transform; finishing: wrap, into
# ---------------------------------------------------------------------------


class TestDefault:
    def test_returned_when_nothing_matches(self, tmp_path):
        loader = Loader(base_dir=tmp_path)
        assert loader.load("no-such-*.json", default={"fallback": True}) == {
            "fallback": True
        }

    def test_returned_when_every_source_fails(self, tmp_path):
        loader = Loader(base_dir=tmp_path, on_error=True)
        assert loader.load("missing.json", default={"fallback": True}) == {
            "fallback": True
        }

    def test_never_merged_with_a_document(self, tmp_path):
        _json(tmp_path / "one.json", {"a": 1})
        loader = Loader(base_dir=tmp_path, merge="deep")
        assert loader.load("one.json", default={"fallback": True}) == {"a": 1}

    def test_none_by_default(self, tmp_path):
        assert Loader(base_dir=tmp_path).load("no-such-*.json") is None

    def test_none_and_empty_layers_are_skipped(self, tmp_path):
        _json(tmp_path / "base.json", {"a": 1})
        loader = Loader(base_dir=tmp_path)
        assert loader.load("base.json", None, "") == {"a": 1}
        assert loader.load(None, default={"d": 1}) == {"d": 1}


class TestFlatten:
    def test_mapping_of_mappings(self, tmp_path):
        _json(tmp_path / "one.json", {"a": 1})
        _json(tmp_path / "two.json", {"b": 2})
        result = Loader(base_dir=tmp_path, collect="hash").load(
            "one.json", "two.json", flatten=True
        )
        assert result == {"a": 1, "b": 2}

    def test_list_of_lists(self, tmp_path):
        _write(tmp_path / "one.json", "[1, 2]")
        _write(tmp_path / "two.json", "[3]")
        result = Loader(base_dir=tmp_path, collect="list").load(
            "one.json", "two.json", flatten=True
        )
        assert result == [1, 2, 3]

    def test_none_members_are_skipped(self, tmp_path):
        _json(tmp_path / "one.json", {"a": 1})
        _write(tmp_path / "empty.json", "null")
        loader = Loader(base_dir=tmp_path)
        assert loader.load("one.json", "empty.json", collect="hash", flatten=True) == {
            "a": 1
        }
        _write(tmp_path / "list.json", "[1]")
        assert loader.load("list.json", "empty.json", collect="list", flatten=True) == [
            1
        ]

    def test_non_mapping_member_names_it(self, tmp_path):
        _json(tmp_path / "one.json", {"a": 1})
        _write(tmp_path / "scalar.json", "42")
        with pytest.raises(ConfigTypeError, match="scalar"):
            Loader(base_dir=tmp_path, collect="hash").load(
                "one.json", "scalar.json", flatten=True
            )

    def test_string_member_of_a_list_is_not_a_sequence(self, tmp_path):
        _write(tmp_path / "one.json", '"abc"')
        with pytest.raises(ConfigTypeError):
            Loader(base_dir=tmp_path, collect="list").load("one.json", flatten=True)

    def test_scalar_result_raises(self):
        with pytest.raises(ConfigTypeError, match="flatten"):
            Loader().load(Data("scalar"), flatten=True)

    def test_type_error_compatible(self):
        with pytest.raises(TypeError):
            Loader().load(Data(5), flatten=True)


class TestTransform:
    def test_runs_on_each_document_before_combining(self, tmp_path):
        _json(tmp_path / "a.json", {"v": 1})
        _json(tmp_path / "b.json", {"v": 2})
        seen = []

        def transform(document, name):
            seen.append((pathlib.PurePath(name).name, document))
            return {pathlib.PurePath(name).stem: document["v"]}

        result = Loader(base_dir=tmp_path).load("a.json", "b.json", transform=transform)
        assert seen == [("a.json", {"v": 1}), ("b.json", {"v": 2})]
        assert result == {"a": 1, "b": 2}

    def test_result_is_combined(self, tmp_path):
        _json(tmp_path / "a.json", {"a": 5})
        result = Loader(base_dir=tmp_path).load(
            "a.json", transform=lambda document, name: document["a"]
        )
        assert result == 5

    def test_load_all(self, tmp_path):
        _json(tmp_path / "a.json", {"a": 5})
        documents = Loader(base_dir=tmp_path).load_all(
            "a.json", transform=lambda document, name: [document]
        )
        assert list(documents) == [[{"a": 5}]]


class TestWrap:
    def test_plain_data_by_default(self):
        result = Loader().load({"db": {"host": "h"}})
        assert type(result) is dict and type(result["db"]) is dict

    @pytest.mark.parametrize("wrap", [True, DotAccessibleDict])
    def test_dot_access(self, wrap):
        result = Loader(wrap=wrap).load({"db": {"host": "h", "port": 3306}})
        assert isinstance(result, DotAccessibleDict)
        assert isinstance(result["db"], DotAccessibleDict)
        assert result.db.host == "h"
        assert result.get("db.port") == 3306
        assert result.get("db.missing", "default") == "default"

    def test_false_is_plain(self):
        assert type(Loader(wrap=False).load({"a": {}})) is dict

    def test_callable(self):
        assert Loader(wrap=lambda result: sorted(result)).load({"b": 1, "a": 2}) == [
            "a",
            "b",
        ]

    def test_ignored_with_into(self):
        @dataclasses.dataclass
        class Config:
            a: int

        result = Loader(wrap=True).load({"a": 1}, into=Config)
        assert result == Config(1)

    def test_load_all_wraps_each(self):
        documents = list(Loader(wrap=True).load_all({"a": {"b": 1}}, {"c": {}}))
        assert all(isinstance(document, DotAccessibleDict) for document in documents)
        assert documents[0].a.b == 1


class TestInto:
    def test_hydrates_the_merged_result(self):
        @dataclasses.dataclass
        class Config:
            host: str
            port: int = 80

        result = Loader(merge="deep").load(
            {"host": "h", "x": 1}, {"port": 81}, into=Config
        )
        assert result == Config("h", 81)

    def test_load_all_hydrates_each(self):
        @dataclasses.dataclass
        class Config:
            a: int = 0

        documents = Loader().load_all({"a": 1}, {"a": 2}, into=Config)
        assert list(documents) == [Config(1), Config(2)]

    def test_nothing_loaded_with_into(self, tmp_path):
        @dataclasses.dataclass
        class Config:
            a: int = 0

        with pytest.raises(ConfigTypeError):
            Loader(base_dir=tmp_path).load("no-such-*.json", into=Config)


# ---------------------------------------------------------------------------
# on_error
# ---------------------------------------------------------------------------


class TestOnError:
    def test_false_raises(self, tmp_path):
        _write(tmp_path / "bad.json", '{"a": }')
        with pytest.raises(ParseError):
            Loader(base_dir=tmp_path).load("bad.json")

    def test_true_skips_and_continues(self, tmp_path):
        _write(tmp_path / "bad.json", '{"a": }')
        _json(tmp_path / "good.json", {"x": 1})
        loader = Loader(base_dir=tmp_path, on_error=True)
        assert loader.load("missing.json", "bad.json", "good.json") == {"x": 1}

    def test_true_warns_naming_source_phase_and_type(self, tmp_path, caplog):
        _write(tmp_path / "bad.json", '{"a": }')
        caplog.set_level(logging.WARNING, logger="anycfg")
        Loader(base_dir=tmp_path, on_error=True).load("bad.json")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "bad.json" in message
        assert "load" in message
        assert "ParseError" in message

    def test_true_warning_omits_the_error_text(self, tmp_path, caplog):
        # The parser's message can quote configuration; the warning names only
        # the error's type.
        _write(tmp_path / "bad.json", '{"token": "SECRETVALUE", "a": }')
        _write(tmp_path / "bad.ini", "[s]\nSECRETVALUE\n")
        caplog.set_level(logging.DEBUG, logger="anycfg")
        Loader(base_dir=tmp_path, on_error=True).load("bad.json", "bad.ini")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2
        for record in warnings:
            assert "SECRETVALUE" not in record.getMessage()

    def test_predicate_receives_a_load_event(self, tmp_path):
        _write(tmp_path / "bad.json", '{"a": }')
        seen = []
        loader = Loader(base_dir=tmp_path, on_error=_recorder(seen))
        with pytest.raises(ParseError) as caught:
            loader.load("bad.json")
        (event,) = seen
        assert isinstance(event, LoadEvent)
        assert event.error is caught.value
        assert event.phase == "load"
        assert str(event.path).endswith("bad.json")
        assert event.key is None

    def test_predicate_decides_per_failure(self, tmp_path):
        _json(tmp_path / "good.json", {"x": 1})
        _write(tmp_path / "bad.json", '{"a": }')
        loader = Loader(
            base_dir=tmp_path,
            on_error=lambda event: isinstance(event.error, FileNotFoundError),
        )
        assert loader.load("nope.json", "good.json") == {"x": 1}
        with pytest.raises(ParseError):
            loader.load("bad.json")

    def test_predicate_skip_logs_debug_only(self, tmp_path, caplog):
        _write(tmp_path / "bad.json", '{"a": }')
        caplog.set_level(logging.DEBUG, logger="anycfg")
        Loader(base_dir=tmp_path, on_error=lambda event: True).load("bad.json")
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert [r for r in caplog.records if "bad.json" in r.getMessage()]

    def test_declined_error_logs_no_warning(self, tmp_path, caplog):
        _write(tmp_path / "bad.json", '{"a": }')
        caplog.set_level(logging.DEBUG, logger="anycfg")
        with pytest.raises(ParseError):
            Loader(base_dir=tmp_path, on_error=lambda event: False).load("bad.json")
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_merge_phase(self, tmp_path):
        _json(tmp_path / "a.json", {"a": 1})
        _json(tmp_path / "b.json", {"b": 2})

        def broken(a, b, **options):
            raise ConfigTypeError("no merging today")

        seen = []
        loader = Loader(base_dir=tmp_path, on_error=_recorder(seen, answer=True))
        result = loader.load("a.json", "b.json", merge=broken)
        assert [(e.phase, pathlib.PurePath(e.path).name) for e in seen] == [
            ("merge", "b.json")
        ]
        assert result == {"a": 1}

    def test_merge_failure_raised_with_context(self, tmp_path):
        _json(tmp_path / "a.json", {"a": 1})
        _json(tmp_path / "b.json", {"b": 2})

        def broken(a, b, **options):
            raise ConfigTypeError("no merging today")

        with pytest.raises(ConfigTypeError) as caught:
            Loader(base_dir=tmp_path).load("a.json", "b.json", merge=broken)
        assert [frame.kind for frame in caught.value.config_frames] == ["merge"]
        assert "b.json" in str(caught.value)

    @needs_yaml
    def test_include_failure_offered_once_per_level(self, tmp_path):
        _write(tmp_path / "top.yaml", "mid: !include mid.yaml\n")
        _write(tmp_path / "mid.yaml", "secrets: !include secrets.json\n")
        _write(tmp_path / "secrets.json", '{"a": }')
        seen = []
        loader = Loader(base_dir=tmp_path, on_error=_recorder(seen))
        with pytest.raises(ParseError) as caught:
            loader.load("top.yaml")
        assert [(e.phase, pathlib.PurePath(e.path).name) for e in seen] == [
            ("load", "secrets.json"),
            ("include", "mid.yaml"),
            ("include", "top.yaml"),
        ]
        # The same exception object at every level; only the phase differs.
        assert all(event.error is caught.value for event in seen)

    @needs_yaml
    def test_skipping_the_included_file_keeps_the_includer(self, tmp_path):
        _write(tmp_path / "parent.yaml", "a: 1\nsecrets: !include secrets.json\n")
        _write(tmp_path / "secrets.json", '{"a": }')
        loader = Loader(base_dir=tmp_path, on_error=lambda event: event.phase == "load")
        assert loader.load("parent.yaml") == {"a": 1, "secrets": None}

    @needs_yaml
    def test_skipping_the_including_file(self, tmp_path):
        _write(tmp_path / "parent.yaml", "secrets: !include secrets.json\n")
        _write(tmp_path / "secrets.json", '{"a": }')
        _json(tmp_path / "other.json", {"other": 1})
        loader = Loader(
            base_dir=tmp_path, on_error=lambda event: event.phase == "include"
        )
        assert loader.load("parent.yaml") is None
        assert loader.load("parent.yaml", "other.json") == {"other": 1}

    def test_budget_error_is_never_offered(self, tmp_path):
        _json(tmp_path / "a.json", {"a": "x" * 100})
        seen = []
        loader = Loader(
            base_dir=tmp_path,
            policy=Policy(max_bytes=10),
            on_error=_recorder(seen, answer=True),
        )
        with pytest.raises(BudgetExceededError):
            loader.load("a.json")
        with pytest.raises(BudgetExceededError):
            Loader(base_dir=tmp_path, policy=Policy(max_bytes=10), on_error=True).load(
                "a.json"
            )
        assert seen == []

    def test_load_all_skips_a_failing_source(self, tmp_path):
        _write(tmp_path / "bad.json", '{"a": }')
        _json(tmp_path / "good.json", {"x": 1})
        seen = []
        loader = Loader(base_dir=tmp_path, on_error=_recorder(seen, answer=True))
        assert list(loader.load_all("bad.json", "good.json")) == [{"x": 1}]
        assert [event.phase for event in seen] == ["load"]

    def test_unknown_format_is_offered(self, tmp_path):
        _write(tmp_path / "notes.txt", "x")
        _json(tmp_path / "good.json", {"x": 1})
        seen = []
        loader = Loader(base_dir=tmp_path, on_error=_recorder(seen, answer=True))
        assert loader.load("notes.txt", "good.json") == {"x": 1}
        assert isinstance(seen[0].error, UnsupportedFormatError)


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------


class TestFormatSelection:
    def test_suffix_selects_each_format(self, tmp_path):
        _write(tmp_path / "a.json", '{"j": 1}')
        _write(tmp_path / "b.ini", "[i]\nk = v\n")
        _write(tmp_path / "c.env", "E=1\n")
        result = Loader(base_dir=tmp_path).load("a.json", "b.ini", "c.env")
        assert result == {"j": 1, "i": {"k": "v"}, "E": "1"}

    @needs_toml
    def test_toml(self, tmp_path):
        _write(tmp_path / "a.toml", '[section]\nkey = "hello"\n')
        assert Loader(base_dir=tmp_path).load("a.toml") == {"section": {"key": "hello"}}

    @needs_yaml
    def test_yaml(self, tmp_path):
        _write(tmp_path / "a.yaml", "key: value\nnumber: 42\n")
        _write(tmp_path / "b.yml", "other: true\n")
        assert Loader(base_dir=tmp_path).load("a.yaml", "b.yml") == {
            "key": "value",
            "number": 42,
            "other": True,
        }

    def test_format_applies_to_every_source(self, tmp_path):
        _write(tmp_path / "a.txt", '{"a": 1}')
        _write(tmp_path / "b.conf", '{"b": 2}')
        _write(tmp_path / "c.ini", '{"c": 3}')
        loader = Loader(base_dir=tmp_path)
        assert loader.load("a.txt", "b.conf", "c.ini", format="json") == {
            "a": 1,
            "b": 2,
            "c": 3,
        }
        json_format = anycfg.formats.get("json")
        assert loader.load("a.txt", format=json_format) == {"a": 1}

    def test_format_reaches_streams(self):
        assert Loader().load(io.StringIO("A=1\n"), format="dotenv") == {"A": "1"}

    def test_unclaimed_name_raises_before_opening(self, tmp_path):
        with pytest.raises(UnsupportedFormatError):
            Loader(base_dir=tmp_path).load("missing.unknown")

    def test_format_options(self, tmp_path):
        _write(tmp_path / "a.json", '{"rate": 1.5}')
        loader = Loader(
            base_dir=tmp_path, format_options={"json": {"parse_float": decimal.Decimal}}
        )
        assert loader.load("a.json") == {"rate": decimal.Decimal("1.5")}
        assert list(loader.load_all("a.json")) == [{"rate": decimal.Decimal("1.5")}]

    def test_format_options_only_reach_their_format(self, tmp_path):
        _write(tmp_path / "a.ini", "[s]\nk = %(missing)s\n")
        loader = Loader(
            base_dir=tmp_path, format_options={"json": {"parse_float": decimal.Decimal}}
        )
        # Basic interpolation of an undefined reference fails in the INI parser.
        with pytest.raises(anycfg.ConfigError):
            loader.load("a.ini")
        loader = Loader(
            base_dir=tmp_path, format_options={"ini": {"interpolation": None}}
        )
        assert loader.load("a.ini") == {"s": {"k": "%(missing)s"}}

    def test_format_options_by_alias(self, tmp_path):
        _write(tmp_path / "a.cfg", "[s]\nk = %(missing)s\n")
        loader = Loader(
            base_dir=tmp_path, format_options={"cfg": {"interpolation": None}}
        )
        assert loader.load("a.cfg") == {"s": {"k": "%(missing)s"}}

    def test_format_options_per_call(self, tmp_path):
        _write(tmp_path / "a.json", '{"n": 1}')
        loader = Loader(base_dir=tmp_path)
        assert loader.load("a.json", format_options={"json": {"parse_int": str}}) == {
            "n": "1"
        }
        assert loader.load("a.json") == {"n": 1}

    def test_unknown_format_option(self, tmp_path):
        _write(tmp_path / "a.json", "{}")
        with pytest.raises(TypeError, match="nope"):
            Loader(base_dir=tmp_path, format_options={"json": {"nope": 1}}).load(
                "a.json"
            )

    def test_formats_does_not_limit_the_caller(self, tmp_path):
        # formats= governs what a document may select; the caller's own sources
        # are read by any registered format.
        _write(tmp_path / "a.json", '{"a": 1}')
        assert Loader(base_dir=tmp_path, formats=["yaml"]).load("a.json") == {"a": 1}


# ---------------------------------------------------------------------------
# Sources of every kind, mixed
# ---------------------------------------------------------------------------


class TestMixedSources:
    def test_every_kind_merges_in_order(self, tmp_path):
        from pathapi.mempath import MemPath

        _json(tmp_path / "base.json", {"order": ["file"], "file": True})
        memory = MemPath("mem.json")
        memory.write_bytes(b'{"mem": true, "order": ["mem"]}')
        result = Loader(base_dir=tmp_path, merge="deep").load(
            "base.json",
            {"mapping": True, "order": ["mapping"]},
            Text('{"text": true, "order": ["text"]}', format="json", name="t"),
            Env("APP_", environ={"APP_ENV": "1", "OTHER": "x"}),
            memory,
        )
        assert result == {
            "order": ["file", "mapping", "text", "mem"],
            "file": True,
            "mapping": True,
            "text": True,
            "ENV": "1",
            "mem": True,
        }

    def test_named_stream_among_paths(self, tmp_path):
        _json(tmp_path / "a.json", {"a": 1})
        stream_file = _json(tmp_path / "s.json", {"s": 1})
        with open(stream_file, encoding="utf-8") as handle:
            assert Loader(base_dir=tmp_path).load("a.json", handle) == {"a": 1, "s": 1}

    def test_mapping_is_deep_copied(self):
        layer = {"db": {"hosts": ["a"]}}
        result = Loader(merge="deep").load(layer, {"db": {"hosts": ["b"]}})
        result["db"]["hosts"].append("c")
        assert layer == {"db": {"hosts": ["a"]}}

    def test_single_mapping_is_copied(self):
        layer = {"db": {"host": "a"}}
        result = Loader().load(layer)
        assert result == layer and result is not layer
        assert result["db"] is not layer["db"]

    def test_env_nested(self):
        environ = {"APP_DB__HOST": "h", "APP_DB__PORT": "5432", "APP_DEBUG": "1"}
        result = Loader().load(Env("APP_", nested="__", environ=environ))
        assert result == {"DB": {"HOST": "h", "PORT": "5432"}, "DEBUG": "1"}

    def test_text_bytes_use_the_loader_encoding(self):
        data = '{"a": "café"}'.encode("cp1252")
        assert Loader(encoding="cp1252").load(Text(data, format="json")) == {
            "a": "café"
        }
        assert Loader().load(Text(data, format="json"), encoding="cp1252") == {
            "a": "café"
        }

    def test_text_str_is_not_re_encoded(self):
        assert Loader(encoding="ascii").load(Text('{"a": "日本"}', format="json")) == {
            "a": "日本"
        }

    def test_command_scheme_is_a_source_not_a_glob(self, tmp_path):
        command = (
            f'cmd+json://"{sys.executable}" -c "print({{\'items\': [1, 2]}}.__len__())"'
        )
        result = Loader(base_dir=tmp_path, policy=Policy.trusted()).load(command)
        assert result == 1

    def test_command_refused_by_default(self):
        with pytest.raises(CommandsDisabledError):
            Loader().load('cmd+json://python -c "print(1)"')

    def test_script_file_is_a_command(self, tmp_path):
        script = self._script(tmp_path, "emit.json")
        assert Loader(policy=Policy.trusted()).load(str(script)) == {"a": 1}

    def test_script_file_refused_without_allow_commands(self, tmp_path):
        script = _write(tmp_path / "emit.json.sh", "echo '{}'\n")
        with pytest.raises(CommandsDisabledError):
            Loader().load(str(script))

    @staticmethod
    def _script(tmp_path, stem):
        if sys.platform == "win32":
            return _write(tmp_path / (stem + ".cmd"), '@echo {"a": 1}\r\n')
        script = _write(tmp_path / (stem + ".sh"), "#!/bin/sh\necho '{\"a\": 1}'\n")
        script.chmod(0o755)
        return script

    def test_script_file_runs_when_format_is_given(self, tmp_path):
        script = self._script(tmp_path, "emit.json")
        assert Loader(policy=Policy.trusted()).load(str(script), format="json") == {
            "a": 1
        }
        with pytest.raises(CommandsDisabledError):
            Loader().load(str(script), format="json")

    def test_loader_format_reads_a_script_files_output(self, tmp_path):
        script = self._script(tmp_path, "emit")
        assert Loader(policy=Policy.trusted()).load(str(script), format="json") == {
            "a": 1
        }


class TestNoSources:
    def test_load(self):
        with pytest.raises(TypeError, match="source"):
            Loader().load()

    def test_load_all(self):
        with pytest.raises(TypeError, match="source"):
            list(Loader().load_all())

    def test_only_skipped_sources_is_not_an_error(self):
        assert Loader().load(None, "") is None
        assert list(Loader().load_all(None, "")) == []


class TestLoadAll:
    def test_yields_each_source(self, tmp_path):
        _json(tmp_path / "a.json", {"a": 1})
        _json(tmp_path / "b.json", {"b": 2})
        loader = Loader(base_dir=tmp_path)
        assert list(loader.load_all("a.json", "b.json")) == [{"a": 1}, {"b": 2}]

    def test_glob_yields_each_match(self, tmp_path):
        _json(tmp_path / "a.json", {"a": 1})
        _json(tmp_path / "b.json", {"b": 2})
        assert list(Loader(base_dir=tmp_path).load_all("*.json")) == [
            {"a": 1},
            {"b": 2},
        ]

    @needs_yaml
    def test_stream_yields_each_document(self, tmp_path):
        _write(tmp_path / "m.yaml", "a: 1\n---\nb: 2\n")
        _json(tmp_path / "c.json", {"c": 3})
        documents = Loader(base_dir=tmp_path).load_all("m.yaml", "c.json")
        assert list(documents) == [{"a": 1}, {"b": 2}, {"c": 3}]

    @needs_yaml
    def test_load_reads_every_document_of_a_stream(self, tmp_path):
        _write(tmp_path / "m.yaml", "a: 1\n---\nb: 2\n")
        loader = Loader(base_dir=tmp_path)
        assert loader.load("m.yaml", collect="list") == [{"a": 1}, {"b": 2}]
        assert loader.load("m.yaml") == {"a": 1, "b": 2}

    def test_documents_are_not_merged(self):
        documents = Loader(merge="deep").load_all({"a": {"x": 1}}, {"a": {"y": 2}})
        assert list(documents) == [{"a": {"x": 1}}, {"a": {"y": 2}}]

    def test_is_lazy(self, tmp_path):
        _json(tmp_path / "a.json", {"a": 1})
        _write(tmp_path / "bad.json", '{"a": }')
        documents = Loader(base_dir=tmp_path).load_all("a.json", "bad.json")
        assert next(documents) == {"a": 1}
        with pytest.raises(ParseError):
            next(documents)


# ---------------------------------------------------------------------------
# base_dir
# ---------------------------------------------------------------------------


class TestBaseDir:
    @pytest.mark.parametrize("kind", ["str", "pathlib", "pathapi"])
    def test_forms(self, tmp_path, kind):
        from pathapi import LocalPath

        _json(tmp_path / "conf" / "app.json", {"a": 1})
        base = {
            "str": str(tmp_path),
            "pathlib": tmp_path,
            "pathapi": LocalPath(str(tmp_path)),
        }[kind]
        loader = Loader(base_dir=base)
        assert isinstance(loader.base_dir, LocalPath)
        assert loader.load("conf/app.json") == {"a": 1}
        assert loader.load(pathlib.Path("conf") / "app.json") == {"a": 1}

    @pytest.mark.parametrize("empty", [None, ""])
    def test_unset_means_the_working_directory(self, tmp_path, monkeypatch, empty):
        _json(tmp_path / "app.json", {"a": 1})
        monkeypatch.chdir(tmp_path)
        loader = Loader(base_dir=empty)
        assert loader.base_dir is None
        assert loader.load("app.json") == {"a": 1}

    def test_relative_base_dir(self, tmp_path, monkeypatch):
        _json(tmp_path / "conf" / "app.json", {"a": 1})
        monkeypatch.chdir(tmp_path)
        assert Loader(base_dir="conf").load("app.json") == {"a": 1}

    def test_absolute_source_is_not_joined(self, tmp_path):
        source = _json(tmp_path / "app.json", {"a": 1})
        other = tmp_path / "other"
        other.mkdir()
        assert Loader(base_dir=other).load(str(source)) == {"a": 1}
        assert Loader(base_dir=other).load(source) == {"a": 1}

    def test_pathapi_source_is_never_joined(self, tmp_path, monkeypatch):
        from pathapi import LocalPath

        _json(tmp_path / "cwd" / "app.json", {"where": "cwd"})
        _json(tmp_path / "base" / "app.json", {"where": "base"})
        monkeypatch.chdir(tmp_path / "cwd")
        loader = Loader(base_dir=tmp_path / "base")
        assert loader.load("app.json") == {"where": "base"}
        assert loader.load(LocalPath("app.json")) == {"where": "cwd"}

    def test_mempath_base_dir(self):
        from pathapi.mempath import MemPath

        root = MemPath("memroot")
        root.mkdir(parents=True, exist_ok=True)
        (root / "a.json").write_bytes(b'{"a": 1}')
        (root / "b.json").write_bytes(b'{"b": 2}')
        loader = Loader(base_dir=root)
        assert loader.load("a.json") == {"a": 1}
        assert loader.load("*.json") == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------


_STREAM_FILES = {
    "toml": ("s.toml", 'title = "x"\n[db]\nport = 5432\n'),
    "dotenv": ("e.env", "A=1\nB=2\n"),
    "ini": ("i.ini", "[s]\nk = v\n"),
    "json_exponent": ("num.json", '{"n": 1e5, "m": 2E-3}\n'),
    "yaml": ("y.yaml", "a: [1, 2]\n"),
}


class TestStreams:
    @pytest.mark.parametrize(
        "key, mode",
        [
            pytest.param("toml", "r", id="toml_text", marks=needs_toml),
            pytest.param("toml", "rb", id="toml_binary", marks=needs_toml),
            pytest.param("dotenv", "r", id="dotenv"),
            pytest.param("ini", "r", id="ini"),
            pytest.param("json_exponent", "r", id="json"),
            pytest.param("json_exponent", "rb", id="json_binary"),
            pytest.param("yaml", "r", id="yaml", marks=needs_yaml),
        ],
    )
    def test_name_selects_the_format(self, tmp_path, key, mode):
        name, content = _STREAM_FILES[key]
        path = _write(tmp_path / name, content)
        kwargs = {} if "b" in mode else {"encoding": "utf-8"}
        with open(path, mode, **kwargs) as handle:
            from_stream = Loader().load(handle)
        assert from_stream == Loader().load(str(path))

    def test_unnamed_stream_needs_format(self):
        with pytest.raises(UnsupportedFormatError):
            Loader().load(io.StringIO('{"a": 1}'))
        assert Loader().load(io.StringIO('{"a": 1}'), format="json") == {"a": 1}
        assert Loader().load(io.BytesIO(b'{"a": 1}'), format="json") == {"a": 1}

    @pytest.mark.parametrize("name", ["<stdin>", "<fdopen>"])
    def test_pseudo_name_is_not_a_file_name(self, name):
        class Named(io.StringIO):
            pass

        stream = Named('{"a": 1}')
        stream.name = name
        with pytest.raises(UnsupportedFormatError):
            Loader().load(stream)
        stream = Named('{"a": 1}')
        stream.name = name
        assert Loader().load(stream, format="json") == {"a": 1}

    def test_descriptor_name_is_not_a_file_name(self, tmp_path):
        path = _json(tmp_path / "c.json", {"a": 1})
        with open(os.open(str(path), os.O_RDONLY), encoding="utf-8") as handle:
            with pytest.raises(UnsupportedFormatError):
                Loader().load(handle)
        with open(os.open(str(path), os.O_RDONLY), encoding="utf-8") as handle:
            assert Loader().load(handle, format="json") == {"a": 1}

    @needs_yaml
    def test_real_file_name_is_the_origin_of_includes(self, tmp_path, monkeypatch):
        _write(tmp_path / "conf" / "app.yaml", "db: !include db.json\n")
        _json(tmp_path / "conf" / "db.json", {"host": "from-conf"})
        _json(tmp_path / "elsewhere" / "db.json", {"host": "from-cwd"})
        monkeypatch.chdir(tmp_path / "elsewhere")
        with open(tmp_path / "conf" / "app.yaml", encoding="utf-8") as handle:
            assert Loader().load(handle) == {"db": {"host": "from-conf"}}

    @needs_yaml
    def test_unnamed_stream_includes_resolve_against_base_dir(self, tmp_path):
        _json(tmp_path / "db.json", {"host": "h"})
        stream = io.StringIO("db: !include db.json\n")
        assert Loader(base_dir=tmp_path).load(stream, format="yaml") == {
            "db": {"host": "h"}
        }

    @needs_yaml
    def test_bytes_stream_uses_loader_encoding_for_includes(self, tmp_path):
        _write(tmp_path / "sub.yaml", "inner: café\n", encoding="cp1252")
        stream = io.BytesIO("sub: !include sub.yaml\n".encode("cp1252"))
        result = Loader(base_dir=tmp_path).load(
            stream, format="yaml", encoding="cp1252"
        )
        assert result == {"sub": {"inner": "café"}}

    def test_text_stream_outside_the_loader_codec(self):
        stream = io.StringIO('{"name": "日本"}')
        assert Loader(encoding="cp1252").load(stream, format="json") == {"name": "日本"}

    def test_duck_reader(self):
        class Reader:
            def read(self, *args):
                return '{"a": 1}'

        assert Loader().load(Reader(), format="json") == {"a": 1}

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_codecs_reader(self, tmp_path):
        import codecs

        path = _json(tmp_path / "c.json", {"a": 1})
        with codecs.open(str(path), encoding="utf-8") as handle:
            assert Loader().load(handle) == {"a": 1}

    def test_spooled_temporary_file(self):
        import tempfile

        with tempfile.SpooledTemporaryFile(mode="w+b") as handle:
            handle.write(b'{"server": {"port": 8080}}')
            handle.seek(0)
            assert Loader().load(handle, format="json") == {"server": {"port": 8080}}

    def test_content_is_never_a_list_of_sources(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _json(tmp_path / "other.json", {"secret": 1})
        assert Loader().load(io.StringIO('"other.json"'), format="json") == "other.json"

    def test_reader_returning_non_text(self):
        class Reader:
            def read(self, *args):
                return 42

        with pytest.raises(TypeError, match="read"):
            Loader().load(Reader(), format="json")

    def test_read_once(self):
        calls = []

        class Reader:
            def read(self, *args):
                calls.append(args)
                return '{"a": 1}'

        Loader().load(Reader(), format="json")
        assert len(calls) == 1

    def test_script_named_stream_is_never_run(self, tmp_path):
        path = _write(tmp_path / "x.sh", '{"a": 1}\n')
        with open(path, encoding="utf-8") as handle:
            # No format reads a .sh name, and a stream's content is data.
            with pytest.raises(UnsupportedFormatError):
                Loader(policy=Policy.trusted()).load(handle)
        with open(path, encoding="utf-8") as handle:
            assert Loader(policy=Policy.trusted()).load(handle, format="json") == {
                "a": 1
            }

    def test_marker_line_is_content(self):
        assert Loader().load(io.StringIO("#!c.json\nA=1\n"), format="dotenv") == {
            "A": "1"
        }

    def test_hash_key_is_the_file_stem(self, tmp_path):
        path = _json(tmp_path / "service.json", {"a": 1})
        with open(path, encoding="utf-8") as handle:
            assert Loader(collect="hash").load(handle) == {"service": {"a": 1}}

    def test_two_streams_stay_distinct(self):
        result = Loader(collect="list").load(
            io.StringIO('{"a": 1}'), io.StringIO('{"b": 2}'), format="json"
        )
        assert result == [{"a": 1}, {"b": 2}]


class TestLoaderEdges:
    def test_a_parsed_source_has_its_delete_markers_pruned(self):
        class Parsed(anycfg.sources.Source):
            name = "parsed"

            def read(self, context):
                return anycfg.sources.Payload(
                    {"a": anycfg.DELETE, "b": 1},
                    name="parsed",
                    identity="parsed",
                    parsed=True,
                )

        assert anycfg.Loader().load(Parsed()) == {"b": 1}

    def test_per_call_policy_none_is_the_loaders(self):
        loader = anycfg.Loader(policy=anycfg.Policy(strict=True))
        assert loader.load({"a": 1}, policy=None) == {"a": 1}

    def test_formats_takes_classes(self):
        json_format = anycfg.formats.get("json")
        loader = anycfg.Loader(formats=[json_format, "yaml"])
        assert loader.load({"a": 1}) == {"a": 1}

    @needs_yaml
    def test_a_format_registered_later_is_reachable_from_documents(self, tmp_path):
        (tmp_path / "a.laterfmt").write_text("x", encoding="utf-8")
        (tmp_path / "app.yaml").write_text("v: !include a.laterfmt", encoding="utf-8")
        loader = anycfg.Loader(base_dir=tmp_path)

        class Later(anycfg.formats.Format):
            name = "laterfmt"
            suffixes = (".laterfmt",)

            def loads(self, data, /, *, context=None, **options):
                return {"later": data.strip()}

        anycfg.formats.register_format(Later)
        try:
            assert loader.load("app.yaml") == {"v": {"later": "x"}}
        finally:
            anycfg.formats.unregister_format("laterfmt")

    def test_flatten_with_nothing_loaded_returns_the_default(self, tmp_path):
        loader = anycfg.Loader(base_dir=tmp_path)
        assert loader.load("*.json", flatten=True) is None

    def test_a_merge_failure_of_unnamed_data_names_it(self):
        def failing(a, b, *, memo=None, **options):
            raise anycfg.ConfigValueError("refused")

        with pytest.raises(anycfg.ConfigValueError) as caught:
            anycfg.Loader(merge=failing).load({"a": 1}, {"b": 2})
        assert "while merging in-memory data" in str(caught.value)
