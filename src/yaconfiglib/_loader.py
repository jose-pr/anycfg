"""The layered loader: read sources, parse them, merge them, finish the result."""

from __future__ import annotations

import codecs
import collections.abc as _abc
import dataclasses
import io
import logging
import os
import pathlib
import types
import typing as _ty

from pathapi import LocalPath, Path

from . import _confine, _paths
from . import formats as _formats
from ._context import LoadContext, Policy, _LoadState
from ._dotaccess import DotAccessibleDict, to_dot_access
from ._hydrate import hydrate
from ._merge import Merge, deep, prune_deletes, resolve_merge, simple, substitute
from ._scalar import resolved
from ._templating import (
    Interpolator,
    InterpolateChoice,
    resolve_interpolator,
    check_template,
    interpolate_document,
    render_file,
    template_suffix,
)
from .exceptions import (
    BudgetExceededError,
    CommandsDisabledError,
    ConfigTypeError,
    ConfigValueError,
    ConfinementError,
    ErrorFrame,
    MissingDependencyError,
    ParseError,
    PolicyError,
    UnsupportedFormatError,
    _add_error_context,
)
from .sources import Payload, Source
from .sources._command import SCRIPT_SUFFIXES, Command

__all__ = ["Loader", "LoadEvent"]

logger = logging.getLogger(__name__)

_COLLECT_MODES = ("last", "list", "hash")
#: How much a file or stream is asked for at a time.
_READ_CHUNK = 64 * 1024

#: Options an ``!include`` mapping may set. Anything else a document supplies —
#: policy fields, ``loader``, callables — is dropped with a warning: a document
#: can only narrow what the caller allowed.
INCLUDE_OPTIONS = frozenset(
    {"encoding", "default", "flatten", "merge", "merge_options", "recursive", "format"}
)
#: The include options' types, checked because a document supplies them.
_INCLUDE_OPTION_TYPES = {
    "encoding": (str,),
    "flatten": (bool,),
    "recursive": (bool,),
    "format": (str,),
    "merge": (str,),
    "merge_options": (_abc.Mapping,),
}
#: Strategies that understand scalars kept as text for a typed load; any other
#: one is given plain values.
_TEXT_AWARE_MERGES = (simple, substitute, deep)


class LoadEvent(_ty.NamedTuple):
    """One failure offered to ``on_error``.

    Attributes:
        error: The exception, unchanged.
        phase: ``"load"`` (reading or parsing *path* failed), ``"include"`` (a
            source *path* includes failed), ``"merge"``, ``"interpolate"`` or
            ``"glob"`` (a directory could not be listed while expanding a
            pattern; *path* is that directory).
        path: The source, or `None` where no single source applies.
        key: For ``"interpolate"``, the key path of the failing value.
    """

    error: BaseException
    phase: str
    path: _ty.Any = None
    key: "_ty.Optional[tuple]" = None


OnError = _ty.Union[bool, _ty.Callable[[LoadEvent], bool]]
#: ``path_filter=``: given each path before it is read; falsy refuses it.
PathFilter = _ty.Callable[[Path], _ty.Any]


@dataclasses.dataclass(frozen=True)
class _Call:
    """The settings of one call: the loader's, with that call's overrides."""

    policy: Policy
    encoding: str
    merge: Merge
    merge_options: _ty.Mapping[str, _ty.Any]
    collect: "_ty.Optional[str]"
    key: "_ty.Callable[[str, _ty.Any], _ty.Any]"
    recursive: bool
    bound_loops: bool
    path_filter: "_ty.Optional[PathFilter]"
    interpolate: InterpolateChoice
    #: What *interpolate* resolves to under *policy*; None when off.
    interpolator: "_ty.Optional[Interpolator]"
    inject_env: bool
    wrap: _ty.Any
    on_error: OnError
    format_options: _ty.Mapping[str, _ty.Mapping[str, _ty.Any]]
    document_formats: "_ty.Optional[_ty.FrozenSet[str]]"
    base_dir: "_ty.Optional[Path]"


def _default_key(name: str, value: _ty.Any) -> str:
    return pathlib.PurePath(name).stem if name else name


def _as_base_dir(value: _ty.Any) -> "_ty.Optional[Path]":
    if value is None or value == "":
        return None
    if isinstance(value, Path):
        return value
    if isinstance(value, (str, os.PathLike)):
        return LocalPath(os.fsdecode(os.fspath(value)))
    raise TypeError(f"base_dir= takes a path, not {type(value).__name__}")


def check_encoding(encoding: _ty.Any) -> str:
    """*encoding*, if it names a text codec; anything else raises.

    A bytes-to-bytes codec (``zlib_codec``, ``base64_codec``) is refused: it
    would turn a few bytes counted against ``max_bytes`` into many.
    """
    if not isinstance(encoding, str):
        raise ConfigTypeError(
            f"an encoding is a codec name, not a {type(encoding).__name__}"
        )
    try:
        info = codecs.lookup(encoding)
    except LookupError:
        info = None
    if info is None:
        raise ConfigValueError(f"unknown encoding {encoding!r}")
    if not getattr(info, "_is_text_encoding", True):
        raise ConfigValueError(f"{encoding!r} is not a text encoding")
    return encoding


