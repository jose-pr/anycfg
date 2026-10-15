"""Templates: whole files rendered before parsing, and values interpolated after.

Both run on tplx (the ``templates`` extra), so every engine tplx knows works.

**File templates.** A source named ``<name>.<fmt>.<engine suffix>`` — for
example ``app.yaml.j2`` or ``app.yaml.liquid`` — is rendered first and the
result parsed as ``<fmt>``. The policy decides:

* ``allow_templates`` off (the default): every template is refused.
* ``sandbox`` on: only engines tplx can sandbox render (Jinja, Liquid, Mustache,
  Handlebars, Go, shell, envsubst), in tplx's sandbox: a template cannot reach
  Python internals, sees plain data, and can include only files under the
  confinement roots — none at all without them. Every other engine is refused
  before the file is read: mako and Cheetah run Python, ERB and EPP programs.
* ``sandbox`` off: any tplx engine renders; one that runs a program (ERB, EPP)
  also needs ``allow_commands``. Includes resolve under the confinement roots,
  or next to the template when there are none.

**Value interpolation** (``Loader(interpolate=...)``) renders the template
text inside the strings of the merged document, once, with the document itself
as the scope. A bare expression (``{{ expr }}``, ``${name}``) keeps its type. It
runs through an :class:`Interpolator`: :class:`TemplateInterpolator` uses a
tplx engine (Jinja by default), sandboxed when the policy is, and
:class:`VarsInterpolator` substitutes ``${a.b}`` references with no expressions
and no extra.
"""

from __future__ import annotations

import collections.abc as _abc
import logging
import os
import pathlib
import re
import threading
import types
import typing as _ty

from . import exceptions as _errors
from ._merge import _mapping_with, _sequence_with
from ._scalar import resolved
from .exceptions import (
    CommandsDisabledError,
    ConfigError,
    ConfigValueError,
    MissingDependencyError,
    TemplateError,
    TemplatesDisabledError,
)

if _ty.TYPE_CHECKING:
    from ._context import Policy

__all__ = [
    "Interpolator",
    "TemplateInterpolator",
    "VarsInterpolator",
    "interpolate_document",
]

logger = logging.getLogger(__name__)


def _tplx() -> _ty.Any:
    try:
        import tplx
    except ImportError:
        raise MissingDependencyError(
            'templates and interpolation need the "templates" extra: '
            'pip install "yaconfiglib[templates]"'
        ) from None
    return tplx


def _nowhere() -> _ty.Any:
    """A template root that holds no file, so a template can include nothing."""
    from pathapi.mempath import MemPath

    return MemPath("yaconfiglib-no-includes")


def _plain(value: _ty.Any, memo: "_ty.Optional[dict]" = None) -> _ty.Any:
    """A sandboxed engine's read-only views turned back into plain data.

    Walks containers an expression built too (``{'x': db}`` holds a view), each
    node once, so shared and self-referencing values stay finite.
    """
    if isinstance(value, (str, bytes, int, float, bool)) or value is None:
        return value
    memo = {} if memo is None else memo
    hit = memo.get(id(value))
    if hit is not None:
        return hit[1]
    if isinstance(value, _abc.Mapping):
        result: _ty.Any = {}
        memo[id(value)] = (value, result)
        for key, item in value.items():
            result[_plain(key, memo)] = _plain(item, memo)
        return result
    if isinstance(value, _abc.Sequence) and not isinstance(value, str):
        items: list = []
        memo[id(value)] = (value, items)
        items.extend(_plain(item, memo) for item in value)
        return tuple(items) if isinstance(value, tuple) else items
    return value


# ---------------------------------------------------------------------------
# Value interpolation


class Interpolator(_ty.Protocol):
    """Renders template text found inside loaded values.

    ``render`` is called with every string of the document and decides itself
    whether the text is a template: text it does not render comes back as it is.
    *scope* is the document's top-level names (plus ``env`` with
    ``inject_env``); reading a name that is itself a template renders it first.
    """

    def render(self, text: str, scope: _ty.Mapping[str, _ty.Any]) -> _ty.Any:
        """*text* rendered; plain text as it is; one expression keeps its type."""


