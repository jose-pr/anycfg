"""Typed hydration: build a model from loaded data, and merge typed layers.

`hydrate` turns one loaded document into an instance of a type, coercing every
field through its annotation. `typed_merge` merges several layers into one
instance field by field. Both run on one engine, so a value is coerced the same
way whichever entry point reached it.
"""

from __future__ import annotations

import collections
import collections.abc as _abc
import contextlib
import dataclasses
import datetime
import decimal
import enum
import inspect
import os
import pathlib
import sys
import types
import typing
import uuid
from argparse import Namespace

from . import exceptions as _errors
from ._scalar import ScalarText, resolved
from .exceptions import ConfigTypeError, ConfigValueError

T = typing.TypeVar("T")

__all__ = [
    "hydrate",
    "dehydrate",
    "typed_merge",
    "OpaqueMerge",
    "opaque",
    "TypedNamespace",
]

#: Types built from one scalar value, never from a mapping of fields.
_SCALAR_TARGETS = (
    str,
    bytes,
    bytearray,
    int,
    float,
    complex,
    decimal.Decimal,
    datetime.date,
    datetime.time,
    datetime.timedelta,
    enum.Enum,
    pathlib.PurePath,
    uuid.UUID,
)

_NONE_TYPE = type(None)
# Accepted spellings of a flag. '1'/'0' are included because INI, dotenv and
# the environment have no booleans and use them often.
_BOOL_WORDS = {
    **{word: True for word in ("true", "yes", "on", "1")},
    **{word: False for word in ("false", "no", "off", "0")},
}
# types.UnionType (the X | Y syntax at runtime) is 3.10+.
_UNION_TYPE = getattr(types, "UnionType", None)
_EXTRA_MODES = ("ignore", "forbid")


def _strip_annotated(hint: object) -> object:
    """Return *hint* with any ``Annotated[...]`` wrappers removed."""
    while typing.get_origin(hint) is typing.Annotated:
        hint = typing.get_args(hint)[0]
    return hint


def _runtime_class(member: object) -> typing.Optional[type]:
    """The class an ``isinstance`` test for union *member* should use, if any."""
    member = _strip_annotated(member)
    cls = typing.get_origin(member) or member
    if cls is typing.Any or not isinstance(cls, type):
        return None
    return cls


def _is_union(hint: object) -> bool:
    origin = typing.get_origin(hint)
    return origin is typing.Union or (_UNION_TYPE is not None and origin is _UNION_TYPE)


def _resolve_hint(hint: object, value: object) -> object:
    """Normalize *hint* for *value*: strip Annotated, pick a union member.

    A union drops its ``NoneType`` members, collapses to ``typing.Any`` if any
    member is ``Any``, and otherwise resolves to the first member *value* is
    already an instance of — so a hint that already accepts the value never
    coerces it — falling back to the first member.
    """
    while True:
        hint = _strip_annotated(hint)
        if _is_union(hint):
            members = [m for m in typing.get_args(hint) if m is not _NONE_TYPE]
            if not members:
                return _NONE_TYPE
            if any(_strip_annotated(m) is typing.Any for m in members):
                return typing.Any
            chosen = None
            for member in members:
                runtime_cls = _runtime_class(member)
                if runtime_cls is None:
                    continue
                try:
                    matches = isinstance(value, runtime_cls)
                except TypeError:
                    # A non-runtime-checkable Protocol cannot claim the value.
                    continue
                if matches:
                    chosen = member
                    break
            hint = members[0] if chosen is None else chosen
            continue
        return hint


if sys.version_info >= (3, 14):

    def _own_annotations(klass: type) -> dict:
        # PEP 649: a class __dict__ no longer carries __annotations__.
        import annotationlib

        return annotationlib.get_annotations(
            klass, format=annotationlib.Format.FORWARDREF
        )

else:

    def _own_annotations(klass: type) -> dict:
        return klass.__dict__.get("__annotations__", {})