def _decode(data: bytes, encoding: str, name: _ty.Optional[str]) -> str:
    """Strict decode; one leading BOM dropped; a failure is a parse failure."""
    check_encoding(encoding)
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as error:
        failure: Exception = ParseError(
            f"not valid {encoding} at byte {error.start}; pass encoding= naming "
            "the file's codec",
            path=name,
        )
    else:
        failure = None
    if failure is not None:
        # Outside the except blocks: the decode error holds the bytes.
        raise failure
    return text[1:] if text.startswith("﻿") else text


def _error_phase(error: BaseException) -> str:
    """``"include"`` when *error* came from a nested include, else ``"load"``."""
    for frame in getattr(error, "config_frames", ()) or ():
        if frame.kind == "include":
            return "include"
    return "load"


def _environ_snapshot() -> _ty.Mapping[str, str]:
    return types.MappingProxyType(dict(os.environ))


def _flatten_result(results: _ty.Any) -> _ty.Any:
    if isinstance(results, _ty.Mapping):
        flat: dict = {}
        for member_key, member in results.items():
            if member is None:
                continue
            if not isinstance(member, _ty.Mapping):
                raise ConfigTypeError(
                    f"flatten=True: member {member_key!r} is a "
                    f"{type(member).__name__}, not a mapping"
                )
            flat.update(member)
        return flat
    if isinstance(results, list):
        items: list = []
        for index, member in enumerate(results):
            if member is None:
                continue
            if isinstance(member, (str, bytes)) or not isinstance(
                member, (list, tuple)
            ):
                raise ConfigTypeError(
                    f"flatten=True: member {index} is a {type(member).__name__}, "
                    "not a sequence"
                )
            items.extend(member)
        return items
    raise ConfigTypeError("flatten=True needs a mapping or a list of results")


#: tplx's built-in template suffixes, for the hint when tplx is not installed.
_TPLX_SUFFIXES = (
    ".j2",
    ".jinja",
    ".jinja2",
    ".mako",
    ".liquid",
    ".hbs",
    ".handlebars",
    ".mustache",
    ".tmpl",
    ".gotmpl",
    ".cheetah",
    ".erb",
    ".epp",
    ".shtpl",
    ".envsubst",
    ".template",
)


def _hint_missing_templates(name: str) -> None:
    """Raise naming the extra when *name* looks like a template and tplx is absent."""
    import importlib.util

    if (
        name.lower().endswith(_TPLX_SUFFIXES)
        and importlib.util.find_spec("tplx") is None
    ):
        raise MissingDependencyError(
            f"{name} looks like a template, and template files need the "
            '"templates" extra: pip install "yaconfiglib[templates]"'
        )


def _format_name(explicit: _ty.Any) -> "_ty.Optional[str]":
    if explicit is None:
        return None
    if isinstance(explicit, type) and issubclass(explicit, _formats.Format):
        return explicit.name
    return str(explicit)


def check_format_options(
    cls: "_ty.Type[_formats.Format]",
    options: _ty.Mapping[str, _ty.Any],
    verb: str = "load",
) -> None:
    """Refuse an option *cls* does not take for *verb* (``load`` or ``dump``)."""
    allowed = cls.load_options if verb == "load" else cls.dump_options
    unknown = set(options) - set(allowed)
    if unknown:
        raise TypeError(
            f"the {cls.name} format takes no {verb} option(s) "
            f"{', '.join(sorted(unknown))}; it accepts: "
            f"{', '.join(sorted(allowed)) or 'none'}"
        )


def _stream_file(stream: _ty.Any) -> "_ty.Optional[str]":
    """The real file a stream reads, or None (``<stdin>``, a descriptor, none)."""
    name = getattr(stream, "name", None)
    if isinstance(name, (bytes, os.PathLike)):
        try:
            name = os.fsdecode(name)
        except (TypeError, ValueError):
            return None
    if not isinstance(name, str) or not name:
        return None
    if name.startswith("<") and name.endswith(">"):
        return None
    return name


def _label(kind: str, item: _ty.Any) -> str:
    """What an error names a source by."""
    if kind == "path":
        return str(item[0])
    if kind == "stream":
        return _stream_file(item) or "<stream>"
    return str(getattr(item, "name", "") or "")


