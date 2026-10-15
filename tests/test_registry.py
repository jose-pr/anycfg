"""The format registry (`yaconfiglib.formats`) and the scheme registry (`yaconfiglib.sources`).

Both registries are process-global. Every test that changes one goes through a
fixture that snapshots the registry's state and restores it afterwards, so the
tests do not depend on their order.
"""

from __future__ import annotations

import logging
import sys
import textwrap

import pytest

import yaconfiglib
from yaconfiglib import formats, sources
from yaconfiglib.formats import Format

BUILTIN_FORMATS = ["dotenv", "ini", "json", "toml", "yaml"]


# -- fixtures ------------------------------------------------------------------


@pytest.fixture
def format_registry():
    """`yaconfiglib.formats`, restored to its state before the test."""
    formats.names()  # load the built-ins and entry points before the snapshot
    registry = sys.modules["yaconfiglib.formats"]
    with registry._lock:
        # The tables are replaced whole on every change, so keeping the
        # reference is a snapshot.
        saved = (registry._tables, dict(registry._state))
    try:
        yield formats
    finally:
        with registry._lock:
            registry._tables = saved[0]
            registry._state.clear()
            registry._state.update(saved[1])


@pytest.fixture
def scheme_registry():
    """`yaconfiglib.sources`, restored to its state before the test."""
    sources.schemes()
    registry = sys.modules["yaconfiglib.sources"]
    with registry._lock:
        saved = (dict(registry._schemes), dict(registry._state))
    try:
        yield sources
    finally:
        with registry._lock:
            registry._schemes.clear()
            registry._schemes.update(saved[0])
            registry._state.clear()
            registry._state.update(saved[1])


