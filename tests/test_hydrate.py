"""Typed results: hydrate(), typed_merge() and load(..., into=)."""

import collections
import dataclasses
import datetime
import enum
import json
import pathlib
import sys
import types
import typing
from argparse import Namespace
from dataclasses import dataclass, field

import pytest

import yaconfiglib
from yaconfiglib import (
    ConfigTypeError,
    ConfigValueError,
    Loader,
    OpaqueMerge,
    TypedNamespace,
    hydrate,
    opaque,
    typed_merge,
)

# Models live at module level: annotations must resolve from the module globals.


@dataclass
class DB:
    host: str = ""
    port: int = 0


@dataclass
class App:
    db: typing.Optional[DB] = None
    name: str = ""


@dataclass
class Inner:
    port: int


@dataclass
class Outer:
    db: Inner
    hosts: typing.List[Inner] = field(default_factory=list)


@dataclass
class TLS:
    cert: str = ""
    key: str = ""


@dataclass
class Server:
    tls: typing.Optional[TLS] = None


@dataclass
class WithInitVar:
    host: str
    seed: dataclasses.InitVar[int] = 0
    derived: int = field(init=False, default=0)

    def __post_init__(self, seed):
        self.derived = seed * 2


@dataclass
class Slotted:
    __slots__ = ("a", "b")
    a: int
    b: int


@dataclass
class KwargsDataclass:
    """A dataclass with its own ``**kwargs`` constructor and a derived attribute."""

    field_1: str
    field_2: int
    field_3: str

    def __init__(self, **kwargs):
        for name in kwargs:
            setattr(self, name, kwargs[name])
        self.field_4 = f"{self.field_1}_{self.field_2}"


@dataclass
class PartiallyTyped:
    port: int = 0
    later: "NotDefinedAnywhere" = None  # noqa: F821 - unresolvable on purpose


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


class Point(typing.NamedTuple):
    x: int
    y: int


class Pair(tuple):
    """A tuple subclass that cannot be rebuilt from an iterable of items."""

    def __new__(cls, a, b):
        return super().__new__(cls, (a, b))


