"""The format registry: which formats exist, and which one reads a given file.

Built-in formats (``json``, ``yaml``, ``toml``, ``ini``, ``dotenv``) are always
listed, whether or not their optional dependency is installed; using one whose
dependency is missing raises naming the extra to install. Another distribution
adds formats through the ``yaconfiglib.formats`` entry-point group, and code
adds one with :func:`register_format`.

Selection is by explicit name first (``format="yaml"``, aliases included,
case-insensitive), then by file name: the longest registered suffix wins,
compared case-insensitively, and a format may also claim names a suffix cannot
describe (dotenv's ``.env.production``). Lookup is linear in the name.
"""

from __future__ import annotations

import importlib
import logging
import threading
import typing as _ty

from ..exceptions import UnsupportedFormatError
from ._base import Format, capabilities

__all__ = [
    "Format",
    "capabilities",
    "register_format",
    "unregister_format",
    "names",
    "get",
    "for_filename",
    "find_for_filename",
    "document_formats",
    "ENTRY_POINT_GROUP",
]

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "yaconfiglib.formats"

#: Built-in formats, imported on first use. Their modules import nothing
#: optional at import time, so every one of them is always listed.
_BUILTINS = {
    "json": "yaconfiglib.formats._json:JSONFormat",
    "yaml": "yaconfiglib.formats._yaml:YAMLFormat",
    "toml": "yaconfiglib.formats._toml:TOMLFormat",
    "ini": "yaconfiglib.formats._ini:INIFormat",
    "dotenv": "yaconfiglib.formats._dotenv:DotenvFormat",
}

#: Where a registration came from. A document can select only built-in and
#: code-registered formats unless the caller names more: installing a package
#: must not make its formats reachable from a hostile file.
_BUILTIN, _REGISTERED, _ENTRY_POINT = "builtin", "registered", "entry_point"


class _Entry(_ty.NamedTuple):
    cls: "_ty.Type[Format]"
    origin: str


_lock = threading.RLock()
_by_name: "_ty.Dict[str, _Entry]" = {}
_by_suffix: "_ty.Dict[str, _ty.List[_Entry]]" = {}
_entries: "_ty.Dict[str, _Entry]" = {}
_state = {"builtins": False, "entry_points": False}


def _import(target: str) -> type:
    module_name, _, attribute = target.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def _validate(cls: object) -> "_ty.Type[Format]":
    if not (isinstance(cls, type) and issubclass(cls, Format)):
        raise TypeError(f"a format must be a Format subclass, not {cls!r}")
    if not cls.name or cls.name != cls.name.lower():
        raise ValueError(f"{cls.__name__}.name must be a non-empty lower-case name")
    for suffix in cls.suffixes:
        if not suffix.startswith(".") or suffix != suffix.lower():
            raise ValueError(
                f"{cls.__name__}.suffixes: {suffix!r} must be lower-case and start "
                "with a dot"
            )
    return cls


def _add(cls: "_ty.Type[Format]", origin: str, replace: bool) -> None:
    """Register *cls*, refusing a name or suffix someone else holds."""
    entry = _Entry(cls, origin)
    keys = [cls.name] + [alias.lower() for alias in cls.aliases]
    for key in keys:
        held = _by_name.get(key)
        if held is not None and held.cls is not cls and not replace:
            raise ValueError(
                f"format name {key!r} is already registered by "
                f"{held.cls.__qualname__}; pass replace=True to take it over"
            )
    for suffix in cls.suffixes:
        for held in _by_suffix.get(suffix, ()):
            if (
                held.cls is not cls
                and held.cls.priority == cls.priority
                and not replace
                and held.cls.name not in keys
            ):
                raise ValueError(
                    f"suffix {suffix!r} is already claimed by "
                    f"{held.cls.__qualname__} at priority {cls.priority}; give "
                    "this format another priority or pass replace=True"
                )
    if replace:
        for key in keys:
            old = _by_name.get(key)
            if old is not None and old.cls is not cls:
                _remove(old.cls.name)
    for key in keys:
        _by_name[key] = entry
    for suffix in cls.suffixes:
        claims = [held for held in _by_suffix.get(suffix, ()) if held.cls is not cls]
        if replace:
            claims = [held for held in claims if held.cls.priority != cls.priority]
        claims.append(entry)
        claims.sort(key=lambda held: -held.cls.priority)
        _by_suffix[suffix] = claims
    _entries[cls.name] = entry


def _remove(name: str) -> bool:
    entry = _entries.pop(name, None)
    if entry is None:
        return False
    for key in [k for k, held in _by_name.items() if held.cls is entry.cls]:
        del _by_name[key]
    for suffix in list(_by_suffix):
        claims = [held for held in _by_suffix[suffix] if held.cls is not entry.cls]
        if claims:
            _by_suffix[suffix] = claims
        else:
            del _by_suffix[suffix]
    return True


def _entry_points() -> _ty.Iterable[_ty.Any]:
    from importlib import metadata

    found = metadata.entry_points()
    select = getattr(found, "select", None)
    if callable(select):
        return select(group=ENTRY_POINT_GROUP)
    return found.get(ENTRY_POINT_GROUP, ())  # Python 3.9


