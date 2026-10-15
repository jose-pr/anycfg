"""Ready-made `Policy` mixins, combined by inheritance or with :func:`compose`.

Each mixin narrows one hook and then asks the next class in line
(``super()``), so any number of them stack: a path must pass every
``check_path``, a command every ``check_command``. Their settings are class
attributes, set on the combined class::

    from yaconfiglib import Loader, policies

    class Production(policies.LocalPathsOnly, policies.AllowedSuffixes,
                     policies.AllowedCommands):
        suffixes = {".yaml", ".json"}
        commands = {"vault read -field=password secret/db"}

    loader = Loader(policy=Production(allow_commands=True))

or, without a class statement::

    Production = policies.compose(
        policies.LocalPathsOnly, policies.AllowedSuffixes, suffixes={".yaml"}
    )

A mixin only ever narrows: the policy's fields (``allow_commands``,
``allow_templates``, ``sandbox``, ``confine_to``) still decide first.
"""

from __future__ import annotations

import typing as _ty

from pathapi import LocalPath

from ._context import Policy

__all__ = [
    "LocalPathsOnly",
    "AllowedSuffixes",
    "AllowedCommands",
    "AllowedEngines",
    "compose",
]


def _engine_name(engine: type) -> str:
    name = engine.__name__.lower()
    return name[: -len("template")] if name.endswith("template") else name


class LocalPathsOnly(Policy):
    """Read only local and in-memory paths: a remote pathapi ``Path`` is refused.

    A document can only ever name local paths, so this vets the paths the
    caller passes.
    """

    def check_path(self, path: _ty.Any) -> bool:
        from pathapi.mempath import MemPath

        return isinstance(path, (LocalPath, MemPath)) and super().check_path(path)


class AllowedSuffixes(Policy):
    """Read only files whose name ends with one of :attr:`suffixes`.

    Compared case-insensitively; a glob pattern is judged by its own last
    component (``*.yaml``) and each match by its name.
    """

    #: The suffixes allowed, with their dot (``{".yaml", ".json"}``).
    suffixes: _ty.ClassVar[_ty.AbstractSet[str]] = frozenset()

    def check_path(self, path: _ty.Any) -> bool:
        name = str(getattr(path, "name", path)).lower()
        allowed = any(name.endswith(suffix.lower()) for suffix in self.suffixes)
        return allowed and super().check_path(path)


class AllowedCommands(Policy):
    """Run only the commands listed in :attr:`commands`, compared as written.

    ``allow_commands`` must still be on; this narrows it to an allowlist. A
    script file is named by its path.
    """

    #: The command texts allowed (what follows ``cmd://``, or a script's path).
    commands: _ty.ClassVar[_ty.AbstractSet[str]] = frozenset()

    def check_command(self, command: str) -> bool:
        return command in self.commands and super().check_command(command)


class AllowedEngines(Policy):
    """Render template files only with the tplx engines named in :attr:`engines`.

    ``allow_templates`` must still be on, and the sandbox still refuses an
    engine it cannot hold.
    """

    #: Engine names as tplx gives them (``"jinja"``, ``"liquid"``, ``"go"``).
    engines: _ty.ClassVar[_ty.AbstractSet[str]] = frozenset({"jinja"})

    def check_template(self, name: str, engine: type) -> bool:
        return _engine_name(engine) in self.engines and super().check_template(
            name, engine
        )


def compose(
    *mixins: "_ty.Type[Policy]", name: str = "ComposedPolicy", **settings: _ty.Any
) -> "_ty.Type[Policy]":
    """A `Policy` subclass combining *mixins*, with *settings* as its class attributes.

    ``compose(LocalPathsOnly, AllowedSuffixes, suffixes={".yaml"})``. Each
    setting must be one a mixin declares.

    Raises:
        TypeError: For a mixin that is not a `Policy` subclass, or an unknown
            setting.
    """
    for mixin in mixins:
        if not (isinstance(mixin, type) and issubclass(mixin, Policy)):
            raise TypeError(f"compose() takes Policy subclasses, not {mixin!r}")
    known = {
        attribute
        for mixin in mixins
        for klass in mixin.__mro__
        for attribute in getattr(klass, "__annotations__", {})
        if klass is not Policy and klass is not object
    }
    unknown = sorted(set(settings) - known)
    if unknown:
        raise TypeError(
            f"compose() got unknown setting(s) {', '.join(unknown)}; the mixins "
            f"declare: {', '.join(sorted(known)) or 'none'}"
        )
    return type(name, tuple(mixins) or (Policy,), dict(settings))
