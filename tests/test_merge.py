"""The merge strategies: by function, by name, and through a Loader."""

import collections
import copy
import datetime
import decimal
import enum
import time
import typing

import pytest

import yaconfiglib
from yaconfiglib import DELETE, DotAccessibleDict, Loader
from yaconfiglib import _merge
from yaconfiglib._merge import deep, simple, substitute

STRATEGIES = [simple, substitute, deep]
STRATEGY_IDS = ["simple", "substitute", "deep"]
RECURSIVE = [substitute, deep]
RECURSIVE_IDS = ["substitute", "deep"]


@pytest.fixture
def isolated_registry(monkeypatch):
    """Register strategies into a copy, so a test cannot leak one into the session."""
    monkeypatch.setattr(_merge, "_STRATEGIES", dict(_merge._STRATEGIES))


class _Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


class _ReadOnly(typing.Mapping):
    """A Mapping that is not a dict and cannot be written to."""

    def __init__(self, **values):
        self._values = dict(values)

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


def _leaves(which):
    if which == "a":
        return {
            "release": datetime.date(2024, 1, 1),
            "at": datetime.datetime(2024, 1, 1, 10, 0),
            "cutoff": datetime.time(9, 0),
            "rate": decimal.Decimal("1.5"),
            "colour": _Colour.RED,
            "tags": {"one"},
            "blob": b"ab",
        }
    return {
        "release": datetime.date(2025, 6, 1),
        "at": datetime.datetime(2025, 6, 1, 11, 30),
        "cutoff": datetime.time(17, 45),
        "rate": decimal.Decimal("2.5"),
        "colour": _Colour.BLUE,
        "tags": {"two"},
        "blob": b"cd",
    }


# ---------------------------------------------------------------------------
# simple
# ---------------------------------------------------------------------------


class TestSimple:
    def test_scalar_replaces(self):
        assert simple(1, 2) == 2

    def test_top_level_keys_update(self):
        assert simple({"x": 1, "y": 2}, {"y": 99, "z": 3}) == {"x": 1, "y": 99, "z": 3}

    def test_nested_mapping_replaces_rather_than_merging(self):
        assert simple({"db": {"host": "h", "port": 1}}, {"db": {"port": 2}}) == {
            "db": {"port": 2}
        }

    def test_list_replaced_wholesale(self):
        assert simple([1, 2, 3], [10, 20]) == [10, 20]
        assert simple([1], [10, 20, 30]) == [10, 20, 30]
        assert simple({"a": [1, 2, 3]}, {"a": [9]}) == {"a": [9]}

    def test_shape_changes_replace(self):
        assert simple("old", {"k": "v"}) == {"k": "v"}
        assert simple("old", [1, 2]) == [1, 2]
        assert simple({"k": 1}, 5) == 5

    def test_list_of_mappings_does_not_fold(self):
        # Folding is a substitute/deep rule; simple replaces.
        assert simple({"a": 1}, [{"b": 2}]) == [{"b": 2}]

    def test_unknown_leaf_type_replaces(self):
        class Weird:
            pass

        weird = Weird()
        assert simple({}, weird) is weird

    def test_list_of_dates_replaces(self):
        assert simple([datetime.date(2024, 1, 1)], [datetime.date(2025, 1, 1)]) == [
            datetime.date(2025, 1, 1)
        ]

    def test_does_not_mutate_aliased_list_elements(self):
        element = {"x": 1}
        simple([element, element], [{"x": 2}])
        assert element == {"x": 1}

    def test_two_top_level_lists_through_a_loader_replace(self):
        assert Loader().load({"l": [1, 2]}, {"l": [3]}) == {"l": [3]}


# ---------------------------------------------------------------------------
# substitute
# ---------------------------------------------------------------------------


