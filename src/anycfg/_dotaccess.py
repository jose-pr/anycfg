"""Attribute and dotted-path access to loaded configuration."""

from __future__ import annotations

import collections.abc
import copy as _copy
import typing

__all__ = ["DotAccessibleDict", "to_dot_access", "dig"]

#: Distinguishes "absent" from a stored ``None`` while digging a dotted path.
_MISSING = object()


def _to_dot_access(value: object, memo: dict) -> object:
    """Return *value* with every nested mapping dot-accessible.

    Copies rather than mutating: a backend may hand back an object the caller
    still owns (`PythonBackend` does), and merges are copy-on-write for the
    same reason.

    *memo* maps ``id(source container)`` to its converted object, so structure
    shared in the source stays shared in the result — a YAML anchor used twice
    is still one object afterwards — and a self-referencing document
    terminates. A container is registered before its children are converted.
    """
    existing = memo.get(id(value), _MISSING)
    if existing is not _MISSING:
        return existing

    if isinstance(value, dict):
        # dict.__new__ keeps a DotAccessibleDict subclass's own type and calls
        # no __init__ (which would convert a second time).
        converted = (
            dict.__new__(type(value))
            if isinstance(value, DotAccessibleDict)
            else DotAccessibleDict()
        )
        memo[id(value)] = converted
        for key, item in value.items():
            dict.__setitem__(converted, key, _to_dot_access(item, memo))
        return converted

    # Only exact list/tuple: a list subclass may carry state a plain list
    # cannot, and a namedtuple would lose its type through tuple(...).
    if type(value) is list:
        converted = []
        memo[id(value)] = converted
        converted.extend(_to_dot_access(item, memo) for item in value)
        return converted
    if type(value) is tuple:
        # Built first, so it can only be registered afterwards; a tuple cannot
        # take part in a cycle it owns.
        converted = tuple(_to_dot_access(item, memo) for item in value)
        memo[id(value)] = converted
        return converted

    # Everything else is returned as it is and not descended into: a non-dict
    # Mapping may be lazy or proxy live state, and materializing it is not this
    # library's decision.
    return value


