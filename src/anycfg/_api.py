"""The one-document functions: ``load``, ``loads``, ``dump``, ``dumps`` and friends."""

from __future__ import annotations

import itertools
import os
import typing as _ty

from pathapi import Path

from . import formats as _formats
from ._context import Policy
from ._loader import Loader, _Session, check_format_options
from ._paths import is_async_source, is_stream
from .exceptions import ConfigValueError, UnsupportedFormatError
from .sources import Source, Text

__all__ = ["load", "loads", "load_all", "loads_all", "dump", "dumps"]

FormatLike = _ty.Union[str, "_ty.Type[_formats.Format]", None]


def _format_class(format: FormatLike) -> "_ty.Type[_formats.Format]":
    if isinstance(format, type) and issubclass(format, _formats.Format):
        return format
    if not isinstance(format, str):
        raise TypeError(
            f"format= takes a format name or a Format class, not {format!r}"
        )
    return _formats.get(format)


def _documents(
    source: _ty.Any,
    *,
    format: FormatLike,
    encoding: str,
    policy: "_ty.Optional[Policy]",
    options: _ty.Mapping[str, _ty.Any],
    single: bool,
    into: _ty.Any = None,
) -> _ty.Iterator[_ty.Any]:
    session = _session(policy, encoding, into)
    for _name, value in session.each(
        [source],
        session.context(),
        explicit=_explicit(format),
        options=options,
        single=single,
    ):
        yield session.finish(value, into=into)


def _session(
    policy: "_ty.Optional[Policy]", encoding: str, into: _ty.Any, merge: _ty.Any = None
) -> _Session:
    settings: _ty.Dict[str, _ty.Any] = {"policy": policy, "encoding": encoding}
    if merge is not None:
        settings["merge"] = merge
    session = _Session(Loader(**settings)._defaults)
    session.keep_text = into is not None
    return session


def _explicit(format: FormatLike) -> "_ty.Optional[_ty.Type[_formats.Format]]":
    return None if format is None else _format_class(format)


def _check_location(source: _ty.Any, name: str, allow_async: bool = False) -> None:
    if source is None:
        raise TypeError(f"{name}() got None; pass a path or an open file")
    if isinstance(source, str) and not source:
        raise ValueError(f"{name}() got an empty path")
    if isinstance(source, (bytes, bytearray)):
        raise TypeError(
            f"{name}() takes a location, and bytes are content; use "
            "loads(data, format=...)"
        )
    if is_async_source(source):
        if allow_async:
            return
        raise TypeError(
            f"a {type(source).__name__} is read asynchronously; use "
            f"await anycfg.a{name}(...)"
        )
    if isinstance(source, (list, tuple)):
        raise TypeError(
            f"{name}() takes locations, not a {type(source).__name__}; unpack it: "
            f"{name}(*sources)"
        )
    if not isinstance(
        source, (str, os.PathLike, Path, Source, _ty.Mapping)
    ) and not is_stream(source):
        raise TypeError(f"{name}() takes a location, not a {type(source).__name__}")


_MISSING = object()


def load(
    *sources: _ty.Any,
    format: FormatLike = None,
    encoding: str = "utf-8",
    policy: "_ty.Optional[Policy]" = None,
    into: _ty.Any = None,
    default: _ty.Any = _MISSING,
    merge: _ty.Any = None,
    **options: _ty.Any,
) -> _ty.Any:
    """Read one document from each source and merge them in order.

    A source is a path, a pathapi ``Path`` or an open file, and each gives
    exactly one document. Several are merged later over earlier with *merge*,
    a strategy as :class:`~anycfg.Loader` takes it (``"simple"`` when `None`:
    top-level keys update, so pass ``merge="deep"`` to merge nested sections).
    ``None`` is an absent layer and is skipped, so
    ``load(*default_configs("app.toml"), default={})`` works; with every source
    ``None``, *default* is the result. For globs or tolerated failures, use
    :class:`~anycfg.Loader`.

    The format is *format*, or else the one each file name's suffix selects;
    a name no format claims raises `UnsupportedFormatError`. A ``str`` is
    always a location, never content (see :func:`loads`). Bytes are decoded
    with *encoding*, strictly, one BOM ignored. *options* go to the format
    and are checked against what it accepts. *into* builds that type from the
    result (:func:`~anycfg.hydrate`).

    Includes, templates and commands follow *policy* (untrusted by default).

    Raises:
        UnsupportedFormatError: When no format is named or claimed.
        ParseError: When a document is not valid.
        ConfigValueError: When a source gives no document or several.
        TypeError: For no source, every source ``None`` without *default*, an
            unknown option, or a source that is not a location.
    """
    return _load(
        sources,
        format=format,
        encoding=encoding,
        policy=policy,
        into=into,
        default=default,
        merge=merge,
        options=options,
    )


