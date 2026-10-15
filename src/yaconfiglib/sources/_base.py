"""The source contract, and the source for Python data."""

from __future__ import annotations

import copy
import typing as _ty

from .._context import LoadContext

__all__ = ["Source", "Payload", "Data", "Text"]


def _copy_data(value: _ty.Any, memo: dict) -> _ty.Any:
    """A deep copy of plain data; any mapping becomes a `dict`.

    `copy.deepcopy` refuses a read-only mapping (a `MappingProxyType`); this
    copies its items instead. Aliases and cycles are kept.
    """
    hit = memo.get(id(value))
    if hit is not None:
        return hit
    if isinstance(value, _ty.Mapping):
        result: _ty.Any = {} if not isinstance(value, dict) else copy.copy(value)
        if isinstance(value, dict):
            result.clear()
        memo[id(value)] = result
        for key, item in value.items():
            result[key] = _copy_data(item, memo)
        return result
    if type(value) is list:
        result = []
        memo[id(value)] = result
        result.extend(_copy_data(item, memo) for item in value)
        return result
    if type(value) is tuple:
        return tuple(_copy_data(item, memo) for item in value)
    try:
        return copy.deepcopy(value, memo)
    except (TypeError, copy.Error):
        return value


class Payload(_ty.NamedTuple):
    """What a source read.

    Attributes:
        data: Text or bytes for a format to parse, or the data itself when
            *parsed* is true.
        name: The source's name, for messages and merge keys.
        identity: A stable key for this source, used for include-cycle
            detection and de-duplication.
        format: The format name the source itself determined (``cmd+json://``,
            a ``#!json`` line), or `None` to let the caller decide.
        origin: The location relative includes resolve against, or `None`.
        parsed: *data* is already data, not text.
        trusted_format: *format* was chosen by the caller, so it may name any
            registered format; one a source read from data (a command's
            ``#!json`` line) is limited to the formats documents may select.
        encoding: The codec *data* is in, and the default for its includes;
            `None` means the load's.
    """

    data: _ty.Any
    name: str
    identity: str
    format: "_ty.Optional[str]" = None
    origin: _ty.Any = None
    parsed: bool = False
    trusted_format: bool = False
    encoding: "_ty.Optional[str]" = None


class Source:
    """Base class for something a loader reads that is not a file path.

    Override :meth:`read`. Set ``executes = True`` for a source that runs a
    program: the loader then refuses it unless the policy allows commands,
    before :meth:`read` is called.
    """

    executes: _ty.ClassVar[bool] = False

    @property
    def name(self) -> str:
        """A name for messages and for the default merge key."""
        return type(self).__name__

    def read(self, context: "LoadContext") -> Payload:
        """Return what this source holds, under *context*'s policy."""
        raise NotImplementedError


class Data(Source):
    """Python data as a source: a mapping or a list, merged like a document.

    The data is deep-copied when read, so a later merge or interpolation never
    reaches back into the caller's object. A `Mapping` or a `list` passed
    straight to a loader is wrapped in one of these.
    """

    def __init__(self, data: _ty.Any, name: str = "") -> None:
        self.data = data
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def read(self, context: "LoadContext") -> Payload:
        return Payload(
            _copy_data(self.data, {}),
            name=self._name,
            identity=f"data:{id(self.data)}",
            parsed=True,
        )

    def __repr__(self) -> str:
        return f"Data({self._name!r})"


class Text(Source):
    """In-memory content in a named format: ``Text("a: 1", format="yaml")``.

    *data* is `str` or `bytes` (decoded with the load's encoding). Relative
    includes inside it resolve against the loader's ``base_dir``. *name* appears
    in messages and is the merge key for ``collect="hash"``.
    """

    def __init__(
        self, data: "_ty.Union[str, bytes]", *, format: str, name: str = ""
    ) -> None:
        if not isinstance(data, (str, bytes, bytearray)):
            raise TypeError(f"Text takes str or bytes, not {type(data).__name__}")
        self.data = bytes(data) if isinstance(data, bytearray) else data
        self.format = format
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def read(self, context: "LoadContext") -> Payload:
        return Payload(
            self.data,
            name=self._name,
            identity=f"text:{id(self)}",
            format=self.format,
            trusted_format=True,
        )

    def __repr__(self) -> str:
        return f"Text(format={self.format!r}, name={self._name!r})"