def _type_hints(origin: type) -> dict:
    """Resolve *origin*'s annotations, entry by entry if the whole set fails.

    ``typing.get_type_hints`` is all-or-nothing; the fallback walks the MRO
    base-first and keeps every annotation that resolves.
    """
    try:
        return typing.get_type_hints(origin)
    except (TypeError, NameError, AttributeError):
        pass

    hints: dict = {}
    for klass in reversed(getattr(origin, "__mro__", (origin,))):
        for name, annotation in _own_annotations(klass).items():
            holder = types.SimpleNamespace(__annotations__={name: annotation})
            try:
                hints.update(
                    typing.get_type_hints(
                        holder,
                        # .get, not indexing: a KeyError would escape the except.
                        globalns=getattr(
                            sys.modules.get(klass.__module__), "__dict__", {}
                        ),
                        localns=dict(vars(klass)),
                    )
                )
            except (TypeError, NameError, AttributeError):
                # A subclass re-annotating a name unresolvably must not leave
                # the base class's hint in place.
                hints.pop(name, None)
    return hints


#: "this failure is the model's, not one field's" — a construction error.
_NO_FIELD = object()


@contextlib.contextmanager
def _field(origin: object, segment: object = _NO_FIELD):
    """Attribute a coercion or construction failure to *origin*'s *segment*.

    Only `TypeError` and `ValueError` — what coercion constructors and this
    module's own raises produce — and the error is always re-raised unchanged.
    """
    try:
        yield
    except (TypeError, ValueError) as error:
        _attribute_field(error, origin, segment)
        raise


def _attribute_field(
    error: BaseException, origin: object, segment: object = _NO_FIELD
) -> None:
    """Record *segment* and *origin*'s name on *error*.

    The key is built from the inside out, so an error deep in a model reads
    ``at db.hosts[0].port (Outer)`` — relative to the model the caller asked for,
    whose name wins because outer handlers run last.
    """
    model = getattr(origin, "__name__", None)
    if segment is _NO_FIELD:
        _errors._add_error_context(error, model=model)
        return
    existing = getattr(error, "config_key", ()) or ()
    try:
        error.config_key = (segment,) + tuple(existing)
    except (AttributeError, TypeError):
        pass
    _errors._add_error_context(error, model=model)


def _pydantic_model(cls: object) -> bool:
    """True for a Pydantic model class, without importing pydantic.

    A class can only subclass ``pydantic.BaseModel`` if pydantic is already
    imported, so probing `sys.modules` is exact.
    """
    pydantic = sys.modules.get("pydantic")
    if pydantic is None or not isinstance(cls, type):
        return False
    base = getattr(pydantic, "BaseModel", None)
    return isinstance(base, type) and issubclass(cls, base)


def _fields_of(obj: object) -> typing.Optional[typing.Mapping]:
    """The fields of a layer, or `None` when it has none to merge.

    A mapping is its own fields; a namespace, dataclass or plain object gives
    its attributes; a slotted dataclass gives its declared fields.
    """
    if isinstance(obj, _abc.Mapping):
        return obj
    if isinstance(obj, (str, bytes, bytearray)):
        return None
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
    try:
        return vars(obj)
    except TypeError:
        return None


def _construct_sequence(origin: type, value: object, items: list) -> object:
    """Build *items* into *origin*, or fall back to *value* if it refuses them."""
    target = origin
    if inspect.isabstract(origin):
        # Sequence[str] resolves to an abstract class: keep the value's own
        # concrete type, else list.
        target = type(value) if isinstance(value, _abc.Sequence) else list
    try:
        return target(items)
    except TypeError:
        # A tuple subclass with a fixed __new__, say. Element errors are raised
        # before this, so the catch cannot swallow one.
        return value


