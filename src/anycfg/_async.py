"""Async loading: ``aload``, ``aload_all``, ``adump`` and ``Loader.aload``.

Gather first, parse second. An async source — anything with ``__aiter__`` that
yields `str` or `bytes` chunks, or an ``async def read()`` — is read here, on the
event loop, and stops as soon as it passes ``max_bytes``. Everything else (paths,
includes, commands, parsing, merging) is the sync pipeline, run in a worker
thread with `asyncio.to_thread`, so the event loop never blocks on a file or a
parser. Cancelling the awaiting task stops the reading of async sources; work
already handed to the worker thread runs to its end.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import os
import typing as _ty

from pathapi import Path

from ._context import Policy
from ._loader import _Session, _stream_file
from ._paths import is_async_source
from .exceptions import BudgetExceededError

if _ty.TYPE_CHECKING:
    from ._loader import Loader

__all__ = ["aload", "aload_all", "adump"]

#: How much an ``async read(n)`` is asked for at a time.
_CHUNK = 64 * 1024


class _Gathered(io.BytesIO):
    """An async source's content, as a sync stream named after it."""

    def __init__(self, data: bytes, name: "_ty.Optional[str]") -> None:
        super().__init__(data)
        if name is not None:
            self.name = name


class _GatheredText(io.StringIO):
    def __init__(self, data: str, name: "_ty.Optional[str]") -> None:
        super().__init__(data)
        if name is not None:
            self.name = name


def _add(parts: list, chunk: _ty.Any, total: int, limit: int, name: str) -> int:
    if isinstance(chunk, (bytearray, memoryview)):
        chunk = bytes(chunk)
    if not isinstance(chunk, (str, bytes)):
        raise TypeError(
            f"an async source yields str or bytes, not {type(chunk).__name__}"
        )
    if parts and type(chunk) is not type(parts[0]):
        raise TypeError("an async source mixes str and bytes chunks")
    total += len(chunk)
    if total > limit:
        raise BudgetExceededError(
            f"reading {name} takes the load past max_bytes={limit}"
        )
    parts.append(chunk)
    return total


async def _read_all(source: _ty.Any, limit: int, name: str) -> _ty.Any:
    """Every chunk of *source*, stopping as soon as they pass *limit*."""
    parts: list = []
    total = 0
    if hasattr(source, "__aiter__"):
        async for chunk in source:
            total = _add(parts, chunk, total, limit, name)
    else:
        while True:
            try:
                chunk = await source.read(_CHUNK)
            except TypeError:
                # A read() that takes no size gives everything at once.
                if parts:
                    raise
                chunk = await source.read()
                if chunk:
                    _add(parts, chunk, total, limit, name)
                break
            if not chunk:
                break
            total = _add(parts, chunk, total, limit, name)
    if parts and isinstance(parts[0], str):
        return "".join(parts)
    return b"".join(parts)


async def _gather(source: _ty.Any, session: _Session) -> _ty.Any:
    """*source* as a sync stream, or None when confinement refused it and
    ``on_error`` skipped the refusal. Confinement is checked before reading."""
    real = _stream_file(source)
    if real is not None and session.confine_hook(real):
        return None
    limit = session.call.policy.max_bytes
    data = await _read_all(source, limit, real or "<async stream>")
    if isinstance(data, str):
        return _GatheredText(data, real)
    return _Gathered(data, real)


async def _prepare(sources: tuple, session: _Session) -> list:
    """*sources* with every async source gathered, nested lists kept, in order.

    A refused and skipped source becomes `None`, which a load skips.
    """
    jobs: list = []

    def walk(items: _ty.Iterable[_ty.Any]) -> list:
        out: list = []
        for item in items:
            if is_async_source(item):
                jobs.append(asyncio.ensure_future(_gather(item, session)))
                out.append(len(jobs) - 1)
            elif isinstance(item, (list, tuple)):
                out.append(walk(item))
            else:
                out.append(("keep", item))
        return out

    shape = walk(sources)
    try:
        gathered = await asyncio.gather(*jobs)
    except BaseException:
        for job in jobs:
            job.cancel()
        raise

    def rebuild(items: list) -> list:
        result = []
        for item in items:
            if isinstance(item, int):
                result.append(gathered[item])
            elif isinstance(item, tuple) and item and item[0] == "keep":
                result.append(item[1])
            else:
                result.append(rebuild(item))
        return result

    return rebuild(shape)


