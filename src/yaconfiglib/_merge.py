"""Merge strategies for combining configuration documents.

Three built-in strategies, selected by name:

* ``simple`` — a mapping over a mapping updates the top-level keys; everything
  else replaces.
* ``substitute`` — mappings merge recursively; lists and other values replace.
* ``deep`` — mappings merge recursively and lists extend with the items not
  already present (``mergelists=True`` also merges mappings at the same index
  that share a key).

Rules every strategy follows:

* A **leaf** — anything that is not a mapping or a list — replaces, and so does
  a value of a different shape. No combination of types raises.
* ``None`` in the overriding document means "no opinion": the earlier value is
  kept. With the merge option ``nulls="replace"`` it sets the key to ``None``
  instead, and :data:`INHERIT` (``!inherit`` in YAML) is the explicit "keep the
  earlier value". To remove a key, set it to :data:`DELETE` (``!delete``).
* A mapping overridden by a non-empty list of mappings folds them in, in order
  (``substitute`` and ``deep``).
* Copy-on-write: a strategy never modifies its inputs, so a document whose keys
  share one object (a YAML anchor) can be overridden without rewriting its
  siblings. The result may share unchanged parts with the inputs.

A strategy is any callable ``(a, b, *, memo=None, **options) -> merged``.
``memo`` is internal: a per-call map of container pairs already merged, which
keeps a node aliased in both inputs aliased in the result and lets a
self-referencing document merge without recursing forever. A new strategy must
thread it into every nested call.
"""

from __future__ import annotations

import collections.abc as _abc
import copy
import threading
import typing as _ty

from ._scalar import ScalarText

__all__ = [
    "DELETE",
    "INHERIT",
    "Merge",
    "simple",
    "substitute",
    "deep",
    "register_merge",
    "merge_strategies",
    "resolve_merge",
]


class _Delete:
    """The type of :data:`DELETE`; one instance, which survives copying and pickling."""

    _instance: "_ty.Optional[_Delete]" = None

    def __new__(cls) -> "_Delete":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "DELETE"

    def __reduce__(self) -> str:
        return "DELETE"

    def __copy__(self) -> "_Delete":
        return self

    def __deepcopy__(self, memo: dict) -> "_Delete":
        return self


#: Set a key to this in an overriding document to remove it from the result.
DELETE = _Delete()


class _Inherit:
    """The type of :data:`INHERIT`; one instance, which survives copying and pickling."""

    _instance: "_ty.Optional[_Inherit]" = None

    def __new__(cls) -> "_Inherit":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "INHERIT"

    def __reduce__(self) -> str:
        return "INHERIT"

    def __copy__(self) -> "_Inherit":
        return self

    def __deepcopy__(self, memo: dict) -> "_Inherit":
        return self


#: Set a key to this in an overriding document to keep the earlier value: what
#: ``None`` means by default, said explicitly. A key no earlier document set is
#: left out.
INHERIT = _Inherit()

_NULLS = ("keep", "replace")


def _nulls(options: _ty.Mapping[str, _ty.Any]) -> bool:
    """Whether ``None`` replaces, from the ``nulls`` merge option."""
    choice = options.get("nulls", "keep")
    if choice not in _NULLS:
        from .exceptions import ConfigValueError

        raise ConfigValueError(
            f"merge option nulls= takes {' or '.join(map(repr, _NULLS))}, not "
            f"{choice!r}"
        )
    return choice == "replace"


class Merge(_ty.Protocol):
    """A merge strategy: combine *a* with the later document *b*."""

    def __call__(
        self,
        a: _ty.Any,
        b: _ty.Any,
        *,
        memo: "_ty.Optional[dict]" = None,
        **options: _ty.Any,
    ) -> _ty.Any: ...


def _is_mapping(obj: object) -> bool:
    # An exact-type test first: the ABC check costs several times as much.
    return type(obj) is dict or isinstance(obj, _abc.Mapping)


def _is_array(obj: object) -> bool:
    """A sequence that is not text, bytes or a mapping."""
    kind = type(obj)
    if kind is list or kind is tuple:
        return True
    if kind is str or kind is dict or kind is bytes:
        return False
    if isinstance(obj, (str, bytes, bytearray, memoryview, _abc.Mapping)):
        return False
    return isinstance(obj, _abc.Sequence)


def _mapping_with(a: _ty.Mapping, items: dict) -> _ty.Mapping:
    """A mapping of *a*'s type holding *items*, without touching *a*.

    A ``dict`` subclass is shallow-copied so its type and state (a
    ``defaultdict``'s factory) survive. Any other Mapping is rebuilt through its
    constructor, given the items positionally (keys are data, not names),
    falling back to a plain dict when that refuses them.
    """
    if type(a) is dict:
        return items
    if isinstance(a, dict):
        result = copy.copy(a)
        result.clear()
        result.update(items)
        return result
    try:
        return type(a)(items)
    except (TypeError, ValueError):
        return dict(items)


