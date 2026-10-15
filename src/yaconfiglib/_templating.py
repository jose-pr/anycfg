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

**Value interpolation** (``Loader(interpolate=True)``) renders ``{{ ... }}``
inside the strings of the merged document, once, with the document itself as
the scope. A bare ``{{ expr }}`` keeps its type. It runs through an
:class:`Interpolator`; the built-in :class:`TemplateInterpolator` uses a tplx
engine (Jinja by default), sandboxed when the policy is.
"""

from __future__ import annotations

import collections.abc as _abc
import logging
import os
import pathlib
import threading
import types
import typing as _ty

from . import exceptions as _errors
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

__all__ = ["Interpolator", "TemplateInterpolator", "interpolate_document"]

logger = logging.getLogger(__name__)

_MARKERS = ("{{", "{%", "{#")


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


def _plain(value: _ty.Any) -> _ty.Any:
    """A sandboxed engine's read-only views turned back into plain data."""
    if isinstance(value, (str, bytes, int, float, bool)) or value is None:
        return value
    if isinstance(value, _abc.Mapping) and not isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, _abc.Sequence) and not isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Value interpolation


class Interpolator(_ty.Protocol):
    """Renders template text found inside loaded values."""

    def references(self, text: str) -> _ty.FrozenSet[str]:
        """The top-level names *text* reads; empty for text that does not parse."""

    def render(self, text: str, scope: _ty.Mapping[str, _ty.Any]) -> _ty.Any:
        """Render *text*; a bare ``{{ expr }}`` returns the expression's own value."""


