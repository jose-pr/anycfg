"""Turning what a caller passes as sources into a flat, ordered list.

Accepted, possibly nested in lists and tuples:

* a `str` or `os.PathLike` — a **local** path, relative ones joined to the
  loader's ``base_dir``, glob patterns expanded; or, written
  ``<scheme>://...``, a registered source scheme (``cmd://``);
* a pathapi ``Path`` — used exactly as given, never joined to ``base_dir``;
* an open file object (anything with a callable ``read``);
* a :class:`~yaconfiglib.sources.Source`, or a mapping, which becomes a
  :class:`~yaconfiglib.sources.Data` source;
* ``None`` and ``""``, which are skipped, so an optional layer can be passed
  unconditionally.

Glob expansion is pathapi's, with ``native=False`` so ``**`` means the same on
every Python. What this module adds: a source is a pattern only by its own
components (a ``base_dir`` named ``proj [v2]`` stays literal), a pattern naming
an existing path loads literally, directory matches are dropped, matches are
sorted so layering is deterministic, and the same file named twice loads once —
a file named explicitly anywhere in the call beats a glob match for it.
"""

from __future__ import annotations

import glob as _glob
import logging
import os
import pathlib
import typing as _ty

from pathapi import LocalPath, Path

from . import sources as _sources
from .sources import Data, Source

logger = logging.getLogger(__name__)

#: ``(kind, payload)``: ``("source", Source)``, ``("stream", file object)`` or
#: ``("path", (path, pattern))`` where *pattern* is the glob a match came from.
Item = _ty.Tuple[str, _ty.Any]


def is_stream(source: object) -> bool:
    """A file object: anything that can `read()` its own content.

    Capability, not class, so `codecs.open()` and a custom reader count. Text,
    bytes and path types are sources in their own right and never streams.
    """
    if isinstance(source, (str, bytes, bytearray, Path, os.PathLike)):
        return False
    return callable(getattr(source, "read", None))


def _flatten(
    sources: _ty.Iterable[_ty.Any], allow_plugins: bool
) -> _ty.Iterator[_ty.Any]:
    for source in sources:
        if source is None or (isinstance(source, str) and not source):
            # The documented way to pass an absent optional layer.
            logger.debug("skipping empty source %r", source)
            continue
        if isinstance(source, Source) or is_stream(source):
            yield source
            continue
        if isinstance(source, _ty.Mapping):
            yield Data(source)
            continue
        if isinstance(source, (bytes, bytearray)):
            raise TypeError(
                "a bytes source is content, not a location; use "
                "yaconfiglib.loads(data, format=...) or pass a file object"
            )
        if isinstance(source, str):
            found = _sources.from_text(source, allow_plugins=allow_plugins)
            yield found if found is not None else source
            continue
        if isinstance(source, Path):
            yield source
            continue
        if isinstance(source, os.PathLike):
            # A stdlib path becomes the text it spells, so it gets the same
            # base_dir join and glob handling as that text.
            yield os.fsdecode(os.fspath(source))
            continue
        if isinstance(source, _ty.Iterable):
            yield from _flatten(source, allow_plugins)
            continue
        raise TypeError(
            f"cannot load a {type(source).__name__} source; pass a path, a file "
            "object, a mapping or a yaconfiglib.sources.Source"
        )


def _components(path: object) -> "_ty.List[str]":
    """The components of *path* that may hold pattern text: the anchor excluded.

    The ``?`` in an extended-length ``\\\\?\\C:\\...`` anchor is path syntax,
    not a wildcard.
    """
    pure = path if isinstance(path, pathlib.PurePath) else pathlib.PurePath(str(path))
    parts = list(pure.parts)
    if pure.anchor and parts and parts[0] == pure.anchor:
        del parts[0]
    return parts


def has_glob_pattern(path: object) -> bool:
    """Whether *path* holds glob wildcards outside its anchor (pathapi decides)."""
    text = str(path)
    if not _glob.has_magic(text):
        return False  # the common case, without building a path
    candidate = path if isinstance(path, Path) else LocalPath(text)
    return candidate.has_glob_pattern()


def literal_base(source: object, glob_base: object) -> str:
    """The deepest directory a pattern names before its first wildcard.

    Checked against confinement **before** expanding, so a pattern aimed outside
    the allowed roots lists nothing there. Computed from the caller's own
    pattern, so wildcard characters in ``base_dir`` stay literal.
    """
    literal = []
    for component in _components(source):
        if _glob.has_magic(component):
            break
        literal.append(component)
    if glob_base is not None:
        base = str(glob_base)
    elif pathlib.PurePath(str(source)).is_absolute():
        base = pathlib.PurePath(str(source)).anchor
    else:
        # A relative pattern with no base_dir expands from the working directory.
        base = os.getcwd()
    return os.path.join(base, *literal) if literal else base


def _dedup_key(path: object) -> str:
    """Lexical and I/O-free: ``./x``, ``x/../x`` and, on Windows, ``X`` are one file.

    Never ``resolve()``: that stats every source and would merge two symlinked
    names a caller meant to keep apart.
    """
    if isinstance(path, pathlib.PurePath):
        return os.path.normcase(os.path.abspath(os.fspath(path)))
    return str(path)


