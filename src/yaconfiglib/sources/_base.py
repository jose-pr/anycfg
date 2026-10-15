"""The source contract, and the source for Python data."""

from __future__ import annotations

import copy
import typing as _ty

from .._context import LoadContext

__all__ = ["Source", "Payload", "Data", "Text", "Chunks"]


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


def _join_chunks(chunks: _ty.Iterable[_ty.Any], limit: int, name: str) -> _ty.Any:
    """*chunks* (all `str` or all bytes-like) joined, stopping past *limit*."""
    from ..exceptions import BudgetExceededError

    parts: list = []
    total = 0
    kind: "_ty.Optional[type]" = None
    for chunk in chunks:
        if isinstance(chunk, (bytearray, memoryview)):
            chunk = bytes(chunk)
        if not isinstance(chunk, (str, bytes)):
            raise TypeError(f"a chunk is str or bytes, not {type(chunk).__name__}")
        if kind is None:
            kind = type(chunk)
        elif type(chunk) is not kind:
            raise TypeError("chunks mix str and bytes")
        total += len(chunk)
        if total > limit:
            # Before the rest arrives: a hostile stream stops here.
            raise BudgetExceededError(
                f"reading {name or '<chunks>'} takes the load past max_bytes={limit}"
            )
        parts.append(chunk)
    if kind is str:
        return "".join(parts)
    return b"".join(parts)


class Chunks(Source):
    """Content arriving in pieces: any iterable of `str` or `bytes` chunks.

    ``Chunks(response.iter_content(), format="json")``. The chunks are joined as
    they arrive and the read stops once they pass the policy's ``max_bytes``. The
    format is *format*, else the one *name*'s suffix selects. An iterator is read
    once, so a source built on one loads once.
    """

    def __init__(
        self,
        chunks: _ty.Iterable[_ty.Any],
        *,
        format: "_ty.Optional[str]" = None,
        name: str = "",
    ) -> None:
        if isinstance(chunks, (str, bytes, bytearray)) or not isinstance(
            chunks, _ty.Iterable
        ):
            raise TypeError(
                f"Chunks takes an iterable of chunks, not {type(chunks).__name__}"
            )
        self.chunks = chunks
        self.format = format
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def read(self, context: "LoadContext") -> Payload:
        data = _join_chunks(self.chunks, context.policy.max_bytes, self._name)
        return Payload(
            data,
            name=self._name,
            identity=f"chunks:{id(self)}",
            format=self.format,
            trusted_format=self.format is not None,
        )

    def __repr__(self) -> str:
        return f"Chunks(format={self.format!r}, name={self._name!r})"