class _Session:
    """One top-level call: its settings, its counters, and include handling."""

    def __init__(self, call: _Call) -> None:
        self.call = call
        self.state = _LoadState()
        self.roots = _confine.resolve_roots(call.policy.confine_to, call.base_dir)
        self.prune = False
        self.keep_text = False

    # -- context -------------------------------------------------------------

    def context(self) -> LoadContext:
        return LoadContext(
            policy=self.call.policy,
            encoding=self.call.encoding,
            keep_text=self.keep_text,
            _loader=self,
            _state=self.state,
        )

    # -- errors --------------------------------------------------------------

    def offer(
        self, error: BaseException, phase: str, path: _ty.Any, key: _ty.Any = None
    ) -> bool:
        """Offer one failure to ``on_error``; True means skip it and continue.

        Every offer logs at DEBUG. A skip under ``on_error=True`` also logs a
        WARNING naming the source, the phase and the error's type — never its
        text, which can quote configuration.
        """
        logger.debug("%s error for %s: %s", phase, path, type(error).__name__)
        handler = self.call.on_error
        if isinstance(error, (BudgetExceededError,)):
            return False
        if callable(handler):
            skip = bool(handler(LoadEvent(error, phase, path, key)))
            if skip:
                logger.debug(
                    "on_error skipped %s during %s", path, phase, exc_info=error
                )
            return skip
        if handler:
            logger.warning(
                "skipped %s during %s after %s (on_error=True)",
                path,
                phase,
                type(error).__name__,
            )
            return True
        return False

    # -- confinement ---------------------------------------------------------

    def check_confinement(
        self, path: _ty.Any, pattern: _ty.Optional[str] = None
    ) -> None:
        """Refuse *path* unless confinement and ``path_filter`` allow it.

        Nothing has been read yet.
        """
        if isinstance(path, Path) and self.call.path_filter is not None:
            self.check_filter(path, pattern)
        roots = self.roots
        if roots is None:
            return
        kind = _confine.kind(path)
        if kind == "exempt" or (kind == "check" and _confine.within_roots(path, roots)):
            return
        if not roots:
            detail = "confine_to= allows nothing: the allowlist is empty"
        elif kind == "foreign":
            detail = (
                "confine_to= allows only local files, and this source is not one; "
                f"allowed roots: {', '.join(roots)}"
            )
        else:
            detail = f"outside every confine_to= root: {', '.join(roots)}"
        named = str(path) if kind == "foreign" else _confine.confinement_key(path)
        if pattern is not None:
            # A match, not a named source: naming it would report a file name the
            # document never wrote. The full path goes to DEBUG only.
            logger.debug("confinement refused %s, matched by %s", named, pattern)
            error = ConfinementError(f"refusing to read a match of {pattern}: {detail}")
            _add_error_context(error, source=str(pattern))
        else:
            error = ConfinementError(f"refusing to read {named}: {detail}")
            _add_error_context(error, source=str(path))
        raise error

    def check_filter(self, path: Path, pattern: "_ty.Optional[str]") -> None:
        """Refuse *path* when the caller's ``path_filter`` returns a falsy value."""
        if self.call.path_filter(path):
            return
        if pattern is not None:
            # As for confinement: never name a file the document did not write.
            logger.debug("path_filter refused %s, matched by %s", path, pattern)
            error = ConfinementError(
                f"refusing to read a match of {pattern}: path_filter refused it"
            )
            _add_error_context(error, source=str(pattern))
        else:
            error = ConfinementError(f"refusing to read {path}: path_filter refused it")
            _add_error_context(error, source=str(path))
        raise error

    def confine_hook(self, path: _ty.Any, pattern: _ty.Optional[str] = None) -> bool:
        """`_paths.resolve`'s ``confine``: refuse, or offer the refusal; True = skip."""
        try:
            self.check_confinement(path, pattern)
        except ConfinementError as error:
            if self.offer(error, "load", path):
                return True
            raise
        return False

    # -- reading -------------------------------------------------------------

    def items(
        self,
        sources: _ty.Iterable[_ty.Any],
        *,
        base_dir: "_ty.Optional[Path]",
        recursive: bool,
        document: bool,
    ) -> _ty.Iterator[_paths.Item]:
        return _paths.resolve(
            sources,
            base_dir=base_dir,
            recursive=recursive,
            bound_loops=self.call.bound_loops,
            on_glob_error=lambda error, directory: self.offer(error, "glob", directory),
            confine=self.confine_hook,
            allow_plugins=not document,
        )

    def read(
        self, kind: str, item: _ty.Any, context: LoadContext, explicit: _ty.Any
    ) -> Payload:
        """Turn one resolved item into a `Payload`, under the policy."""
        if kind == "path":
            path, pattern = item
            self.check_confinement(path, pattern)
            name = str(path)
            if name.lower().endswith(SCRIPT_SUFFIXES):
                kind, item = "source", Command.script(
                    name, format=_format_name(explicit)
                )
            else:
                identity = (
                    _confine.confinement_key(path)
                    if _confine.kind(path) == "check"
                    else name
                )
                if identity in context.chain:
                    # Before reading: on POSIX `missing/../a.yaml` cannot even be
                    # opened, while lexically it is the file already loading.
                    raise ConfigValueError(
                        "include cycle: " + " -> ".join(context.trail + (name,))
                    )
                with path.open("rb") as handle:
                    data = self.read_within(handle.read, name)
                self.count_bytes(len(data), name)
                return Payload(data, name=name, identity=identity, origin=path)
        if kind == "source":
            source: Source = item
            if source.executes and not context.policy.allow_commands:
                raise CommandsDisabledError(
                    f"refusing to run command source {source.name!r}: the policy "
                    "does not allow commands"
                )
            payload = source.read(context)
            if payload.parsed:
                # Parsed data can hold DELETE markers no format flagged.
                self.prune = True
            if isinstance(payload.data, (str, bytes)):
                self.count_bytes(len(payload.data), payload.name)
            return payload
        # A stream.
        stream = item
        real = _stream_file(stream)
        if real is not None:
            self.check_confinement(real)
        name = real or "<stream>"
        data = self.read_within(
            stream.read,
            name,
            kind=type(stream).__name__,
            # Only an io stream promises what read(n) means; any other reader
            # is called once, as it always was.
            chunked=isinstance(stream, io.IOBase),
        )
        self.count_bytes(len(data), name)
        return Payload(
            data,
            name=name,
            identity=f"stream:{id(stream)}",
            origin=LocalPath(real) if real else None,
        )

    def read_within(
        self,
        read: _ty.Callable[..., _ty.Any],
        name: str,
        kind: str = "file",
        chunked: bool = True,
    ) -> _ty.Any:
        """Everything *read* gives, asked for in chunks, stopping past the budget.

        A source larger than what is left of ``max_bytes`` raises one chunk past
        the budget, not after it has all been held in memory. Without *chunked*
        ``read()`` is called once.
        """
        remaining = self.call.policy.max_bytes - self.state.bytes
        parts: list = []
        total = 0
        parts_done = not chunked
        chunk = read(_READ_CHUNK) if chunked else read()
        while True:
            if not isinstance(chunk, (str, bytes, bytearray)):
                raise TypeError(
                    f"{kind}.read() returned {type(chunk).__name__}; a stream "
                    "source must read as str or bytes"
                )
            if not chunk:
                break
            if parts and type(chunk) is not type(parts[0]):
                raise TypeError(f"{kind}.read() mixed str and bytes")
            total += len(chunk)
            if total > remaining:
                raise BudgetExceededError(
                    f"reading {name} takes the load past max_bytes="
                    f"{self.call.policy.max_bytes}"
                )
            parts.append(chunk)
            if parts_done:
                break
            chunk = read(_READ_CHUNK)
        if parts and isinstance(parts[0], str):
            return "".join(parts)
        return b"".join(bytes(part) for part in parts)

    def count_bytes(self, size: int, name: str) -> None:
        self.state.bytes += size
        if self.state.bytes > self.call.policy.max_bytes:
            raise BudgetExceededError(
                f"reading {name} takes the load past max_bytes="
                f"{self.call.policy.max_bytes}"
            )

    # -- parsing -------------------------------------------------------------

    def plan(
        self,
        name: str,
        *,
        explicit: _ty.Any,
        document: bool,
        declared: "_ty.Optional[str]" = None,
        trusted: bool = False,
        command: bool = False,
    ) -> "_ty.Tuple[type, _ty.Optional[str]]":
        """``(format class, template suffix or None)`` for a source named *name*.

        *explicit* (a caller's ``format=``) wins and may name any registered
        format; a template file still renders first. A format a source read from
        data (*declared*, not *trusted*), or a file name a document chose, must
        be one documents may select. A template's policy is checked here,
        before anything is read.
        """
        allowed = self.call.document_formats
        if allowed is None:
            # Read per call: a format registered after the loader was built counts.
            allowed = _formats.document_formats()
        restrict = allowed if document else None
        suffix = None
        if name and declared is None and not command:
            suffix = template_suffix(name)
        if explicit is not None:
            if isinstance(explicit, type) and issubclass(explicit, _formats.Format):
                cls = explicit
            else:
                cls = _formats.get(explicit, allowed=restrict)
        elif declared is not None:
            cls = _formats.get(
                declared, allowed=None if trusted and not document else allowed
            )
        elif command:
            raise ConfigValueError(
                f"command source {name!r} gives no output format: write "
                "cmd+<format>://, pass format=, or print a #!<format> first line"
            )
        else:
            cls = None
            if suffix is not None:
                cls = _formats.find_for_filename(name[: -len(suffix)], allowed=restrict)
            if cls is None:
                suffix = None
                try:
                    cls = _formats.for_filename(name, allowed=restrict)
                except UnsupportedFormatError:
                    _hint_missing_templates(name)
                    raise
        if suffix is not None:
            check_template(name, suffix, self.call.policy)
        return cls, suffix

    def documents(
        self,
        payload: Payload,
        context: LoadContext,
        *,
        explicit: _ty.Any = None,
        document: bool = False,
        options: "_ty.Optional[_ty.Mapping[str, _ty.Any]]" = None,
        single: bool = False,
        planned: "_ty.Optional[_ty.Tuple[type, _ty.Optional[str]]]" = None,
    ) -> _ty.List[_ty.Any]:
        """Parse *payload* into its documents (one, or every one of a stream).

        *planned* is :meth:`plan`'s answer when the caller already asked it.
        """
        if payload.parsed:
            return [payload.data]
        if payload.identity in context.chain:
            raise ConfigValueError(
                "include cycle: " + " -> ".join(context.trail + (payload.name,))
            )
        if len(context.chain) >= self.call.policy.max_depth:
            raise BudgetExceededError(
                f"includes nest deeper than max_depth={self.call.policy.max_depth}"
            )
        command = payload.identity.startswith("command:")
        if planned is not None and payload.format is None and not command:
            fmt_cls, suffix = planned
        else:
            fmt_cls, suffix = self.plan(
                payload.name,
                explicit=explicit,
                document=document,
                declared=payload.format,
                trusted=payload.trusted_format,
                command=command,
            )
        fmt = fmt_cls()
        encoding = payload.encoding or context.encoding
        data = payload.data
        if not fmt.binary and isinstance(data, bytes):
            data = _decode(data, encoding, payload.name or None)
        if suffix is not None:
            try:
                data = render_file(
                    data,
                    path=payload.origin or payload.name,
                    suffix=suffix,
                    policy=context.policy,
                    roots=self.roots,
                    inject_env=self.call.inject_env,
                )
            except PolicyError:
                raise
            except Exception as error:  # noqa: BLE001 - names the template, re-raises
                _add_error_context(error, frame=ErrorFrame("render", payload.name))
                raise
        merged = dict(self.call.format_options.get(fmt_cls.name, {}))
        merged.update(options or {})
        check_format_options(fmt_cls, merged)
        inner = context.derive(
            origin=payload.origin,
            name=payload.name or None,
            encoding=encoding,
            chain=context.chain + (payload.identity,),
            trail=context.trail + (payload.name,),
        )
        try:
            if single or "load_all" not in _formats.capabilities(fmt_cls):
                return [fmt.loads(data, context=inner, **merged)]
            return list(fmt.loads_all(data, context=inner, **merged))
        except Exception as error:  # noqa: BLE001 - names the source, re-raises
            if suffix is not None:
                _add_error_context(error, frame=ErrorFrame("render", payload.name))
            elif command:
                _add_error_context(error, frame=ErrorFrame("command", payload.name))
            raise

    # -- the source loop -----------------------------------------------------

    def each(
        self,
        sources: _ty.Iterable[_ty.Any],
        context: LoadContext,
        *,
        explicit: _ty.Any = None,
        options: "_ty.Optional[_ty.Mapping[str, _ty.Any]]" = None,
        transform: "_ty.Optional[_ty.Callable[[_ty.Any, str], _ty.Any]]" = None,
        base_dir: "_ty.Optional[Path]" = None,
        recursive: "_ty.Optional[bool]" = None,
        document: bool = False,
        single: bool = False,
    ) -> _ty.Iterator[_ty.Tuple[str, _ty.Any]]:
        """Yield ``(name, document)`` for every document of every source.

        A failure is offered to ``on_error`` once, here, at this level.
        """
        items = self.items(
            sources,
            base_dir=self.call.base_dir if base_dir is None else base_dir,
            recursive=self.call.recursive if recursive is None else recursive,
            document=document,
        )
        for kind, item in items:
            label = _label(kind, item)
            try:
                planned = None
                if kind == "path" and not str(item[0]).lower().endswith(
                    SCRIPT_SUFFIXES
                ):
                    # Decided from the name alone, so a file no format reads, or
                    # a template the policy refuses, fails before it is opened.
                    planned = self.plan(
                        str(item[0]), explicit=explicit, document=document
                    )
                payload = self.read(kind, item, context, explicit)
                values = self.documents(
                    payload,
                    context,
                    explicit=explicit,
                    document=document,
                    options=options,
                    single=single,
                    planned=planned,
                )
            except Exception as error:  # noqa: BLE001 - feeds the on_error predicate
                if label:
                    _add_error_context(error, source=label)
                if self.offer(error, _error_phase(error), label or None):
                    continue
                raise
            for value in values:
                if transform is not None:
                    # Plain values: a callable never sees text kept for into=.
                    value = transform(self.plain(value), payload.name)
                yield payload.name, value

    def plain(self, value: _ty.Any) -> _ty.Any:
        """*value* as the format read it, for code that is not the typed build."""
        return resolved(value) if self.keep_text else value

    def combine(
        self,
        documents: _ty.Iterable[_ty.Tuple[str, _ty.Any]],
        *,
        merge: Merge,
        merge_options: _ty.Mapping[str, _ty.Any],
        collect: "_ty.Optional[str]",
        default: _ty.Any,
    ) -> "_ty.Tuple[_ty.Any, bool]":
        """``(result, whether any document was combined)``."""
        result = default
        started = False
        plain_merge = self.keep_text and merge not in _TEXT_AWARE_MERGES
        for name, value in documents:
            if collect == "last":
                result, started = value, True
                continue
            if collect == "list":
                if not started:
                    result, started = [], True
                result.append(value)
                continue
            if collect == "hash":
                if not name and self.call.key is _default_key:
                    raise ConfigValueError(
                        'collect="hash" needs a named source; in-memory content has '
                        "no key"
                    )
                key = self.call.key(name, self.plain(value))
                if not started:
                    result, started = {}, True
                if key in result:
                    logger.warning(
                        'collect="hash": key %r comes from more than one source; the '
                        "later document replaces the earlier one (pass key= to keep both)",
                        key,
                    )
                result[key] = value
                continue
            if not started:
                result, started = value, True
                continue
            try:
                if plain_merge:
                    result = merge(resolved(result), resolved(value), **merge_options)
                else:
                    result = merge(result, value, **merge_options)
            except Exception as error:  # noqa: BLE001 - feeds the on_error predicate
                _add_error_context(error, frame=ErrorFrame("merge", name))
                if self.offer(error, "merge", name):
                    continue
                raise
        return result, started

    # -- includes ------------------------------------------------------------

    def include(
        self, context: LoadContext, sources: tuple, options: _ty.Mapping[str, _ty.Any]
    ) -> _ty.Any:
        """``context.include``: load *sources* as part of the current document."""
        policy = context.policy
        if not policy.allow_includes:
            raise PolicyError("refusing !include: the policy does not allow includes")
        dropped = sorted(name for name in options if name not in INCLUDE_OPTIONS)
        if dropped:
            logger.warning(
                "ignoring include option(s) not allowed in a document: %s",
                ", ".join(map(str, dropped)),
            )
        options = {k: v for k, v in options.items() if k in INCLUDE_OPTIONS}
        for name, accepted in _INCLUDE_OPTION_TYPES.items():
            value = options.get(name)
            if value is not None and not isinstance(value, accepted):
                raise ConfigTypeError(
                    f"an include's {name}: takes a {accepted[0].__name__}, not a "
                    f"{type(value).__name__}"
                )
        self.state.includes += 1
        if self.state.includes > policy.max_includes:
            raise BudgetExceededError(
                f"more than max_includes={policy.max_includes} includes in one load"
            )
        merge_name = options.get("merge")
        try:
            merge = resolve_merge(merge_name) if merge_name else self.call.merge
        except ValueError as error:
            # A document's mistake, not the caller's: a configuration error.
            raise ConfigValueError(str(error)) from None
        merge_options = options.get("merge_options") or self.call.merge_options
        encoding = check_encoding(options.get("encoding") or context.encoding)
        nested = context.derive(encoding=encoding)
        origin = context.origin
        base = (
            origin.parent
            if isinstance(origin, Path) and _confine.kind(origin) != "exempt"
            else self.call.base_dir
        )
        documents = self.each(
            sources,
            nested,
            explicit=options.get("format"),
            base_dir=base,
            recursive=options.get("recursive", self.call.recursive),
            document=True,
        )
        result, loaded = self.combine(
            documents,
            merge=merge,
            merge_options=merge_options,
            collect=None,
            default=options.get("default"),
        )
        if options.get("flatten") and loaded:
            result = _flatten_result(result)
        return result

    # -- finishing -----------------------------------------------------------

    def finish(self, result: _ty.Any, *, into: _ty.Any) -> _ty.Any:
        call = self.call
        if self.prune or self.state.deletes:
            result = prune_deletes(result)
        if call.interpolator is not None:
            result = interpolate_document(
                result,
                call.interpolator,
                extra={"env": _environ_snapshot()} if call.inject_env else None,
                strict=call.policy.strict,
                on_error=lambda error, key: self.offer(error, "interpolate", None, key),
            )
        if into is not None:
            return hydrate(into, result)
        wrap = call.wrap
        if wrap is None or wrap is False:
            return result
        if wrap is True or wrap is DotAccessibleDict:
            return to_dot_access(result)
        return wrap(result)


