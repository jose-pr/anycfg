"""Every public annotation resolves on the running interpreter, and says what the runtime accepts.

The targets are **discovered** from the public namespaces, not listed: a
hand-written list silently stops covering new API. This must pass on the 3.9
floor, where ``X | Y`` and ``typing.Self`` are not evaluable.
"""

import importlib.resources
import inspect
import pathlib
import typing

import pytest

import anycfg
import anycfg.formats
import anycfg.sources
import anycfg.testing

_NAMESPACES = (
    anycfg,
    anycfg.formats,
    anycfg.sources,
    anycfg.testing,
)


def _is_ours(obj):
    return (getattr(obj, "__module__", "") or "").startswith("anycfg")


def _class_members(cls):
    """``{attribute: function}`` for the methods *cls* itself defines.

    Public names and dunders count; ``_private`` ones do not. Properties
    contribute their getter.
    """
    found = {}
    for attr, member in vars(cls).items():
        if attr.startswith("_") and not (attr.startswith("__") and attr.endswith("__")):
            continue
        function = member
        if isinstance(member, (classmethod, staticmethod)):
            function = member.__func__
        elif isinstance(member, property):
            function = member.fget
        if inspect.isfunction(function) and _is_ours(function):
            found[attr] = function
    return found


def _public_classes():
    """Every public class reachable from the public namespaces, plus the built-in formats."""
    classes = {}
    for namespace in _NAMESPACES:
        for name in getattr(namespace, "__all__", ()):
            obj = getattr(namespace, name)
            if inspect.isclass(obj) and _is_ours(obj):
                classes.setdefault(f"{obj.__module__}.{obj.__qualname__}", obj)
    # Built-in formats are public through formats.get(); their classes import
    # nothing optional, so every one is listed whatever extras are installed.
    for name in anycfg.formats.names():
        cls = anycfg.formats.get(name)
        classes.setdefault(f"{cls.__module__}.{cls.__qualname__}", cls)
    return classes


def _public_callables():
    """Every public callable, as ``{qualified name: function}``."""
    found = {}
    for namespace in _NAMESPACES:
        for name in getattr(namespace, "__all__", ()):
            obj = getattr(namespace, name)
            if inspect.isfunction(obj) and _is_ours(obj):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    for qualname, cls in _public_classes().items():
        for attr, function in _class_members(cls).items():
            found[f"{qualname}.{attr}"] = function
    return found


_CLASSES = _public_classes()
_CALLABLES = _public_callables()
_IDS = sorted(_CALLABLES)

#: Hints naming `LoadContext`, which these modules import only under
#: TYPE_CHECKING, so `get_type_hints` raises NameError on every interpreter.


def _callable_params():
    params = []
    for qualname in _IDS:
        params.append(pytest.param(qualname, id=qualname))
    return params


def _flatten_union(hint):
    """Every member of *hint*, flattening nested unions."""
    if typing.get_origin(hint) is typing.Union:
        members = []
        for member in typing.get_args(hint):
            members.extend(_flatten_union(member))
        return members
    return [hint]


def _hints(obj):
    return typing.get_type_hints(obj)


class TestResolvable:
    @pytest.mark.parametrize("qualname", _callable_params())
    def test_public_callable_hints_resolve(self, qualname):
        typing.get_type_hints(_CALLABLES[qualname])

    @pytest.mark.parametrize("qualname", sorted(_CLASSES))
    def test_public_class_attribute_hints_resolve(self, qualname):
        typing.get_type_hints(_CLASSES[qualname])

    def test_discovery_covers_the_core_api(self):
        # A pin on the discovery rule itself: a rule that stops finding these is
        # broken, and every parametrized case above would still pass.
        core = [
            "anycfg._api.load",
            "anycfg._api.loads",
            "anycfg._api.load_all",
            "anycfg._api.loads_all",
            "anycfg._api.dump",
            "anycfg._api.dumps",
            "anycfg._loader.Loader.__init__",
            "anycfg._loader.Loader.load",
            "anycfg._loader.Loader.load_all",
            "anycfg._loader.Loader.policy",
            "anycfg._loader.Loader.base_dir",
            "anycfg._hydrate.hydrate",
            "anycfg._hydrate.typed_merge",
            "anycfg._hydrate.opaque",
            "anycfg._hydrate.OpaqueMerge.__merge__",
            "anycfg._hydrate.TypedNamespace.__init__",
            "anycfg._merge.register_merge",
            "anycfg._merge.merge_strategies",
            "anycfg._merge.Merge.__call__",
            "anycfg._dotaccess.DotAccessibleDict.get",
            "anycfg._dotaccess.DotAccessibleDict.__getattr__",
            "anycfg._context.LoadContext.include",
            "anycfg._context.LoadContext.derive",
            "anycfg._context.Policy.trusted",
            "anycfg._context.Policy.untrusted",
            "anycfg._context.Policy.replace",
            "anycfg.formats._base.Format.loads",
            "anycfg.formats._base.Format.loads_all",
            "anycfg.formats._base.Format.dumps",
            "anycfg.formats.register_format",
            "anycfg.formats.get",
            "anycfg.formats.for_filename",
            "anycfg.sources.register_scheme",
            "anycfg.sources.from_text",
            "anycfg.sources._base.Source.read",
            "anycfg.sources._command.Command.script",
        ]
        missing = [name for name in core if name not in _CALLABLES]
        assert not missing, f"discovery missed: {missing}"

    def test_discovery_covers_every_builtin_format(self):
        loads = {
            qualname
            for qualname in _CALLABLES
            if qualname.startswith("anycfg.formats._") and qualname.endswith(".loads")
        }
        # Format.loads plus one per built-in format.
        assert len(loads) >= 1 + len({"json", "yaml", "toml", "ini", "dotenv"}), sorted(
            loads
        )

    def test_named_tuple_and_dataclass_fields_resolve(self):
        for cls in (
            anycfg.LoadEvent,
            anycfg.ErrorFrame,
            anycfg.Policy,
            anycfg.LoadContext,
            anycfg.sources.Payload,
        ):
            assert typing.get_type_hints(cls), cls


