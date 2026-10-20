"""``.cfg``: a document that names its own format, else TOML."""

from __future__ import annotations

import typing as _ty

from ._base import Format, capabilities

from .._context import LoadContext

__all__ = ["CfgFormat"]


class CfgFormat(Format):
    """A ``.cfg`` file: read in the format it names, else as TOML.

    ``.cfg`` is used by many formats, so the name decides nothing: the document
    is asked first, as :func:`~anycfg.formats.declared` asks — a ``#!<format>``
    first line (``#!ini``, ``#!/usr/bin/env yaml``; removed before parsing) or
    a format's own marker such as YAML's ``%YAML`` directive. A document that
    names none is TOML. A named format is one documents may select by default
    (built-in or registered in code).

    No load option is taken, since the format is known only once the document
    is read. ``dumps`` writes TOML, so ``dump(x, "app.cfg")`` and
    ``load("app.cfg")`` round-trip.
    """

    name = "cfg"
    suffixes = (".cfg",)

    @staticmethod
    def _named(data: _ty.Any) -> "_ty.Tuple[_ty.Type[Format], _ty.Any]":
        from . import declared, document_formats, get

        name, data = declared(data)
        if name is None or name == CfgFormat.name:
            return get("toml"), data
        return get(name, allowed=document_formats()), data

    def loads(
        self,
        data: _ty.Any,
        /,
        *,
        context: "_ty.Optional[LoadContext]" = None,
        **options: _ty.Any,
    ) -> _ty.Any:
        cls, data = self._named(data)
        return cls().loads(data, context=context)

    def loads_all(
        self,
        data: _ty.Any,
        /,
        *,
        context: "_ty.Optional[LoadContext]" = None,
        **options: _ty.Any,
    ) -> _ty.Iterator[_ty.Any]:
        cls, data = self._named(data)
        if "load_all" in capabilities(cls):
            yield from cls().loads_all(data, context=context)
        else:
            yield cls().loads(data, context=context)

    def dumps(self, obj: _ty.Any, /, **options: _ty.Any) -> str:
        from . import get

        return get("toml")().dumps(obj, **options)