def _load(
    sources: _ty.Tuple[_ty.Any, ...],
    *,
    format: FormatLike,
    encoding: str,
    policy: "_ty.Optional[Policy]",
    into: _ty.Any,
    default: _ty.Any,
    merge: _ty.Any,
    options: _ty.Mapping[str, _ty.Any],
) -> _ty.Any:
    if not sources:
        raise TypeError("load() needs at least one source")
    present = [source for source in sources if source is not None]
    for source in present:
        _check_location(source, "load")
    if not present and default is _MISSING:
        raise TypeError(
            "load() got None; pass a path or an open file, or default= for when "
            "every layer is absent"
        )
    session = _session(policy, encoding, into, merge)
    explicit = _explicit(format)
    context = session.context()

    def documents() -> _ty.Iterator[_ty.Tuple[str, _ty.Any]]:
        for source in present:
            # Two at most: a glob pattern is one location that can name several files.
            found = list(
                itertools.islice(
                    session.each(
                        [source],
                        context,
                        explicit=explicit,
                        options=options,
                        single=True,
                    ),
                    2,
                )
            )
            if len(found) != 1:
                raise ConfigValueError(
                    f"load() reads one document per source, and {source!r} gave "
                    f"{'none' if not found else 'more than one'}; expand a glob "
                    "with anycfg.Loader().load(...)"
                )
            yield found[0]

    result, _loaded = session.combine(
        documents(),
        merge=session.call.merge,
        merge_options=session.call.merge_options,
        collect=None,
        default=None if default is _MISSING else default,
    )
    return session.finish(result, into=into)


def load_all(
    source: _ty.Any,
    /,
    *,
    format: FormatLike = None,
    encoding: str = "utf-8",
    policy: "_ty.Optional[Policy]" = None,
    into: _ty.Any = None,
    **options: _ty.Any,
) -> _ty.Iterator[_ty.Any]:
    """Yield every document of *source* (a ``---``-separated YAML stream).

    A format with one document per file yields that one. *into* builds each
    document into that type. Otherwise as :func:`load`.
    """
    _check_location(source, "load_all")
    return _documents(
        source,
        format=format,
        encoding=encoding,
        policy=policy,
        options=options,
        single=False,
        into=into,
    )


def _text(data: _ty.Any, cls: "_ty.Optional[_ty.Type[_formats.Format]]") -> Text:
    """*data* as a source: in *cls*, or in the format it names itself."""
    if cls is not None:
        return Text(data, format=cls.name)
    name, _rest = _formats.declared(data)
    if name is None:
        raise UnsupportedFormatError(
            "loads() needs format=, or a document that names its own: a "
            "#!<format> first line (#!json) or a %YAML directive"
        )
    return Text(data)


def loads(
    data: _ty.Union[str, bytes, bytearray],
    /,
    *,
    format: FormatLike = None,
    encoding: str = "utf-8",
    policy: "_ty.Optional[Policy]" = None,
    into: _ty.Any = None,
    **options: _ty.Any,
) -> _ty.Any:
    """Parse one document from *data* in *format*.

    *data* is `str`, or `bytes` decoded with *encoding*. Without *format*, the
    document must name its own: a ``#!<format>`` first line (``#!json``,
    ``#!/usr/bin/env yaml``; removed before parsing) or a format's marker such
    as YAML's ``%YAML`` directive. Nothing is guessed from content that merely
    parses. Touches no file — except through an include, which *policy*
    governs and which resolves against the working directory.

    Raises:
        UnsupportedFormatError: Without *format*, when the document names none.
        ParseError: When *data* is not valid *format*.
        TypeError: When *data* is not text or bytes, or for an unknown option.
    """
    cls = None if format is None else _format_class(format)
    if not isinstance(data, (str, bytes, bytearray)):
        raise TypeError(f"loads() takes str or bytes, not {type(data).__name__}")
    # Not load(): a stray merge= or default= stays an option the format refuses.
    return _load(
        (_text(data, cls),),
        format=cls,
        encoding=encoding,
        policy=policy,
        into=into,
        default=_MISSING,
        merge=None,
        options=options,
    )


