"""Scalars that remember how they were written."""

from __future__ import annotations

import collections.abc as _abc
import re
import typing as _ty

__all__ = ["ScalarText", "resolved", "core_scalar", "coercer"]


class ScalarText(str):
    """A plain scalar as written in the document, carrying the value it resolved to.

    Produced only while loading into a type (``into=``): the target type then
    decides between the text and the format's own reading of it, so a YAML
    ``NO`` destined for a `str` field stays ``"NO"`` instead of becoming
    `False`. Anything without type information gets :attr:`value`.
    """

    value: _ty.Any

    def __new__(cls, text: str, value: _ty.Any) -> "ScalarText":
        instance = super().__new__(cls, text)
        instance.value = value
        return instance

    def __reduce__(self) -> tuple:
        return (ScalarText, (str(self), self.value))

    def __repr__(self) -> str:
        return f"ScalarText({str(self)!r}, {self.value!r})"


def resolved(value: _ty.Any, memo: "_ty.Optional[dict]" = None) -> _ty.Any:
    """*value* with every `ScalarText` replaced by its resolved value.

    Copies only containers that change; aliases and cycles are kept.
    """
    if isinstance(value, ScalarText):
        return value.value
    kind = type(value)
    if kind in (str, int, float, bool) or value is None:
        return value
    memo = {} if memo is None else memo
    hit = memo.get(id(value))
    if hit is not None:
        return hit[1]
    if isinstance(value, _abc.Mapping):
        memo[id(value)] = (value, value)
        items = [(resolved(k, memo), resolved(v, memo)) for k, v in value.items()]
        changed = any(
            nk is not k or nv is not v for (nk, nv), (k, v) in zip(items, value.items())
        )
        result = dict(items) if changed else value
    elif kind in (list, tuple):
        memo[id(value)] = (value, value)
        items_list = [resolved(item, memo) for item in value]
        changed = any(new is not old for new, old in zip(items_list, value))
        result = kind(items_list) if changed else value
    else:
        return value
    memo[id(value)] = (value, result)
    return result


#: The YAML 1.2 core schema's plain scalars: what ``dialect="1.2"`` and
#: ``coerce=True`` read a bare word as.
CORE_NULL = re.compile(r"(?:~|null|Null|NULL)\Z")
CORE_BOOL = re.compile(r"(?:true|True|TRUE|false|False|FALSE)\Z")
CORE_INT = re.compile(r"(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)\Z")
CORE_FLOAT = re.compile(
    r"(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))\Z"
)


def core_int(text: str) -> int:
    if text.startswith("0o"):
        return int(text[2:], 8)
    if text.startswith("0x"):
        return int(text[2:], 16)
    return int(text, 10)


def core_float(text: str) -> float:
    lowered = text.lower()
    if lowered.endswith(".inf"):
        return float("-inf") if lowered.startswith("-") else float("inf")
    if lowered == ".nan":
        return float("nan")
    return float(text)


def core_scalar(text: str) -> _ty.Any:
    """*text* as the YAML 1.2 core schema reads a plain scalar; else *text*.

    ``null``/``~``, ``true``/``false`` (three spellings each), decimal, ``0o``
    and ``0x`` integers, and floats with ``.inf``/``.nan``. ``yes``, ``on``,
    ``0755``-as-octal and dates stay text; so does the empty string.
    """
    if CORE_NULL.match(text):
        return None
    if CORE_BOOL.match(text):
        return text[0] in "tT"
    if CORE_INT.match(text):
        return core_int(text)
    if CORE_FLOAT.match(text):
        return core_float(text)
    return text


Coerce = _ty.Union[bool, _ty.Callable[[str], _ty.Any]]


def coercer(choice: Coerce) -> "_ty.Optional[_ty.Callable[[str], _ty.Any]]":
    """What a ``coerce=`` option means: `None` (keep text), the YAML 1.2 core
    rules for `True`, or the caller's ``(text) -> value``.

    Raises:
        TypeError: For anything else.
    """
    if choice is False or choice is None:
        return None
    if choice is True:
        return core_scalar
    if callable(choice):
        return choice
    raise TypeError(
        f"coerce= takes a bool or a callable (text) -> value, not {choice!r}"
    )