def _sequence_with(a: _ty.Sequence, items: list) -> _ty.Sequence:
    """A sequence of *a*'s type holding *items*, without touching *a*.

    A NamedTuple takes its items as fields, and becomes a plain tuple when they
    no longer fit them.
    """
    if type(a) is list:
        return items
    if isinstance(a, list):
        result = copy.copy(a)
        result[:] = items
        return result
    if isinstance(a, tuple) and hasattr(a, "_fields"):
        try:
            return type(a)(*items)
        except TypeError:
            return tuple(items)
    try:
        return type(a)(items)
    except TypeError:
        return items


def _fold(
    strategy: Merge, a: _ty.Any, b: _ty.Any, memo: dict, options: dict
) -> _ty.Any:
    """A mapping overridden by a non-empty list of mappings: merge each in, in order."""
    result = a
    for item in b:
        result = strategy(result, item, memo=memo, **options)
    return result


def _folds(a: _ty.Any, b: _ty.Any) -> bool:
    return (
        _is_mapping(a)
        and _is_array(b)
        and len(b) > 0
        and all(_is_mapping(item) for item in b)
    )


def _merge_mappings(
    strategy: Merge,
    a: _ty.Mapping,
    b: _ty.Mapping,
    memo: dict,
    options: dict,
    recurse: bool,
) -> _ty.Mapping:
    key = (id(a), id(b))
    hit = memo.get(key)
    if hit is not None:
        return hit[2]
    target = dict(a)
    if isinstance(a, dict) and type(a) is not dict:
        # Registered before recursing, so a document that refers to itself
        # resolves to the in-progress result.
        result = copy.copy(a)
        result.clear()
    else:
        result = target if type(a) is dict else None
    if result is not None:
        memo[key] = (a, b, result)
    replace_nulls = _nulls(options)
    for name, value in b.items():
        if value is DELETE:
            target.pop(name, None)
        elif value is INHERIT:
            continue
        elif value is None:
            if replace_nulls:
                target[name] = None
            else:
                target.setdefault(name, None)
        elif recurse and name in target:
            target[name] = strategy(target[name], value, memo=memo, **options)
        else:
            target[name] = value
    if result is None:
        result = _mapping_with(a, target)
        memo[key] = (a, b, result)
    elif result is not target:
        result.update(target)
    return result


def simple(
    a: _ty.Any, b: _ty.Any, *, memo: "_ty.Optional[dict]" = None, **options: _ty.Any
) -> _ty.Any:
    """Update top-level keys; everything else replaces."""
    if b is None or b is INHERIT:
        return a
    if b is DELETE:
        return None
    if _is_mapping(b) and _is_mapping(a):
        return _merge_mappings(
            simple, a, b, {} if memo is None else memo, options, False
        )
    return b


def substitute(
    a: _ty.Any, b: _ty.Any, *, memo: "_ty.Optional[dict]" = None, **options: _ty.Any
) -> _ty.Any:
    """Merge mappings recursively; lists and other values replace."""
    if b is None or b is INHERIT:
        return a
    if b is DELETE:
        return None
    if a is None:
        return b
    memo = {} if memo is None else memo
    if _is_mapping(a) and _is_mapping(b):
        return _merge_mappings(substitute, a, b, memo, options, True)
    if _folds(a, b):
        return _fold(substitute, a, b, memo, options)
    return b


class _StructuralKeys:
    """Hashable stand-ins for unhashable values, for Deep list de-duplication.

    Each distinct container is visited once (memoized by ``id()``) and named by
    a small integer for its structure, so a list of aliased lists — the same
    object at every level of a YAML anchor chain — costs one visit per distinct
    node, and comparing two keys never recurses. A value that contains itself
    gets a key without recursing forever.
    """

    __slots__ = ("_by_id", "_table")

    def __init__(self) -> None:
        self._by_id: dict = {}
        self._table: dict = {}

    def key(self, value: object) -> object:
        if isinstance(value, ScalarText):
            # A scalar kept for a typed load is a duplicate of what it reads as.
            value = value.value
        try:
            hash(value)
        except TypeError:
            pass
        else:
            # Type-aware: True is not a duplicate of 1.
            return (type(value), value)
        known = self._by_id.get(id(value))
        if known is not None:
            return known[1]
        self._by_id[id(value)] = (value, ("cycle", id(value)))
        if _is_mapping(value):
            structure: object = (
                type(value),
                frozenset((self.key(k), self.key(v)) for k, v in value.items()),
            )
        elif isinstance(value, _abc.Iterable):
            structure = (type(value), tuple(self.key(item) for item in value))
        else:
            structure = (type(value), "object", id(value))
        result = ("node", self._table.setdefault(structure, len(self._table)))
        self._by_id[id(value)] = (value, result)
        return result