class TestSubstitute:
    def test_scalar_replaces(self):
        assert substitute("a", "b") == "b"

    def test_lists_replace(self):
        assert substitute([1, 2], [3, 4]) == [3, 4]
        assert substitute({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}

    def test_mappings_recurse(self):
        a = {"a": 1, "b": {"x": 10, "y": 20}}
        b = {"b": {"y": 99, "z": 30}, "c": 3}
        assert substitute(a, b) == {"a": 1, "b": {"x": 10, "y": 99, "z": 30}, "c": 3}

    def test_none_a_takes_b(self):
        assert substitute(None, {"a": 1}) == {"a": 1}


# ---------------------------------------------------------------------------
# deep
# ---------------------------------------------------------------------------


class TestDeep:
    def test_scalar_replaces(self):
        assert deep(1, 2) == 2

    def test_none_a_takes_b(self):
        assert deep(None, 42) == 42

    def test_mappings_recurse(self):
        a = {"a": 1, "b": {"x": 10, "y": 20}}
        b = {"b": {"y": 99, "z": 30}, "c": 3}
        assert deep(a, b) == {"a": 1, "b": {"x": 10, "y": 99, "z": 30}, "c": 3}

    def test_list_extends_with_new_items_in_order(self):
        assert deep([1, 2, 3], [3, 4, 5]) == [1, 2, 3, 4, 5]
        assert deep([1, 2], [1, 2]) == [1, 2]
        assert deep([], [datetime.date(2025, 1, 1)]) == [datetime.date(2025, 1, 1)]

    def test_list_under_a_key_extends(self):
        assert deep({"hosts": ["a"]}, {"hosts": ["a", "b"]}) == {"hosts": ["a", "b"]}

    def test_dedupe_is_type_aware(self):
        # 1 == True == 1.0, but they are different configuration values.
        result = deep([1, 0], [True, False, 1.0])
        assert [type(item) for item in result] == [int, int, bool, bool, float]

    def test_dedupe_inside_b_itself(self):
        assert deep([], [1, 1, 2, 1]) == [1, 2]

    def test_unhashable_items_compare_by_structure(self):
        assert deep([[1]], [[1], [2]]) == [[1], [2]]
        assert deep([[1, [2]]], [[1, [2]]]) == [[1, [2]]]
        assert deep([{1}], [{1}, {2}]) == [{1}, {2}]

    def test_structural_dedupe_is_type_aware(self):
        result = deep([[1]], [[True], (1,)])
        assert result == [[1], [True], (1,)]
        assert [type(item) for item in result] == [list, list, tuple]

    def test_mappings_in_a_list_are_never_deduplicated(self):
        assert deep([{"a": 1}], [{"a": 1}]) == [{"a": 1}, {"a": 1}]

    @pytest.mark.parametrize("mergelists", [False, True], ids=["plain", "mergelists"])
    def test_extension_keeps_source_order(self, mergelists):
        assert deep([], [1, {"a": 1}, 2], mergelists=mergelists) == [1, {"a": 1}, 2]

    def test_without_mergelists_mappings_append(self):
        assert deep([{"k": 1}], [{"k": 2}]) == [{"k": 1}, {"k": 2}]
        assert deep([{"k": 1}], [{"k": 2}], mergelists=False) == [{"k": 1}, {"k": 2}]

    def test_mergelists_merges_same_index_mappings_sharing_a_key(self):
        result = deep([{"k": 1, "v": "a"}], [{"k": 1, "v": "b"}], mergelists=True)
        assert result == [{"k": 1, "v": "b"}]

    def test_mergelists_keeps_a_mapping_sharing_no_key(self):
        assert deep([{"k": 1}], [{"z": 9}], mergelists=True) == [{"k": 1}, {"z": 9}]

    def test_mergelists_mixed_overlap(self):
        a = [{"k": 1}, {"p": 1}]
        b = [{"k": 2}, {"q": 2}]
        assert deep(a, b, mergelists=True) == [{"k": 2}, {"p": 1}, {"q": 2}]

    def test_mergelists_through_a_loader(self):
        result = Loader(merge="deep", merge_options={"mergelists": True}).load(
            {"svc": [{"name": "a", "port": 1}]}, {"svc": [{"name": "a", "port": 2}]}
        )
        assert result == {"svc": [{"name": "a", "port": 2}]}

    def test_mergelists_as_a_per_call_override(self):
        loader = Loader(merge="deep")
        a, b = {"svc": [{"name": "a"}]}, {"svc": [{"name": "a", "port": 2}]}
        assert loader.load(a, b) == {"svc": [{"name": "a"}, {"name": "a", "port": 2}]}
        assert loader.load(a, b, merge_options={"mergelists": True}) == {
            "svc": [{"name": "a", "port": 2}]
        }

    def test_tuple_extends_as_a_tuple(self):
        result = deep((1, 2), (2, 3))
        assert result == (1, 2, 3)
        assert type(result) is tuple

    def test_bytearray_replaces(self):
        assert deep(bytearray(b"ab"), bytearray(b"bc")) == bytearray(b"bc")

    @pytest.mark.parametrize("mergelists", [False, True], ids=["plain", "mergelists"])
    def test_list_keeps_leaf_items_of_any_type(self, mergelists):
        a = [datetime.date(2024, 1, 1), datetime.datetime(2024, 1, 1, 9, 0), {"x"}]
        b = [datetime.date(2025, 1, 1), datetime.datetime(2025, 1, 1, 9, 0), {"y"}]
        result = deep(a, b, mergelists=mergelists)
        for item in a + b:
            assert item in result


# ---------------------------------------------------------------------------
# Rules every strategy follows
# ---------------------------------------------------------------------------


class TestLeafRule:
    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_any_non_container_value_replaces(self, strategy):
        result = strategy({"section": _leaves("a")}, {"section": _leaves("b")})
        assert result["section"] == _leaves("b")

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_text_is_a_leaf_not_a_sequence(self, strategy):
        assert strategy("abc", "de") == "de"
        assert strategy(b"abc", b"de") == b"de"


class TestShapeChanges:
    CASES = {
        "scalar_to_dict": (5, {"k": 1}),
        "dict_to_scalar": ({"k": 1}, 5),
        "str_to_list": ("a", [1, 2]),
        "list_to_dict": ([1, 2], {"k": 1}),
        "dict_to_scalar_list": ({"k": 1}, [1, 2]),
        "list_to_scalar": ([1, 2], "x"),
    }

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    @pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
    def test_later_value_replaces(self, strategy, case):
        a, b = self.CASES[case]
        assert strategy({"x": a}, {"x": b})["x"] == b


class TestNoneIsNoOpinion:
    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_top_level_none_keeps_earlier(self, strategy):
        assert strategy("keep", None) == "keep"
        assert strategy({"a": 1}, None) == {"a": 1}

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_none_value_keeps_earlier_value(self, strategy):
        assert strategy({"a": 1, "b": {"c": 2}}, {"a": None, "b": None}) == {
            "a": 1,
            "b": {"c": 2},
        }

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_none_for_a_new_key_adds_it_as_none(self, strategy):
        result = strategy({"a": 1}, {"b": None})
        assert result == {"a": 1, "b": None}

    @pytest.mark.parametrize("strategy", RECURSIVE, ids=RECURSIVE_IDS)
    def test_nested_none_keeps_earlier_value(self, strategy):
        result = strategy({"db": {"host": "h", "port": 1}}, {"db": {"host": None}})
        assert result == {"db": {"host": "h", "port": 1}}

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_through_a_loader(self, strategy):
        result = Loader(merge=strategy).load({"a": 1}, {"a": None, "b": None})
        assert result == {"a": 1, "b": None}


class TestDelete:
    def test_delete_is_exported_and_a_singleton(self):
        assert yaconfiglib.DELETE is DELETE
        assert copy.copy(DELETE) is DELETE
        assert copy.deepcopy({"k": DELETE})["k"] is DELETE
        assert repr(DELETE) == "DELETE"

    def test_delete_pickles_to_itself(self):
        import pickle

        assert pickle.loads(pickle.dumps(DELETE)) is DELETE

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_delete_removes_a_key(self, strategy):
        assert strategy({"a": 1, "b": 2}, {"a": DELETE}) == {"b": 2}

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_delete_of_an_absent_key_is_a_no_op(self, strategy):
        assert strategy({"a": 1}, {"z": DELETE}) == {"a": 1}

    @pytest.mark.parametrize("strategy", RECURSIVE, ids=RECURSIVE_IDS)
    def test_nested_delete(self, strategy):
        result = strategy({"db": {"host": "h", "port": 1}}, {"db": {"port": DELETE}})
        assert result == {"db": {"host": "h"}}

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_whole_document_delete_gives_none(self, strategy):
        assert strategy({"a": 1}, DELETE) is None

    def test_deep_list_skips_delete_items(self):
        assert deep([1], [DELETE, 2]) == [1, 2]

    @pytest.mark.parametrize("merge", ["simple", "substitute", "deep"])
    def test_through_a_loader(self, merge):
        result = Loader(merge=merge).load({"a": 1, "b": 2}, {"a": DELETE})
        assert result == {"b": 2}

    def test_markers_left_in_a_result_are_pruned(self):
        # simple does not recurse, so the nested marker reaches the result.
        result = Loader(merge="simple").load(
            {"db": {"host": "h"}}, {"db": {"host": DELETE, "port": 1}}
        )
        assert result == {"db": {"port": 1}}
        # A single source is not merged at all; its markers are still pruned.
        assert Loader().load({"a": DELETE, "b": [1, DELETE]}) == {"b": [1]}

    @pytest.mark.usefixtures("needs_yaml")
    @pytest.mark.parametrize("merge", ["simple", "substitute", "deep"])
    def test_yaml_delete_tag(self, tmp_path, merge):
        (tmp_path / "base.yaml").write_text(
            "host: h\nport: 1\ndb:\n  user: u\n  password: p\n", encoding="utf-8"
        )
        (tmp_path / "local.yaml").write_text(
            "port: !delete\ndb:\n  user: u\n  password: !delete\n", encoding="utf-8"
        )
        result = Loader(base_dir=tmp_path, merge=merge).load("base.yaml", "local.yaml")
        assert result == {"host": "h", "db": {"user": "u"}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_yaml_delete_tag_in_a_single_document_is_pruned(self, tmp_path):
        (tmp_path / "one.yaml").write_text("a: !delete\nb: 1\n", encoding="utf-8")
        assert Loader(base_dir=tmp_path).load("one.yaml") == {"b": 1}


class TestFolding:
    @pytest.mark.parametrize("strategy", RECURSIVE, ids=RECURSIVE_IDS)
    def test_list_of_mappings_folds_in_order(self, strategy):
        assert strategy({"a": 1}, [{"b": 2}, {"c": 3}]) == {"a": 1, "b": 2, "c": 3}
        assert strategy({"a": 1}, [{"a": 2}, {"a": 3}]) == {"a": 3}

    @pytest.mark.parametrize("strategy", RECURSIVE, ids=RECURSIVE_IDS)
    def test_any_other_list_replaces(self, strategy):
        assert strategy({"a": 1}, [42]) == [42]
        assert strategy({"a": 1}, [{"b": 2}, 3]) == [{"b": 2}, 3]
        # An empty list is "clear this", not "fold nothing in".
        assert strategy({"a": 1}, []) == []

    def test_folds_recursively_under_deep(self):
        result = deep(
            {"db": {"host": "h", "opts": {"x": 1}}}, {"db": [{"opts": {"y": 2}}]}
        )
        assert result == {"db": {"host": "h", "opts": {"x": 1, "y": 2}}}


# ---------------------------------------------------------------------------
# Copy-on-write, type preservation, memo
# ---------------------------------------------------------------------------


class TestCopyOnWrite:
    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_inputs_unchanged(self, strategy):
        a = {"db": {"host": "shared", "pool": 5}, "hosts": ["a"], "n": 1, "x": 1}
        b = {"db": {"host": "prod"}, "hosts": ["b"], "x": DELETE, "y": None}
        a_before, b_before = copy.deepcopy(a), copy.deepcopy(b)

        result = strategy(a, b)

        assert a == a_before
        assert b == b_before
        assert result is not a

    @pytest.mark.parametrize("mergelists", [False, True], ids=["plain", "mergelists"])
    def test_deep_lists_unchanged(self, mergelists):
        a = [{"k": 1}, 1]
        b = [{"k": 2}, 2]
        a_before, b_before = copy.deepcopy(a), copy.deepcopy(b)
        deep(a, b, mergelists=mergelists)
        assert a == a_before
        assert b == b_before

    @pytest.mark.parametrize("strategy", RECURSIVE, ids=RECURSIVE_IDS)
    def test_override_does_not_rewrite_an_aliased_sibling(self, strategy):
        shared = {"host": "shared", "pool": 5}
        a = {"svc_a": shared, "svc_b": shared}

        result = strategy(a, {"svc_a": {"pool": 9}})

        assert result["svc_a"] == {"host": "shared", "pool": 9}
        assert result["svc_b"] == {"host": "shared", "pool": 5}
        assert shared == {"host": "shared", "pool": 5}

    def test_folding_does_not_modify_the_target(self):
        a = {"a": 1}
        substitute(a, [{"b": 2}])
        deep(a, [{"b": 2}])
        assert a == {"a": 1}

    @pytest.mark.usefixtures("needs_yaml")
    @pytest.mark.parametrize("merge", ["deep", "substitute"])
    def test_yaml_merge_key_override_does_not_leak(self, tmp_path, merge):
        # `<<:` copies only top-level keys, so both environments share one `db`
        # mapping; an override layer must not rewrite it for everyone.
        (tmp_path / "app.yaml").write_text(
            "defaults: &defaults\n"
            "  db:\n"
            "    host: shared\n"
            "    pool: 5\n"
            "  hosts: [a]\n"
            "development:\n"
            "  <<: *defaults\n"
            "production:\n"
            "  <<: *defaults\n",
            encoding="utf-8",
        )
        (tmp_path / "override.yaml").write_text(
            "production:\n  db:\n    host: prod-db\n  hosts: [b]\n", encoding="utf-8"
        )

        result = Loader(base_dir=tmp_path, merge=merge).load(
            "app.yaml", "override.yaml"
        )

        assert result["production"]["db"]["host"] == "prod-db"
        assert result["development"]["db"]["host"] == "shared"
        assert result["defaults"]["db"]["host"] == "shared"
        assert result["development"]["hosts"] == ["a"]

    @pytest.mark.usefixtures("needs_yaml")
    @pytest.mark.parametrize("merge", ["deep", "substitute"])
    def test_later_layer_does_not_rewrite_an_earlier_layer_anchor(
        self, tmp_path, merge
    ):
        (tmp_path / "base.yaml").write_text("v: 0\n", encoding="utf-8")
        (tmp_path / "mid.yaml").write_text(
            "common: &c\n  x: 1\nb: *c\n", encoding="utf-8"
        )
        (tmp_path / "top.yaml").write_text("b:\n  x: 2\n", encoding="utf-8")

        result = Loader(base_dir=tmp_path, merge=merge).load(
            "base.yaml", "mid.yaml", "top.yaml"
        )

        assert result["b"]["x"] == 2
        assert result["common"]["x"] == 1

    def test_mapping_sources_are_not_modified_by_a_loader(self):
        a = {"db": {"host": "h"}, "l": [1]}
        b = {"db": {"port": 1}, "l": [2]}
        a_before, b_before = copy.deepcopy(a), copy.deepcopy(b)
        Loader(merge="deep").load(a, b)
        assert (a, b) == (a_before, b_before)


class TestTypePreservation:
    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_ordered_dict(self, strategy):
        ordered = collections.OrderedDict([("a", 1)])
        result = strategy(ordered, {"b": 2})
        assert type(result) is collections.OrderedDict
        assert list(result.items()) == [("a", 1), ("b", 2)]

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_defaultdict_keeps_its_factory(self, strategy):
        source = collections.defaultdict(list, {"a": 1})
        result = strategy(source, {"b": 2})
        assert type(result) is collections.defaultdict
        assert result.default_factory is list
        assert result == {"a": 1, "b": 2}
        assert dict(source) == {"a": 1}

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_dict_subclasses(self, strategy):
        class Tagged(dict):
            pass

        assert type(strategy(Tagged(a=1), {"b": 2})) is Tagged
        dot = DotAccessibleDict({"a": 1})
        assert type(strategy(dot, {"b": 2})) is DotAccessibleDict

    @pytest.mark.parametrize("strategy", RECURSIVE, ids=RECURSIVE_IDS)
    def test_nested_subclass_kept(self, strategy):
        inner = collections.OrderedDict([("x", 1)])
        result = strategy({"in": inner}, {"in": {"y": 2}})
        assert type(result["in"]) is collections.OrderedDict
        assert result["in"] == {"x": 1, "y": 2}

    @pytest.mark.parametrize("strategy", STRATEGIES, ids=STRATEGY_IDS)
    def test_read_only_mapping_with_overlapping_keys(self, strategy):
        source = _ReadOnly(a=1, b=2)
        result = strategy(source, {"b": 99})
        assert dict(result) == {"a": 1, "b": 99}
        assert dict(source) == {"a": 1, "b": 2}

    def test_list_subclass_kept_by_deep(self):
        class Tagged(list):
            pass

        result = deep(Tagged([1]), [2])
        assert type(result) is Tagged
        assert result == [1, 2]


class TestMemo:
    @pytest.mark.parametrize("strategy", RECURSIVE, ids=RECURSIVE_IDS)
    def test_node_aliased_in_both_inputs_stays_aliased(self, strategy):
        shared_a = {"k": 1}
        shared_b = {"k": 2}
        result = strategy(
            {"x": shared_a, "y": shared_a}, {"x": shared_b, "y": shared_b}
        )
        assert result["x"] is result["y"]
        assert result["x"] == {"k": 2}

    def test_aliased_lists_stay_aliased_under_deep(self):
        la, lb = [1], [2]
        result = deep({"x": la, "y": la}, {"x": lb, "y": lb})
        assert result["x"] is result["y"]
        assert result["x"] == [1, 2]

    @pytest.mark.parametrize("strategy", RECURSIVE, ids=RECURSIVE_IDS)
    def test_cyclic_mappings_merge(self, strategy):
        a = {"name": "one"}
        a["self"] = a
        b = {"name": "two"}
        b["self"] = b

        result = strategy(a, b)

        assert result["name"] == "two"
        assert result["self"] is result

    def test_cyclic_lists_terminate_under_deep(self):
        a = [1]
        a.append(a)
        b = [2]
        b.append(b)
        result = deep(a, b)
        assert len(result) == 4
        assert result[:1] == [1] and result[2] == 2

    def test_custom_strategy_threads_memo(self):
        seen = []

        def tracing(a, b, *, memo=None, **options):
            seen.append(memo)
            return deep(a, b, memo=memo, **options)

        memo = {}
        tracing({"a": {"b": 1}}, {"a": {"c": 2}}, memo=memo)
        assert seen == [memo]
        assert memo  # deep recorded the pairs it merged


# ---------------------------------------------------------------------------
# Strategy registry and resolution
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_builtins_listed_sorted(self):
        names = yaconfiglib.merge_strategies()
        assert {"simple", "substitute", "deep"} <= set(names)
        assert names == sorted(names)

    @pytest.mark.parametrize("name", ["simple", "substitute", "deep", "DEEP", "Simple"])
    def test_builtin_names_resolve_case_insensitively(self, name):
        assert _merge.resolve_merge(name) is getattr(_merge, name.lower())

    def test_default_strategy_is_simple(self):
        result = Loader().load({"db": {"host": "h"}}, {"db": {"port": 1}})
        assert result == {"db": {"port": 1}}

    @pytest.mark.usefixtures("isolated_registry")
    def test_register_merge_makes_a_name_selectable(self):
        def right(a, b, *, memo=None, **options):
            return b

        yaconfiglib.register_merge("Right", right)
        assert "right" in yaconfiglib.merge_strategies()
        assert _merge.resolve_merge("right") is right
        assert Loader(merge="right").load({"a": 1}, {"b": 2}) == {"b": 2}
        assert Loader().load({"a": 1}, {"b": 2}, merge="RIGHT") == {"b": 2}

    @pytest.mark.usefixtures("isolated_registry")
    def test_register_merge_refuses_a_taken_name(self):
        def right(a, b, *, memo=None, **options):
            return b

        with pytest.raises(ValueError, match="already registered"):
            yaconfiglib.register_merge("deep", right)
        with pytest.raises(ValueError):
            yaconfiglib.register_merge("DEEP", right)
        yaconfiglib.register_merge("deep", right, replace=True)
        assert _merge.resolve_merge("deep") is right

    def test_registry_isolation_restored(self):
        # Runs after the tests above in file order: the replaced "deep" is gone.
        assert _merge.resolve_merge("deep") is deep

    def test_unknown_name_is_value_error_listing_names(self):
        with pytest.raises(ValueError, match="deep") as caught:
            Loader(merge="no-such-strategy")
        assert "no-such-strategy" in str(caught.value)
        with pytest.raises(ValueError):
            Loader().load({"a": 1}, merge="no-such-strategy")

    @pytest.mark.parametrize("bad", [42, None, dict, ["deep"]])
    def test_non_callable_is_type_error(self, bad):
        with pytest.raises(TypeError):
            Loader(merge=bad)

    def test_callable_strategy(self):
        calls = []

        def concat(a, b, *, memo=None, **options):
            calls.append(options)
            return {"seen": a.get("seen", [a]) + [b]}

        result = Loader(merge=concat, merge_options={"flag": 1}).load(
            {"a": 1}, {"b": 2}, {"c": 3}
        )
        assert result == {"seen": [{"a": 1}, {"b": 2}, {"c": 3}]}
        assert calls == [{"flag": 1}, {"flag": 1}]

    def test_callable_instance_strategy(self):
        class Last:
            def __call__(self, a, b, *, memo=None, **options):
                return b

        assert Loader(merge=Last()).load({"a": 1}, {"b": 2}) == {"b": 2}

    def test_merge_protocol_is_exported(self):
        assert yaconfiglib.Merge is _merge.Merge
        assert "Merge" in yaconfiglib.__all__

    def test_merge_failure_carries_a_merge_frame(self):
        def broken(a, b, *, memo=None, **options):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError) as caught:
            Loader(merge=broken).load({"a": 1}, {"b": 2})
        frames = getattr(caught.value, "config_frames", ())
        assert [frame.kind for frame in frames] == ["merge"]

    def test_merge_failure_offered_to_on_error(self):
        events = []

        def broken(a, b, *, memo=None, **options):
            if "bad" in b:
                raise RuntimeError("boom")
            return {**a, **b}

        def on_error(event):
            events.append(event.phase)
            return True

        result = Loader(merge=broken, on_error=on_error).load(
            {"a": 1}, {"bad": 1}, {"c": 3}
        )
        assert result == {"a": 1, "c": 3}
        assert events == ["merge"]


# ---------------------------------------------------------------------------
# Performance guards (not benchmarks: an order-of-magnitude bound)
# ---------------------------------------------------------------------------


class TestPerformance:
    def test_wide_list_extension_is_linear(self):
        a = list(range(4000))
        b = list(range(2000, 6000))
        started = time.perf_counter()
        result = deep(a, b)
        elapsed = time.perf_counter() - started
        assert result == list(range(6000))
        assert elapsed < 1.0, elapsed

    def test_wide_unhashable_list_extension_is_linear(self):
        a = [[i] for i in range(4000)]
        b = [[i] for i in range(2000, 6000)]
        started = time.perf_counter()
        result = deep(a, b)
        elapsed = time.perf_counter() - started
        assert len(result) == 6000
        assert elapsed < 1.0, elapsed

    def test_alias_chain_of_lists_is_linear(self):
        # Each level holds the previous one twice: 2**40 paths, 41 distinct nodes.
        node = [0]
        for _ in range(40):
            node = [node, node]
        other = [1]
        for _ in range(40):
            other = [other, other]
        started = time.perf_counter()
        result = deep([node], [node, other])
        elapsed = time.perf_counter() - started
        assert len(result) == 2
        assert elapsed < 1.0, elapsed

    @pytest.mark.parametrize("strategy", RECURSIVE, ids=RECURSIVE_IDS)
    def test_alias_chain_of_mappings_is_linear(self, strategy):
        a = {"v": 0}
        b = {"v": 1}
        for _ in range(40):
            a = {"l": a, "r": a}
            b = {"l": b, "r": b}
        started = time.perf_counter()
        result = strategy(a, b)
        elapsed = time.perf_counter() - started
        assert result["l"] is result["r"]
        assert elapsed < 1.0, elapsed