_OVERRIDABLE = frozenset(
    {
        "policy",
        "encoding",
        "merge",
        "merge_options",
        "collect",
        "key",
        "recursive",
        "interpolate",
        "inject_env",
        "wrap",
        "on_error",
        "path_filter",
        "format_options",
    }
)


class Loader:
    """Load layered configuration: many sources, merged in order.

    Construct one with the settings every call shares, then call
    :meth:`load` (merge everything into one result) or :meth:`load_all` (each
    document on its own). Each setting can be overridden per call by passing it
    to either method.

    Args:
        base_dir: The directory relative path sources resolve against, and
            includes inside in-memory content. `None` means the working
            directory. A pathapi ``Path`` source is never joined to it.
        policy: The :class:`~yaconfiglib.Policy`; untrusted by default.
        encoding: The codec bytes are decoded with (strict; one BOM ignored),
            and the default for includes.
        merge: A strategy name (``"simple"``, ``"substitute"``, ``"deep"``, or
            one added with :func:`~yaconfiglib.register_merge`) or a callable
            ``(a, b, *, memo=None, **options) -> merged``.
        merge_options: Keywords every merge call receives (``mergelists``).
        collect: Instead of merging: ``"last"`` keeps the last document,
            ``"list"`` collects them in order, ``"hash"`` keys them with *key*.
        key: ``(name, document) -> key`` for ``collect="hash"``; the source's
            file stem by default.
        recursive: Whether ``**`` in a glob source descends into directories.
        bound_loops: Bound a ``**`` walk through a Windows junction loop.
        path_filter: ``(path) -> bool``, asked about every path before it is
            read — a top-level source, an include, a script, a glob pattern
            before it is expanded and each match — with the pathapi ``Path``
            that would be read. A falsy result refuses it with
            `ConfinementError` (offered to ``on_error``); an exception it
            raises propagates. A ``str`` source, top-level or from a document,
            always becomes a local path; any other kind of ``Path`` comes only
            from the caller, so this is where a caller who passes remote paths
            vets them.
        interpolate: Render the template text in the merged document's
            strings, once, after merging: `True` with Jinja, a tplx engine's
            name or class (``"liquid"``, ``"envsubst"``) with that engine,
            ``"vars"`` with :class:`~yaconfiglib.VarsInterpolator` (``${a.b}``
            references; needs no extra), or an
            :class:`~yaconfiglib.Interpolator` object. Engines are sandboxed
            and strict as the policy says. `False` (the default) is off.
        inject_env: Give templates and interpolation a read-only ``env``
            snapshot of the environment.
        wrap: `True` (or `DotAccessibleDict`) returns dot-accessible mappings;
            a callable is applied to the result; `None` returns plain data.
        on_error: `True` skips every failing source (logged at WARNING); a
            callable ``(LoadEvent) -> bool`` decides per failure; `False`
            raises.
        format_options: ``{"json": {...}}``: options for a format whenever
            this loader uses it.
        formats: The format names a **document** may select (an include, a
            command's ``#!`` line). Built-in and code-registered formats by
            default; a format from an installed plugin must be named here.
    """

    def __init__(
        self,
        *,
        base_dir: _ty.Any = None,
        policy: "_ty.Optional[Policy]" = None,
        encoding: str = "utf-8",
        merge: "_ty.Union[str, Merge]" = "simple",
        merge_options: "_ty.Optional[_ty.Mapping[str, _ty.Any]]" = None,
        collect: "_ty.Optional[str]" = None,
        key: "_ty.Optional[_ty.Callable[[str, _ty.Any], _ty.Any]]" = None,
        recursive: bool = False,
        bound_loops: bool = True,
        path_filter: "_ty.Optional[PathFilter]" = None,
        interpolate: InterpolateChoice = False,
        inject_env: bool = False,
        wrap: _ty.Any = None,
        on_error: OnError = False,
        format_options: "_ty.Optional[_ty.Mapping[str, _ty.Mapping[str, _ty.Any]]]" = None,
        formats: "_ty.Optional[_ty.Iterable[str]]" = None,
    ) -> None:
        self._defaults = self._settle(
            dict(
                policy=Policy() if policy is None else policy,
                encoding=encoding,
                merge=merge,
                merge_options=merge_options,
                collect=collect,
                key=key,
                recursive=recursive,
                bound_loops=bound_loops,
                path_filter=path_filter,
                interpolate=interpolate,
                inject_env=inject_env,
                wrap=wrap,
                on_error=on_error,
                format_options=format_options,
                document_formats=(
                    None
                    if formats is None
                    else frozenset(
                        _formats.get(_format_name(name)).name for name in formats
                    )
                ),
                base_dir=_as_base_dir(base_dir),
            )
        )

    @staticmethod
    def _settle(values: dict) -> _Call:
        policy = values["policy"]
        if not isinstance(policy, Policy):
            raise TypeError(f"policy= takes a Policy, not {type(policy).__name__}")
        collect = values["collect"]
        if collect is not None and collect not in _COLLECT_MODES:
            raise ValueError(f"collect= takes one of {_COLLECT_MODES}, not {collect!r}")
        on_error = values["on_error"]
        if not (isinstance(on_error, bool) or callable(on_error)):
            raise TypeError("on_error= takes a bool or a callable (LoadEvent) -> bool")
        path_filter = values["path_filter"]
        if path_filter is not None and not callable(path_filter):
            raise TypeError("path_filter= takes a callable (path) -> bool, or None")
        format_options = dict(values["format_options"] or {})
        for name, opts in format_options.items():
            # An unknown format name or option raises here, not at first use.
            check_format_options(_formats.get(name), dict(opts))
        return _Call(
            policy=policy,
            encoding=check_encoding(values["encoding"] or "utf-8"),
            merge=resolve_merge(values["merge"]),
            merge_options=dict(values["merge_options"] or {}),
            collect=collect,
            key=values["key"] or _default_key,
            recursive=bool(values["recursive"]),
            bound_loops=bool(values["bound_loops"]),
            path_filter=path_filter,
            interpolate=values["interpolate"],
            interpolator=resolve_interpolator(values["interpolate"], policy),
            inject_env=bool(values["inject_env"]),
            wrap=values["wrap"],
            on_error=on_error,
            format_options={
                _formats.get(name).name: dict(opts)
                for name, opts in format_options.items()
            },
            document_formats=values["document_formats"],
            base_dir=values["base_dir"],
        )

    @property
    def policy(self) -> Policy:
        """The loader's default :class:`~yaconfiglib.Policy`."""
        return self._defaults.policy

    @property
    def base_dir(self) -> "_ty.Optional[Path]":
        """The directory relative sources resolve against, or `None`."""
        return self._defaults.base_dir

    def _call(self, overrides: _ty.Mapping[str, _ty.Any]) -> _Call:
        unknown = set(overrides) - _OVERRIDABLE
        if unknown:
            raise TypeError(
                f"unexpected keyword argument(s) {', '.join(sorted(unknown))}; "
                "format options go in format_options={'<format>': {...}}"
            )
        if "policy" in overrides and overrides["policy"] is None:
            # None means "the loader's", as it means "the default" when building.
            overrides = {k: v for k, v in overrides.items() if k != "policy"}
        if not overrides:
            return self._defaults
        values = {
            field.name: getattr(self._defaults, field.name)
            for field in dataclasses.fields(_Call)
        }
        values.update(overrides)
        return self._settle(values)

    def load(
        self,
        *sources: _ty.Any,
        format: _ty.Any = None,
        into: _ty.Any = None,
        default: _ty.Any = None,
        flatten: bool = False,
        transform: "_ty.Optional[_ty.Callable[[_ty.Any, str], _ty.Any]]" = None,
        **overrides: _ty.Any,
    ) -> _ty.Any:
        """Load *sources*, combine them in order, and return the result.

        Args:
            *sources: Paths, glob patterns, ``cmd://`` strings, pathapi paths,
                file objects, mappings, :class:`~yaconfiglib.sources.Source`
                objects, or nested lists of any of these; ``None`` and ``""``
                are skipped.
            format: Read every source as this format (a name or a
                :class:`~yaconfiglib.formats.Format` class), instead of by its
                file name.
            into: Build this type from the result (see
                :func:`~yaconfiglib.hydrate`).
            default: Returned when no source loads — nothing matched, or every
                one failed under ``on_error``. Never merged with a document.
            flatten: Flatten a mapping of mappings, or a list of lists, by one
                level; ``None`` members are skipped.
            transform: ``(document, name) -> document``, applied to each
                document before it is combined.
            **overrides: Any constructor setting, for this call only.

        Raises:
            TypeError: For no sources, or an unknown keyword.
        """
        if not sources:
            raise TypeError("load() needs at least one source")
        call = self._call(overrides)
        session = _Session(call)
        session.keep_text = into is not None
        documents = session.each(
            sources, session.context(), explicit=format, transform=transform
        )
        result, loaded = session.combine(
            documents,
            merge=call.merge,
            merge_options=call.merge_options,
            collect=call.collect,
            default=default,
        )
        if flatten and loaded:
            result = _flatten_result(result)
        return session.finish(result, into=into)

    def load_all(
        self,
        *sources: _ty.Any,
        format: _ty.Any = None,
        into: _ty.Any = None,
        transform: "_ty.Optional[_ty.Callable[[_ty.Any, str], _ty.Any]]" = None,
        **overrides: _ty.Any,
    ) -> _ty.Iterator[_ty.Any]:
        """Yield every document of every source, each finished on its own.

        For a directory of unrelated files rather than layers of one
        configuration. A multi-document source (a ``---`` YAML stream) yields
        each of its documents. Takes the same arguments as :meth:`load`,
        except those that only make sense when combining.
        """
        if not sources:
            raise TypeError("load_all() needs at least one source")
        call = self._call(overrides)
        session = _Session(call)
        session.keep_text = into is not None
        # Checked above, not on the first next(): the documents are a generator.
        return self._documents(session, sources, format, into, transform)

    @staticmethod
    def _documents(
        session: _Session,
        sources: tuple,
        format: _ty.Any,
        into: _ty.Any,
        transform: _ty.Any,
    ) -> _ty.Iterator[_ty.Any]:
        for _name, value in session.each(
            sources, session.context(), explicit=format, transform=transform
        ):
            yield session.finish(value, into=into)

    async def aload(
        self,
        *sources: _ty.Any,
        format: _ty.Any = None,
        into: _ty.Any = None,
        default: _ty.Any = None,
        flatten: bool = False,
        transform: "_ty.Optional[_ty.Callable[[_ty.Any, str], _ty.Any]]" = None,
        **overrides: _ty.Any,
    ) -> _ty.Any:
        """:meth:`load`, awaitable; a source may also be an async stream.

        An async source — an async iterable of `str`/`bytes` chunks, or an
        object with ``async def read()`` — is read on the event loop first,
        stopping past ``max_bytes``; the rest of the load runs in a worker
        thread.
        """
        from ._async import loader_aload

        if not sources:
            raise TypeError("aload() needs at least one source")
        return await loader_aload(
            self,
            sources,
            dict(
                format=format,
                into=into,
                default=default,
                flatten=flatten,
                transform=transform,
                **overrides,
            ),
        )

    def aload_all(
        self,
        *sources: _ty.Any,
        format: _ty.Any = None,
        into: _ty.Any = None,
        transform: "_ty.Optional[_ty.Callable[[_ty.Any, str], _ty.Any]]" = None,
        **overrides: _ty.Any,
    ) -> _ty.AsyncIterator[_ty.Any]:
        """:meth:`load_all` as an async iterator; sources as :meth:`aload`."""
        from ._async import loader_aload_all

        if not sources:
            raise TypeError("aload_all() needs at least one source")
        self._call(overrides)  # checked now, not on the first iteration
        return loader_aload_all(
            self,
            sources,
            dict(format=format, into=into, transform=transform, **overrides),
        )

    def __repr__(self) -> str:
        return f"Loader(base_dir={self.base_dir!r}, policy={self.policy!r})"
