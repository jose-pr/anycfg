"""Exception types yaconfiglib raises.

Every condition the library reports is a `ConfigError`. Each class also inherits
the builtin exception that describes the same condition, so ``except ValueError``
around a parse and ``except PermissionError`` around a refused read keep working.

A parser's own error never crosses the API: every format translates it into
`ParseError`, which carries the position but never the document's text. An
`OSError` from reading a file is let through as itself. So one clause covers every
load failure::

    try:
        config = yaconfiglib.load("app.yaml")
    except (yaconfiglib.ConfigError, OSError) as error:
        ...

A leaf module: stdlib imports only, so every other module can import it.
"""

from __future__ import annotations

import configparser as _configparser
import subprocess as _subprocess
import sys as _sys
import typing as _ty

__all__ = [
    "ConfigError",
    "ConfigValueError",
    "ConfigTypeError",
    "ParseError",
    "TemplateError",
    "UnsupportedFormatError",
    "MissingDependencyError",
    "PolicyError",
    "CommandsDisabledError",
    "TemplatesDisabledError",
    "ConfinementError",
    "BudgetExceededError",
    "CommandError",
    "CommandTimeoutError",
    "ErrorFrame",
]

#: The last stderr lines a failing command's message carries, and the character
#: cap on them. A tool prints its reason last, so the tail is the useful part;
#: the cap keeps a traceback readable when a command logs thousands of lines.
STDERR_TAIL_LINES = 20
STDERR_TAIL_CHARS = 2000


def _stderr_tail(stderr: object) -> str:
    """The tail of *stderr*, as one ``stderr: ...`` block, or ``""``.

    Only stderr: stdout is the command's payload, which is frequently the very
    secret the command was run to fetch.
    """
    if isinstance(stderr, (bytes, bytearray)):
        stderr = stderr.decode("utf-8", "replace")
    if not isinstance(stderr, str) or not stderr.strip():
        return ""
    lines = [line for line in stderr.splitlines() if line.strip()]
    tail = "\n".join(lines[-STDERR_TAIL_LINES:])
    if len(tail) > STDERR_TAIL_CHARS:
        tail = "..." + tail[-STDERR_TAIL_CHARS:]
    return f"\nstderr: {tail}"


class ErrorFrame(_ty.NamedTuple):
    """One step on the way from the load call to the source that failed.

    *kind* is ``"include"``, ``"render"``, ``"command"`` or ``"merge"``;
    *source* is the file, template or command text of that step, and *line* the
    line within it where known (the line an ``!include`` is written on).
    """

    kind: str
    source: str
    line: "_ty.Optional[int]" = None


class ConfigError(Exception):
    """Base class for every error yaconfiglib raises.

    An `OSError` from reading a source is not wrapped; catch
    ``(ConfigError, OSError)`` to cover every load failure.
    """

    def __str__(self) -> str:
        # super(), not BaseException: a subclass that also inherits a stdlib
        # error (CalledProcessError, TimeoutExpired) keeps that error's own
        # wording and only gains the suffix.
        return super().__str__() + _render_context(self)


class ConfigValueError(ConfigError, ValueError):
    """A configuration value or structure the library cannot accept."""


class ConfigTypeError(ConfigError, TypeError):
    """A configuration value of a type the requested operation cannot use."""


class ParseError(ConfigError, ValueError):
    """A document is not valid in its format.

    Every format raises this, translated from its parser's own error and raised
    ``from None``, so the parser library is not part of the API and none of the
    document's text reaches the message, the attributes or the exception chain.

    Attributes:
        path: The source's name, or `None` for in-memory content.
        lineno: 1-based line, or `None` when the parser does not say.
        colno: 1-based column, or `None`.
        msg: The problem, without document text.
    """

    def __init__(
        self,
        msg: str,
        *,
        path: "_ty.Optional[str]" = None,
        lineno: "_ty.Optional[int]" = None,
        colno: "_ty.Optional[int]" = None,
    ) -> None:
        self.msg = msg
        self.path = path
        self.lineno = lineno
        self.colno = colno
        super().__init__(msg)

    def __str__(self) -> str:
        where = [part for part in (self.path, self.lineno, self.colno) if part]
        position = ":".join(str(part) for part in where)
        text = f"{position}: {self.msg}" if position else self.msg
        return text + _render_context(self)

    def __reduce__(self):
        return (
            _rebuild_parse_error,
            (self.msg, self.path, self.lineno, self.colno),
            self.__dict__,
        )


def _rebuild_parse_error(msg, path, lineno, colno) -> ParseError:
    return ParseError(msg, path=path, lineno=lineno, colno=colno)


class TemplateError(ConfigError, ValueError):
    """Rendering a template file or interpolating a value failed.

    The engine's own exception is the ``__cause__``. An interpolation failure
    carries ``config_key``, the path of the value that failed.
    """


