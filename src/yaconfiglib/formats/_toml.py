"""TOML: tomllib (or its tomli backport) to read, tomli-w to write."""

from __future__ import annotations

import re
import typing as _ty

from ..exceptions import ConfigTypeError, MissingDependencyError, ParseError
from ._base import Format

from .._context import LoadContext

__all__ = ["TOMLFormat"]

#: tomllib's position suffix, for interpreters whose error has no lineno.
_POSITION = re.compile(r"\s*\(at line (\d+), column (\d+)\)$")


def _reader() -> _ty.Any:
    try:
        import tomllib

        return tomllib
    except ImportError:
        pass
    try:
        import tomli
    except ImportError:
        raise MissingDependencyError(
            'TOML on Python < 3.11 needs the "toml" extra: '
            'pip install "yaconfiglib[toml]"'
        ) from None
    return tomli


def _writer() -> _ty.Any:
    try:
        import tomli_w
    except ImportError:
        raise MissingDependencyError(
            'writing TOML needs the "toml" extra: pip install "yaconfiglib[toml]"'
        ) from None
    return tomli_w


class TOMLFormat(Format):
    """TOML 1.0 through :mod:`tomllib` (3.11+) or its ``tomli`` backport.

    The third-party ``toml`` package is never used: it implements TOML 0.5 and
    would read some 1.0 documents differently. ``tomli`` 2.4+ also accepts some
    TOML 1.1 syntax that 3.11-3.14's ``tomllib`` refuses, so only 1.0 is
    portable. Dialect: duplicate keys raise; there is no null; datetimes load as
    `datetime` objects.

    ``dumps`` needs ``tomli-w`` (the ``toml`` extra); a `None` anywhere, which
    TOML cannot spell, raises before anything is written.
    """

    name = "toml"
    suffixes = (".toml",)

    def loads(
        self,
        data: _ty.Any,
        /,
        *,
        context: "_ty.Optional[LoadContext]" = None,
        **options: _ty.Any,
    ) -> _ty.Any:
        reader = _reader()
        path = (context.name if context is not None else None) or None
        try:
            return reader.loads(data)
        except reader.TOMLDecodeError as error:
            msg = getattr(error, "msg", None) or str(error)
            lineno = getattr(error, "lineno", None)
            colno = getattr(error, "colno", None)
            found = _POSITION.search(msg)
            if found is not None:
                msg = msg[: found.start()]
                lineno = lineno or int(found.group(1))
                colno = colno or int(found.group(2))
            failure = ParseError(msg, path=path, lineno=lineno, colno=colno)
        except RecursionError:
            failure = ParseError("document is nested too deeply", path=path)
        # Outside the except blocks: TOMLDecodeError keeps the whole document.
        raise failure

    def dumps(self, obj: _ty.Any, /, **options: _ty.Any) -> str:
        writer = _writer()
        if not isinstance(obj, _ty.Mapping):
            raise ConfigTypeError(
                f"a TOML document is a table, not a {type(obj).__name__}"
            )
        try:
            return writer.dumps(obj)
        except (TypeError, ValueError) as error:
            raise ConfigTypeError(f"cannot write TOML: {error}") from None