def _engine_key(engine: type) -> str:
    """tplx's name for an engine class: ``JinjaTemplate`` is ``jinja``."""
    name = engine.__name__.lower()
    if name.endswith("template") and name != "template":
        name = name[: -len("template")]
    return name


class TemplateInterpolator:
    """Interpolation through a tplx engine; sandboxed by default.

    *engine* is a tplx engine's name or class (``"jinja"`` by default,
    ``"liquid"``, ``"go"``, ``"envsubst"``, ...). *sandbox* uses tplx's sandbox:
    an untrusted value cannot reach Python internals and sees plain data — a
    mapping or list as itself, any other non-plain value (a date) as its text —
    and an engine tplx cannot sandbox is refused. *strict* makes an undefined
    name raise instead of rendering empty. A template can include no file.
    Compiled templates are cached per instance.

    Raises:
        MissingDependencyError: Without tplx, or the engine's own extra.
        TemplatesDisabledError: Under *sandbox*, for an engine tplx cannot
            sandbox (mako, Cheetah, ERB, EPP).
        ValueError: For a name no tplx engine has.
    """

    def __init__(
        self,
        engine: "_ty.Union[str, type]" = "jinja",
        *,
        sandbox: bool = True,
        strict: bool = False,
    ) -> None:
        tplx = _tplx()
        self.engine = engine
        self.sandbox = sandbox
        self.strict = strict
        self._lookup = tplx.Loader(
            [_nowhere()],
            undefined="strict" if strict else "lenient",
            sandbox=sandbox,
        ).lookup()
        failure: "_ty.Optional[Exception]" = None
        try:
            # Fails now, not once per value, when the engine's library is missing.
            probe = self._lookup.compile("", engine=engine)
        except tplx.EngineUnavailableError as error:
            hint = (
                '; Jinja comes with the "templates" extra: '
                'pip install "yaconfiglib[templates]"'
                if _engine_name(engine) in ("jinja", "j2", "jinja2")
                else ""
            )
            failure = MissingDependencyError(f"{error}{hint}")
        except tplx.SandboxError:
            failure = TemplatesDisabledError(
                f"refusing to interpolate with {_engine_name(engine)}: the engine "
                "cannot be sandboxed, and the policy's sandbox is on"
            )
        if failure is not None:
            raise failure
        #: The tplx engine class *engine* names.
        self.engine_class: type = type(probe)

    def render(self, text: str, scope: _ty.Mapping[str, _ty.Any]) -> _ty.Any:
        """tplx's ``eval``: plain text back as it is, one expression with its
        type, anything else rendered to text. The engine is the only syntax
        check, and the template sees only the names it reads."""
        tplx = _tplx()
        names = _ReadNames(scope, self._lookup, text, self.engine_class)
        try:
            return _plain(self._lookup.eval(text, names, engine=self.engine_class))
        except tplx.TplxError as error:
            root = _root(error)
            if isinstance(root, ConfigError):
                # A name it read failed to render (a cycle, say): that error.
                raise root from None
            raise _template_error(error) from root


class _ReadNames(_abc.Mapping):
    """The names a template reads, looked up only once the engine asks.

    A sandboxed engine converts every name it is given, so handing it the whole
    document would cost the document per value; plain text never asks at all.
    An engine that cannot list its references gets every name.
    """

    __slots__ = ("_scope", "_lookup", "_text", "_engine", "_names")

    def __init__(
        self, scope: _ty.Mapping[str, _ty.Any], lookup: _ty.Any, text: str, engine: type
    ) -> None:
        self._scope = scope
        self._lookup = lookup
        self._text = text
        self._engine = engine
        self._names: "_ty.Optional[_ty.List[str]]" = None

    def _read(self) -> "_ty.List[str]":
        if self._names is None:
            try:
                wanted = self._lookup.compile(self._text, engine=self._engine)
                names = list(wanted.references())
            except NotImplementedError:
                names = list(self._scope)
            self._names = [name for name in names if name in self._scope]
        return self._names

    def __getitem__(self, key: str) -> _ty.Any:
        return self._scope[key]

    def __contains__(self, key: object) -> bool:
        return key in self._scope

    def __iter__(self) -> _ty.Iterator[str]:
        return iter(self._read())

    def __len__(self) -> int:
        return len(self._read())


