"""The process environment as a source."""

from __future__ import annotations

import os
import typing as _ty

from .._scalar import Coerce, coercer
from ..exceptions import ConfigValueError
from ._base import Payload, Source

from .._context import LoadContext

__all__ = ["Env"]

#: Windows environment names are case-insensitive and os.environ upper-cases
#: them, so a lower-case prefix would match nothing there. A module flag rather
#: than an inline os.name test, so tests can exercise both modes anywhere.
_KEYS_CASE_INSENSITIVE = os.name == "nt"


def _has_prefix(key: str, prefix: str) -> bool:
    if _KEYS_CASE_INSENSITIVE:
        return key.upper().startswith(prefix.upper())
    return key.startswith(prefix)


def _conflict(name: str, other: str, path: "_ty.Tuple[str, ...]") -> ConfigValueError:
    return ConfigValueError(
        f"environment variables {other!r} and {name!r} both define "
        f"{'.'.join(path)!r}: one is a value and the other nests keys under it. "
        "Rename or remove one of them."
    )


def _set_nested(
    result: "_ty.Dict[str, _ty.Any]",
    parts: "_ty.List[str]",
    value: str,
    origins: "_ty.Dict[_ty.Tuple[str, ...], str]",
    name: str,
) -> None:
    """Write *value* at *parts*, refusing to overwrite another variable's work.

    *origins* maps each key path to the variable that produced it, parents
    included, so a collision names both variables.
    """
    current = result
    walked: "_ty.Tuple[str, ...]" = ()
    for part in parts[:-1]:
        walked += (part,)
        existing = current.get(part)
        if not isinstance(existing, dict):
            if part in current:
                raise _conflict(name, origins.get(walked, "<unknown>"), walked)
            existing = {}
            current[part] = existing
            origins[walked] = name
        current = existing
    walked += (parts[-1],)
    if isinstance(current.get(parts[-1]), dict):
        raise _conflict(name, origins.get(walked, "<unknown>"), walked)
    current[parts[-1]] = value
    origins[walked] = name


class Env(Source):
    """Environment variables as a configuration document.

    Only variables starting with *prefix* are read, with the prefix removed.
    With *nested* (``"__"``), ``APP_DB__PORT`` becomes ``{"DB": {"PORT": ...}}``;
    a variable that is both a value and a parent of nested keys raises, naming
    both. Values stay strings — types come from hydration (``load(into=...)``)
    — unless *coerce* is set: `True` reads each value by the YAML 1.2 core
    rules (``80`` an int, ``true`` a bool, ``null`` `None`; ``yes`` and ``0755``
    stay text), a callable ``(text) -> value`` decides itself.
    Keys keep their case unless *lowercase* is set. On Windows the prefix is
    matched case-insensitively, as the environment is.

    *environ* replaces `os.environ`, for tests and for a captured snapshot.
    """

    def __init__(
        self,
        prefix: str = "",
        *,
        nested: "_ty.Optional[str]" = None,
        lowercase: bool = False,
        coerce: "Coerce" = False,
        environ: "_ty.Optional[_ty.Mapping[str, str]]" = None,
    ) -> None:
        self.prefix = prefix
        self.nested = nested
        self.lowercase = lowercase
        self.coerce = coerce
        self._coerce = coercer(coerce)
        self.environ = environ

    @property
    def name(self) -> str:
        return f"env:{self.prefix}"

    def read(self, context: "LoadContext") -> Payload:
        environ = os.environ if self.environ is None else self.environ
        result: "_ty.Dict[str, _ty.Any]" = {}
        origins: "_ty.Dict[_ty.Tuple[str, ...], str]" = {}
        prefix = self.prefix
        for key, value in environ.items():
            if prefix and not _has_prefix(key, prefix):
                continue
            clean = key[len(prefix) :]
            if self._coerce is not None:
                value = self._coerce(value)
            if self.lowercase:
                clean = clean.lower()
            if not clean:
                # A variable equal to the prefix would produce a "" key.
                continue
            if self.nested and self.nested in clean:
                parts = [part for part in clean.split(self.nested) if part]
                if parts:
                    _set_nested(result, parts, value, origins, key)
                continue
            if not self.nested:
                # Every variable is a leaf: no collision is possible, and this
                # is the common path.
                result[clean] = value
                continue
            if isinstance(result.get(clean), dict):
                raise _conflict(key, origins.get((clean,), "<unknown>"), (clean,))
            result[clean] = value
            origins[(clean,)] = key
        return Payload(result, name=self.name, identity=self.name, parsed=True)

    def __repr__(self) -> str:
        return f"Env({self.prefix!r})"