def _merge_sequence(
    origin: type, cls_args: tuple, hints: dict, value: object, init: bool, extra: str
) -> object:
    """Rebuild *value* as a sequence of type *origin*, coercing every item."""
    if (
        isinstance(value, str)
        or isinstance(value, _abc.Mapping)
        or not isinstance(value, _abc.Iterable)
    ):
        raise ConfigTypeError(
            f"cannot merge a {type(value).__name__} value into sequence type "
            f"{origin.__name__}"
        )
    if origin is range:
        return value

    fields = getattr(origin, "_fields", None)
    if issubclass(origin, tuple) and fields is not None:
        # A NamedTuple: never zip(fields, value), which truncates.
        items = []
        for i, item in enumerate(value):
            hint = hints.get(fields[i], type(item)) if i < len(fields) else type(item)
            with _field(origin, fields[i] if i < len(fields) else i):
                items.append(_merge(hint, (item,), init, extra))
        with _field(origin):
            try:
                return origin(*items)
            except TypeError as error:
                raise ConfigTypeError(
                    f"cannot build {origin.__name__} from {len(items)} items: {error}"
                ) from error

    child_cls = cls_args[0] if cls_args else None
    if issubclass(origin, tuple):
        # 3.9 spells Tuple[()]'s args as ((),) where later versions give ().
        args = () if cls_args == ((),) else cls_args
        if args and not (len(args) == 2 and args[1] is Ellipsis):
            items = list(value)
            if len(items) != len(args):
                raise ConfigTypeError(
                    f"cannot merge {len(items)} items into a "
                    f"{len(args)}-element {origin.__name__} hint"
                )
            coerced = []
            for index, (arg, item) in enumerate(zip(args, items)):
                with _field(origin, index):
                    coerced.append(_merge(arg or type(item), (item,), init, extra))
            return _construct_sequence(origin, value, coerced)
        child_cls = args[0] if args else None

    items = []
    for index, item in enumerate(value):
        with _field(origin, index):
            items.append(_merge(child_cls or type(item), (item,), init, extra))
    return _construct_sequence(origin, value, items)


def _construct_mapping(origin: type, objects: tuple, merged: dict) -> object:
    """Build *merged* into mapping type *origin*."""
    if inspect.isabstract(origin):
        return dict(merged)
    if issubclass(origin, collections.defaultdict):
        # The factory is defaultdict's first positional argument.
        factory = None
        for obj in objects:
            if isinstance(obj, collections.defaultdict):
                factory = obj.default_factory
        return origin(factory, merged)
    try:
        # Positional: mapping keys are data, not identifiers.
        return origin(merged)
    except TypeError:
        if all(isinstance(key, str) for key in merged):
            # A dict subclass whose __init__ takes only **kwargs.
            return origin(**merged)
        raise


def _accepted_keys(origin: type) -> typing.Optional[typing.FrozenSet[str]]:
    """The keyword names *origin*'s constructor takes, or `None` for "any".

    The class signature, not ``__init__``: it excludes ``self`` and
    ``field(init=False)``, and keeps ``InitVar`` parameters.
    """
    try:
        parameters = inspect.signature(origin).parameters.values()
    except (TypeError, ValueError):
        return None
    names = set()
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return None
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            names.add(parameter.name)
    return frozenset(names)