def _engine_name(engine: _ty.Any) -> str:
    return _engine_key(engine) if isinstance(engine, type) else str(engine).lower()


#: ``$${`` (an escaped ``${``) or ``${reference}``.
_VAR = re.compile(r"\$\$\{|\$\{([^{}]*)\}")


class VarsInterpolator:
    """``${name}`` and ``${a.b.0}`` references; no expressions, no extra needed.

    A reference is a dotted path into the document: each step a mapping key or
    a list index. A value that is exactly one reference keeps the referenced
    value's type; inside other text it is written with `str` (``None`` as
    nothing). ``$${`` writes a literal ``${``. An undefined reference renders
    empty (``None`` alone), or raises `TemplateError` when *strict*. It runs no
    code and reads no attribute, so it needs no sandbox.
    """

    engine = "vars"

    def __init__(self, *, strict: bool = False) -> None:
        self.strict = strict

    def render(self, text: str, scope: _ty.Mapping[str, _ty.Any]) -> _ty.Any:
        if "${" not in text:
            return text
        whole = _VAR.fullmatch(text.rstrip("\r\n"))
        if whole is not None and whole.group(1) is not None:
            return self._lookup(whole.group(1), scope)

        def substitute(match: "re.Match[str]") -> str:
            if match.group(1) is None:
                return "${"
            value = self._lookup(match.group(1), scope)
            return "" if value is None else str(value)

        return _VAR.sub(substitute, text)

    def _lookup(self, path: str, scope: _ty.Mapping[str, _ty.Any]) -> _ty.Any:
        steps = path.strip().split(".")
        if not all(steps):
            raise TemplateError(f"malformed reference ${{{path}}}")
        value: _ty.Any = scope
        for step in steps:
            if isinstance(value, _abc.Mapping) and step in value:
                value = value[step]
            elif (
                isinstance(value, _abc.Sequence)
                and not isinstance(value, (str, bytes))
                and step.isdigit()
                and int(step) < len(value)
            ):
                value = value[int(step)]
            elif self.strict:
                raise TemplateError(f"undefined reference ${{{path.strip()}}}")
            else:
                return None
        return value


def _root(error: BaseException) -> BaseException:
    """The engine's own exception behind tplx's wrapper, or *error* itself."""
    return error.__cause__ or error


def _template_error(error: BaseException) -> TemplateError:
    root = _root(error)
    return TemplateError(f"{type(root).__name__}: {root}")


_INTERPOLATORS: dict = {}
_INTERPOLATORS_LOCK = threading.Lock()

#: What ``interpolate=`` takes: off, the default engine, an engine, or an object.
InterpolateChoice = _ty.Union[bool, str, type, Interpolator, None]


def default_interpolator(
    engine: "_ty.Union[str, type]", sandbox: bool, strict: bool
) -> _ty.Any:
    """The shared interpolator for an engine and a policy, so its cache stays warm.

    ``"vars"`` is :class:`VarsInterpolator`; anything else a tplx engine.
    """
    key = (engine, sandbox, strict)
    with _INTERPOLATORS_LOCK:
        found = _INTERPOLATORS.get(key)
        if found is None:
            if isinstance(engine, str) and engine.lower() == "vars":
                found = VarsInterpolator(strict=strict)
            else:
                found = TemplateInterpolator(engine, sandbox=sandbox, strict=strict)
            _INTERPOLATORS[key] = found
    return found


def resolve_interpolator(choice: InterpolateChoice, policy: "Policy") -> _ty.Any:
    """The interpolator ``interpolate=choice`` means under *policy*, or None for off.

    ``True`` is Jinja; a name or tplx engine class is that engine, sandboxed and
    strict as the policy says; an object with ``render`` is used as it is. An engine that runs a program (ERB, EPP) also needs
    ``allow_commands``.

    Raises:
        TypeError: For anything else.
    """
    if choice is None or choice is False:
        return None
    if choice is True:
        choice = "jinja"
    if isinstance(choice, (str, type)):
        if not (isinstance(choice, str) and choice.lower() == "vars"):
            # Before the engine is built: building one probes for its program.
            engine = _engine_class(choice)
            tplx = _tplx()
            if (
                engine is not None
                and issubclass(engine, tplx.SubprocessTemplate)
                and not policy.allow_commands
            ):
                raise CommandsDisabledError(
                    f"refusing to interpolate with {engine.__name__}, which runs a "
                    "program: the policy does not allow commands"
                )
        return default_interpolator(choice, policy.sandbox, policy.strict)
    if callable(getattr(choice, "render", None)):
        return choice
    raise TypeError(
        "interpolate= takes a bool, an engine name or class, or an object with "
        f"render(), not {choice!r}"
    )