@pytest.fixture
def install_plugin(tmp_path, monkeypatch):
    """Install a distribution with entry points: a ``.dist-info`` on `sys.path`.

    ``install_plugin(group, {name: "module:attr"}, {module: source})``.
    """
    root = tmp_path / "site-packages"
    root.mkdir()
    added = []

    def install(group, entries, modules):
        for module, source in modules.items():
            (root / f"{module}.py").write_text(
                textwrap.dedent(source), encoding="utf-8"
            )
            added.append(module)
        dist = root / "yaconfiglib_test_plugin-1.0.dist-info"
        dist.mkdir(exist_ok=True)
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: yaconfiglib-test-plugin\nVersion: 1.0\n",
            encoding="utf-8",
        )
        lines = [f"[{group}]"] + [
            f"{name} = {target}" for name, target in entries.items()
        ]
        with open(dist / "entry_points.txt", "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        monkeypatch.syspath_prepend(str(root))

    yield install
    for module in added:
        sys.modules.pop(module, None)


def _reload_format_entry_points():
    sys.modules["yaconfiglib.formats"]._state["entry_points"] = False
    formats.names()


def _reload_scheme_entry_points():
    registry = sys.modules["yaconfiglib.sources"]
    registry._schemes.clear()
    registry._state["loaded"] = False
    sources.schemes()


def _setup_log(caplog):
    """What was logged while the fixtures read the entry points."""
    return "\n".join(record.getMessage() for record in caplog.get_records("setup"))


def _format(name, suffixes=(), *, aliases=(), priority=0, **attributes):
    """A minimal `Format` subclass that returns what it was given."""

    def loads(self, data, /, *, context=None, **options):
        return {"format": name, "data": data, "options": options}

    namespace = {
        "name": name,
        "suffixes": tuple(suffixes),
        "aliases": tuple(aliases),
        "priority": priority,
        "loads": loads,
    }
    namespace.update(attributes)
    return type(f"Fmt_{name}_{priority}", (Format,), namespace)


# -- the format registry: lookup -----------------------------------------------


class TestFormatLookup:
    def test_builtin_names_are_listed_sorted(self):
        listed = formats.names()
        assert listed == sorted(listed)
        assert set(BUILTIN_FORMATS) <= set(listed)

    @pytest.mark.parametrize(
        "asked, expected",
        [
            ("json", "json"),
            ("JSON", "json"),
            ("yml", "yaml"),
            ("YaMl", "yaml"),
            ("cfg", "ini"),
            ("Dotenv", "dotenv"),
        ],
    )
    def test_get_takes_aliases_case_insensitively(self, asked, expected):
        assert formats.get(asked).name == expected

    def test_get_unknown_lists_the_names(self):
        with pytest.raises(yaconfiglib.UnsupportedFormatError) as caught:
            formats.get("jsn")
        message = str(caught.value)
        assert "jsn" in message
        for name in BUILTIN_FORMATS:
            assert name in message
        assert isinstance(caught.value, ValueError)

    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("a.json", "json"),
            ("A.JSON", "json"),
            ("a.yml", "yaml"),
            ("a.yaml", "yaml"),
            ("a.toml", "toml"),
            ("a.ini", "ini"),
            ("a.cfg", "ini"),
            ("a.env", "dotenv"),
            (".env", "dotenv"),
            (".env.production", "dotenv"),
            ("app.env.yaml", "yaml"),
            (".env.local.json", "json"),
            ("dir.yaml/a.json", "json"),
            ("dir.yaml\\a.json", "json"),
            ("/abs/conf.d/10-base.toml", "toml"),
        ],
    )
    def test_for_filename(self, filename, expected):
        assert formats.for_filename(filename).name == expected

    @pytest.mark.parametrize(
        "filename", ["Makefile", "a.xyz", "", "dir/", "a.json.bak"]
    )
    def test_nothing_claims(self, filename):
        assert formats.find_for_filename(filename) is None

    def test_for_filename_raises_naming_the_registered_formats(self):
        with pytest.raises(yaconfiglib.UnsupportedFormatError) as caught:
            formats.for_filename("plain.xyz")
        message = str(caught.value)
        assert "plain.xyz" in message and "format=" in message
        assert "json" in message and "yaml" in message

    def test_load_of_unclaimed_name_raises_before_opening(self, tmp_path):
        missing = tmp_path / "nope.xyz"
        with pytest.raises(yaconfiglib.UnsupportedFormatError):
            yaconfiglib.load(str(missing))

    def test_lookup_is_linear_in_the_name(self):
        name = "a" + ".x" * 50000 + ".json"
        assert formats.for_filename(name).name == "json"

    def test_dump_selects_by_target_name(self, tmp_path):
        target = tmp_path / "out.JSON"
        yaconfiglib.dump({"a": 1}, str(target))
        assert yaconfiglib.load(str(target)) == {"a": 1}
        with pytest.raises(yaconfiglib.UnsupportedFormatError):
            yaconfiglib.dump({"a": 1}, str(tmp_path / "out.xyz"))


class TestCapabilities:
    def test_builtins(self):
        assert formats.capabilities(formats.get("yaml")) == {"load", "load_all", "dump"}
        for name in ("json", "toml", "ini", "dotenv"):
            assert formats.capabilities(formats.get(name)) == {"load", "dump"}

    def test_follows_overrides_on_class_or_instance(self):
        reader = _format("zzreader")

        class Writer(reader):
            def dumps(self, obj, /, **options):
                return repr(obj)

        class Streams(Writer):
            def loads_all(self, data, /, *, context=None, **options):
                yield from ()

        assert formats.capabilities(reader) == {"load"}
        assert formats.capabilities(reader()) == {"load"}
        assert formats.capabilities(Writer) == {"load", "dump"}
        assert formats.capabilities(Streams()) == {"load", "load_all", "dump"}

    def test_dumps_with_a_reader_only_format(self):
        with pytest.raises(yaconfiglib.UnsupportedFormatError, match="cannot write"):
            yaconfiglib.dumps({"a": 1}, format=_format("zzreader"))


# -- the format registry: registration -----------------------------------------