def _merge_fields(
    origin: type, cls_args: tuple, hints: dict, objects: tuple, init: bool, extra: str
) -> object:
    """Collect every layer's fields, merge each, and build one *origin*."""
    is_mapping = issubclass(origin, _abc.Mapping)
    key_cls = cls_args[0] if is_mapping and len(cls_args) > 1 else None
    child_cls = cls_args[1] if is_mapping and len(cls_args) > 1 else None

    # A TypedNamespace normalizes its fields in __init__, so the instance is
    # built up front and its hooks applied here, once.
    target = origin.__new__(origin) if issubclass(origin, TypedNamespace) else None

    collected: "dict[object, list]" = {}
    for obj in objects:
        props = _fields_of(obj)
        # A TypedNamespace source was normalized at construction.
        parsed_source = isinstance(obj, TypedNamespace)
        for prop, value in props.items():
            if key_cls is not None:
                # Coerce keys while collecting, so '80' and 80 group as one
                # entry: most formats only produce str keys.
                prop = _merge(key_cls, (prop,), init, extra)
            if not parsed_source:
                parser = (
                    getattr(obj, f"_parse_{prop}", None)
                    if not isinstance(obj, _abc.Mapping)
                    else None
                )
                if parser is None and target is not None:
                    parser = getattr(target, f"_parse_{prop}", None)
                if parser is not None:
                    value = parser(value)
            collected.setdefault(prop, []).append(value)

    if not is_mapping and target is None and init:
        accepted = _accepted_keys(origin)
        if accepted is not None:
            unknown = [name for name in collected if name not in accepted]
            if unknown and extra == "forbid":
                raise ConfigTypeError(
                    f"{origin.__name__} has no field(s) "
                    f"{', '.join(repr(name) for name in unknown)}"
                )
            for name in unknown:
                del collected[name]

    merged: "dict[object, object]" = {}
    for name, values in collected.items():
        present = [value for value in values if value is not None]
        if not present:
            merged[name] = None
            continue
        # The fallback hint comes from the last non-None value: type(None)
        # would drive the merge into NoneType.
        hint = hints.get(name, child_cls or type(present[-1]))
        with _field(origin, name):
            merged[name] = (
                _merge(hint, tuple(values), init, extra) if hint else present[-1]
            )

    if target is not None:
        for prop, value in merged.items():
            setattr(target, prop, value)
        return target

    if is_mapping:
        if init:
            with _field(origin):
                return _construct_mapping(origin, objects, merged)
        if inspect.isabstract(origin):
            return dict(merged)
        inst = origin.__new__(origin)
        for prop, value in merged.items():
            inst[prop] = value
        return inst

    if init:
        with _field(origin):
            try:
                return origin(**merged)
            except TypeError as error:
                # A missing required field, mostly: the data's fault.
                raise ConfigTypeError(
                    f"cannot build {origin.__name__}: {error}"
                ) from error

    inst = origin.__new__(origin)
    for prop, value in merged.items():
        setattr(inst, prop, value)
    return inst


def _coerce_scalar(origin: type, value: object) -> object:
    """*value* as an *origin*, refusing a conversion that would lose data."""
    if isinstance(value, ScalarText):
        if origin is str or origin is ScalarText:
            return value.value if origin is ScalarText else str(value)
        found = value.value
        if isinstance(found, origin) and not (
            isinstance(found, bool) and origin is not bool
        ):
            return found
        # The format's reading does not fit: build the type from the text.
        value = str(value)
    if isinstance(value, origin):
        return value
    if origin is bool:
        if isinstance(value, str):
            # bool('false') is True, which would invert every flag read from a
            # format that has no booleans.
            try:
                return _BOOL_WORDS[value.strip().lower()]
            except KeyError:
                raise ConfigValueError(
                    f"cannot interpret {value!r} as a bool"
                ) from None
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise ConfigTypeError(f"cannot interpret a {type(value).__name__} as a bool")
    if origin is int and not isinstance(value, bool):
        if isinstance(value, float):
            if not value.is_integer():
                raise ConfigValueError(f"{value!r} is not a whole number")
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip(), 10)
            except ValueError:
                raise ConfigValueError(
                    f"cannot interpret {value!r} as an int"
                ) from None
    if origin is float and isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            raise ConfigValueError(f"cannot interpret {value!r} as a float") from None
    if origin in (datetime.date, datetime.datetime, datetime.time) and isinstance(
        value, str
    ):
        try:
            return origin.fromisoformat(value.strip())
        except ValueError:
            raise ConfigValueError(
                f"cannot interpret {value!r} as an ISO {origin.__name__}"
            ) from None
    if issubclass(origin, enum.Enum):
        try:
            return origin(value)
        except ValueError:
            if isinstance(value, str) and value in origin.__members__:
                return origin[value]
            raise ConfigValueError(
                f"{value!r} is neither a value nor a member name of {origin.__name__}"
            ) from None
    if origin in (pathlib.Path, pathlib.PurePath) and isinstance(
        value, (str, os.PathLike)
    ):
        return origin(value)
    return origin(value)