def _engine_class(engine: "_ty.Union[str, type]") -> "_ty.Optional[type]":
    """The registered tplx engine class *engine* names, matched as tplx does.

    None when no engine has that name; building the interpolator then reports it.
    """
    if isinstance(engine, type):
        return engine
    word = engine.lower().lstrip(".")
    tplx = _tplx()
    for cls in tplx.registered_engines():
        names = [_engine_key(cls)] + [
            suffix.lstrip(".") for suffix in tplx.template_extensions(cls) or ()
        ]
        if word in names:
            return cls
    return None


class _Scope(_abc.Mapping):
    """A document's top-level names, each rendered the first time a template reads it.

    So a chain (``logs: "{{ base }}/logs"``, ``err: "{{ logs }}/err"``) resolves
    whatever order it is written in, with no pass to find the references first.
    A key read while it is being rendered is a cycle: its current value is read,
    or, under *strict*, `ConfigValueError` is raised — except a key reading into
    itself (``db.url: "{{ db.host }}"``), which sees its loaded value. *extra*
    names win over keys of the same name. Values are as the format read them: a
    scalar kept as text for a typed load is still a number or a date here.
    """

    def __init__(
        self,
        document: _ty.Mapping,
        extra: _ty.Mapping[str, _ty.Any],
        walker: "_Pass",
        strict: bool,
        on_error: "_ty.Optional[_ty.Callable[[BaseException, tuple], bool]]",
    ) -> None:
        self.document = document
        self.extra = extra
        self.walker = walker
        self.strict = strict
        self.on_error = on_error
        self.rendered: dict = {}
        self.resolving: list = []

    def resolve(self, key: _ty.Any) -> _ty.Any:
        """*key*'s value rendered, rendering it now if no template read it yet."""
        if key in self.rendered:
            return self.rendered[key]
        if key in self.resolving:
            if self.strict and key != self.resolving[-1]:
                chain = self.resolving[self.resolving.index(key) :] + [key]
                error = ConfigValueError(
                    "interpolation reference cycle: "
                    + " -> ".join(str(item) for item in chain)
                )
                _errors._add_error_context(error, key=(key,))
                if self.on_error is None or not self.on_error(error, (key,)):
                    raise error
            return self.document[key]
        self.resolving.append(key)
        try:
            result = self.walker.value(self.document[key], self, (key,))
        finally:
            self.resolving.pop()
        self.rendered[key] = result
        return result

    def __getitem__(self, key: str) -> _ty.Any:
        if key in self.extra:
            return self.extra[key]
        if not isinstance(key, str) or key not in self.document:
            raise KeyError(key)
        return resolved(self.resolve(key))

    def __contains__(self, key: object) -> bool:
        return key in self.extra or (isinstance(key, str) and key in self.document)

    def __iter__(self) -> _ty.Iterator[str]:
        for key in self.document:
            if isinstance(key, str) and key not in self.extra:
                yield key
        yield from self.extra

    def __len__(self) -> int:
        return sum(1 for _ in self)