class TestRegisterFormat:
    def test_as_decorator(self, format_registry):
        @format_registry.register_format
        class Decorated(Format):
            name = "zzdeco"
            suffixes = (".zzdeco",)

            def loads(self, data, /, *, context=None, **options):
                return data

        assert format_registry.get("zzdeco") is Decorated
        assert format_registry.for_filename("a.ZZDECO") is Decorated

    def test_as_decorator_with_arguments(self, format_registry):
        @format_registry.register_format(replace=False)
        class Decorated(Format):
            name = "zzdeco2"

            def loads(self, data, /, *, context=None, **options):
                return data

        assert format_registry.get("zzdeco2") is Decorated

    def test_registered_format_is_used_by_load_and_documents(
        self, format_registry, tmp_path
    ):
        cls = format_registry.register_format(
            _format("zzfmt", [".zz"], aliases=["zzalias"])
        )
        assert "zzfmt" in format_registry.names()
        assert "zzalias" not in format_registry.names()
        assert "zzfmt" in format_registry.document_formats()
        assert format_registry.get("ZZALIAS") is cls
        source = tmp_path / "c.zz"
        source.write_text("hello", encoding="utf-8")
        assert yaconfiglib.load(str(source))["data"] == "hello"
        assert yaconfiglib.loads("x", format="zzalias")["format"] == "zzfmt"

    def test_registering_the_same_class_again_is_harmless(self, format_registry):
        cls = _format("zzsame", [".zzsame"])
        format_registry.register_format(cls)
        format_registry.register_format(cls)
        assert format_registry.get("zzsame") is cls

    def test_name_conflict_raises(self, format_registry):
        format_registry.register_format(_format("zzname"))
        with pytest.raises(ValueError, match="zzname"):
            format_registry.register_format(_format("zzname", priority=1))

    @pytest.mark.parametrize("taken", ["json", "yml", "cfg"])
    def test_builtin_name_or_alias_conflict_raises(self, format_registry, taken):
        before = format_registry.get(taken)
        with pytest.raises(ValueError, match="replace=True"):
            format_registry.register_format(_format("zzother", aliases=[taken]))
        assert format_registry.get(taken) is before
        assert "zzother" not in format_registry.names()

    def test_suffix_conflict_at_the_same_priority_raises(self, format_registry):
        with pytest.raises(ValueError, match=r"\.json"):
            format_registry.register_format(_format("zzjson", [".json"]))
        assert format_registry.for_filename("a.json").name == "json"

    def test_higher_priority_takes_a_suffix(self, format_registry):
        high = format_registry.register_format(
            _format("zzhigh", [".json"], priority=10)
        )
        assert format_registry.for_filename("a.json") is high
        assert format_registry.get("json").name == "json"
        format_registry.unregister_format("zzhigh")
        assert format_registry.for_filename("a.json").name == "json"

    def test_lower_priority_does_not_take_a_suffix(self, format_registry):
        format_registry.register_format(_format("zzlow", [".json"], priority=-1))
        assert format_registry.for_filename("a.json").name == "json"
        format_registry.unregister_format("json")
        assert format_registry.for_filename("a.json").name == "zzlow"

    def test_longest_suffix_wins(self, format_registry):
        tar = format_registry.register_format(_format("zztar", [".tar.json"]))
        assert format_registry.for_filename("backup.tar.json") is tar
        assert format_registry.for_filename("BACKUP.TAR.JSON") is tar
        assert format_registry.for_filename("a.json").name == "json"
        assert format_registry.find_for_filename("a.tar") is None

    def test_replace_takes_a_name(self, format_registry):
        first = format_registry.register_format(_format("zzrep", [".zzrep"]))
        second = format_registry.register_format(
            _format("zzrep", [".zzrep2"], priority=3), replace=True
        )
        assert format_registry.get("zzrep") is second
        assert format_registry.for_filename("a.zzrep2") is second
        assert format_registry.find_for_filename("a.zzrep") is None
        assert first not in {format_registry.get(n) for n in format_registry.names()}

    def test_replace_takes_a_suffix_at_the_same_priority(self, format_registry):
        mine = format_registry.register_format(
            _format("zzmine", [".json"]), replace=True
        )
        assert format_registry.for_filename("a.json") is mine
        assert format_registry.get("json").name == "json"

    def test_replace_a_builtin(self, format_registry):
        mine = format_registry.register_format(
            _format("json", [".json"], aliases=["jsonish"]), replace=True
        )
        assert format_registry.get("json") is mine
        assert yaconfiglib.loads("{}", format="json")["format"] == "json"
        assert format_registry.names().count("json") == 1

    @pytest.mark.parametrize(
        "bad, error",
        [
            (object, TypeError),
            (dict, TypeError),
            (42, TypeError),
            (_format(""), ValueError),
            (_format("Upper"), ValueError),
            (_format("zzsfx", ["json"]), ValueError),
            (_format("zzsfx", [".JSON"]), ValueError),
        ],
        ids=[
            "object",
            "dict",
            "int",
            "empty-name",
            "upper-name",
            "no-dot",
            "upper-suffix",
        ],
    )
    def test_invalid_classes_are_refused(self, format_registry, bad, error):
        before = format_registry.names()
        with pytest.raises(error):
            format_registry.register_format(bad)
        assert format_registry.names() == before


