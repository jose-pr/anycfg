"""Scalars that remember how they were written."""

from __future__ import annotations

import collections.abc as _abc
import typing as _ty

__all__ = ["ScalarText", "resolved"]


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