class UnsupportedFormatError(ConfigError, ValueError):
    """No registered format reads or writes this source.

    The message names the formats that are registered, and the extra to install
    when the format is known but its dependency is missing.
    """


class MissingDependencyError(UnsupportedFormatError, ImportError):
    """A format or feature is known, but the extra it needs is not installed.

    Also an `ImportError`. The message names the extra to install.
    """


class PolicyError(ConfigError, PermissionError):
    """The load's `Policy` refuses an operation.

    Also a `PermissionError`, and so an `OSError`: "refused to do this" is what
    that type means. Raised before anything is read or run.
    """


class CommandsDisabledError(PolicyError):
    """A command source was reached while the policy does not allow commands."""


class TemplatesDisabledError(PolicyError):
    """A template source was reached while the policy does not allow it.

    Raised for any template when ``allow_templates`` is off, and under a
    sandboxed policy for a template engine that cannot be sandboxed.
    """


class ConfinementError(PolicyError):
    """A file read resolved outside every root ``confine_to`` allows.

    The message names the resolved target and every root it was checked
    against, because a refusal nobody can diagnose gets switched off. For a glob
    match it names the pattern instead, since the match is a file name the
    document did not write.
    """


class BudgetExceededError(PolicyError):
    """A load went past one of the policy's bounds: includes, depth or bytes."""


class CommandError(ConfigError, _subprocess.CalledProcessError):
    """A command source exited non-zero.

    Also a `subprocess.CalledProcessError`, with its constructor and its
    ``returncode``/``output``/``stderr`` attributes. The message ends with the
    tail of stderr; stdout, the payload, stays on ``.output``.
    """

    def __str__(self) -> str:
        return (
            _subprocess.CalledProcessError.__str__(self)
            + _stderr_tail(self.stderr)
            + _render_context(self)
        )


class CommandTimeoutError(ConfigError, _subprocess.TimeoutExpired):
    """A command source did not finish within its timeout.

    Also a `subprocess.TimeoutExpired`, keeping its constructor, its
    ``timeout``/``cmd`` attributes and its wording. The process tree was killed.
    """

    def __str__(self) -> str:
        return (
            _subprocess.TimeoutExpired.__str__(self)
            + _stderr_tail(self.stderr)
            + _render_context(self)
        )


def _render_key(key: "_ty.Sequence[_ty.Union[str, int]]") -> str:
    """``("a", "b", 0, "c")`` as ``a.b[0].c``."""
    parts: "_ty.List[str]" = []
    for segment in key:
        if isinstance(segment, int) and not isinstance(segment, bool):
            parts.append(f"[{segment}]")
        elif parts:
            parts.append(f".{segment}")
        else:
            parts.append(str(segment))
    return "".join(parts)


def _render_context(error: BaseException) -> str:
    """The ``[in ...; included from ...]`` suffix for *error*, or ``""``.

    Recomputed from the record every time, so adding a frame re-renders rather
    than appending a second copy.
    """
    parts: "_ty.List[str]" = []
    source = getattr(error, "config_source", None)
    if source and not getattr(error, "_config_source_named", False):
        parts.append(f"in {source}")
    for frame in getattr(error, "config_frames", ()) or ():
        if frame.kind == "include":
            where = f"included from {frame.source}"
            parts.append(f"{where}, line {frame.line}" if frame.line else where)
        elif frame.kind == "render":
            # A template is its own document, and saying the path twice is
            # noise; only a different template is worth naming.
            parts.append(
                "rendered"
                if frame.source == source
                else f"rendered from {frame.source}"
            )
        elif frame.kind == "command":
            parts.append(f"output of command {frame.source!r}")
        elif frame.kind == "merge":
            parts.append(f"while merging {frame.source or 'in-memory data'}")
        else:
            parts.append(f"{frame.kind} {frame.source}")
    key = getattr(error, "config_key", None)
    model = getattr(error, "config_model", None)
    if key and model:
        parts.append(f"at {_render_key(key)} ({model})")
    elif key:
        parts.append(f"at {_render_key(key)}")
    elif model:
        parts.append(f"({model})")
    return f" [{'; '.join(parts)}]" if parts else ""


def _source_already_named(error: BaseException, source: str) -> bool:
    """Whether *error*'s **own** message already identifies *source*.

    A `ParseError`, an INI error or an `OSError` names the source itself, and a
    second copy in the suffix is noise. Decided once, when the source is first
    recorded, so every later re-render agrees. The suffix already written is
    removed first, so it is never mistaken for the error's own wording.
    """
    if isinstance(error, OSError) and error.filename == source:
        return True
    if isinstance(error, ParseError):
        return error.path == source
    try:
        text = str(error)
    except Exception:  # noqa: BLE001 - a broken __str__ must not hide the error
        return False
    written = _render_context(error)
    # Both spellings: a carrier that holds the suffix on its own line stores it
    # without the leading space.
    for spelling in (written, written.strip()):
        if spelling:
            text = text.replace(spelling, "")
    if source in text:
        return True
    # A Windows path in a message written with as_posix(), or vice versa.
    return source.replace("\\", "/") in text.replace("\\", "/")


