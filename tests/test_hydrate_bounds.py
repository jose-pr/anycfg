"""Hydration cost and shape rules: aliases, cycles, and the coercions at the edges."""

from __future__ import annotations

import dataclasses
import decimal
import time
import typing

import pytest

import anycfg
from anycfg import ConfigTypeError, ConfigValueError, hydrate, typed_merge

# list[list[...[str]]], eight deep: every level is coerced, none is Any.
DEEP: typing.Any = str
for _ in range(8):
    DEEP = typing.List[DEEP]


@dataclasses.dataclass
class Node:
    items: DEEP


@dataclasses.dataclass
class Tree:
    children: typing.List["Tree"]


class Settings(typing.TypedDict):
    port: int
    name: str


class Partial(typing.TypedDict, total=False):
    port: int


@dataclasses.dataclass
class Amount:
    value: decimal.Decimal


@dataclasses.dataclass
class Count:
    n: int


@dataclasses.dataclass
class Ratio:
    r: float


def _laughs(depth: int) -> dict:
    """Ten references to the level below, *depth* times: 10**depth paths."""
    level: typing.Any = ["lol"]
    for _ in range(depth):
        level = [level] * 10
    return {"items": level}


class TestAliases:
    def test_an_aliased_document_builds_in_linear_time(self):
        # 10**8 paths, 9 distinct nodes: building per path would never finish.
        start = time.perf_counter()
        node = hydrate(Node, _laughs(8))
        assert time.perf_counter() - start < 2
        assert node.items[0] is node.items[1]

    @pytest.mark.usefixtures("needs_yaml")
    def test_a_yaml_billion_laughs_loads_into_a_type(self):
        lines = ['a: &a ["lol"]']
        for index in range(1, 9):
            refs = ", ".join([f"*{chr(96 + index)}"] * 10)
            lines.append(f"{chr(97 + index)}: &{chr(97 + index)} [{refs}]")
        lines.append(f"items: *{chr(97 + 8)}")
        start = time.perf_counter()
        node = anycfg.loads("\n".join(lines) + "\n", format="yaml", into=Node)
        assert time.perf_counter() - start < 2
        assert len(node.items) == 10

    def test_data_that_contains_itself_raises(self):
        tree: dict = {"children": []}
        tree["children"].append(tree)
        with pytest.raises(ConfigValueError, match="contains itself"):
            hydrate(Tree, tree)

    @pytest.mark.usefixtures("needs_yaml")
    def test_a_recursive_yaml_anchor_raises_a_config_error(self):
        with pytest.raises(ConfigValueError):
            anycfg.loads("&x {children: [*x]}\n", format="yaml", into=Tree)

    def test_too_deep_is_a_config_error(self):
        data: dict = {"children": []}
        for _ in range(5000):
            data = {"children": [data]}
        with pytest.raises(ConfigValueError, match="nests too deeply"):
            hydrate(Tree, data)


class TestTypedDict:
    def test_extra_forbid_and_ignore(self):
        with pytest.raises(ConfigTypeError, match="zzz"):
            hydrate(Settings, {"port": 1, "name": "x", "zzz": 1}, extra="forbid")
        assert hydrate(Settings, {"port": "1", "name": "x", "zzz": 1}) == {
            "port": 1,
            "name": "x",
        }

    def test_required_keys(self):
        with pytest.raises(ConfigTypeError, match="'name'"):
            hydrate(Settings, {"port": 1})
        assert hydrate(Partial, {}) == {}


class TestNumbers:
    def test_bad_decimal_is_a_config_value_error(self):
        with pytest.raises(ConfigValueError) as caught:
            hydrate(Amount, {"value": "abc"})
        assert caught.value.config_key == ("value",)

    def test_decimal_from_a_float_uses_its_text(self):
        assert hydrate(Amount, {"value": 1.1}).value == decimal.Decimal("1.1")

    def test_a_bool_is_not_an_int(self):
        with pytest.raises(ConfigTypeError, match="bool"):
            hydrate(Count, {"n": True})

    def test_a_fractional_decimal_is_not_an_int(self):
        with pytest.raises(ConfigValueError, match="whole"):
            hydrate(Count, {"n": decimal.Decimal("1.5")})

    def test_an_int_too_large_for_a_float(self):
        with pytest.raises(ConfigValueError, match="too large"):
            hydrate(Ratio, {"r": 2**60 + 1})


class TestShapes:
    def test_named_tuple_from_a_mapping(self):
        class Point(typing.NamedTuple):
            x: int
            y: int = 0

        assert typed_merge(Point, {"x": "1"}) == Point(1, 0)
        with pytest.raises(ConfigTypeError, match="z"):
            typed_merge(Point, {"x": 1, "z": 2}, extra="forbid")

    def test_frozenset_and_abstract_set(self):
        assert typed_merge(typing.FrozenSet[int], ["1", "2"]) == frozenset({1, 2})
        assert typed_merge(typing.AbstractSet[str], ["a"]) == {"a"}

    def test_a_mapping_is_not_a_set(self):
        with pytest.raises(ConfigTypeError):
            typed_merge(typing.Set[int], {"a": 1})
