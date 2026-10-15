"""The format contract: text in, data out, and optionally data back to text."""

from __future__ import annotations

import typing as _ty

from .._context import LoadContext

__all__ = ["Format", "capabilities"]


class Format:
    """Base class for a configuration format.

    A format turns document text into data and, optionally, data back into
    text. It never touches a file, a stream or the environment: the loader reads
    the bytes, decodes them and hands the text over, so confinement, encoding and
    the policy apply to every format alike.

    Subclass it, set the class attributes, override :meth:`loads` and any of the
    optional methods, then register the class with
    :func:`yaconfiglib.formats.register_format` (or the ``yaconfiglib.formats``
    entry-point group).

    Class attributes:
        name: The registry name, lower-case (``"yaml"``).
        aliases: Other names ``format=`` accepts (``("yml",)``).
        suffixes: File suffixes this format claims, lower-case with the dot
            (``(".yaml", ".yml")``); matched case-insensitively, longest first.
        priority: Breaks a tie when two formats claim the same suffix.
        load_options: The keywords :meth:`loads` accepts; any other is refused
            with `TypeError` before the format is called.
        dump_options: The keywords :meth:`dumps` accepts, likewise.
        binary: When true, :meth:`loads` receives `bytes` instead of text.

    Capabilities follow from what is overridden: :func:`capabilities` reports
    ``"load_all"`` when :meth:`loads_all` is and ``"dump"`` when :meth:`dumps`
    is. A format holds no state, so one instance serves every load.

    Errors: a malformed document raises
    :class:`~yaconfiglib.exceptions.ParseError`, translated from the parser's
    own error and raised **after** the ``except`` block, so neither
    ``__cause__`` nor ``__context__`` keeps the parser's error (which may hold
    the document), and carrying no document text itself.
    """

    name: _ty.ClassVar[str] = ""
    aliases: _ty.ClassVar[_ty.Tuple[str, ...]] = ()
    suffixes: _ty.ClassVar[_ty.Tuple[str, ...]] = ()
    priority: _ty.ClassVar[int] = 0
    load_options: _ty.ClassVar[_ty.FrozenSet[str]] = frozenset()
    dump_options: _ty.ClassVar[_ty.FrozenSet[str]] = frozenset()
    binary: _ty.ClassVar[bool] = False

    def loads(
        self,
        data: _ty.Any,
        /,
        *,
        context: "_ty.Optional[LoadContext]" = None,
        **options: _ty.Any,
    ) -> _ty.Any:
        """Parse one document from *data* and return plain data.

        *context* is `None` for a direct call. A format with includes reads
        them through ``context.include(...)``, and refuses them without one.
        """
        raise NotImplementedError

    def loads_all(
        self,
        data: _ty.Any,
        /,
        *,
        context: "_ty.Optional[LoadContext]" = None,
        **options: _ty.Any,
    ) -> _ty.Iterator[_ty.Any]:
        """Yield every document in *data*. Override for a multi-document format."""
        yield self.loads(data, context=context, **options)

    def dumps(self, obj: _ty.Any, /, **options: _ty.Any) -> str:
        """Serialize *obj*. Override for a format that can write.

        Raises :class:`~yaconfiglib.exceptions.ConfigTypeError` for a value the
        format cannot represent, before anything is written.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"


def _overrides(cls: type, method: str) -> bool:
    return getattr(cls, method) is not getattr(Format, method)


def capabilities(fmt: "_ty.Union[Format, _ty.Type[Format]]") -> _ty.FrozenSet[str]:
    """What *fmt* can do: ``"load"``, plus ``"load_all"`` and ``"dump"`` when overridden."""
    cls = fmt if isinstance(fmt, type) else type(fmt)
    found = {"load"}
    if _overrides(cls, "loads_all"):
        found.add("load_all")
    if _overrides(cls, "dumps"):
        found.add("dump")
    return frozenset(found)