def loads_all(
    data: _ty.Union[str, bytes, bytearray],
    /,
    *,
    format: FormatLike = None,
    encoding: str = "utf-8",
    policy: "_ty.Optional[Policy]" = None,
    into: _ty.Any = None,
    **options: _ty.Any,
) -> _ty.Iterator[_ty.Any]:
    """Yield every document in *data*; otherwise as :func:`loads`."""
    cls = None if format is None else _format_class(format)
    if not isinstance(data, (str, bytes, bytearray)):
        raise TypeError(f"loads_all() takes str or bytes, not {type(data).__name__}")
    return load_all(
        _text(data, cls),
        format=cls,
        encoding=encoding,
        policy=policy,
        into=into,
        **options,
    )


def dumps(obj: _ty.Any, /, *, format: FormatLike, **options: _ty.Any) -> str:
    """Serialize *obj* as *format* and return the text.

    *obj* is first turned into plain data (:func:`~anycfg.dehydrate`), so
    a dataclass, a namespace, an enum member, a path or a type with a
    ``_dump_()`` method writes as what it holds. *options* go to the format and
    are checked against what it accepts.

    Raises:
        UnsupportedFormatError: When the format cannot write.
        ConfigTypeError: When *obj* holds a value the format cannot represent.
    """
    cls = _format_class(format)
    if "dump" not in _formats.capabilities(cls):
        raise UnsupportedFormatError(f"the {cls.name} format cannot write")
    check_format_options(cls, options, "dump")
    from ._hydrate import dehydrate

    return cls().dumps(dehydrate(obj), **options)


def _target_name(target: _ty.Any) -> "_ty.Optional[str]":
    if isinstance(target, Path):
        return target.name
    if isinstance(target, (str, os.PathLike)):
        return os.path.basename(os.fsdecode(os.fspath(target)))
    name = getattr(target, "name", None)
    if isinstance(name, str) and not (name.startswith("<") and name.endswith(">")):
        return os.path.basename(name)
    return None


def dump(
    obj: _ty.Any,
    target: _ty.Any,
    /,
    *,
    format: FormatLike = None,
    encoding: str = "utf-8",
    **options: _ty.Any,
) -> None:
    """Serialize *obj* and write it to *target*: a path, a pathapi ``Path`` or an open file.

    The format is *format*, or else the one *target*'s file name selects, the
    same rule :func:`load` uses, so ``dump(x, "a.json")`` then ``load("a.json")``
    gives ``x`` back. A name no format claims raises. *obj* is serialized
    completely before *target* is opened, so a value the format cannot write
    leaves an existing file untouched. Paths are written with *encoding* and LF
    line endings; a text stream keeps its own codec.

    Raises:
        UnsupportedFormatError: When no format is named or claimed, or the
            format cannot write.
        ConfigTypeError: When *obj* holds a value the format cannot represent.
        TypeError: When *target* is not a path or a writable file.
    """
    if format is None:
        name = _target_name(target)
        if name is None:
            raise UnsupportedFormatError(
                f"cannot tell which format to write to {target!r}; pass format="
            )
        format = _formats.for_filename(name)
    text = dumps(obj, format=format, **options)
    if not text.endswith("\n"):
        text += "\n"
    if not isinstance(target, (str, bytes, os.PathLike, Path)) and callable(
        getattr(target, "write", None)
    ):
        try:
            target.write(text)
        except TypeError:
            # A binary target rejects str before writing anything.
            target.write(text.encode(encoding))
        return
    if isinstance(target, Path):
        target.write_bytes(text.encode(encoding))
        return
    if isinstance(target, (str, os.PathLike)):
        with open(os.fspath(target), "w", encoding=encoding, newline="") as handle:
            handle.write(text)
        return
    raise TypeError(
        f"dump() cannot write to a {type(target).__name__}; pass a path or an open file"
    )