class KwOnlyDict(dict):
    """A dict subclass whose constructor takes no positional argument."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class Zone(OpaqueMerge, Namespace):
    pass


class HostsNS(TypedNamespace):
    def _parse_hosts(self, value):
        # Not idempotent: a second pass over a list raises AttributeError.
        return value.split(",")


class Recorder:
    seen = None

    @classmethod
    def __merge__(cls, *objects, init=True):
        Recorder.seen = objects
        return objects[-1]


T = typing.TypeVar("T")


class Box(typing.Generic[T]):
    @classmethod
    def __merge__(cls, *objects, init=True):
        return ("box",) + objects


def ip_factory(value):
    """A factory FUNCTION used as an annotation: callable, but not a class."""
    return f"net:{value}"


class NetConfig(Namespace):
    network: ip_factory


@dataclass
class OptionalZone:
    zone: typing.Optional[Zone] = None


@dataclass
class OptionalScalars:
    name: typing.Optional[str] = None
    debug: typing.Optional[bool] = None


@dataclass
class AnyField:
    extra: typing.Any = None


@dataclass
class Ports:
    ports: typing.List[int] = field(default_factory=list)


@dataclass
class Limits:
    limits: typing.Dict[str, int] = field(default_factory=dict)


@dataclass
class IntKeyed:
    ports: typing.Optional[typing.Dict[int, str]] = None


@dataclass
class Hosts:
    hosts: typing.Sequence[str] = ()


@dataclass
class BareList:
    items: list = field(default_factory=list)


@dataclass
class Flags:
    debug: bool = False


@dataclass
class Schedule:
    day: typing.Optional[datetime.date] = None
    at: typing.Optional[datetime.time] = None
    when: typing.Optional[datetime.datetime] = None
    colour: typing.Optional[Colour] = None


@dataclass
class Derived:
    a: int
    b: int = field(init=False, default=0)

    def __post_init__(self):
        self.b = self.a * 2


@pytest.fixture
def fake_pydantic(monkeypatch):
    """Install a stand-in ``pydantic`` module; returns a factory for its BaseModel."""

    def install(version):
        module = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **values):
                self.values = values

            if version == 2:

                @classmethod
                def model_validate(cls, data):
                    built = cls(**data)
                    built.built_with = "model_validate"
                    return built

            else:

                @classmethod
                def parse_obj(cls, data):
                    built = cls(**data)
                    built.built_with = "parse_obj"
                    return built

        module.BaseModel = BaseModel
        monkeypatch.setitem(sys.modules, "pydantic", module)
        return BaseModel

    return install


# ---------------------------------------------------------------------------
# hydrate and the load entry points
# ---------------------------------------------------------------------------


class TestHydrate:
    def test_dataclass(self):
        assert hydrate(DB, {"host": "h", "port": "6"}) == DB(host="h", port=6)

    def test_unknown_keys_ignored_by_default(self):
        assert hydrate(DB, {"host": "h", "self": "oops", "bogus": 1}) == DB(host="h")

    def test_unknown_keys_refused_with_extra_forbid(self):
        with pytest.raises(ConfigTypeError) as caught:
            hydrate(DB, {"host": "h", "bogus": 1, "other": 2}, extra="forbid")
        message = str(caught.value)
        assert "'bogus'" in message and "'other'" in message
        assert "DB" in message

    def test_extra_forbid_in_a_nested_model_names_the_path(self):
        with pytest.raises(ConfigTypeError) as caught:
            hydrate(Outer, {"db": {"port": 1, "prot": 1}}, extra="forbid")
        assert caught.value.config_key == ("db",)
        assert caught.value.config_model == "Outer"

    def test_invalid_extra_mode(self):
        with pytest.raises(ValueError):
            hydrate(DB, {}, extra="allow")
        with pytest.raises(ValueError):
            typed_merge(DB, {}, extra="bogus")

    def test_none_data_raises(self):
        with pytest.raises(ConfigTypeError, match="DB"):
            hydrate(DB, None)

    def test_non_mapping_data_raises_config_type_error(self):
        with pytest.raises(ConfigTypeError) as caught:
            hydrate(DB, [1, 2])
        assert "DB" in str(caught.value) and "list" in str(caught.value)

    def test_initvar_kept_and_init_false_dropped(self):
        result = hydrate(WithInitVar, {"host": "h", "seed": 21, "derived": 5})
        assert result.derived == 42

    def test_init_false_field_is_unknown_under_forbid(self):
        with pytest.raises(ConfigTypeError, match="derived"):
            hydrate(WithInitVar, {"host": "h", "derived": 5}, extra="forbid")

    def test_kwargs_dataclass_constructor_receives_every_key(self):
        result = hydrate(KwargsDataclass, {"field_1": 1, "field_2": "2", "x": 3})
        assert vars(result) == {
            "field_1": "1",
            "field_2": 2,
            "x": 3,
            "field_4": "1_2",
        }

    def test_kwargs_dataclass_accepts_any_key_even_under_forbid(self):
        result = hydrate(
            KwargsDataclass, {"field_1": "a", "field_2": 1, "x": 3}, extra="forbid"
        )
        assert result.x == 3

    def test_plain_class_takes_fields_as_keywords(self):
        class Plain:
            def __init__(self, a, b=0):
                self.a = a
                self.b = b

        result = hydrate(Plain, {"a": 7})
        assert (result.a, result.b) == (7, 0)

    def test_plain_kwargs_class_receives_every_key(self):
        class Keywords:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        assert hydrate(Keywords, {"a": 1, "b": 2}).kwargs == {"a": 1, "b": 2}

    def test_nested_models(self):
        result = hydrate(Outer, {"db": {"port": "1"}, "hosts": [{"port": 2}]})
        assert result == Outer(db=Inner(port=1), hosts=[Inner(port=2)])

    def test_optional_nested_model_none_is_kept(self):
        assert hydrate(App, {"db": None}).db is None
        assert hydrate(App, {"db": {"host": "h"}}).db == DB(host="h")

    def test_mapping_type(self):
        assert hydrate(typing.Dict[str, int], {"a": "1"}) == {"a": 1}

    def test_namespace(self):
        result = hydrate(Namespace, {"a": 1})
        assert isinstance(result, Namespace) and result.a == 1

    def test_exported(self):
        for name in (
            "hydrate",
            "typed_merge",
            "OpaqueMerge",
            "opaque",
            "TypedNamespace",
        ):
            assert name in yaconfiglib.__all__
        assert yaconfiglib.hydrate is hydrate


class TestLoadInto:
    def test_load_into(self, tmp_path):
        (tmp_path / "db.json").write_text('{"host": "h", "port": "6"}', "utf-8")
        assert yaconfiglib.load(tmp_path / "db.json", into=DB) == DB("h", 6)

    def test_loads_into(self):
        assert yaconfiglib.loads('{"host": "h"}', format="json", into=DB) == DB("h")

    def test_loader_load_into_merges_layers_first(self):
        result = Loader(merge="deep").load(
            {"host": "a", "port": 1}, {"port": "2"}, into=DB
        )
        assert result == DB(host="a", port=2)

    def test_loader_load_into_from_files(self, tmp_path):
        (tmp_path / "a.json").write_text(json.dumps({"db": {"port": 1}}), "utf-8")
        (tmp_path / "b.json").write_text(json.dumps({"hosts": [{"port": 2}]}), "utf-8")
        result = Loader(base_dir=tmp_path).load("a.json", "b.json", into=Outer)
        assert result == Outer(db=Inner(port=1), hosts=[Inner(port=2)])

    def test_loader_load_all_into_hydrates_each_document(self):
        result = list(Loader().load_all({"host": "a"}, {"host": "b"}, into=DB))
        assert result == [DB(host="a"), DB(host="b")]

    def test_into_ignores_wrap(self):
        assert type(Loader(wrap=True).load({"host": "h"}, into=DB)) is DB

    def test_nothing_loaded_raises(self, tmp_path):
        loader = Loader(base_dir=tmp_path)
        with pytest.raises(ConfigTypeError, match="no data"):
            loader.load("conf.d/*.json", into=DB)

    def test_non_mapping_document_names_the_model_and_type(self):
        with pytest.raises(ConfigTypeError) as caught:
            yaconfiglib.loads("[1, 2]", format="json", into=Inner)
        assert "Inner" in str(caught.value) and "list" in str(caught.value)

    def test_nested_missing_field_names_the_path(self):
        with pytest.raises(TypeError) as caught:
            yaconfiglib.loads('{"db": {"prot": 1}}', format="json", into=Outer)
        error = caught.value
        assert error.config_key == ("db",)
        assert "(Outer)" in str(error)

    def test_pydantic_never_imported(self, monkeypatch):
        looked_up = []

        class Finder:
            def find_spec(self, name, path=None, target=None):
                looked_up.append(name)
                return None

        monkeypatch.delitem(sys.modules, "pydantic", raising=False)
        monkeypatch.setattr(sys, "meta_path", [Finder()] + list(sys.meta_path))
        hydrate(DB, {"host": "h"})
        Loader().load({"host": "h"}, into=DB)
        assert "pydantic" not in looked_up
        assert "pydantic" not in sys.modules


class TestPydantic:
    def test_v2_model_validate(self, fake_pydantic):
        base = fake_pydantic(2)

        class Settings(base):
            pass

        result = hydrate(Settings, {"a": 1})
        assert result.built_with == "model_validate"
        assert result.values == {"a": 1}

    def test_v1_parse_obj(self, fake_pydantic):
        base = fake_pydantic(1)

        class Settings(base):
            pass

        assert hydrate(Settings, {"a": 1}).built_with == "parse_obj"

    def test_layers_deep_merged_then_validated_once(self, fake_pydantic):
        base = fake_pydantic(2)

        class Settings(base):
            pass

        result = typed_merge(Settings, {"a": {"x": 1}, "l": [1]}, {"a": {"y": 2}})
        assert result.values == {"a": {"x": 1, "y": 2}, "l": [1]}

    def test_through_a_loader(self, fake_pydantic):
        base = fake_pydantic(2)

        class Settings(base):
            pass

        result = Loader().load({"a": 1}, {"b": 2}, into=Settings)
        assert result.values == {"a": 1, "b": 2}

    def test_an_instance_is_kept(self, fake_pydantic):
        base = fake_pydantic(2)

        class Settings(base):
            pass

        built = Settings(a=1)
        assert typed_merge(Settings, built) is built


# ---------------------------------------------------------------------------
# typed_merge: layers
# ---------------------------------------------------------------------------


class TestLayers:
    def test_scalar_last_wins(self):
        assert typed_merge(int, 1, 2, 3) == 3

    def test_no_or_all_none_layers_give_none(self):
        assert typed_merge(int) is None
        assert typed_merge(str, None, None) is None
        assert typed_merge(App, None) is None

    def test_none_layers_skipped(self):
        assert typed_merge(str, "a", None) == "a"
        assert typed_merge(bool, True, None) is True
        assert typed_merge(int, 7, None) == 7
        assert typed_merge(typing.Optional[typing.List[str]], ["a"], None) == ["a"]

    def test_none_field_keeps_the_earlier_value(self):
        merged = typed_merge(OptionalScalars, OptionalScalars("x", True), {})
        assert (merged.name, merged.debug) == ("x", True)
        merged = typed_merge(
            OptionalScalars, {"name": "x", "debug": True}, {"name": None}
        )
        assert merged.name == "x"

    def test_none_in_any_position_for_a_nested_model(self):
        db = DB(host="h", port=5)
        assert typed_merge(App, App(db=db), App()).db == db
        assert typed_merge(App, App(), App(db=db)).db == db
        merged = typed_merge(App, {"db": {"host": "h", "port": "5"}}, {"db": None})
        assert merged.db == DB(host="h", port=5)

    def test_untyped_mapping_none_values(self):
        assert typed_merge(dict, {"a": 1}, {"a": None}) == {"a": 1}
        assert typed_merge(dict, {"a": 1}, {"a": None}, {"a": 3}) == {"a": 3}
        assert typed_merge(dict, {"a": None}, {"a": None}) == {"a": None}

    def test_mapping_layers_merge_by_key(self):
        assert typed_merge(dict, {"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_dataclass_layers(self):
        merged = typed_merge(DB, DB(host="a", port=1), {"port": "2"})
        assert merged == DB(host="a", port=2)

    def test_slotted_dataclass_layers(self):
        merged = typed_merge(Slotted, Slotted(1, 2), {"b": "3"})
        assert (merged.a, merged.b) == (1, 3)
        assert typed_merge(Slotted, Slotted(1, 2), Slotted(5, 6)) == Slotted(5, 6)

    def test_init_false_builds_without_calling_init(self):
        merged = typed_merge(Derived, {"a": 3}, init=False)
        assert merged.a == 3
        assert "b" not in vars(merged)

    def test_dataclass_init_false_field_recomputed(self):
        merged = typed_merge(Derived, Derived(1), Derived(2))
        assert (merged.a, merged.b) == (2, 4)

    def test_kwargs_constructor_receives_unknown_keys(self):
        merged = typed_merge(
            KwargsDataclass,
            KwargsDataclass(field_1=11, field_2=22, field_3=33),
            dict(field_1=1, field_2=2),
        )
        assert vars(merged) == {
            "field_1": "1",
            "field_2": 2,
            "field_3": "33",
            "field_4": "1_2",
        }

    def test_unresolvable_annotation_keeps_the_other_hints(self):
        merged = typed_merge(PartiallyTyped, PartiallyTyped(port="8080"))
        assert merged.port == 8080
        assert merged.later is None


class TestShapeChange:
    def test_scalar_then_mapping_merges_as_the_mapping(self):
        merged = typed_merge(Server, {"tls": "off"}, {"tls": {"cert": "c"}})
        assert merged.tls == TLS(cert="c")

    def test_scalar_layer_replaces_every_earlier_layer(self):
        merged = typed_merge(TLS, {"key": "k"}, "off", {"cert": "c"})
        assert merged == TLS(cert="c")

    def test_final_scalar_layer_raises_config_type_error(self):
        with pytest.raises(ConfigTypeError) as caught:
            typed_merge(Server, {"tls": {"cert": "c"}}, {"tls": "off"})
        assert caught.value.config_key == ("tls",)
        assert caught.value.config_model == "Server"
        assert "vars()" not in str(caught.value)

    @pytest.mark.parametrize("value", ["off", 5, [1, 2], 1.5])
    def test_top_level_final_scalar_raises_config_type_error(self, value):
        with pytest.raises(ConfigTypeError, match="TLS"):
            typed_merge(TLS, {"cert": "c"}, value)


# ---------------------------------------------------------------------------
# typed_merge: hints
# ---------------------------------------------------------------------------


class TestUnionsAndSpecialForms:
    def test_optional_scalar(self):
        assert typed_merge(typing.Optional[int], 1, "2") == 2

    def test_union_skips_nonetype_member(self):
        assert typed_merge(typing.Union[None, int], "5") == 5

    def test_union_keeps_a_value_a_member_accepts(self):
        assert typed_merge(typing.Union[str, int], 5) == 5
        assert typed_merge(typing.Union[int, str], "abc") == "abc"
        assert typed_merge(typing.Union[int, str], "8080") == "8080"

    def test_union_coerces_through_first_member(self):
        assert typed_merge(typing.Union[int, float], "2") == 2

    @pytest.mark.skipif(sys.version_info < (3, 10), reason="X | Y is 3.10+")
    def test_pep604_union(self):
        int_or_none = eval("int | None")  # noqa: S307 - unevaluable on 3.9
        dict_or_none = eval("typing.Dict[str, int] | None", {"typing": typing})
        assert typed_merge(int_or_none, "5") == 5
        assert typed_merge(dict_or_none, {"a": "1"}, {"b": "2"}) == {"a": 1, "b": 2}

    def test_annotated_unwrapped(self):
        assert typed_merge(typing.Annotated[int, "port"], "5") == 5
        assert typed_merge(
            typing.Dict[str, typing.Annotated[int, "port"]], {"a": "5"}
        ) == {"a": 5}
        assert typed_merge(typing.Optional[typing.Annotated[int, "port"]], "5") == 5

    def test_any_keeps_the_last_value(self):
        assert typed_merge(typing.Any, {"a": 1}, "last") == "last"
        assert typed_merge(typing.Optional[typing.Any], 1, "last") == "last"
        assert typed_merge(typing.List[typing.Any], [1, "a", {"b": 2}]) == [
            1,
            "a",
            {"b": 2},
        ]
        assert typed_merge(
            typing.Dict[str, typing.Any], {"a": {"x": 1}}, {"a": "flat"}
        ) == {"a": "flat"}

    def test_any_field(self):
        merged = typed_merge(AnyField, AnyField(extra={"a": 1}), AnyField(extra=[1]))
        assert merged.extra == [1]

    def test_str_hint_is_not_a_sequence(self):
        assert typed_merge(str, "hello") == "hello"
        assert typed_merge(typing.Optional[str], "a", "b") == "b"


class TestNonClassHints:
    def test_factory_function_coerces_the_last_value(self):
        assert typed_merge(ip_factory, "10.0.0.0/8", "192.168.0.0/16") == (
            "net:192.168.0.0/16"
        )

    def test_factory_rejecting_the_value_falls_back_to_it(self):
        def strict(value):
            if not isinstance(value, str):
                raise TypeError("need a str")
            return value.upper()

        assert typed_merge(strict, 1, 2) == 2

    def test_non_callable_hint_last_wins(self):
        assert typed_merge(object(), 1, 2) == 2

    def test_factory_field_hint_on_a_namespace(self):
        merged = typed_merge(
            NetConfig, NetConfig(network="10.0.0.0/8"), NetConfig(network="1.0.0.0/8")
        )
        assert merged.network == "net:1.0.0.0/8"


class TestGenerics:
    def test_mapping_values_coerced(self):
        result = typed_merge(typing.Dict[str, int], {"a": "1"}, {"a": "2", "b": "3"})
        assert result == {"a": 2, "b": 3}
        assert all(type(v) is int for v in result.values())

    def test_mapping_keys_coerced_while_collecting(self):
        merged = typed_merge(typing.Dict[int, str], {"80": "http"}, {80: "web"})
        assert merged == {80: "web"}
        assert list(merged) == [80]

    def test_non_string_keys(self):
        assert typed_merge(dict, {80: "http"}, {443: "https"}) == {
            80: "http",
            443: "https",
        }

    def test_dataclass_int_keyed_field(self):
        merged = typed_merge(
            IntKeyed, IntKeyed(ports={"80": "http"}), IntKeyed(ports={"443": "tls"})
        )
        assert merged.ports == {80: "http", 443: "tls"}

    def test_list_items_coerced_last_layer_wins(self):
        assert typed_merge(typing.List[str], [1, 2]) == ["1", "2"]
        assert typed_merge(typing.List[str], [1, 2, 3], [9]) == ["9"]

    def test_bare_list_keeps_item_types(self):
        assert typed_merge(list, [1, "a"]) == [1, "a"]
        merged = typed_merge(BareList, BareList(items=[1, 2]), BareList(items=[3]))
        assert merged.items == [3]

    def test_list_and_dict_fields(self):
        merged = typed_merge(Ports, Ports(ports=["1"]), Ports(ports=["8080", "443"]))
        assert merged.ports == [8080, 443]
        merged = typed_merge(Limits, Limits(limits={"a": "1"}), {"limits": {"b": "2"}})
        assert merged.limits == {"a": 1, "b": 2}

    def test_optional_generics(self):
        assert typed_merge(
            typing.Optional[typing.Dict[str, int]], {"a": 1}, {"b": "2"}
        ) == {"a": 1, "b": 2}
        assert typed_merge(typing.Optional[typing.List[str]], [1, 2]) == ["1", "2"]

    def test_homogeneous_tuple(self):
        result = typed_merge(typing.Tuple[str, ...], (1, 2))
        assert result == ("1", "2") and type(result) is tuple
        assert typed_merge(typing.Tuple[int, ...], ("1", "2", "3")) == (1, 2, 3)

    def test_heterogeneous_tuple_coerces_by_position(self):
        assert typed_merge(typing.Tuple[int, str], ("1", 2)) == (1, "2")

    @pytest.mark.parametrize("value", [("1", "2", "3"), ("1",)])
    def test_heterogeneous_tuple_length_mismatch(self, value):
        with pytest.raises(ConfigTypeError):
            typed_merge(typing.Tuple[int, str], value)

    def test_empty_tuple_hint(self):
        assert typed_merge(typing.Tuple[()], ()) == ()

    def test_namedtuple_rebuilt_with_field_types(self):
        merged = typed_merge(Point, ("1", "2"))
        assert merged == Point(1, 2) and type(merged) is Point
        assert typed_merge(typing.List[Point], [("1", "2")]) == [Point(1, 2)]

    @pytest.mark.parametrize("value", [("1", "2", "3"), ("1",)])
    def test_namedtuple_arity_enforced(self, value):
        # A surplus item must not be silently dropped.
        with pytest.raises(TypeError):
            typed_merge(Point, value)

    def test_namedtuples_and_ranges_kept_in_a_bare_list(self):
        points = [Point(1, 2), Point(3, 4)]
        assert typed_merge(list, points) == points
        assert typed_merge(list, [range(3)]) == [range(3)]
        r = range(5)
        assert typed_merge(range, r) is r

    def test_abstract_sequence_hints(self):
        assert typed_merge(typing.Sequence[str], [1, 2]) == ["1", "2"]
        assert typed_merge(typing.MutableSequence[int], ["1", "2"]) == [1, 2]
        # An abstract hint keeps the value's own concrete type.
        assert typed_merge(typing.Sequence[int], ("1", "2")) == (1, 2)

    def test_sequence_field(self):
        assert typed_merge(Hosts, Hosts(hosts=["a"]), Hosts(hosts=[1, 2])).hosts == [
            "1",
            "2",
        ]
        assert typed_merge(Hosts, {"hosts": ["a"]}, {"hosts": [1]}).hosts == ["1"]

    @pytest.mark.parametrize(
        "hint, value",
        [
            (typing.List[str], "abc"),
            (typing.List[str], {"a": 1}),
            (typing.List[int], 5),
        ],
        ids=["str", "mapping", "scalar"],
    )
    def test_sequence_hint_refuses_non_sequences(self, hint, value):
        with pytest.raises(ConfigTypeError):
            typed_merge(hint, value)

    def test_unconstructible_sequence_falls_back_to_the_value(self):
        pair = Pair("1", "2")
        assert typed_merge(Pair, pair) is pair

    def test_bytearray_from_bytes(self):
        assert typed_merge(bytearray, b"ab") == bytearray(b"ab")

    def test_abstract_mapping_builds_a_dict(self):
        merged = typed_merge(typing.Mapping[str, int], {"a": "1"}, {"b": "2"})
        assert merged == {"a": 1, "b": 2} and type(merged) is dict

    def test_concrete_mapping_types_kept(self):
        merged = typed_merge(
            collections.OrderedDict, collections.OrderedDict(a=1), {"b": 2}
        )
        assert type(merged) is collections.OrderedDict
        assert merged == {"a": 1, "b": 2}
        kw = typed_merge(KwOnlyDict, {"a": 1}, {"b": 2})
        assert type(kw) is KwOnlyDict and kw == {"a": 1, "b": 2}

    def test_defaultdict_keeps_its_factory(self):
        source = collections.defaultdict(list, {"a": [1]})
        merged = typed_merge(collections.defaultdict, source, {"b": [2]})
        assert type(merged) is collections.defaultdict
        assert merged.default_factory is list
        assert merged == {"a": [1], "b": [2]}
        assert merged["fresh"] == []

    def test_typeddict(self):
        class TD(typing.TypedDict):
            host: str
            port: int

        assert typed_merge(TD, {"host": "h", "port": "1"}, {"port": "2"}) == {
            "host": "h",
            "port": 2,
        }


class TestScalarCoercion:
    @pytest.mark.parametrize("word", ["true", "TRUE", " yes ", "On", "1"])
    def test_bool_true_words(self, word):
        assert typed_merge(bool, word) is True

    @pytest.mark.parametrize("word", ["false", "FALSE", " no ", "Off", "0"])
    def test_bool_false_words(self, word):
        assert typed_merge(bool, word) is False

    def test_bool_field_from_text(self):
        assert hydrate(Flags, {"debug": "false"}).debug is False
        assert hydrate(Flags, {"debug": 1}).debug is True

    def test_bool_refuses_other_words(self):
        with pytest.raises(ConfigValueError):
            typed_merge(bool, "maybe")

    def test_bool_refuses_other_numbers(self):
        with pytest.raises(ConfigTypeError):
            typed_merge(bool, 2)

    def test_int_takes_integer_strings_and_whole_floats(self):
        assert typed_merge(int, "80") == 80
        assert typed_merge(int, " 80 ") == 80
        result = typed_merge(int, 2.0)
        assert result == 2 and type(result) is int

    @pytest.mark.parametrize("value", [1.9, "1.5", "0x10", "eighty"])
    def test_int_never_loses_data(self, value):
        with pytest.raises(ConfigValueError):
            typed_merge(int, value)

    def test_float_from_text(self):
        assert typed_merge(float, "1.5") == 1.5
        with pytest.raises(ConfigValueError):
            typed_merge(float, "fast")

    def test_iso_dates_and_times(self):
        merged = hydrate(
            Schedule,
            {
                "day": "2024-01-02",
                "at": "10:20",
                "when": "2024-01-02T03:04:05",
                "colour": "red",
            },
        )
        assert merged.day == datetime.date(2024, 1, 2)
        assert merged.at == datetime.time(10, 20)
        assert merged.when == datetime.datetime(2024, 1, 2, 3, 4, 5)
        assert merged.colour is Colour.RED

    def test_dates_already_typed_are_kept(self):
        day = datetime.date(2024, 1, 2)
        assert typed_merge(datetime.date, day) is day

    @pytest.mark.parametrize("hint", [datetime.date, datetime.time, datetime.datetime])
    def test_bad_iso_text(self, hint):
        with pytest.raises(ConfigValueError):
            typed_merge(hint, "not a date")

    def test_enum_by_value_then_name(self):
        assert typed_merge(Colour, "blue") is Colour.BLUE
        assert typed_merge(Colour, "BLUE") is Colour.BLUE
        assert typed_merge(Level, 2) is Level.HIGH
        assert typed_merge(Level, "LOW") is Level.LOW

    def test_enum_unknown(self):
        with pytest.raises(ValueError):
            typed_merge(Colour, "green")

    def test_path(self):
        result = typed_merge(pathlib.Path, "a/b")
        assert result == pathlib.Path("a/b") and isinstance(result, pathlib.Path)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


class TestHooks:
    def test_merge_hook_takes_over(self):
        Recorder.seen = None
        r = Recorder()
        assert typed_merge(Recorder, r, None) is r
        # None layers never reach the hook.
        assert Recorder.seen == (r,)

    def test_merge_hook_found_through_optional_and_generic_alias(self):
        assert typed_merge(typing.Optional[Box], 1, 2) == ("box", 1, 2)
        assert typed_merge(Box[int], 1, 2) == ("box", 1, 2)

    def test_opaque_mixin_last_wins(self):
        merged = typed_merge(Zone, Zone(x=1), Zone(x=2))
        assert merged.x == 2

    def test_opaque_decorator_last_wins(self):
        @opaque
        class Area(Namespace):
            pass

        second = Area(x="z")
        assert typed_merge(Area, Area(x="a"), second) is second

    def test_opaque_ignores_field_hints(self):
        class Net(OpaqueMerge, Namespace):
            network: ip_factory

        merged = typed_merge(Net, Net(network="a"), Net(network="b"))
        assert merged.network == "b"

    def test_opaque_inside_optional(self):
        z1, z2 = Zone(x="a"), Zone(x="z")
        assert typed_merge(typing.Optional[Zone], z1, z2) is z2
        assert typed_merge(OptionalZone, OptionalZone(z1), OptionalZone(z2)).zone is z2

    def test_typed_namespace_parses_at_construction(self):
        class Cfg(TypedNamespace):
            def _parse_port(self, value):
                return int(value)

        cfg = Cfg(port="8080", host="db")
        assert (cfg.port, cfg.host) == (8080, "db")

    def test_typed_namespace_parses_each_value_once(self):
        merged = typed_merge(HostsNS, HostsNS(hosts="a,b"))
        assert merged.hosts == ["a", "b"] and isinstance(merged, HostsNS)

    def test_typed_namespace_mixed_layers_parse_once(self):
        assert typed_merge(HostsNS, HostsNS(hosts="a,b"), {"hosts": "c,d"}).hosts == [
            "c",
            "d",
        ]
        assert typed_merge(HostsNS, {"hosts": "c,d"}).hosts == ["c", "d"]
        assert hydrate(HostsNS, {"hosts": "e"}).hosts == ["e"]

    def test_parse_hook_on_a_layer_object(self):
        class Source(Namespace):
            def _parse_port(self, value):
                return int(value)

        assert typed_merge(Namespace, Source(port="8080")).port == 8080

    def test_parse_hook_not_read_from_a_mapping_layer(self):
        class Weird(dict):
            def _parse_port(self, value):
                raise AssertionError("a mapping's attributes are not hooks")

        assert typed_merge(dict, Weird(port="1")) == {"port": "1"}


# ---------------------------------------------------------------------------
# Error context
# ---------------------------------------------------------------------------


class TestErrorContext:
    def test_field_coercion_error_names_the_field_path(self):
        with pytest.raises(ConfigValueError) as caught:
            hydrate(Outer, {"db": {"port": "abc"}})
        error = caught.value
        assert error.config_key == ("db", "port")
        assert error.config_model == "Outer"
        assert "at db.port (Outer)" in str(error)

    def test_sequence_item_error_names_the_index(self):
        with pytest.raises(ConfigValueError) as caught:
            hydrate(Outer, {"db": {"port": 1}, "hosts": [{"port": 1}, {"port": "x"}]})
        assert caught.value.config_key == ("hosts", 1, "port")
        assert "hosts[1].port" in str(caught.value)

    def test_construction_error_names_the_model(self):
        with pytest.raises(TypeError) as caught:
            typed_merge(Inner, {"prot": 1})
        # The parenthesized form is the added context; 3.10+ already names
        # "Inner.__init__()" in its own message.
        assert "(Inner)" in str(caught.value)
        assert caught.value.config_model == "Inner"

    def test_own_refusals_are_config_errors(self):
        with pytest.raises(ConfigTypeError) as caught:
            typed_merge(Ports, {"ports": "80"})
        assert isinstance(caught.value, TypeError)
        assert caught.value.config_key == ("ports",)

    def test_outermost_model_is_named(self):
        with pytest.raises(ConfigValueError) as caught:
            hydrate(App, {"db": {"port": "x"}})
        assert caught.value.config_model == "App"
        assert caught.value.config_key == ("db", "port")

    def test_error_through_load_into(self):
        with pytest.raises(ConfigValueError) as caught:
            Loader().load({"db": {"port": "x"}}, into=Outer)
        assert caught.value.config_key == ("db", "port")
        assert caught.value.config_model == "Outer"
