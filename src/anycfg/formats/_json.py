"""JSON, through the standard library."""

from __future__ import annotations

import json
import typing as _ty

from ..exceptions import ConfigTypeError, ConfigValueError, ParseError
from ._base import Format

from .._context import LoadContext

__all__ = ["JSONFormat"]


def _check_finite(value: _ty.Any) -> _ty.Any:
    """`parse_constant` hook: ``NaN`` and the infinities are not JSON."""
    raise ValueError(f"{value} is not valid JSON")


class JSONFormat(Format):
    """JSON (RFC 8259) through :mod:`json`.

    Dialect: duplicate keys keep the last value, as :mod:`json` does; ``NaN``
    and ``Infinity`` are refused in both directions; numbers keep Python's
    ``int``/``float`` split. ``dumps`` writes UTF-8 text unescaped, indented two
    spaces, keys in insertion order.

    Load options are :func:`json.loads`'s hooks: ``object_hook``,
    ``object_pairs_hook``, ``parse_float``, ``parse_int``. Dump options:
    ``indent``, ``sort_keys``, ``ensure_ascii``, ``separators``, ``default``.
    """

    name = "json"
    suffixes = (".json",)
    load_options = frozenset(
        {"object_hook", "object_pairs_hook", "parse_float", "parse_int"}
    )
    dump_options = frozenset(
        {"indent", "sort_keys", "ensure_ascii", "separators", "default"}
    )

    def loads(
        self,
        data: _ty.Any,
        /,
        *,
        context: "_ty.Optional[LoadContext]" = None,
        **options: _ty.Any,
    ) -> _ty.Any:
        path = (context.name if context is not None else None) or None
        # Raised after the except blocks, so the parser's own exception, which
        # may hold the document, is not even kept as __context__.
        try:
            return json.loads(data, parse_constant=_check_finite, **options)
        except json.JSONDecodeError as error:
            failure = ParseError(
                error.msg, path=path, lineno=error.lineno, colno=error.colno
            )
        except ValueError as error:
            # parse_constant's refusal, or a parse_int/parse_float hook's.
            failure = ParseError(str(error), path=path)
        except RecursionError:
            failure = ParseError("document is nested too deeply", path=path)
        raise failure

    def dumps(self, obj: _ty.Any, /, **options: _ty.Any) -> str:
        options.setdefault("indent", 2)
        options.setdefault("ensure_ascii", False)
        try:
            return json.dumps(obj, allow_nan=False, **options)
        except TypeError as error:
            raise ConfigTypeError(f"cannot write JSON: {error}") from None
        except ValueError as error:
            # json's own wording names the problem (an out-of-range float, a
            # circular reference), never the value.
            raise ConfigValueError(f"cannot write JSON: {error}") from None