class _Pass:
    """One interpolation pass over a document: copy-on-write, each node once."""

    def __init__(
        self,
        interpolator: Interpolator,
        on_error: "_ty.Optional[_ty.Callable[[BaseException, tuple], bool]]",
    ) -> None:
        self.interpolator = interpolator
        self.on_error = on_error
        # id(original) -> (original, result): keeps the original alive so its
        # id() cannot be reused within the pass, and keeps aliases aliased.
        self.memo: dict = {}

    def value(self, data: _ty.Any, scope: _ty.Mapping, keypath: tuple) -> _ty.Any:
        kind = type(data)
        if kind is str:
            return self.text(data, scope, keypath)
        if kind in (int, float, bool) or data is None:
            return data
        hit = self.memo.get(id(data))
        if hit is not None:
            return hit[1]
        if kind is dict or isinstance(data, _abc.Mapping):
            self.memo[id(data)] = (data, data)
            items = []
            changed = False
            for key, item in data.items():
                new_key = self.value(key, scope, keypath + (key,))
                new_item = self.value(item, scope, keypath + (key,))
                changed = changed or new_key is not key or new_item is not item
                items.append((new_key, new_item))
            result = _mapping_with(data, dict(items)) if changed else data
        elif kind in (list, tuple) or isinstance(data, (_abc.Sequence, _abc.Set)):
            if isinstance(data, (str, bytes, bytearray)):
                return data
            self.memo[id(data)] = (data, data)
            items = [
                self.value(item, scope, keypath + (index,))
                for index, item in enumerate(data)
            ]
            changed = any(new is not old for new, old in zip(items, data))
            result = _sequence_with(data, items) if changed else data
        else:
            return data
        self.memo[id(data)] = (data, result)
        return result

    def text(self, data: str, scope: _ty.Mapping, keypath: tuple) -> _ty.Any:
        try:
            try:
                result = self.interpolator.render(data, scope)
            except ConfigError:
                raise
            except (
                Exception
            ) as cause:  # noqa: BLE001 - an expression can raise anything
                # `{{ 1/0 }}` raises ZeroDivisionError; the engine's types are
                # not part of this API, so every failure is a TemplateError.
                raise TemplateError(f"{type(cause).__name__}: {cause}") from cause
        except MissingDependencyError:
            raise
        except ConfigError as error:
            if getattr(error, "config_key", None) is None and keypath:
                _errors._add_error_context(error, key=keypath)
            if self.on_error is not None and self.on_error(error, keypath):
                return data
            raise
        if result is data or (type(result) is str and result == data):
            return data  # not a template, to this interpolator
        # The template, never the result: the result may be a secret.
        logger.debug("interpolated %s from %r", _errors._render_key(keypath), data)
        return result


def interpolate_document(
    document: _ty.Any,
    interpolator: Interpolator,
    *,
    extra: "_ty.Optional[_ty.Mapping[str, _ty.Any]]" = None,
    strict: bool = False,
    on_error: "_ty.Optional[_ty.Callable[[BaseException, tuple], bool]]" = None,
) -> _ty.Any:
    """Render every template in *document* once, against the document itself.

    The interpolator's ``render`` gets every string and is the only syntax
    check. A top-level key renders the first time a template reads it, so a
    chain (``logs: "{{ base }}/logs"``, ``err: "{{ logs }}/err"``) resolves in
    one pass whatever order it is written in. A rendered value is never
    rendered again. A reference to a key still being rendered sees its current
    value; a cycle raises when *strict* is set. *extra* names (``env``) win
    over keys of the same name.

    Copy-on-write: *document* is never modified, containers keep their types,
    and a document with no template at all is returned as it is.

    *on_error(error, keypath)* is offered each failing value; returning true
    keeps that value's template text and rendering continues.

    Raises:
        ConfigValueError: When two keys render to the same key, or for a
            reference cycle under *strict*.
    """
    extra = dict(extra or {})
    walker = _Pass(interpolator, on_error)
    if not isinstance(document, _abc.Mapping):
        return walker.value(document, extra, ())

    scope = _Scope(document, extra, walker, strict, on_error)
    rendered: dict = {}
    for key in list(document):
        if isinstance(key, str):
            rendered[key] = scope.resolve(key)
        else:
            rendered[key] = walker.value(document[key], scope, (key,))

    # Keys that are templates render last, so they see the rendered values.
    items = []
    seen_keys: dict = {}
    for key in document:
        new_key = walker.value(key, scope, (key,))
        if new_key in seen_keys:
            # Names the templates, never what they rendered to.
            error = ConfigValueError(
                f"interpolated key {key!r} renders to the same key as "
                f"{seen_keys[new_key]!r}"
            )
            _errors._add_error_context(error, key=(key,))
            if on_error is not None and on_error(error, (key,)):
                # Skipped: the entry stays under its template text.
                new_key = key
            else:
                raise error
        seen_keys[new_key] = key
        items.append((new_key, rendered[key]))
    changed = any(
        new_key is not key or value is not document[key]
        for (new_key, value), key in zip(items, document)
    )
    return (
        _mapping_with(document, dict(items))
        if changed or len(items) != len(document)
        else document
    )