def _ensure_loaded() -> None:
    """Import the built-ins, then read the entry points, once each."""
    if _state["builtins"] and _state["entry_points"]:
        return
    with _lock:
        if not _state["builtins"]:
            for target in _BUILTINS.values():
                _add(_validate(_import(target)), _BUILTIN, False)
            _state["builtins"] = True
        if not _state["entry_points"]:
            # Set first: an entry point that imports yaconfiglib.formats while
            # loading must not recurse into this loop.
            _state["entry_points"] = True
            for point in _entry_points():
                try:
                    cls = _validate(point.load())
                    if cls.name in _BUILTINS or any(
                        alias.lower() in _BUILTINS for alias in cls.aliases
                    ):
                        raise ValueError("an entry point cannot take a built-in name")
                    _add(cls, _ENTRY_POINT, False)
                except (
                    Exception
                ) as error:  # noqa: BLE001 - one bad plugin must not break loading
                    # Logged and skipped, as tplx does: an installed package
                    # that registers badly cannot be allowed to stop every load.
                    logger.warning(
                        "skipping format entry point %r: %s: %s",
                        getattr(point, "name", point),
                        type(error).__name__,
                        error,
                    )


def register_format(
    cls: "_ty.Optional[_ty.Type[Format]]" = None, *, replace: bool = False
) -> _ty.Any:
    """Register a format class; usable bare or as a decorator.

    Raises `ValueError` when another class holds one of its names, or claims
    one of its suffixes at the same priority, unless *replace* is true. A
    registered format can be selected by documents, like a built-in.
    """

    def apply(target: "_ty.Type[Format]") -> "_ty.Type[Format]":
        _ensure_loaded()
        with _lock:
            _add(_validate(target), _REGISTERED, replace)
        return target

    return apply if cls is None else apply(cls)


def unregister_format(name: str) -> bool:
    """Remove the format registered under *name*; return whether one was."""
    _ensure_loaded()
    with _lock:
        entry = _by_name.get(name.lower())
        return _remove(entry.cls.name) if entry is not None else False


def names() -> "_ty.List[str]":
    """Every registered format name, sorted."""
    _ensure_loaded()
    return sorted(_entries)


def document_formats() -> "_ty.FrozenSet[str]":
    """The formats a document may select by default: built-in and code-registered."""
    _ensure_loaded()
    return frozenset(
        name for name, entry in _entries.items() if entry.origin != _ENTRY_POINT
    )


def _refuse_unreachable(
    entry: _Entry, allowed: "_ty.Optional[_ty.Collection[str]]", asked: str
) -> None:
    if allowed is not None and entry.cls.name not in allowed:
        raise UnsupportedFormatError(
            f"format {entry.cls.name!r} (for {asked!r}) is not enabled for "
            f"documents; enabled: {', '.join(sorted(allowed)) or 'none'}. Name it "
            "in the loader's formats= to allow it."
        )


def get(
    name: str, *, allowed: "_ty.Optional[_ty.Collection[str]]" = None
) -> "_ty.Type[Format]":
    """The format class registered under *name* or one of its aliases.

    *allowed*, when given, is the set of format names that may be returned;
    anything else is refused like an unknown name.

    Raises:
        UnsupportedFormatError: For a name nothing registers, listing the names
            that are.
    """
    _ensure_loaded()
    entry = _by_name.get(str(name).lower())
    if entry is None:
        raise UnsupportedFormatError(
            f"unknown format {name!r}; registered: {', '.join(names())}"
        )
    _refuse_unreachable(entry, allowed, name)
    return entry.cls


def find_for_filename(
    filename: str, *, allowed: "_ty.Optional[_ty.Collection[str]]" = None
) -> "_ty.Optional[_ty.Type[Format]]":
    """The format a file name selects, or `None` when none claims it.

    *filename* is a base name or a path; only the last component is used.
    """
    _ensure_loaded()
    base = str(filename).replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not base:
        return None
    # Every registered suffix that fits in the name's tail, longest first:
    # "a.tar.json" asks ".tar.json", then ".json". Only the last `longest`
    # characters are ever examined, so the lookup is linear in the name.
    longest = max((len(suffix) for suffix in _by_suffix), default=0)
    tail = base[-longest:] if longest else ""
    position = tail.find(".")
    while position != -1:
        claims = _by_suffix.get(tail[position:])
        if claims and base.endswith(tail[position:]):
            return _first_reachable(claims, allowed, filename)
        position = tail.find(".", position + 1)
    for entry in _entries.values():
        claims_name = getattr(entry.cls, "claims_name", None)
        if callable(claims_name) and claims_name(base):
            _refuse_unreachable(entry, allowed, filename)
            return entry.cls
    return None


def _first_reachable(
    claims: "_ty.List[_Entry]",
    allowed: "_ty.Optional[_ty.Collection[str]]",
    asked: str,
) -> "_ty.Type[Format]":
    """The highest-priority claim *allowed* admits; a plugin outranking a
    built-in does not take the suffix away from documents."""
    for entry in claims:
        if allowed is None or entry.cls.name in allowed:
            return entry.cls
    _refuse_unreachable(claims[0], allowed, asked)
    return claims[0].cls


def for_filename(
    filename: str, *, allowed: "_ty.Optional[_ty.Collection[str]]" = None
) -> "_ty.Type[Format]":
    """Like :func:`find_for_filename`, but raises when no format claims the name.

    Raises:
        UnsupportedFormatError: Naming the registered formats.
    """
    found = find_for_filename(filename, allowed=allowed)
    if found is None:
        raise UnsupportedFormatError(
            f"no format reads {filename}; pass format=<name> "
            f"(registered: {', '.join(names())})"
        )
    return found
