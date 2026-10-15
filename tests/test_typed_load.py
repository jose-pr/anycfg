"""Loading ``into=`` a type: the type steers scalars, and nothing else changes.

YAML keeps a plain scalar's text beside its reading while a load builds a type.
That text is for the typed build only: merging, includes, ``transform`` and
duplicate-key checks must see what the format read, exactly as without
``into=``.
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
import typing

import pytest

import yaconfiglib
from yaconfiglib import Loader
from yaconfiglib._scalar import ScalarText
from yaconfiglib.sources import Text

pytestmark = pytest.mark.usefixtures("needs_yaml")


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


class Mode(enum.Enum):
    FAST = 1
    SAFE = 2


Port = typing.NewType("Port", int)


class Point(typing.NamedTuple):
    host: str
    port: int


@dataclasses.dataclass
class Levels:
    level: Level
    mode: Mode = Mode.FAST


@dataclasses.dataclass
class Numbers:
    count: int = 0
    ratio: float = 0.0
    exact: decimal.Decimal = decimal.Decimal(0)


@dataclasses.dataclass
class Either:
    value: typing.Union[int, str]


@dataclasses.dataclass
class Kinds:
    port: Port
    size: typing.Literal[1, 2]
    tags: typing.Set[int]
    anything: object


@dataclasses.dataclass
class Located:
    at: Point


@dataclasses.dataclass
class Ports:
    ports: typing.List[int]


@dataclasses.dataclass
class Holder:
    data: dict


class TestReadValueFirst:
    def test_int_enum_from_a_yaml_integer(self):
        assert yaconfiglib.loads("level: 2\nmode: 2\n", format="yaml", into=Levels) == (
            Levels(Level.HIGH, Mode.SAFE)
        )

    def test_enum_round_trip(self, tmp_path):
        target = tmp_path / "e.yaml"
        yaconfiglib.dump(Levels(Level.LOW, Mode.SAFE), str(target))
        assert yaconfiglib.load(str(target), into=Levels) == Levels(
            Level.LOW, Mode.SAFE
        )

    def test_numbers_take_the_read_value(self):
        numbers = yaconfiglib.loads(
            "count: 1.0\nratio: 0x10\nexact: 0x10\n", format="yaml", into=Numbers
        )
        assert numbers == Numbers(1, 16.0, decimal.Decimal(16))
        assert type(numbers.count) is int

    def test_decimal_keeps_the_text(self):
        numbers = yaconfiglib.loads("exact: 0.10\n", format="yaml", into=Numbers)
        assert str(numbers.exact) == "0.10"

    def test_a_flag_is_not_a_number(self):
        with pytest.raises(yaconfiglib.ConfigValueError, match="'yes'.*int"):
            yaconfiglib.loads("count: yes\n", format="yaml", into=Numbers)

    def test_union_falls_back_to_the_text(self):
        assert yaconfiglib.loads("value: yes\n", format="yaml", into=Either) == (
            Either("yes")
        )
        assert yaconfiglib.loads("value: 80\n", format="yaml", into=Either) == (
            Either(80)
        )

    def test_newtype_literal_set_and_object(self):
        kinds = yaconfiglib.loads(
            "port: 80\nsize: 2\ntags: [1, 2]\nanything: [yes, 1.10]\n",
            format="yaml",
            into=Kinds,
        )
        assert kinds == Kinds(80, 2, {1, 2}, [True, 1.1])
        assert type(kinds.port) is int and type(kinds.size) is int
        assert 1 in kinds.tags
        assert not any(isinstance(item, ScalarText) for item in kinds.anything)

    def test_literal_refuses_another_value(self):
        with pytest.raises(yaconfiglib.ConfigValueError, match="not one of"):
            yaconfiglib.loads(
                "port: 1\nsize: 3\ntags: []\nanything: x\n", format="yaml", into=Kinds
            )

    def test_named_tuple_round_trip(self, tmp_path):
        target = tmp_path / "p.yaml"
        yaconfiglib.dump(Located(Point("h", 1)), str(target))
        assert yaconfiglib.load(str(target), into=Located) == Located(Point("h", 1))


class TestIntoChangesNothingElse:
    def test_deep_merge_deduplicates_by_the_read_value(self, tmp_path):
        (tmp_path / "base.yaml").write_text("ports: [80, 443]\n", encoding="utf-8")
        (tmp_path / "over.json").write_text('{"ports": [80, 8080]}', encoding="utf-8")
        loader = Loader(base_dir=tmp_path, merge="deep")
        plain = loader.load("base.yaml", "over.json")
        typed = loader.load("base.yaml", "over.json", into=Ports)
        assert plain["ports"] == typed.ports == [80, 443, 8080]

    def test_include_options_are_the_read_values(self, tmp_path):
        (tmp_path / "x.yaml").write_text("k: 1\n", encoding="utf-8")
        # Two levels down: a non-recursive `**` matches one directory only.
        (tmp_path / "sub" / "deep").mkdir(parents=True)
        (tmp_path / "sub" / "deep" / "z.yaml").write_text("z: 1\n", encoding="utf-8")
        (tmp_path / "app.yaml").write_text(
            "data:\n"
            "  a: !include {pathname: x.yaml, flatten: false}\n"
            "  b: !include {pathname: '**/z.yaml', recursive: false, default: 0}\n",
            encoding="utf-8",
        )
        loader = Loader(base_dir=tmp_path)
        plain = loader.load("app.yaml")
        typed = loader.load("app.yaml", into=Holder)
        assert plain["data"] == typed.data == {"a": {"k": 1}, "b": 0}

    def test_transform_sees_the_read_values(self):
        seen = []

        def transform(document, name):
            seen.append(document)
            return {"n": document["n"] + 1}

        result = Loader().load(
            Text("n: 1\n", format="yaml"), transform=transform, into=dict
        )
        assert result == {"n": 2}
        assert not isinstance(seen[0]["n"], ScalarText)

    def test_a_custom_merge_sees_the_read_values(self):
        seen = []

        def merge(a, b, *, memo=None, **options):
            seen.extend([a, b])
            return {**a, **b}

        Loader(merge=merge).load(
            Text("a: 1\n", format="yaml"), Text("b: yes\n", format="yaml"), into=dict
        )
        assert not any(
            isinstance(value, ScalarText) for doc in seen for value in doc.values()
        )


class TestPydantic:
    """Real Pydantic, when installed (CI has a job for it)."""

    @pytest.fixture
    def pydantic(self):
        return pytest.importorskip("pydantic")

    def test_text_for_str_fields_and_readings_elsewhere(self, pydantic):
        class Model(pydantic.BaseModel):
            name: str
            port: int
            meta: dict
            anything: typing.Any = None
            flags: typing.List[typing.Any] = []

        model = yaconfiglib.loads(
            "name: NO\nport: 0x10\nmeta: {count: 3}\nanything: 12\nflags: [yes, 2]\n",
            format="yaml",
            into=Model,
        )
        assert model.name == "NO"
        assert model.port == 16
        assert model.meta == {"count": 3} and type(model.meta["count"]) is int
        assert model.anything == 12 and type(model.anything) is int
        assert model.flags[0] is True

    def test_strict_model(self, pydantic):
        config = getattr(pydantic, "ConfigDict", None)
        if config is None:
            pytest.skip("pydantic 1 has no ConfigDict")

        class Strict(pydantic.BaseModel):
            model_config = config(strict=True)
            port: int
            debug: bool

        model = yaconfiglib.loads("port: 80\ndebug: yes\n", format="yaml", into=Strict)
        assert (model.port, model.debug) == (80, True)