class TemplateInterpolator:
    """Interpolation through a tplx engine; sandboxed by default.

    *engine* is any tplx engine with expressions (``"jinja"`` by default,
    ``"liquid"``, ...). *sandbox* uses tplx's sandbox: an untrusted value
    cannot reach Python internals and sees plain data — a mapping or list as
    itself, any other non-plain value (a date) as its text. *strict* makes an
    undefined name raise instead of rendering empty. A template can include no
    file. Compiled templates are cached per instance.
    """

    def __init__(
        self, engine: str = "jinja", *, sandbox: bool = True, strict: bool = False
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
        try:
            # Fails now, not once per value, when the engine's library is missing.
            self._lookup.compile("", engine=engine)
        except tplx.EngineUnavailableError as error:
            raise MissingDependencyError(
                f'{error}; Jinja comes with the "templates" extra: '
                'pip install "yaconfiglib[templates]"'
            ) from None

    def references(self, text: str) -> _ty.FrozenSet[str]:
        try:
            return frozenset(
                self._lookup.compile(text, engine=self.engine).references()
            )
        except Exception:  # noqa: BLE001 - rendering reports a bad template better
            return frozenset()

    def render(self, text: str, scope: _ty.Mapping[str, _ty.Any]) -> _ty.Any:
        tplx = _tplx()
        try:
            template = self._lookup.compile(text, engine=self.engine)
            # Only the names the template reads: a sandboxed engine converts all
            # the data it is given on every render, so handing it the whole
            # document would cost the whole document per value.
            try:
                names = template.references()
            except NotImplementedError:
                names = scope
            values = {name: scope[name] for name in names if name in scope}
            # Only a trailing newline may surround a bare expression: a YAML `|`
            # block adds one, while spaces inside a quoted scalar are text.
            stripped = text.rstrip("\r\n")
            if stripped.startswith("{{") and stripped.endswith("}}"):
                inner = stripped[2:-2].strip()
                if "{{" not in inner and "}}" not in inner:
                    return _plain(
                        self._lookup.evaluate(inner, values, engine=self.engine)
                    )
            return template.render(values)
        except tplx.TplxError as error:
            raise _template_error(error) from _root(error)


def _root(error: BaseException) -> BaseException:
    """The engine's own exception behind tplx's wrapper, or *error* itself."""
    return error.__cause__ or error


def _template_error(error: BaseException) -> TemplateError:
    root = _root(error)
    return TemplateError(f"{type(root).__name__}: {root}")


_INTERPOLATORS: dict = {}
_INTERPOLATORS_LOCK = threading.Lock()


def default_interpolator(sandbox: bool, strict: bool) -> TemplateInterpolator:
    """The shared Jinja interpolator for ``(sandbox, strict)``, so its cache stays warm."""
    key = (sandbox, strict)
    with _INTERPOLATORS_LOCK:
        found = _INTERPOLATORS.get(key)
        if found is None:
            found = _INTERPOLATORS[key] = TemplateInterpolator(
                sandbox=sandbox, strict=strict
            )
    return found


def _scope(document: _ty.Mapping, extra: _ty.Mapping[str, _ty.Any]) -> dict:
    """Template names from *document*: string keys only, *extra* winning.

    Values as the format read them: a scalar kept as text for a typed load is
    still a number or a date to a template.
    """
    scope = {
        key: resolved(value) for key, value in document.items() if isinstance(key, str)
    }
    scope.update(extra)
    return scope


def _rebuild_mapping(original: _ty.Mapping, items: list) -> _ty.Mapping:
    if type(original) is dict:
        return dict(items)
    if isinstance(original, dict):
        import copy

        result = copy.copy(original)
        result.clear()
        result.update(items)
        return result
    try:
        return type(original)(items)
    except TypeError:
        return dict(items)


def _rebuild_sequence(original: _ty.Any, items: list) -> _ty.Any:
    if type(original) is list:
        return items
    try:
        return type(original)(items)
    except TypeError:
        return items


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
            result = _rebuild_mapping(data, items) if changed else data
        elif kind in (list, tuple) or isinstance(data, (_abc.Sequence, _abc.Set)):
            if isinstance(data, (str, bytes, bytearray)):
                return data
            self.memo[id(data)] = (data, data)
            items = [
                self.value(item, scope, keypath + (index,))
                for index, item in enumerate(data)
            ]
            changed = any(new is not old for new, old in zip(items, data))
            result = _rebuild_sequence(data, items) if changed else data
        else:
            return data
        self.memo[id(data)] = (data, result)
        return result

    def text(self, data: str, scope: _ty.Mapping, keypath: tuple) -> _ty.Any:
        if not any(marker in data for marker in _MARKERS):
            return data
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
        # The template, never the result: the result may be a secret.
        logger.debug("interpolated %s from %r", _errors._render_key(keypath), data)
        return result


def _has_template(value: _ty.Any, seen: set) -> bool:
    """Whether any string in *value* holds a template marker; each node once."""
    kind = type(value)
    if kind is str:
        return any(marker in value for marker in _MARKERS)
    if kind in (int, float, bool) or value is None:
        return False
    if id(value) in seen:
        return False
    seen.add(id(value))
    if kind is dict or isinstance(value, _abc.Mapping):
        return any(
            _has_template(key, seen) or _has_template(item, seen)
            for key, item in value.items()
        )
    if isinstance(value, (_abc.Sequence, _abc.Set)) and not isinstance(
        value, (bytes, bytearray)
    ):
        return any(_has_template(item, seen) for item in value)
    return False


def _references(interpolator: Interpolator, value: _ty.Any, cache: dict) -> set:
    """Names every template inside *value* refers to; containers once each."""
    kind = type(value)
    if kind is str:
        if not any(marker in value for marker in _MARKERS):
            return set()
        return set(interpolator.references(value))
    if kind in (int, float, bool) or value is None:
        return set()
    if id(value) in cache:
        return cache[id(value)]
    cache[id(value)] = set()  # a self-referencing container terminates here
    names: set = set()
    if kind is dict or isinstance(value, _abc.Mapping):
        for key, item in value.items():
            names |= _references(interpolator, key, cache)
            names |= _references(interpolator, item, cache)
    elif isinstance(value, (_abc.Sequence, _abc.Set)) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for item in value:
            names |= _references(interpolator, item, cache)
    cache[id(value)] = names
    return names


def interpolate_document(
    document: _ty.Any,
    interpolator: Interpolator,
    *,
    extra: "_ty.Optional[_ty.Mapping[str, _ty.Any]]" = None,
    strict: bool = False,
    on_error: "_ty.Optional[_ty.Callable[[BaseException, tuple], bool]]" = None,
) -> _ty.Any:
    """Render every template in *document* once, against the document itself.

    Each top-level key renders after the keys it refers to, so a chain
    (``logs: "{{ base }}/logs"``, ``err: "{{ logs }}/err"``) resolves in one
    pass whatever order it is written in. A rendered value is never rendered
    again. A reference to a key still being resolved sees its current value; a
    cycle raises when *strict* is set. *extra* names (``env``) win over keys
    of the same name.

    Copy-on-write: *document* is never modified, containers keep their types,
    and a document with no template at all is returned as it is.

    *on_error(error, keypath)* is offered each failing value; returning true
    keeps that value's template text and rendering continues.

    Raises:
        ConfigValueError: When two keys render to the same key, or for a
            reference cycle under *strict*.
    """
    extra = dict(extra or {})
    if not _has_template(document, set()):
        return document
    walker = _Pass(interpolator, on_error)
    if not isinstance(document, _abc.Mapping):
        return walker.value(document, extra, ())

    scope = _scope(document, extra)
    rendered: dict = {}
    resolving: list = []
    references: dict = {}

    def resolve(key: _ty.Any) -> None:
        if key in rendered:
            return
        if key in resolving:
            if strict:
                chain = resolving[resolving.index(key) :] + [key]
                error = ConfigValueError(
                    "interpolation reference cycle: "
                    + " -> ".join(str(item) for item in chain)
                )
                _errors._add_error_context(error, key=(key,))
                if on_error is not None and on_error(error, (key,)):
                    return
                raise error
            return  # lenient: the reference sees the key's current value
        resolving.append(key)
        try:
            for name in _references(interpolator, document[key], references):
                # A value referring into its own key (`db.url: "{{ db.host }}"`)
                # sees that key's current value; only another key can cycle.
                if name != key and name in document and name not in extra:
                    resolve(name)
        finally:
            resolving.pop()
        result = walker.value(document[key], scope, (key,))
        rendered[key] = result
        if isinstance(key, str) and key not in extra:
            scope[key] = resolved(result)

    for key in list(document):
        resolve(key)

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
        _rebuild_mapping(document, items)
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