class TestUnregisterFormat:
    def test_by_name_and_alias(self, format_registry):
        format_registry.register_format(_format("zzgone", [".zzgone"], aliases=["zzg"]))
        assert format_registry.unregister_format("ZZG") is True
        assert "zzgone" not in format_registry.names()
        assert format_registry.find_for_filename("a.zzgone") is None
        with pytest.raises(yaconfiglib.UnsupportedFormatError):
            format_registry.get("zzg")
        assert format_registry.unregister_format("zzgone") is False

    def test_unknown_name(self, format_registry):
        assert format_registry.unregister_format("never-registered") is False

    def test_a_builtin(self, format_registry):
        assert format_registry.unregister_format("yml") is True
        assert "yaml" not in format_registry.names()
        assert format_registry.find_for_filename("a.yaml") is None
        assert "yaml" not in format_registry.document_formats()


class TestAllowed:
    def test_get_refuses_a_name_outside_allowed(self):
        assert formats.get("json", allowed={"json"}).name == "json"
        with pytest.raises(yaconfiglib.UnsupportedFormatError, match="formats="):
            formats.get("yml", allowed={"json"})

    def test_for_filename_refuses_rather_than_skips(self):
        with pytest.raises(yaconfiglib.UnsupportedFormatError):
            formats.for_filename("a.yaml", allowed={"json"})
        with pytest.raises(yaconfiglib.UnsupportedFormatError):
            formats.find_for_filename(".env.prod", allowed=frozenset())
        assert formats.find_for_filename("a.xyz", allowed={"json"}) is None

    def test_document_formats_are_builtin_and_registered(self, format_registry):
        assert set(BUILTIN_FORMATS) <= format_registry.document_formats()
        format_registry.register_format(_format("zzdoc"))
        assert "zzdoc" in format_registry.document_formats()

    def test_loader_formats_names_are_checked(self):
        with pytest.raises(yaconfiglib.UnsupportedFormatError):
            yaconfiglib.Loader(formats=["json", "no-such-format"])


# -- formats outside the registry ----------------------------------------------


