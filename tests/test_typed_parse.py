"""A target type steers how scalars are read, and types convert through hooks."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import pathlib
import pickle
import typing

import pytest

import yaconfiglib
from yaconfiglib import Loader, dehydrate, hydrate
from yaconfiglib._scalar import ScalarText, resolved
from yaconfiglib.sources import Text

pytestmark = pytest.mark.usefixtures("needs_yaml")


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Port:
    """A type that builds itself from configuration and writes itself back."""

    def __init__(self, number: int) -> None:
        self.number = number

    @classmethod
    def _load_(cls, value: object) -> "Port":
        return cls(int(value))

    def _dump_(self) -> int:
        return self.number

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Port) and other.number == self.number


@dataclasses.dataclass
class App:
    country: str
    version: str
    mode: str
    debug: bool
    when: datetime.date
    port: Port
    color: Color
    ratio: decimal.Decimal
    extra: dict
    anything: typing.Any = None
    either: typing.Union[int, str] = 0


class Version:
    seen: list = []

    def __init__(self, text: str) -> None:
        self.text = text

    @classmethod
    def _load_(cls, value: object) -> "Version":
        cls.seen.append(value)
        return cls(value)


@dataclasses.dataclass
class Pkg:
    version: Version


class Endpoint:
    @classmethod
    def _load_(cls, value: object) -> object:
        return ("endpoint", value)


@dataclasses.dataclass
class EndpointConf:
    api: Endpoint


@dataclasses.dataclass
class Sub:
    version: str


@dataclasses.dataclass
class Top:
    country: str
    sub: Sub


@dataclasses.dataclass
class Conf:
    port: int
    url: str


@dataclasses.dataclass
class OptionalPort:
    port: typing.Optional[Port] = None


@dataclasses.dataclass
class Holder:
    table: dict


class Settings(typing.TypedDict):
    port: int
    name: str


DOC = (
    "country: NO\n"
    "version: 1.10\n"
    "mode: 0755\n"
    "debug: yes\n"
    "when: 2024-01-02\n"
    "port: '8080'\n"
    "color: RED\n"
    "ratio: 0.10\n"
    "extra: {flag: yes, n: 1.10}\n"
    "anything: on\n"
    "either: 7\n"
)


class TestTypeSteersScalars:
    def test_str_fields_keep_the_text_as_written(self):
        app = yaconfiglib.loads(DOC, format="yaml", into=App)
        assert app.country == "NO"
        assert app.version == "1.10"
        assert app.mode == "0755"
        assert type(app.country) is str

    def test_typed_fields_use_the_read_value_or_parse_the_text(self):
        app = yaconfiglib.loads(DOC, format="yaml", into=App)
        assert app.debug is True
        assert app.when == datetime.date(2024, 1, 2)
        assert app.color is Color.RED
        assert app.ratio == decimal.Decimal("0.10")

    def test_untyped_values_are_what_the_format_read(self):
        app = yaconfiglib.loads(DOC, format="yaml", into=App)
        assert app.extra == {"flag": True, "n": 1.1}
        assert app.anything is True
        assert not any(isinstance(v, ScalarText) for v in app.extra.values())

    def test_a_union_picks_by_the_read_value(self):
        app = yaconfiglib.loads(DOC, format="yaml", into=App)
        assert app.either == 7 and type(app.either) is int

    def test_without_into_values_are_what_the_format_read(self):
        data = yaconfiglib.loads(DOC, format="yaml")
        assert data["country"] is False
        assert data["version"] == 1.1
        assert not isinstance(data["version"], ScalarText)

    def test_keys_are_never_text_kept(self):

        holder = yaconfiglib.loads(
            "table: {2: a, yes: b}\n", format="yaml", into=Holder
        )
        assert set(holder.table) == {2, True}
        assert not any(isinstance(key, ScalarText) for key in holder.table)

    def test_duplicate_keys_compare_as_read(self):
        # `yes` and `true` are one key to YAML, kept text or not.
        for into in (None, Holder):
            with pytest.raises(yaconfiglib.ParseError, match="duplicate key"):
                yaconfiglib.loads(
                    "table: {yes: 1, true: 2}\n", format="yaml", into=into
                )

    def test_loader_into_and_includes(self, tmp_path):
        (tmp_path / "inc.yaml").write_text("version: 2.10\n", encoding="utf-8")
        (tmp_path / "app.yaml").write_text(
            "country: NO\nsub: !include inc.yaml\n", encoding="utf-8"
        )

        top = Loader(base_dir=tmp_path).load("app.yaml", into=Top)
        assert top == Top(country="NO", sub=Sub(version="2.10"))

    def test_typed_dict_target(self):

        result = yaconfiglib.loads(
            "port: '80'\nname: NO\n", format="yaml", into=Settings
        )
        assert result == {"port": 80, "name": "NO"}

    def test_interpolation_sees_read_values(self):

        conf = Loader(interpolate=True).load(
            Text("port: 80\nurl: 'x:{{ port + 1 }}'\n", format="yaml"), into=Conf
        )
        assert conf == Conf(port=80, url="x:81")


class TestHooks:
    def test_load_hook_gets_the_text_written(self):
        Version.seen.clear()
        pkg = yaconfiglib.loads("version: 1.10\n", format="yaml", into=Pkg)
        assert Version.seen == ["1.10"] and type(Version.seen[0]) is str
        assert pkg.version.text == "1.10"

    def test_load_hook_gets_plain_data_for_a_mapping(self):
        conf = yaconfiglib.loads(
            "api: {host: h, tls: yes}\n", format="yaml", into=EndpointConf
        )
        assert conf.api == ("endpoint", {"host": "h", "tls": True})

    def test_load_hook_layers_merge_first(self):
        merged = yaconfiglib.typed_merge(Endpoint, {"a": 1}, {"b": 2})
        assert merged == ("endpoint", {"a": 1, "b": 2})

    def test_load_hook_through_optional(self):

        assert hydrate(OptionalPort, {"port": "81"}).port == Port(81)

    def test_dump_hook_and_round_trip(self, tmp_path):
        app = yaconfiglib.loads(DOC, format="yaml", into=App)
        target = tmp_path / "out.yaml"
        yaconfiglib.dump(app, str(target))
        again = yaconfiglib.load(str(target), into=App)
        assert again == app

    def test_dumps_dataclass_as_json(self):
        app = yaconfiglib.loads(DOC, format="yaml", into=App)
        text = yaconfiglib.dumps(
            dataclasses.replace(app, when=None, ratio=None), format="json"
        )
        assert '"port": 8080' in text and '"color": "red"' in text


class TestDehydrate:
    def test_plain_data_is_unchanged(self):
        data = {"a": [1, "x", None, True]}
        assert dehydrate(data) == data

    def test_types_become_plain(self):
        Point = typing.NamedTuple("Point", [("x", int), ("y", int)])
        out = dehydrate(
            {
                "color": Color.BLUE,
                "path": pathlib.PurePosixPath("/a/b"),
                "point": Point(1, 2),
                "port": Port(9),
            }
        )
        assert out == {
            "color": "blue",
            "path": "/a/b",
            "point": {"x": 1, "y": 2},
            "port": 9,
        }

    def test_cycles_and_aliases_are_kept(self):
        shared = [1]
        doc = {"a": shared, "b": shared}
        doc["self"] = doc
        out = dehydrate(doc)
        assert out["a"] is out["b"]
        assert out["self"] is out


class TestScalarText:
    def test_is_a_str_with_its_value(self):
        text = ScalarText("NO", False)
        assert text == "NO" and text.value is False

    def test_pickles(self):
        text = pickle.loads(pickle.dumps(ScalarText("1.10", 1.1)))
        assert text == "1.10" and text.value == 1.1

    def test_resolved_replaces_every_one(self):
        doc = {"a": [ScalarText("1", 1)], "b": ScalarText("NO", False), "c": "x"}
        assert resolved(doc) == {"a": [1], "b": False, "c": "x"}
        plain = {"a": [1]}
        assert resolved(plain) is plain