class TestHintsSayWhatTheRuntimeAccepts:
    def test_entry_points_return_any(self):
        # A document can be a mapping, a list or a scalar, and `config.db.host`
        # is documented; `object` would make every such access a type error.
        for obj in (
            anycfg.load,
            anycfg.loads,
            anycfg.Loader.load,
            anycfg.DotAccessibleDict.get,
            anycfg.DotAccessibleDict.__getattr__,
            anycfg.typed_merge,
        ):
            assert _hints(obj)["return"] is typing.Any, obj
        for obj in (
            anycfg.load_all,
            anycfg.loads_all,
            anycfg.Loader.load_all,
        ):
            assert typing.get_args(_hints(obj)["return"]) == (typing.Any,), obj

    def test_none_defaults_are_optional(self):
        # PEP 484 forbids implicit Optional: a `= None` default with a hint that
        # excludes None makes every checker reject passing None.
        violations = []
        for qualname in _IDS:
            function = _CALLABLES[qualname]
            try:
                hints = typing.get_type_hints(function)
                signature = inspect.signature(function)
            except (NameError, TypeError, ValueError):
                # Unresolvable hints fail test_public_callable_hints_resolve.
                continue
            for name, parameter in signature.parameters.items():
                if parameter.default is not None:
                    continue
                if name not in hints:
                    violations.append(f"{qualname}({name}): no annotation")
                    continue
                hint = hints[name]
                if hint in (typing.Any, object):
                    continue
                if type(None) not in _flatten_union(hint):
                    violations.append(f"{qualname}({name}): {hint!r}")
        assert not violations, "\n".join(violations)

    def test_public_variadics_are_annotated(self):
        missing = []
        for qualname in _IDS:
            try:
                signature = inspect.signature(_CALLABLES[qualname])
            except (TypeError, ValueError):
                continue
            for name, parameter in signature.parameters.items():
                if (
                    parameter.kind
                    in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                    and parameter.annotation is inspect.Parameter.empty
                ):
                    missing.append(f"{qualname}(*{name})")
        assert not missing, "\n".join(missing)

    def test_merge_hint_accepts_a_name_or_a_callable(self):
        members = _flatten_union(_hints(anycfg.Loader.__init__)["merge"])
        assert str in members
        assert anycfg.Merge in members

    def test_merge_options_hint_is_a_keyword_mapping(self):
        members = _flatten_union(_hints(anycfg.Loader.__init__)["merge_options"])
        assert any(typing.get_args(m) == (str, typing.Any) for m in members), members

    def test_typed_merge_accepts_special_form_hints(self):
        # `Type[T]` alone rejects Optional[...] and Dict[str, int], which are
        # documented hints but not classes.
        assert _hints(anycfg.typed_merge)["cls"] is typing.Any
        get_overloads = getattr(typing, "get_overloads", None)
        if get_overloads is not None:  # 3.11+
            assert len(get_overloads(anycfg.typed_merge)) == 3

    def test_hydrate_returns_the_class_type(self):
        hints = _hints(anycfg.hydrate)
        assert typing.get_origin(hints["cls"]) is type
        assert typing.get_args(hints["cls"]) == (hints["return"],)

    def test_opaque_preserves_the_class_type(self):
        hints = _hints(anycfg.opaque)
        assert hints["return"] == hints["cls"]
        assert typing.get_origin(hints["cls"]) is type

    def test_merge_protocol_shape(self):
        signature = inspect.signature(anycfg.Merge.__call__)
        kinds = {name: p.kind for name, p in signature.parameters.items()}
        assert kinds["memo"] is inspect.Parameter.KEYWORD_ONLY
        assert inspect.Parameter.VAR_KEYWORD in kinds.values()
        assert type(None) in _flatten_union(_hints(anycfg.Merge.__call__)["memo"])


class TestTyped:
    def test_py_typed_is_shipped(self):
        marker = importlib.resources.files("anycfg") / "py.typed"
        assert marker.is_file()

    def test_py_typed_sits_beside_the_package(self):
        assert pathlib.Path(anycfg.__file__).with_name("py.typed").is_file()
