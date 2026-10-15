"""The trust policy of a load, and the context every format and source receives."""

from __future__ import annotations

import dataclasses
import typing as _ty

from ._confine import ConfineTo
from .exceptions import ConfigValueError

__all__ = ["Policy", "LoadContext"]


@dataclasses.dataclass(frozen=True)
class Policy:
    """What a load may do beyond parsing the documents it was given.

    The default is the **untrusted** posture: a document may include other
    files, but cannot run commands, cannot render templates, and interpolation
    runs sandboxed. ``Policy.trusted()`` turns commands and templates on and the
    sandbox off, for configuration you wrote yourself.

    A document can never change the policy: an include mapping cannot carry any
    of these fields, and every nested load (an include, a command's output)
    runs under the policy of the load that reached it.

    **Subclass it** to apply your own trust rules. The hooks :meth:`check_path`,
    :meth:`check_command` and :meth:`check_template` are asked after the fields
    have allowed something, so a subclass can only narrow them; returning a
    falsy value refuses. ``trusted()``, ``untrusted()`` and ``replace()`` keep
    the subclass::

        class LocalOnly(Policy):
            def check_path(self, path):
                return isinstance(path, pathapi.LocalPath)

        Loader(policy=LocalOnly())

    Attributes:
        allow_commands: Run command sources (``cmd://``, ``exec://``,
            ``sh://``, script files) instead of raising
            :class:`~yaconfiglib.exceptions.CommandsDisabledError`.
        allow_templates: Render template sources (``app.yaml.j2``) instead of
            raising :class:`~yaconfiglib.exceptions.TemplatesDisabledError`.
        allow_includes: Honour ``!include``/``!load``.
        sandbox: Render templates and interpolate values in a sandbox that
            blocks Python internals. A template engine that cannot be sandboxed
            is refused while this is on.
        strict: An undefined name in a template or interpolation raises.
        confine_to: Roots every local file read must fall inside: a sequence
            of absolute paths, one `os.pathsep`-separated string, a path,
            `True` for the loader's ``base_dir``, or `None`/`False` for none.
            With `None` the ``YACONFIGLIB_CONFINE_TO`` environment variable is
            read instead.
        max_includes: Most includes one load may resolve.
        max_depth: Deepest nesting of includes.
        max_bytes: Most bytes one load may read, all sources together.
        command_timeout: Seconds a command may run before it and its child
            processes are killed; `None` waits indefinitely.
    """

    allow_commands: bool = False
    allow_templates: bool = False
    allow_includes: bool = True
    sandbox: bool = True
    strict: bool = False
    confine_to: ConfineTo = None
    max_includes: int = 1000
    max_depth: int = 64
    max_bytes: int = 64 * 1024 * 1024
    command_timeout: "_ty.Optional[float]" = 60.0

    @classmethod
    def untrusted(cls, **changes: _ty.Any) -> "Policy":
        """The default posture, with *changes* applied."""
        return cls(**changes)

    @classmethod
    def trusted(cls, **changes: _ty.Any) -> "Policy":
        """Commands and templates allowed, no sandbox, with *changes* applied."""
        values: _ty.Dict[str, _ty.Any] = dict(
            allow_commands=True, allow_templates=True, sandbox=False
        )
        values.update(changes)
        return cls(**values)

    def replace(self, **changes: _ty.Any) -> "Policy":
        """A copy with *changes* applied."""
        return dataclasses.replace(self, **changes)

    @classmethod
    def using(cls, **settings: _ty.Any) -> "_ty.Type[Policy]":
        """This class with *settings* as class attributes: a mixin carrying its
        settings, as in ``AllowedSuffixes.using(suffixes={".yaml"})``.

        Raises:
            TypeError: For a setting the class does not declare.
        """
        from .policies import compose

        return compose(cls, name=cls.__name__, **settings)

    # -- hooks a subclass overrides; each may only narrow what the fields allow --

    def check_path(self, path: _ty.Any) -> bool:
        """Whether *path*, a pathapi ``Path``, may be read.

        Asked about every path before it is read: a top-level source, an
        include, a script file, a glob pattern before it is expanded and each
        match. A ``str`` source, top-level or from a document, is always a
        local path; any other kind of ``Path`` comes only from the caller. A
        refusal is a `ConfinementError`. Allows everything by default.
        """
        return True

    def check_command(self, command: str) -> bool:
        """Whether the command source *command* may run, once ``allow_commands``
        is on. *command* is the text a source names (``cmd://`` and friends) or
        a script file's path. A refusal is a `CommandsDisabledError`."""
        return True

    def check_template(self, name: str, engine: type) -> bool:
        """Whether the template file *name* may render with the tplx *engine*
        class, once ``allow_templates`` and the sandbox allow it. A refusal is a
        `TemplatesDisabledError`."""
        return True


class _LoadState:
    """Counters one top-level load shares across every nested load."""

    __slots__ = ("includes", "bytes", "deletes")

    def __init__(self) -> None:
        self.includes = 0
        self.bytes = 0
        self.deletes = False


@dataclasses.dataclass(frozen=True)
class LoadContext:
    """What a format or source knows about the load it is part of.

    Formats receive it as ``context=``; a format with includes resolves them
    through :meth:`include`, which applies the policy, confinement, the budget
    and cycle detection.

    Attributes:
        policy: The load's :class:`Policy`.
        encoding: The codec bytes are decoded with, and the default for every
            include that names none of its own.
        origin: The path the current document was read from, which relative
            includes resolve against; `None` for in-memory content.
        name: The current document's name, for messages.
        chain: Identities of the documents being loaded, outermost first.
        trail: Their names, for messages.
        keep_text: The load builds a type (``into=``): a format that resolves
            plain scalars by their text (YAML) keeps that text, as
            ``yaconfiglib._scalar.ScalarText``, for the type to decide.
    """

    policy: Policy = dataclasses.field(default_factory=Policy)
    encoding: str = "utf-8"
    origin: _ty.Any = None
    name: "_ty.Optional[str]" = None
    chain: "_ty.Tuple[str, ...]" = ()
    trail: "_ty.Tuple[str, ...]" = ()
    keep_text: bool = False
    _loader: _ty.Any = dataclasses.field(default=None, repr=False, compare=False)
    _state: _ty.Any = dataclasses.field(default=None, repr=False, compare=False)

    def include(self, source: _ty.Any, *more: _ty.Any, **options: _ty.Any) -> _ty.Any:
        """Load *source* (and *more*, merged in order) as part of this document.

        *options* are the document-settable include options (``encoding``,
        ``default``, ``flatten``, ``merge``, ``merge_options``, ``recursive``,
        ``format``); the caller filters anything else out.

        Raises:
            ConfigValueError: Without a loader, as for a direct format call.
        """
        if self._loader is None:
            raise ConfigValueError(
                "includes are only available while a yaconfiglib Loader is loading"
            )
        return self._loader.include(self, (source,) + more, options)

    def derive(self, **changes: _ty.Any) -> "LoadContext":
        """A copy with *changes* applied, sharing the load's counters."""
        return dataclasses.replace(self, **changes)
