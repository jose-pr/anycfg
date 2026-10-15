"""Sources that are not files: commands, the environment, Python data.

A file is a path — a `str`, an `os.PathLike` or a pathapi `Path` — and needs no
source object. Everything else a loader reads is a :class:`Source`: an object
whose :meth:`~Source.read` returns a :class:`Payload`, either text for a format
to parse or data already parsed.

A **string** source written as ``<scheme>://...`` (or ``<scheme>+<fmt>://``)
is looked up here. The built-in schemes are ``cmd``, ``exec`` and ``sh``, which
run a command; another distribution adds schemes through the
``yaconfiglib.sources`` entry-point group, and code with
:func:`register_scheme`. A document can name only built-in and code-registered
schemes; an entry-point scheme needs the caller to allow it.
"""

from __future__ import annotations

import importlib
import logging
import re
import threading
import typing as _ty

from ._base import Chunks, Data, Payload, Source, Text
from ._command import Command
from ._env import Env

__all__ = [
    "Source",
    "Payload",
    "Command",
    "Env",
    "Data",
    "Text",
    "Chunks",
    "register_scheme",
    "unregister_scheme",
    "schemes",
    "from_text",
    "SCHEME_REGEX",
    "ENTRY_POINT_GROUP",
]

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "yaconfiglib.sources"

#: ``<scheme>[+<format>]://``. Only this spelling: a bare ``cmd:echo`` is a
#: file name, and a drive letter (``C:\``) never matches.
SCHEME_REGEX = re.compile(r"^([A-Za-z][A-Za-z0-9.\-]*?)(?:\+([A-Za-z0-9_.\-]+))?://")

_BUILTINS = {
    "cmd": "yaconfiglib.sources._command:Command",
    "exec": "yaconfiglib.sources._command:Command",
    "sh": "yaconfiglib.sources._command:Command",
}

_BUILTIN, _REGISTERED, _ENTRY_POINT = "builtin", "registered", "entry_point"

_lock = threading.RLock()
#: Replaced whole under the lock, never mutated once published, so readers
#: need no lock.
_schemes: "_ty.Dict[str, _ty.Tuple[_ty.Callable[[str], Source], str]]" = {}
_state = {"loaded": False}


def _import(target: str) -> _ty.Any:
    module_name, _, attribute = target.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def _entry_points() -> _ty.Iterable[_ty.Any]:
    from importlib import metadata

    found = metadata.entry_points()
    select = getattr(found, "select", None)
    if callable(select):
        return select(group=ENTRY_POINT_GROUP)
    return found.get(ENTRY_POINT_GROUP, ())  # Python 3.9


def _ensure_loaded() -> None:
    if _state["loaded"]:
        return
    global _schemes
    with _lock:
        if _state["loaded"]:
            return
        _state["loaded"] = True
        table = dict(_schemes)
        for scheme, target in _BUILTINS.items():
            table[scheme] = (_import(target), _BUILTIN)
        for point in _entry_points():
            scheme = getattr(point, "name", "").lower()
            try:
                if scheme in table:
                    raise ValueError("the scheme is already registered")
                factory = point.load()
                if not callable(factory):
                    raise TypeError("the entry point is not callable")
                table[scheme] = (factory, _ENTRY_POINT)
            except (
                Exception
            ) as error:  # noqa: BLE001 - one bad plugin must not break loading
                logger.warning(
                    "skipping source entry point %r: %s: %s",
                    scheme,
                    type(error).__name__,
                    error,
                )
        _schemes = table


def register_scheme(
    scheme: str, factory: "_ty.Callable[[str], Source]", *, replace: bool = False
) -> None:
    """Make ``<scheme>://...`` strings load through ``factory(text)``.

    Raises `ValueError` when *scheme* is taken, unless *replace* is true.
    """
    global _schemes
    _ensure_loaded()
    key = scheme.lower()
    with _lock:
        if key in _schemes and not replace:
            raise ValueError(
                f"scheme {key!r} is already registered; pass replace=True to take it over"
            )
        _schemes = {**_schemes, key: (factory, _REGISTERED)}


def unregister_scheme(scheme: str) -> bool:
    """Remove *scheme*; return whether it was registered."""
    global _schemes
    _ensure_loaded()
    with _lock:
        table = dict(_schemes)
        found = table.pop(scheme.lower(), None) is not None
        _schemes = table
        return found


def schemes() -> "_ty.List[str]":
    """Every registered scheme, sorted."""
    _ensure_loaded()
    return sorted(_schemes)


def from_text(text: str, *, allow_plugins: bool = True) -> "_ty.Optional[Source]":
    """The source *text* names, or `None` when it is not a registered scheme.

    *allow_plugins* false limits the lookup to built-in and code-registered
    schemes, which is what a document is allowed to reach.
    """
    match = SCHEME_REGEX.match(text)
    if match is None:
        return None
    _ensure_loaded()
    found = _schemes.get(match.group(1).lower())
    if found is None:
        return None
    factory, origin = found
    if origin == _ENTRY_POINT and not allow_plugins:
        return None
    return factory(text)