class TestFormatClassesDirectly:
    def test_an_unregistered_class_works_as_format(self):
        cls = _format("zzloose")
        assert "zzloose" not in formats.names()
        assert yaconfiglib.loads("text", format=cls)["data"] == "text"

    def test_options_are_checked_against_load_options(self):
        cls = _format("zzopts", load_options=frozenset({"flag"}))
        assert yaconfiglib.loads("x", format=cls, flag=1)["options"] == {"flag": 1}
        with pytest.raises(TypeError, match="other"):
            yaconfiglib.loads("x", format=cls, other=1)

    def test_dump_options_are_checked(self):
        def dumps(self, obj, /, **options):
            return repr((obj, options))

        cls = _format("zzdump", dump_options=frozenset({"pretty"}), dumps=dumps)
        assert yaconfiglib.dumps(1, format=cls, pretty=True) == "(1, {'pretty': True})"
        with pytest.raises(TypeError, match="ugly"):
            yaconfiglib.dumps(1, format=cls, ugly=True)

    def test_a_binary_format_receives_bytes(self, tmp_path):
        cls = _format("zzbin", binary=True)
        source = tmp_path / "blob.bin"
        source.write_bytes(b"\xff\x00")
        assert yaconfiglib.load(str(source), format=cls)["data"] == b"\xff\x00"
        assert yaconfiglib.loads(b"\xff", format=cls)["data"] == b"\xff"

    def test_a_text_format_receives_decoded_text(self, tmp_path):
        cls = _format("zztext")
        source = tmp_path / "t.txt"
        source.write_bytes(b"\xef\xbb\xbfcaf\xc3\xa9")
        assert yaconfiglib.load(str(source), format=cls)["data"] == "café"

    def test_format_must_be_a_name_or_a_format_class(self):
        with pytest.raises(TypeError):
            yaconfiglib.loads("{}", format=42)
        with pytest.raises(TypeError):
            yaconfiglib.loads("{}", format=dict)

    def test_format_is_required_for_loads_and_dumps(self):
        with pytest.raises(TypeError):
            yaconfiglib.loads("{}")
        with pytest.raises(TypeError):
            yaconfiglib.dumps({})

    def test_repr_names_the_format(self):
        assert "json" in repr(formats.get("json")())


class TestClaimsName:
    def test_claims_a_name_no_suffix_describes(self, format_registry):
        def claims_name(name):
            return name == "procfile"

        cls = format_registry.register_format(
            _format("zzproc", claims_name=staticmethod(claims_name))
        )
        assert format_registry.for_filename("Procfile") is cls
        assert format_registry.for_filename("deploy/PROCFILE") is cls
        assert format_registry.find_for_filename("Procfile.bak") is None

    def test_a_suffix_beats_claims_name(self, format_registry):
        format_registry.register_format(
            _format("zzgreedy", claims_name=staticmethod(lambda name: True))
        )
        assert format_registry.for_filename("a.json").name == "json"
        assert format_registry.for_filename("noext").name == "zzgreedy"


# -- format entry points -------------------------------------------------------

PLUGIN_FORMATS = """
    from yaconfiglib.formats import Format


    class PlugFormat(Format):
        name = "zzplug"
        suffixes = (".zzplug",)

        def loads(self, data, /, *, context=None, **options):
            return {"plug": data.strip()}


    class TakesBuiltin(Format):
        name = "json"
        suffixes = (".zzjson",)

        def loads(self, data, /, *, context=None, **options):
            return "hijacked"


    class AliasesBuiltin(Format):
        name = "zzalias"
        aliases = ("YAML",)

        def loads(self, data, /, *, context=None, **options):
            return "hijacked"


    class BadName(Format):
        name = "Upper"


    NotAFormat = object
"""

PLUGIN_ENTRIES = {
    "zzplug": "yacfg_test_plugfmt:PlugFormat",
    "takes_builtin": "yacfg_test_plugfmt:TakesBuiltin",
    "aliases_builtin": "yacfg_test_plugfmt:AliasesBuiltin",
    "bad_name": "yacfg_test_plugfmt:BadName",
    "not_a_format": "yacfg_test_plugfmt:NotAFormat",
    "broken": "yacfg_test_no_such_module:Thing",
}


@pytest.fixture
def plugin_formats(format_registry, install_plugin, caplog):
    install_plugin(
        "yaconfiglib.formats", PLUGIN_ENTRIES, {"yacfg_test_plugfmt": PLUGIN_FORMATS}
    )
    with caplog.at_level(logging.WARNING, logger="yaconfiglib.formats"):
        _reload_format_entry_points()
    return format_registry