def _drop_replaced(origin: type, objects: tuple) -> tuple:
    """Keep the layers after the last one that is not field-bearing.

    The merge rule for a shape change: a later value of another shape replaces
    everything before it, so ``"off"`` followed by ``{"cert": ...}`` merges as
    just the mapping. When the LAST layer has no fields it is the result, and
    building *origin* from it is an error.
    """
    for index in range(len(objects) - 1, -1, -1):
        if _fields_of(objects[index]) is None:
            if index == len(objects) - 1:
                raise ConfigTypeError(
                    f"cannot build {origin.__name__} from a "
                    f"{type(objects[index]).__name__}"
                )
            return objects[index + 1 :]
    return objects


def _merge(cls: typing.Any, objects: tuple, init: bool, extra: str) -> typing.Any:
    objects = tuple(obj for obj in objects if obj is not None)
    if not objects:
        return None

    # Normalize the hint against the value it is applied to; for a scalar
    # written in a document, against the value the format read it as.
    last = objects[-1]
    origin = _resolve_hint(cls, last.value if isinstance(last, ScalarText) else last)
    # Generic args from the UNWRAPPED hint, read before the origin strip.
    cls_args = typing.get_args(origin)
    _stripped = typing.get_origin(origin)
    origin = origin if _stripped is None else _stripped

    # On the STRIPPED origin: typing aliases do not forward dunders, so the
    # hook would be missed for Optional[Zone] or MyGeneric[int].
    merge_fn = getattr(origin, "__merge__", None)
    if merge_fn:
        return merge_fn(*objects, init=init)

    # typing.Any is a class on 3.11+; last value wins, uncoerced, everywhere.
    if origin is typing.Any or origin is ScalarText:
        return resolved(objects[-1])

    load_hook = getattr(origin, "_load_", None)
    if callable(load_hook):
        return _call_load_hook(origin, load_hook, objects)

    if not isinstance(origin, type):
        # A factory function used as an annotation: last value wins, coerced
        # through the callable when it accepts it.
        value = objects[-1]
        if isinstance(value, ScalarText):
            value = str(value)
        if callable(origin):
            try:
                return origin(value)
            except (TypeError, ValueError):
                return resolved(value)
        return resolved(value)

    if _pydantic_model(origin):
        from ._merge import deep

        data = objects[0]
        for layer in objects[1:]:
            data = deep(data, layer)
        if isinstance(data, origin):
            return data
        with _field(origin):
            if hasattr(origin, "model_validate"):  # pydantic 2
                return origin.model_validate(data)
            return origin.parse_obj(data)  # pydantic 1

    hints = _type_hints(origin)

    # A CLASS test on the unwrapped origin; str and bytes are Sequences too.
    if issubclass(origin, _abc.Sequence) and not issubclass(origin, (str, bytes)):
        return _merge_sequence(origin, cls_args, hints, objects[-1], init, extra)

    if issubclass(origin, (_abc.Mapping, Namespace)) or dataclasses.is_dataclass(
        origin
    ):
        objects = _drop_replaced(origin, objects)
        return _merge_fields(origin, cls_args, hints, objects, init, extra)

    if _fields_from(objects[-1]) and not issubclass(origin, _SCALAR_TARGETS):
        # An ordinary class built from a mapping: its fields are keywords,
        # coerced through its annotations or its constructor's.
        objects = _drop_replaced(origin, objects)
        hints = {**_init_hints(origin), **hints}
        return _merge_fields(origin, cls_args, hints, objects, init, extra)

    return _coerce_scalar(origin, objects[-1])