# ---------------------------------------------------------------------------
# File templates


def template_suffix(name: str) -> "_ty.Optional[str]":
    """The template suffix *name* ends with, if a tplx engine claims it.

    A catch-all engine (tplx's byte copy) never counts. Without tplx installed
    nothing is a template.
    """
    try:
        import tplx
    except ImportError:
        return None
    lowered = name.lower()
    for engine in tplx.registered_engines():
        for suffix in tplx.template_extensions(engine) or ():
            if lowered.endswith(suffix):
                return suffix
    return None


def _engine_for(suffix: str) -> _ty.Any:
    tplx = _tplx()
    for engine in tplx.registered_engines():
        if suffix in (tplx.template_extensions(engine) or ()):
            return engine
    raise TemplatesDisabledError(f"no template engine renders {suffix} files")


def check_template(name: str, suffix: str, policy: "Policy") -> None:
    """Refuse to render the template *name* when *policy* does not allow it.

    Called before the file is read, and again by :func:`render_file`.
    """
    if not policy.allow_templates:
        raise TemplatesDisabledError(
            f"refusing to render template {name}: the policy does not allow "
            "templates (Policy(allow_templates=True))"
        )
    tplx = _tplx()
    engine = _engine_for(suffix)
    if policy.sandbox and not getattr(engine, "SANDBOXED", False):
        raise TemplatesDisabledError(
            f"refusing to render {name}: the {engine.__name__} engine cannot be "
            "sandboxed, and the policy's sandbox is on"
        )
    if issubclass(engine, tplx.SubprocessTemplate) and not policy.allow_commands:
        raise CommandsDisabledError(
            f"refusing to render {name} with {engine.__name__}, which runs a "
            "program: the policy does not allow commands"
        )
    if not policy.check_template(name, engine):
        raise TemplatesDisabledError(
            f"refusing to render {name} with {engine.__name__}: the policy refused it"
        )


def render_file(
    text: str,
    *,
    path: _ty.Any,
    suffix: str,
    policy: "Policy",
    roots: "_ty.Optional[_ty.Sequence[str]]",
    inject_env: bool,
) -> str:
    """Render the template *text* read from *path*, under *policy*.

    Raises:
        TemplatesDisabledError: When the policy does not allow templates, or
            under a sandbox for an engine tplx cannot sandbox.
        CommandsDisabledError: For an engine that runs a program, when the
            policy does not allow commands.
        MissingDependencyError: Without the ``templates`` extra, or the
            engine's own tplx extra.
        TemplateError: When rendering fails.
    """
    name = str(path)
    check_template(name, suffix, policy)
    tplx = _tplx()
    posix = pathlib.PurePosixPath(name.replace("\\", "/"))
    # `pathname` is a path to a trusted template and its text to a sandboxed
    # one (tplx shows a sandbox only plain data), so the file name is given too.
    data: dict = {"pathname": posix, "filename": posix.name}
    if inject_env:
        data["env"] = types.MappingProxyType(dict(os.environ))
    # Includes search the confinement roots. Without them, a sandboxed
    # template includes nothing and a trusted one looks next to itself.
    if roots:
        search: list = list(roots)
    elif policy.sandbox:
        search = [_nowhere()]
    else:
        search = [os.path.dirname(os.path.abspath(name)) or "."]
    loader = tplx.Loader(
        search,
        undefined="strict" if policy.strict else "lenient",
        sandbox=policy.sandbox,
    )
    engine = _engine_for(suffix)
    try:
        template = engine(text, lookup=loader.lookup(), origin=name)
        rendered = template.render(data)
    except tplx.EngineUnavailableError as cause:
        raise MissingDependencyError(str(cause)) from None
    except Exception as cause:  # noqa: BLE001 - the engine's types are not API
        raise _template_error(cause) from _root(cause)
    if isinstance(rendered, bytes):
        rendered = rendered.decode("utf-8")
    return rendered