class DotAccessibleDict(dict):
    """Dictionary subclass supporting dot-notation queries and attribute access.

    Nested mappings are converted **once, at construction**, so item access,
    attribute access and object identity all agree however the value is
    reached, whatever the read order. Reads never write: `get()` follows
    `dict.get`'s contract and a miss returns the default without storing it.

    Values assigned after construction are stored exactly as given — a plain
    dict written with ``cfg["x"] = {...}`` stays a plain dict.

    A key that collides with something the class defines (``items``, ``get``,
    ``copy``, ...) is reachable only through item access: attribute *reads*
    find the method, so attribute writes and deletes of such a name raise
    `AttributeError` rather than letting the two disagree. A subclass that
    needs a real instance attribute uses ``object.__setattr__``.

    ``dict(cfg)`` is the way to get a plain `dict` back.
    """

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        # Seed the memo with this object, and with the source mapping when
        # there is one, so a self-referencing source resolves to this instance.
        memo = {id(self): self}
        if len(args) == 1 and isinstance(args[0], dict):
            memo[id(args[0])] = self
        for key, value in list(self.items()):
            dict.__setitem__(self, key, _to_dot_access(value, memo))

    def __getattr__(self, name: str) -> typing.Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            ) from None

    def __setattr__(self, name: str, value: object) -> None:
        # A dict subclass cannot expose "items", "keys", "get" and friends as
        # attributes, so refusing the write is the only way attribute reads and
        # writes cannot disagree about such a name. Use cfg[name] for the key;
        # object.__setattr__ for a real instance attribute in a subclass.
        if hasattr(type(self), name):
            raise AttributeError(
                f"{name!r} is read-only on {type(self).__name__!r} because the class "
                f"defines it; use cfg[{name!r}] to set that key"
            )
        self[name] = value

    def __delattr__(self, name: str) -> None:
        if hasattr(type(self), name):
            raise AttributeError(
                f"{name!r} is read-only on {type(self).__name__!r} because the class "
                f"defines it; use del cfg[{name!r}] to remove that key"
            )
        try:
            del self[name]
        except KeyError:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            ) from None

    def copy(self) -> "DotAccessibleDict":
        """A shallow copy of the same class — like `dict.copy`, but typed."""
        return _copy.copy(self)

    def __or__(self, other: typing.Any) -> typing.Any:
        if not isinstance(other, dict):
            return NotImplemented
        merged = type(self)()
        # Built like dict's own operator: key order of the left operand, then
        # the right's, right wins. Values are stored as given -- conversion
        # happens at construction only.
        for mapping in (self, other):
            for key, value in mapping.items():
                dict.__setitem__(merged, key, value)
        return merged

    def __ror__(self, other: typing.Any) -> typing.Any:
        if not isinstance(other, dict):
            return NotImplemented
        merged = type(self)()
        for mapping in (other, self):
            for key, value in mapping.items():
                dict.__setitem__(merged, key, value)
        return merged

    def get(
        self, key: typing.Hashable, default: typing.Any = None, dig: bool = True
    ) -> typing.Any:
        """Look up *key*, optionally as a path into nested containers.

        The order is: an **exact** key wins; then, when *dig* is true, a
        dotted-string or tuple path is traversed; otherwise *default*.

        A dotted string (``"database.credentials.user"``) walks mappings by
        segment. A segment of ASCII digits indexes a list or tuple
        (``"servers.0.host"``), but on a mapping it is always the *string* key,
        so ``"codes.0"`` never means the integer ``0``. Exact-key precedence
        applies at the top level only, so a nested key that itself contains a
        dot is unreachable this way — pass a **tuple** path for that
        (``("metadata", "labels", "app.kubernetes.io/name")``), which also
        reaches integer keys and uses ints, not digit strings, as indexes.

        A ``None`` (or any non-traversable value) reached with segments still
        to go yields *default*; the final segment's value is returned as
        stored, ``None`` included — so an explicit ``null`` stays
        distinguishable from an absent key.

        Never inserts: a miss returns *default* itself, as `dict.get` does.
        """
        if key in self:
            return super().get(key, default)
        if not dig:
            return default

        if isinstance(key, str):
            if "." not in key:
                return default
            # Inlined rather than delegated: this is the benchmarked read path,
            # and a helper call with keyword arguments showed up in it.
            segments = key.split(".")
            current: object = self
            last = len(segments) - 1
            for index, segment in enumerate(segments):
                if isinstance(current, dict):
                    # dict.get, so a defaultdict stored later does not grow a
                    # key through a read, and __missing__ is never consulted.
                    current = dict.get(current, segment, _MISSING)
                    if current is _MISSING:
                        return default
                elif current.__class__ is list or current.__class__ is tuple:
                    # ASCII digits only: a Unicode digit must not reach int(),
                    # and a negative or non-numeric segment ends the path.
                    if not (segment.isascii() and segment.isdigit()):
                        return default
                    position = int(segment)
                    if position >= len(current):
                        return default
                    current = current[position]
                else:
                    # A scalar, a None, or a non-dict Mapping: the path stops.
                    return default
                if current is None and index != last:
                    return default
            return current
        if isinstance(key, tuple):
            if not key:
                return default
            return self._dig(key, default)
        # Any other hashable key: absent is absent. (dict.get's contract; an
        # unhashable one has already raised TypeError above.)
        return default

    def dig(self, *keys: typing.Any, default: typing.Any = None) -> typing.Any:
        """:func:`anycfg.dig` on this mapping: keys as given, a miss is *default*."""
        return dig(self, *keys, default=default)

    def _dig(
        self, segments: typing.Sequence[typing.Hashable], default: object
    ) -> object:
        """Walk a **tuple** path, returning *default* the moment it breaks.

        Each segment is used as given: any hashable key on a mapping, and on a
        list or tuple only a real `int`. The string form is inlined in `get()`.
        """
        current: object = self
        last = len(segments) - 1
        for index, segment in enumerate(segments):
            if isinstance(current, dict):
                # dict.get, so a defaultdict stored later does not grow a key
                # through a read, and __missing__ is never consulted.
                current = dict.get(current, segment, _MISSING)
                if current is _MISSING:
                    return default
            elif current.__class__ is list or current.__class__ is tuple:
                # bool is excluded: True == 1 would index silently.
                if (
                    not isinstance(segment, int)
                    or isinstance(segment, bool)
                    or not 0 <= segment < len(current)
                ):
                    return default
                current = current[segment]
            else:
                # A scalar, a None, or a non-dict Mapping: the path stops here.
                return default
            if current is None and index != last:
                return default
        return current


def dig(data: typing.Any, *keys: typing.Any, default: typing.Any = None) -> typing.Any:
    """The value at *keys* inside *data*, as Puppet's and Ruby's ``dig`` find it.

    Each key is used exactly as given — no dotted strings, so a key holding a
    ``.`` needs no quoting: a mapping is indexed by the key, a list or tuple by
    an `int` (negative counts from the end). The first step that finds nothing
    — a missing key, an index out of range, or ``None`` with keys still to go —
    gives *default*; a value found at the last key is returned as stored, a
    ``None`` included. Digging into anything else (a string, a number) raises::

        dig(config, "db", "hosts", 0, "port", default=5432)

    Works on plain results and on `DotAccessibleDict` alike.

    Raises:
        ConfigTypeError: When a step reaches a value that is neither a mapping
            nor a list or tuple, or indexes a list with a non-`int`.
    """
    from .exceptions import ConfigTypeError

    current = data
    for depth, key in enumerate(keys):
        if current is None:
            return default
        if isinstance(current, dict):
            # dict.get: a defaultdict must not grow a key through a read.
            current = dict.get(current, key, _MISSING)
        elif isinstance(current, collections.abc.Mapping):
            current = current[key] if key in current else _MISSING
        elif isinstance(current, (list, tuple)):
            if not isinstance(key, int) or isinstance(key, bool):
                raise ConfigTypeError(
                    f"dig: step {depth + 1} indexes a {type(current).__name__} with "
                    f"a {type(key).__name__}, not an int"
                )
            in_range = -len(current) <= key < len(current)
            current = current[key] if in_range else _MISSING
        else:
            raise ConfigTypeError(
                f"dig: step {depth + 1} reaches a {type(current).__name__}, which "
                "holds no keys"
            )
        if current is _MISSING:
            return default
    return current


def to_dot_access(value: object) -> object:
    """*value* with every nested ``dict`` a `DotAccessibleDict`, copied, not mutated."""
    return _to_dot_access(value, {})