async def aload(
    *sources: _ty.Any,
    format: _ty.Any = None,
    encoding: str = "utf-8",
    policy: "_ty.Optional[Policy]" = None,
    into: _ty.Any = None,
    **options: _ty.Any,
) -> _ty.Any:
    """:func:`~anycfg.load`, awaitable, and able to read async sources.

    Each source is anything :func:`~anycfg.load` takes, or an async stream: an
    async iterable of `str`/`bytes` chunks (``response.aiter_bytes()``) or an
    object with ``async def read()`` (aiofiles, `asyncio.StreamReader`). An
    async source is read whole first, stopping past ``max_bytes``; its format
    comes from *format* or its ``name``. Several sources merge as in
    :func:`~anycfg.load`, and ``default=`` is passed through.
    """
    from ._api import _check_location, load

    for source in sources:
        if source is not None:
            _check_location(source, "aload", allow_async=True)
    prepared = await _prepare(sources, _session(policy, encoding))
    return await asyncio.to_thread(
        load,
        *prepared,
        format=format,
        encoding=encoding,
        policy=policy,
        into=into,
        **options,
    )


_DONE = object()


async def _drain(iterator: _ty.Iterator[_ty.Any]) -> _ty.AsyncIterator[_ty.Any]:
    """Yield what a sync iterator yields, each step in a worker thread."""
    while True:
        item = await asyncio.to_thread(next, iterator, _DONE)
        if item is _DONE:
            return
        yield item


async def aload_all(
    source: _ty.Any,
    /,
    *,
    format: _ty.Any = None,
    encoding: str = "utf-8",
    policy: "_ty.Optional[Policy]" = None,
    into: _ty.Any = None,
    **options: _ty.Any,
) -> _ty.AsyncIterator[_ty.Any]:
    """:func:`~anycfg.load_all` as an async iterator; sources as :func:`aload`."""
    from ._api import _check_location, load_all

    _check_location(source, "aload_all", allow_async=True)
    (prepared,) = await _prepare((source,), _session(policy, encoding))
    documents = await asyncio.to_thread(
        load_all,
        prepared,
        format=format,
        encoding=encoding,
        policy=policy,
        into=into,
        **options,
    )
    async for document in _drain(documents):
        yield document


def _is_async_writer(target: object) -> bool:
    if isinstance(target, (str, bytes, bytearray, Path, os.PathLike)):
        return False
    return inspect.iscoroutinefunction(
        getattr(target, "write", None)
    ) or inspect.iscoroutinefunction(getattr(target, "drain", None))


async def adump(
    obj: _ty.Any,
    target: _ty.Any,
    /,
    *,
    format: _ty.Any = None,
    encoding: str = "utf-8",
    **options: _ty.Any,
) -> None:
    """:func:`~anycfg.dump`, awaitable, and able to write an async target.

    *target* is anything :func:`~anycfg.dump` takes (written in a worker
    thread), or an async writer: an ``async def write()`` (aiofiles; given `str`,
    else bytes in *encoding*) or a writer with ``write()`` and
    ``async def drain()`` (`asyncio.StreamWriter`; given bytes). *obj* is
    serialized completely before anything is written.
    """
    from . import formats as _formats
    from ._api import _target_name, dump, dumps

    if not _is_async_writer(target):
        await asyncio.to_thread(
            dump, obj, target, format=format, encoding=encoding, **options
        )
        return
    if format is None:
        name = _target_name(target)
        if name is None:
            from .exceptions import UnsupportedFormatError

            raise UnsupportedFormatError(
                f"cannot tell which format to write to {target!r}; pass format="
            )
        format = _formats.for_filename(name)
    text = await asyncio.to_thread(dumps, obj, format=format, **options)
    if not text.endswith("\n"):
        text += "\n"
    drain = getattr(target, "drain", None)
    if inspect.iscoroutinefunction(drain):
        # A StreamWriter: bytes, then wait for the buffer to empty.
        target.write(text.encode(encoding))
        await drain()
        return
    try:
        await target.write(text)
    except TypeError:
        # A binary async file refuses str before writing anything.
        await target.write(text.encode(encoding))


async def loader_aload(
    loader: "Loader", sources: tuple, kwargs: _ty.Dict[str, _ty.Any]
) -> _ty.Any:
    session = _Session(loader._call(_overrides(kwargs)))
    prepared = await _prepare(sources, session)
    return await asyncio.to_thread(lambda: loader.load(*prepared, **kwargs))


async def loader_aload_all(
    loader: "Loader", sources: tuple, kwargs: _ty.Dict[str, _ty.Any]
) -> _ty.AsyncIterator[_ty.Any]:
    session = _Session(loader._call(_overrides(kwargs)))
    prepared = await _prepare(sources, session)
    documents = await asyncio.to_thread(lambda: loader.load_all(*prepared, **kwargs))
    async for document in _drain(documents):
        yield document


_CALL_ARGUMENTS = ("format", "into", "default", "flatten", "transform")


def _session(policy: "_ty.Optional[Policy]", encoding: str) -> _Session:
    from ._loader import Loader

    return _Session(Loader(policy=policy, encoding=encoding)._defaults)


def _overrides(kwargs: _ty.Mapping[str, _ty.Any]) -> dict:
    return {k: v for k, v in kwargs.items() if k not in _CALL_ARGUMENTS}