def _fields_from(value: object) -> bool:
    return isinstance(value, _abc.Mapping)


def _init_hints(origin: type) -> dict:
    """The constructor's annotations, for a class that declares none of its own."""
    init = getattr(origin, "__init__", None)
    if init is None or init is object.__init__:
        return {}
    try:
        hints = typing.get_type_hints(init)
    except (TypeError, NameError, AttributeError):
        return {}
    hints.pop("return", None)
    return hints


def _call_load_hook(origin: type, hook: typing.Any, objects: tuple) -> object:
    """Build *origin* through its ``_load_`` classmethod.

    A scalar arrives as the text written in the document when that is known;
    layers of mappings are deep-merged first and arrive as plain data.
    """
    last = objects[-1]
    if isinstance(last, ScalarText):
        value: object = str(last)
    elif isinstance(last, _abc.Mapping) and len(objects) > 1:
        from ._merge import deep

        value = objects[0]
        for layer in objects[1:]:
            value = deep(value, layer)
        value = resolved(value)
    else:
        value = resolved(last)
    with _field(origin):
        return hook(value)


# Order matters: the no-objects overload comes first, or the variadic one
# matches a bare typed_merge(cls) and promises a T that is really None.
@typing.overload
def typed_merge(
    cls: "typing.Type[T]", *, init: bool = True, extra: str = "ignore"
) -> None: ...


@typing.overload
def typed_merge(
    cls: "typing.Type[T]", *objects: object, init: bool = True, extra: str = "ignore"
) -> T: ...


@typing.overload
def typed_merge(
    cls: typing.Any, *objects: object, init: bool = True, extra: str = "ignore"
) -> typing.Any: ...


def typed_merge(
    cls: typing.Any, *objects: object, init: bool = True, extra: str = "ignore"
) -> typing.Any:
    """Merge *objects*, in order, into one instance of *cls*.

    Type-guided: a mapping, dataclass or namespace merges field by field, each
    field through its own annotation; a sequence takes the last layer's value
    with every item coerced; a scalar takes the last value, coerced through the
    hint. ``None`` layers are skipped at every level, so a later ``None`` never
    overrides an earlier value; all-``None`` (or no) layers give ``None``. A
    layer of another shape replaces everything before it.

    Coercion never loses data: ``int`` accepts a whole-number float or an
    integer string, never ``1.9``; ``bool`` reads ``true/yes/on/1`` and
    ``false/no/off/0``; ``date``/``datetime``/``time`` read ISO strings; an
    ``Enum`` takes a value, then a member name. A Pydantic model's layers are
    deep-merged and validated by Pydantic.

    *extra* decides what a key the target class does not accept does:
    ``"ignore"`` drops it, ``"forbid"`` raises `ConfigTypeError` naming it. A
    constructor taking ``**kwargs`` accepts every key. *init* false builds the
    instance without calling ``__init__``.

    Hooks: a classmethod ``__merge__(cls, *objects, init=True)`` takes over a
    type's merging entirely (see `OpaqueMerge`); a classmethod
    ``_load_(cls, value)`` builds the type from one value (a scalar as written
    in the document when that is known, otherwise plain data); a
    ``_parse_<field>(value)`` method coerces that field as it is collected
    from each layer. Without a hook the basic coercions above apply.
    """
    if extra not in _EXTRA_MODES:
        raise ValueError(f"extra= takes {' or '.join(_EXTRA_MODES)}, not {extra!r}")
    return _merge(cls, objects, init, extra)