def _deep_lists(
    a: _ty.Sequence, b: _ty.Sequence, memo: dict, options: dict
) -> _ty.Sequence:
    key = (id(a), id(b))
    hit = memo.get(key)
    if hit is not None:
        return hit[2]
    result = list(a)
    merged_at = set()
    if options.get("mergelists"):
        # Mappings at the same index that share a key merge; every other item
        # of b is appended below, in b's order.
        for index in range(min(len(result), len(b))):
            left, right = result[index], b[index]
            if (
                _is_mapping(left)
                and _is_mapping(right)
                and any(k in left for k in right)
            ):
                merged_at.add(index)
                result[index] = deep(left, right, memo=memo, **options)
    # Unique by type and value: True is not a duplicate of 1. Unhashable items
    # compare by structure, keyed once per container, so a list of aliased
    # lists stays linear in its distinct nodes.
    keys = _StructuralKeys()
    seen = {keys.key(item) for item in result if not _is_mapping(item)}
    for index, item in enumerate(b):
        if index in merged_at or item is DELETE or item is INHERIT:
            continue
        if _is_mapping(item):
            result.append(item)  # mappings are never de-duplicated
            continue
        item_key = keys.key(item)
        if item_key not in seen:
            seen.add(item_key)
            result.append(item)
    merged = _sequence_with(a, result)
    memo[key] = (a, b, merged)
    return merged


def deep(
    a: _ty.Any, b: _ty.Any, *, memo: "_ty.Optional[dict]" = None, **options: _ty.Any
) -> _ty.Any:
    """Merge mappings recursively and extend lists with new items."""
    if b is None or b is INHERIT:
        return a
    if b is DELETE:
        return None
    if a is None:
        return b
    memo = {} if memo is None else memo
    b_mapping = _is_mapping(b)
    if not b_mapping and not _is_array(b):
        return b
    if b_mapping and _is_mapping(a):
        return _merge_mappings(deep, a, b, memo, options, True)
    if not b_mapping and _is_array(a):
        return _deep_lists(a, b, memo, options)
    if _folds(a, b):
        return _fold(deep, a, b, memo, options)
    return b


_lock = threading.Lock()
_STRATEGIES: "_ty.Dict[str, Merge]" = {
    "simple": simple,
    "substitute": substitute,
    "deep": deep,
}


def merge_strategies() -> "_ty.List[str]":
    """Every registered strategy name, sorted."""
    return sorted(_STRATEGIES)


def register_merge(name: str, strategy: Merge, *, replace: bool = False) -> None:
    """Make *strategy* selectable as ``merge=name``.

    Raises `ValueError` when *name* is taken, unless *replace* is true.
    """
    global _STRATEGIES
    key = name.lower()
    with _lock:
        if key in _STRATEGIES and not replace:
            raise ValueError(
                f"merge strategy {key!r} is already registered; pass replace=True"
            )
        # Replaced whole, so a reader never sees the table change under it.
        _STRATEGIES = {**_STRATEGIES, key: strategy}


def resolve_merge(merge: "_ty.Union[str, Merge]") -> Merge:
    """The strategy *merge* names, or *merge* itself when it is callable.

    Raises:
        ValueError: For an unknown name, listing the registered ones.
        TypeError: For anything that is neither.
    """
    if isinstance(merge, str):
        found = _STRATEGIES.get(merge.lower())
        if found is None:
            raise ValueError(
                f"unknown merge strategy {merge!r}; registered: "
                f"{', '.join(merge_strategies())}"
            )
        return found
    if callable(merge) and not isinstance(merge, type):
        return merge
    raise TypeError(
        f"merge= takes a strategy name or a callable (a, b, *, memo) -> merged, "
        f"not {merge!r}"
    )


def prune_deletes(value: _ty.Any, memo: "_ty.Optional[dict]" = None) -> _ty.Any:
    """*value* with every :data:`DELETE` and :data:`INHERIT` marker removed,
    copying only what changes."""
    memo = {} if memo is None else memo
    if value is DELETE or value is INHERIT:
        return None
    hit = memo.get(id(value))
    if hit is not None:
        return hit[1]
    if _is_mapping(value):
        memo[id(value)] = (value, value)
        items = {}
        changed = False
        for key, item in value.items():
            if item is DELETE or item is INHERIT:
                changed = True
                continue
            new = prune_deletes(item, memo)
            changed = changed or new is not item
            items[key] = new
        result = _mapping_with(value, items) if changed else value
        memo[id(value)] = (value, result)
        return result
    if _is_array(value):
        memo[id(value)] = (value, value)
        kept = [
            prune_deletes(item, memo)
            for item in value
            if item is not DELETE and item is not INHERIT
        ]
        changed = len(kept) != len(value) or any(
            new is not old for new, old in zip(kept, value)
        )
        result = _sequence_with(value, kept) if changed else value
        memo[id(value)] = (value, result)
        return result
    return value