def _set_attribute(error: BaseException, name: str, value: object) -> bool:
    """Set *name* on *error*, reporting whether it took.

    An exception with ``__slots__`` (or a read-only attribute) refuses; that is
    not a failure worth raising from inside an ``except`` block.
    """
    try:
        setattr(error, name, value)
    except (AttributeError, TypeError):
        return False
    return True


def _write_context(error: BaseException) -> None:
    """Render the record into whichever field *error*'s ``__str__`` reads.

    `ConfigError` renders in its own ``__str__``, so it needs no field. For
    every other type the original text is kept the first time and the field is
    rewritten as ``original + suffix``, which keeps repeated calls idempotent.
    """
    if isinstance(error, ConfigError):
        return
    suffix = _render_context(error)
    if not suffix:
        return

    # Carriers, most specific first. UnicodeDecodeError must reach `reason`
    # before the args[0] fallback: its args are a fixed 5-tuple the type
    # re-reads.
    field: _ty.Optional[str] = None
    if isinstance(error, UnicodeError) and isinstance(
        getattr(error, "reason", None), str
    ):
        field = "reason"
    elif isinstance(error, OSError) and isinstance(error.strerror, str):
        field = "strerror"
    elif isinstance(error, _configparser.Error) and isinstance(
        getattr(error, "message", None), str
    ):
        field = "message"

    if field is not None:
        original = error.__dict__.get("_config_original")
        if original is None:
            original = getattr(error, field) or ""
            error.__dict__["_config_original"] = original
        if _set_attribute(error, field, f"{original}{suffix}".lstrip()):
            return

    if _writes_args0(error):
        original = error.__dict__.get("_config_original")
        if original is None:
            original = error.args[0]
            error.__dict__["_config_original"] = original
        if _set_attribute(error, "args", (original + suffix,) + error.args[1:]):
            return

    _add_note(error, suffix.strip(" []"))


def _writes_args0(error: BaseException) -> bool:
    """Whether rewriting ``args[0]`` changes what ``str(error)`` shows."""
    if not error.args or not isinstance(error.args[0], str):
        return False
    jinja2 = _sys.modules.get("jinja2")
    template_error = getattr(getattr(jinja2, "exceptions", None), "TemplateError", None)
    if isinstance(template_error, type) and isinstance(error, template_error):
        return True
    return type(error).__str__ is BaseException.__str__


def _add_note(error: BaseException, note: str) -> None:
    """Attach *note* as a PEP 678 note, on any interpreter.

    ``add_note`` is 3.11+; below that the list is set directly, which pytest
    prints even though a plain traceback does not.
    """
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        notes = getattr(error, "__notes__", None)
        if notes and note in notes:
            return
        add_note(note)
        return
    notes = getattr(error, "__notes__", None)
    if notes is None:
        _set_attribute(error, "__notes__", [note])
    elif note not in notes:
        notes.append(note)


def _add_error_context(
    error: BaseException,
    *,
    source: "_ty.Optional[str]" = None,
    frame: "_ty.Optional[ErrorFrame]" = None,
    key: "_ty.Optional[_ty.Sequence[_ty.Union[str, int]]]" = None,
    model: "_ty.Optional[str]" = None,
) -> BaseException:
    """Record where *error* came from, and render it into *error*'s message.

    Returns the same object, with its type and identity untouched, plus
    `config_source`, `config_frames`, `config_key` and `config_model`.

    *model* overwrites any model already recorded, so as the error travels out
    of nested hydration the **outermost** model is the one named — the one the
    caller asked for, and the one the key path is relative to.

    Never raises: it runs inside ``except`` blocks, where an error of its own
    would replace the one being annotated.
    """
    if source and getattr(error, "config_source", None) is None:
        # Decided BEFORE config_source is set: setting it first changes the
        # rendering, and the old text would be mistaken for the error's own.
        named = _source_already_named(error, source)
        if _set_attribute(error, "config_source", source):
            _set_attribute(error, "_config_source_named", named)
    if frame is not None:
        frames = getattr(error, "config_frames", ()) or ()
        # Innermost first: an outer frame is reached later, so it appends.
        _set_attribute(error, "config_frames", tuple(frames) + (frame,))
    if key and getattr(error, "config_key", None) is None:
        _set_attribute(error, "config_key", tuple(key))
    if model is not None:
        _set_attribute(error, "config_model", model)
    _write_context(error)
    return error