def hydrate(cls: "typing.Type[T]", data: object, *, extra: str = "ignore") -> T:
    """Build one *cls* from *data*, coercing every field through its annotation.

    The single-layer form of :func:`typed_merge`, and what
    ``load(..., into=cls)`` uses: a dataclass, a namespace, a mapping type, a
    Pydantic model (validated by Pydantic) or any class whose constructor takes
    the fields as keywords.

    Raises:
        ConfigTypeError: When *data* cannot be built into *cls*, or for an
            unknown key under ``extra="forbid"``.
        ConfigValueError: When a value cannot be coerced.
    """
    if data is None:
        raise ConfigTypeError(
            f"cannot build {getattr(cls, '__name__', cls)} from no data"
        )
    return typed_merge(cls, data, extra=extra)


def dehydrate(obj: object, memo: "typing.Optional[dict]" = None) -> object:
    """*obj* as plain data a format can write: the inverse of :func:`hydrate`.

    A type with a ``_dump_()`` method gives its own plain form; a dataclass, a
    namespace or another object with fields becomes a mapping; an ``Enum``
    member its value; a path, a `Decimal` or a `UUID` its text; mappings, lists
    and tuples are walked.
    Anything else is returned as it is, for the format to accept or refuse.
    Aliases and cycles are kept.
    """
    if isinstance(obj, ScalarText):
        return obj.value
    if obj is None or type(obj) in (str, int, float, bool):
        return obj
    memo = {} if memo is None else memo
    hit = memo.get(id(obj))
    if hit is not None:
        return hit[1]
    dump_hook = getattr(type(obj), "_dump_", None)
    if callable(dump_hook):
        return dehydrate(obj._dump_(), memo)
    if isinstance(obj, enum.Enum):
        return dehydrate(obj.value, memo)
    if isinstance(obj, (pathlib.PurePath, decimal.Decimal, uuid.UUID)):
        # Text no format misreads, and `hydrate` builds the type from it again.
        return str(obj)
    if isinstance(obj, _abc.Mapping):
        result: dict = {}
        memo[id(obj)] = (obj, result)
        for key, value in obj.items():
            result[dehydrate(key, memo)] = dehydrate(value, memo)
        return result
    if isinstance(obj, (list, tuple)) and not hasattr(obj, "_fields"):
        items: list = []
        memo[id(obj)] = (obj, items)
        items.extend(dehydrate(item, memo) for item in obj)
        return items
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        fields = {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
        return dehydrate(fields, memo)
    if isinstance(obj, tuple):  # a NamedTuple
        return dehydrate(obj._asdict(), memo)
    if isinstance(obj, Namespace):
        return dehydrate(vars(obj), memo)
    return obj


def _last_wins(_cls, *objects: object, init: bool = True) -> object:
    return objects[-1] if objects else None


class OpaqueMerge:
    """Mixin marking a type opaque to :func:`typed_merge`: the last layer wins.

    For a fully built object whose ``__init__`` already normalized its fields,
    and which should not be re-merged field by field — including under
    ``Optional[...]`` and other unions.
    """

    @classmethod
    def __merge__(cls, *objects: object, init: bool = True) -> object:
        return _last_wins(cls, *objects, init=init)


def opaque(cls: "typing.Type[T]") -> "typing.Type[T]":
    """Class decorator equivalent of :class:`OpaqueMerge`, leaving the bases alone."""
    cls.__merge__ = classmethod(_last_wins)
    return cls


class TypedNamespace(Namespace):
    """An :class:`argparse.Namespace` that applies ``_parse_<field>`` coercers.

    At construction each keyword's value goes through a ``_parse_<name>``
    method when the class defines one, so a built instance is already
    normalized. :func:`typed_merge` applies the same hooks to raw layers, skips
    them for a layer that is already a ``TypedNamespace``, and assembles the
    result without calling ``__init__`` — so every value is parsed exactly once
    and a parser need not be idempotent.
    """

    def __init__(self, **kwargs: object) -> None:
        for name, value in list(kwargs.items()):
            parser = getattr(self, f"_parse_{name}", None)
            if parser is not None:
                kwargs[name] = parser(value)
        super().__init__(**kwargs)
