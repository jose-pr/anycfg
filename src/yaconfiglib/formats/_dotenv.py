"""Dotenv files: ``KEY=value`` assignments."""

from __future__ import annotations

import logging
import re
import typing as _ty

from .._scalar import Coerce, coercer
from ..exceptions import ConfigTypeError, ParseError
from ._base import Format

from .._context import LoadContext

__all__ = ["DotenvFormat"]

logger = logging.getLogger(__name__)

# Whitespace in this grammar is any whitespace character EXCEPT a newline. Only
# a newline ends an entry, so a form feed, U+0085 or U+2028 in a value is data;
# a bare \s would cut the value there, which is also why str.splitlines() cannot
# split the file.
_SPACE = r"[^\S\n]"
_ASSIGNMENT_RE = re.compile(
    rf"^{_SPACE}*(?:export{_SPACE}+)?([A-Za-z_][A-Za-z0-9_.\-]*){_SPACE}*="
)
_LEADING_SPACE_RE = re.compile(rf"{_SPACE}*")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
#: The only escapes decoded inside a double-quoted value. Single-quoted values
#: are raw, so a Windows path or a literal backslash belongs in single quotes.
_DOUBLE_QUOTE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
_ENCODE_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _scan_quoted(text: str, start: int, quote: str) -> "_ty.Tuple[str, int, bool]":
    """Read the quoted value opening at *start*.

    Returns the value, the index just past the closing quote, and whether a
    closing quote was found. The value may span newlines.
    """
    out: "_ty.List[str]" = []
    index = start + 1
    end = len(text)
    while index < end:
        char = text[index]
        if char == "\\":
            following = text[index + 1] if index + 1 < end else ""
            if quote == '"' and following in _DOUBLE_QUOTE_ESCAPES:
                out.append(_DOUBLE_QUOTE_ESCAPES[following])
                index += 2
                continue
            if quote == "'" and following == quote:
                # Raw, but an escaped quote still does not close the value.
                out.append(char)
                out.append(following)
                index += 2
                continue
            out.append(char)
            index += 1
            continue
        if char == quote:
            return "".join(out), index + 1, True
        out.append(char)
        index += 1
    return "".join(out), end, False


def _scan_unquoted(raw: str) -> str:
    """An unquoted value: what follows ``=``, minus a ``#`` comment and spaces.

    A ``#`` starts a comment only after whitespace, so ``B=#start`` is the
    value ``#start``. Quote characters are ordinary here, so ``it's here # c``
    loses only the comment.
    """
    for index, char in enumerate(raw):
        if char == "#" and index > 0 and raw[index - 1].isspace():
            return raw[:index].strip()
    return raw.strip()


class DotenvFormat(Format):
    """Dotenv: a flat ``{KEY: value}`` mapping of strings.

    Claims ``.env``, ``*.env`` and staged names like ``.env.production`` —
    unless the name's last suffix belongs to another format (``app.env.yaml``
    is YAML).

    Dialect, following python-dotenv where they agree:

    * ``KEY=value`` and ``export KEY=value``; spaces around ``=`` are allowed
      and dropped; keys may contain ``.`` and ``-`` after the first character
      and keep their case unless ``lowercase=True``;
    * double-quoted values may span lines and decode ``\\n \\r \\t \\" \\\\``;
      single-quoted values may span lines and are raw;
    * an unquoted value runs to the end of the line, minus a ``#`` comment
      preceded by whitespace; quote characters inside it are ordinary;
    * only a newline ends an entry; a later duplicate key replaces an earlier
      one.

    A line that is not an assignment logs a warning naming the line (never its
    text) and is skipped, or raises `ParseError` with ``strict=True``. An
    unterminated quote always raises.

    Values are strings unless the load option ``coerce`` is set: `True` reads
    each **unquoted** value by the YAML 1.2 core rules (``PORT=80`` an int,
    ``DEBUG=true`` a bool, ``PORT="80"`` still text), a callable
    ``(text) -> value`` decides itself.

    ``dumps`` writes a flat mapping of string values, double-quoted and
    escaped, so the round trip is exact.
    """

    name = "dotenv"
    suffixes = (".env",)
    load_options = frozenset({"lowercase", "strict", "coerce"})

    @staticmethod
    def claims_name(name: str) -> bool:
        """A staged name, ``.env.<stage>``, that no format's suffix claimed first."""
        return name.startswith(".env.")

    def loads(
        self,
        data: _ty.Any,
        /,
        *,
        context: "_ty.Optional[LoadContext]" = None,
        lowercase: bool = False,
        strict: bool = False,
        coerce: "Coerce" = False,
        **options: _ty.Any,
    ) -> "_ty.Dict[str, _ty.Any]":
        convert = coercer(coerce)
        path = (context.name if context is not None else None) or None
        label = path or "<string>"
        text = data.replace("\r\n", "\n").replace("\r", "\n")

        def reject(line: int, reason: str) -> None:
            if strict:
                raise ParseError(reason, path=path, lineno=line)
            logger.warning("%s: line %d: %s; skipped", label, line, reason)

        result: "_ty.Dict[str, str]" = {}
        end = len(text)
        position = 0
        line = 1
        while position < end:
            eol = text.find("\n", position)
            if eol < 0:
                eol = end
            raw = text[position:eol]
            statement = raw.strip()
            if not statement or statement.startswith("#"):
                line += 1
                position = eol + 1
                continue

            match = _ASSIGNMENT_RE.match(raw)
            if match is None:
                reject(line, "not a KEY=value assignment")
                line += 1
                position = eol + 1
                continue

            key = match.group(1)
            value_at = position + _LEADING_SPACE_RE.match(raw, match.end()).end()
            quote = text[value_at] if value_at < eol else ""
            if quote in ('"', "'"):
                value, after, closed = _scan_quoted(text, value_at, quote)
                if not closed:
                    raise ParseError(
                        f"unterminated quoted value for {key!r}", path=path, lineno=line
                    )
                tail_eol = text.find("\n", after)
                if tail_eol < 0:
                    tail_eol = end
                tail = text[after:tail_eol].strip()
                consumed = text.count("\n", position, tail_eol)
                if tail and not tail.startswith("#"):
                    reject(line, f"text after the closing quote of {key!r}")
                    line += consumed + 1
                    position = tail_eol + 1
                    continue
                line += consumed + 1
                position = tail_eol + 1
            else:
                value = _scan_unquoted(raw[match.end() :])
                if convert is not None:
                    value = convert(value)
                line += 1
                position = eol + 1

            result[key.lower() if lowercase else key] = value

        return result

    def dumps(self, obj: _ty.Any, /, **options: _ty.Any) -> str:
        if not isinstance(obj, _ty.Mapping):
            raise ConfigTypeError(
                f"dotenv holds a flat mapping, not a {type(obj).__name__}"
            )
        lines = []
        for key, value in obj.items():
            if not isinstance(key, str) or not _KEY_RE.match(key):
                raise ConfigTypeError(f"{key!r} is not a dotenv variable name")
            if not isinstance(value, str):
                raise ConfigTypeError(
                    f"dotenv values are strings; {key} is a {type(value).__name__}"
                )
            escaped = "".join(_ENCODE_ESCAPES.get(char, char) for char in value)
            lines.append(f'{key}="{escaped}"\n')
        return "".join(lines)