class TestFormatEntryPoints:
    def test_a_plugin_format_is_listed(self, plugin_formats):
        cls = plugin_formats.get("zzplug")
        assert cls.__name__ == "PlugFormat"
        assert plugin_formats.for_filename("x.zzplug") is cls
        assert yaconfiglib.loads(" hi ", format="zzplug") == {"plug": "hi"}

    def test_a_plugin_cannot_take_a_builtin_name(self, plugin_formats):
        assert plugin_formats.get("json").__module__ == "yaconfiglib.formats._json"
        assert plugin_formats.get("yaml").__module__ == "yaconfiglib.formats._yaml"
        assert "zzalias" not in plugin_formats.names()
        assert plugin_formats.find_for_filename("a.zzjson") is None

    def test_broken_plugins_are_logged_and_skipped(self, plugin_formats, caplog):
        logged = _setup_log(caplog)
        for name in (
            "takes_builtin",
            "aliases_builtin",
            "bad_name",
            "not_a_format",
            "broken",
        ):
            assert name in logged, name
        assert "zzplug" not in logged
        assert sorted(plugin_formats.names()) == sorted(BUILTIN_FORMATS + ["zzplug"])

    def test_a_plugin_is_not_a_document_format(self, plugin_formats):
        assert "zzplug" not in plugin_formats.document_formats()
        allowed = plugin_formats.document_formats()
        with pytest.raises(yaconfiglib.UnsupportedFormatError):
            plugin_formats.get("zzplug", allowed=allowed)
        with pytest.raises(yaconfiglib.UnsupportedFormatError):
            plugin_formats.for_filename("x.zzplug", allowed=allowed)

    def test_the_caller_may_load_a_plugin_format(self, plugin_formats, tmp_path):
        source = tmp_path / "c.zzplug"
        source.write_text("top\n", encoding="utf-8")
        assert yaconfiglib.load(str(source)) == {"plug": "top"}
        assert yaconfiglib.Loader().load(str(source)) == {"plug": "top"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_a_document_reaches_a_plugin_only_when_named(
        self, plugin_formats, tmp_path
    ):
        (tmp_path / "c.zzplug").write_text("inner\n", encoding="utf-8")
        main = tmp_path / "main.yaml"
        main.write_text("x: !include c.zzplug\n", encoding="utf-8")
        with pytest.raises(yaconfiglib.UnsupportedFormatError, match="formats="):
            yaconfiglib.Loader().load(str(main))
        loader = yaconfiglib.Loader(formats=["yaml", "zzplug"])
        assert loader.load(str(main)) == {"x": {"plug": "inner"}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_formats_also_limits_builtins_in_documents(self, tmp_path):
        (tmp_path / "c.json").write_text('{"a": 1}', encoding="utf-8")
        main = tmp_path / "main.yaml"
        main.write_text("x: !include c.json\n", encoding="utf-8")
        assert yaconfiglib.Loader().load(str(main)) == {"x": {"a": 1}}
        with pytest.raises(yaconfiglib.UnsupportedFormatError):
            yaconfiglib.Loader(formats=["yaml"]).load(str(main))

    def test_entry_points_are_read_once(self, format_registry, install_plugin):
        format_registry.names()
        install_plugin(
            "yaconfiglib.formats",
            {"zzplug": "yacfg_test_plugfmt:PlugFormat"},
            {"yacfg_test_plugfmt": PLUGIN_FORMATS},
        )
        # Already read for this process: a distribution installed later is
        # not picked up by a plain lookup.
        assert "zzplug" not in format_registry.names()


# -- the scheme registry -------------------------------------------------------


def _data_factory(text):
    return sources.Data({"from": text}, name=text)


class TestSchemeRegex:
    @pytest.mark.parametrize(
        "text, groups",
        [
            ("cmd://echo", ("cmd", None)),
            ("cmd+json://echo", ("cmd", "json")),
            ("CMD+YAML://x", ("CMD", "YAML")),
            ("vault.v2-x://secret/path", ("vault.v2-x", None)),
            ("s3+yaml.gz://b/k", ("s3", "yaml.gz")),
        ],
    )
    def test_matches(self, text, groups):
        assert sources.SCHEME_REGEX.match(text).groups() == groups

    @pytest.mark.parametrize(
        "text",
        [
            "cmd:echo",
            "C:\\conf\\a.yaml",
            "C:/conf/a.yaml",
            "1cmd://x",
            "a.yaml",
            "./cmd://x",
            "+json://x",
            "",
        ],
    )
    def test_does_not_match(self, text):
        assert sources.SCHEME_REGEX.match(text) is None


class TestFromText:
    def test_builtin_schemes(self):
        assert sources.schemes() == sorted(sources.schemes())
        assert {"cmd", "exec", "sh"} <= set(sources.schemes())
        for text in ("cmd://echo hi", "exec://echo", "sh://echo", "CMD+json://x"):
            assert isinstance(sources.from_text(text), sources.Command), text

    @pytest.mark.parametrize(
        "text", ["a.yaml", "zzunknown://x", "cmd:echo", "C:\\x\\cmd.yaml"]
    )
    def test_not_a_registered_scheme(self, text):
        assert sources.from_text(text) is None

    def test_builtins_are_reachable_without_plugins(self):
        assert isinstance(
            sources.from_text("cmd://echo", allow_plugins=False), sources.Command
        )


class TestRegisterScheme:
    def test_register_and_resolve(self, scheme_registry):
        scheme_registry.register_scheme("ZZData", _data_factory)
        assert "zzdata" in scheme_registry.schemes()
        found = scheme_registry.from_text("zzdata://a")
        assert isinstance(found, sources.Data) and found.data == {"from": "zzdata://a"}
        assert scheme_registry.from_text("ZZDATA+json://b").data == {
            "from": "ZZDATA+json://b"
        }
        assert scheme_registry.from_text("zzdata://c", allow_plugins=False) is not None

    def test_a_registered_scheme_loads(self, scheme_registry):
        scheme_registry.register_scheme("zzdata", _data_factory)
        loader = yaconfiglib.Loader()
        assert loader.load("zzdata://a", {"b": 1}) == {"from": "zzdata://a", "b": 1}

    @pytest.mark.usefixtures("needs_yaml")
    def test_a_registered_scheme_is_reachable_from_documents(
        self, scheme_registry, tmp_path
    ):
        scheme_registry.register_scheme("zzdata", _data_factory)
        main = tmp_path / "main.yaml"
        main.write_text("x: !include 'zzdata://inner'\n", encoding="utf-8")
        assert yaconfiglib.load(str(main)) == {"x": {"from": "zzdata://inner"}}

    def test_conflict_raises(self, scheme_registry):
        scheme_registry.register_scheme("zzdata", _data_factory)
        with pytest.raises(ValueError, match="replace=True"):
            scheme_registry.register_scheme("ZZDATA", _data_factory)
        with pytest.raises(ValueError):
            scheme_registry.register_scheme("cmd", _data_factory)
        assert isinstance(scheme_registry.from_text("cmd://x"), sources.Command)

    def test_replace(self, scheme_registry):
        scheme_registry.register_scheme("cmd", _data_factory, replace=True)
        assert scheme_registry.from_text("cmd://x").data == {"from": "cmd://x"}

    def test_unregister(self, scheme_registry):
        scheme_registry.register_scheme("zzdata", _data_factory)
        assert scheme_registry.unregister_scheme("ZZDATA") is True
        assert scheme_registry.unregister_scheme("zzdata") is False
        assert scheme_registry.from_text("zzdata://a") is None
        assert "zzdata" not in scheme_registry.schemes()

    def test_unregister_a_builtin(self, scheme_registry):
        assert scheme_registry.unregister_scheme("sh") is True
        assert scheme_registry.from_text("sh://echo") is None


PLUGIN_SOURCES = """
    from yaconfiglib.sources import Data


    def factory(text):
        return Data({"plugin": text}, name=text)


    VALUE = 42
"""

PLUGIN_SCHEMES = {
    "zzplug": "yacfg_test_plugsrc:factory",
    "cmd": "yacfg_test_plugsrc:factory",
    "zznotcallable": "yacfg_test_plugsrc:VALUE",
    "zzbroken": "yacfg_test_no_such_module:factory",
}


@pytest.fixture
def plugin_schemes(scheme_registry, install_plugin, caplog):
    install_plugin(
        "yaconfiglib.sources", PLUGIN_SCHEMES, {"yacfg_test_plugsrc": PLUGIN_SOURCES}
    )
    with caplog.at_level(logging.WARNING, logger="yaconfiglib.sources"):
        _reload_scheme_entry_points()
    return scheme_registry


class TestSchemeEntryPoints:
    def test_a_plugin_scheme_resolves(self, plugin_schemes):
        assert "zzplug" in plugin_schemes.schemes()
        assert plugin_schemes.from_text("zzplug://x").data == {"plugin": "zzplug://x"}
        assert yaconfiglib.Loader().load("zzplug://x") == {"plugin": "zzplug://x"}

    def test_allow_plugins_false_hides_it(self, plugin_schemes):
        assert plugin_schemes.from_text("zzplug://x", allow_plugins=False) is None

    def test_a_plugin_cannot_take_a_builtin_scheme(self, plugin_schemes):
        assert isinstance(plugin_schemes.from_text("cmd://x"), sources.Command)

    def test_broken_plugins_are_logged_and_skipped(self, plugin_schemes, caplog):
        logged = _setup_log(caplog)
        for name in ("'cmd'", "zznotcallable", "zzbroken"):
            assert name in logged, name
        assert "zznotcallable" not in plugin_schemes.schemes()
        assert "zzbroken" not in plugin_schemes.schemes()

    @pytest.mark.usefixtures("needs_yaml")
    def test_a_document_cannot_reach_a_plugin_scheme(self, plugin_schemes, tmp_path):
        main = tmp_path / "main.yaml"
        main.write_text("x: !include 'zzplug://inner'\n", encoding="utf-8")
        with pytest.raises((yaconfiglib.ConfigError, OSError)):
            yaconfiglib.load(str(main))


class TestReplaceTakesOnlyWhatCollides:
    def test_taking_an_alias_leaves_the_other_format(self, format_registry):
        class Cfg(Format):
            name = "cfg"
            suffixes = (".cfgx",)

            def loads(self, data, /, *, context=None, **options):
                return {}

        format_registry.register_format(Cfg, replace=True)
        assert "ini" in format_registry.names()
        assert format_registry.get("cfg") is Cfg
        assert format_registry.for_filename("a.ini").name == "ini"
        assert yaconfiglib.loads("[s]\nk = v\n", format="ini") == {"s": {"k": "v"}}

    def test_taking_the_name_replaces_the_format(self, format_registry):
        class Json(Format):
            name = "json"
            suffixes = (".json",)

            def loads(self, data, /, *, context=None, **options):
                return "mine"

        format_registry.register_format(Json, replace=True)
        assert yaconfiglib.loads("{}", format="json") == "mine"


class TestConcurrentReads:
    def test_lookups_while_another_thread_registers(self, format_registry):
        import threading

        errors = []
        stop = threading.Event()

        def read():
            while not stop.is_set():
                try:
                    format_registry.find_for_filename("a.json")
                    format_registry.names()
                    format_registry.document_formats()
                except Exception as error:  # noqa: BLE001 - recorded for the assert
                    errors.append(error)
                    return

        readers = [threading.Thread(target=read) for _ in range(3)]
        for thread in readers:
            thread.start()
        try:
            for index in range(300):
                cls = type(
                    f"F{index}",
                    (Format,),
                    {"name": f"f{index}", "suffixes": (f".f{index}",)},
                )
                format_registry.register_format(cls)
                format_registry.unregister_format(cls.name)
        finally:
            stop.set()
            for thread in readers:
                thread.join()
        assert errors == []