def _ordered_files(matches: _ty.Iterable[_ty.Any]) -> "_ty.List[_ty.Any]":
    """Drop directory matches, then sort by component.

    Ordering is this library's contract, not glob's: matches merge in the order
    they come, so the same tree must layer the same way on every filesystem.
    """
    files = []
    for match in matches:
        try:
            if match.is_dir():
                logger.debug("skipping directory match %s", match)
                continue
        except OSError:
            # Vanished or unreadable: let the read report it.
            pass
        files.append(match)
    files.sort(key=lambda match: tuple(_components(match)))
    return files


def _glob_hook(
    on_error: "_ty.Callable[[OSError, _ty.Any], bool]",
) -> "_ty.Callable[[OSError], None]":
    """Adapt ``on_error(error, directory) -> skip`` to pathapi's ``glob(on_error=)``.

    pathapi reports one unreadable directory several times for a ``**``
    pattern; the caller is asked once per directory. A missing or
    non-directory parent is not a failure: the pattern simply matches nothing.
    """
    asked: set = set()

    def hook(error: OSError) -> None:
        if isinstance(error, (FileNotFoundError, NotADirectoryError)):
            return
        filename = getattr(error, "filename", None)
        if str(filename) in asked:
            return
        asked.add(str(filename))
        directory = LocalPath(filename) if filename else None
        if on_error(error, directory):
            logger.debug("skipping directory %s during glob expansion", directory)
            return
        raise error

    return hook


def resolve(
    sources: _ty.Iterable[_ty.Any],
    *,
    base_dir: "_ty.Optional[Path]",
    recursive: bool,
    bound_loops: bool = True,
    on_glob_error: "_ty.Optional[_ty.Callable[[OSError, _ty.Any], bool]]" = None,
    confine: "_ty.Optional[_ty.Callable[..., bool]]" = None,
    allow_plugins: bool = True,
    memo: "_ty.Optional[set]" = None,
) -> _ty.Iterator[Item]:
    """Yield ``(kind, payload)`` for every source, in order.

    Everything is classified before anything is yielded, so an unsupported
    source raises before any source loads, and a file named explicitly anywhere
    in the call beats a glob match for it.

    *confine* is ``confine(location, pattern=None) -> skip``: asked about a
    pattern's literal base before expanding it and about every match before it
    is yielded. *memo* collects the files already yielded, across calls.
    """
    memo = set() if memo is None else memo
    hook = _glob_hook(on_glob_error) if on_glob_error is not None else None

    def pattern_or_literal(
        path: Path, glob_base: _ty.Any, source: _ty.Any
    ) -> "_ty.Optional[_ty.Tuple[str, _ty.Any]]":
        if not has_glob_pattern(source):
            return ("literal", path)
        # Confined before the existence probe, so a pattern aimed outside the
        # roots cannot learn whether a file of that name exists there.
        if confine is not None and (
            confine(literal_base(source, glob_base)) or confine(path)
        ):
            return None
        if path.exists():
            return ("literal", path)
        return ("pattern", (path, glob_base, source))

    classified: "_ty.List[_ty.Tuple[str, _ty.Any]]" = []
    for source in _flatten(sources, allow_plugins):
        if isinstance(source, Source):
            classified.append(("source", source))
        elif is_stream(source):
            classified.append(("stream", source))
        else:
            if isinstance(source, Path):
                path, glob_base = source, None
            else:
                path, glob_base = LocalPath(source), None
                if base_dir is not None and not path.is_absolute():
                    glob_base = base_dir
                    path = base_dir / source
            entry = pattern_or_literal(path, glob_base, source)
            if entry is not None:
                classified.append(entry)

    literal_keys = {_dedup_key(item) for kind, item in classified if kind == "literal"}
    for kind, item in classified:
        if kind in ("source", "stream"):
            yield kind, item
            continue
        if kind == "literal":
            key = _dedup_key(item)
            if key in memo:
                logger.warning("ignoring duplicated file %s", item)
                continue
            memo.add(key)
            yield "path", (item, None)
            continue
        path, glob_base, source = item
        if glob_base is not None:
            matches = glob_base.glob(
                str(source),
                recursive=recursive,
                on_error=hook,
                bound_loops=bound_loops,
                native=False,
            )
        else:
            matches = path.glob(
                None,
                recursive=recursive,
                on_error=hook,
                bound_loops=bound_loops,
                native=False,
            )
        for match in _ordered_files(matches):
            key = _dedup_key(match)
            if key in literal_keys:
                logger.debug("%s is also named explicitly; skipping the match", match)
                continue
            if key in memo:
                continue
            # A wildcard can leave the roots after a literal prefix inside them
            # (`*/../../elsewhere/*`), so each match is checked too, naming the
            # pattern rather than the file expansion found.
            if confine is not None and confine(match, str(source)):
                continue
            memo.add(key)
            yield "path", (match, str(source))
