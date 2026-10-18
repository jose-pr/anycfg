"""Confinement: whether a file read falls inside the roots a policy allows.

The comparison is lexical and I/O-free. A path is made absolute, ``..`` is
removed, case is folded where the platform folds it and a Windows
extended-length prefix is stripped; containment is `os.path.commonpath`, never a
string-prefix test, which would accept ``/srv/confidential`` under the root
``/srv/conf``. Symlinks are deliberately not resolved: a link inside a root was
put there by whoever administers the root, and the threat this closes is a
hostile document, not a hostile root.
"""

from __future__ import annotations

import os
import typing as _ty

from pathapi import LocalPath, Path
from pathapi.mempath import MemPath

from .exceptions import ConfigTypeError

#: Where `Policy(confine_to=None)` looks for roots instead. Read only when the
#: setting is None: a variable that could widen an explicit in-code allowlist
#: would be an escalation for anyone able to set the environment.
CONFINE_TO_ENV = "ANYCFG_CONFINE_TO"

#: Windows extended-length prefixes. `os.path.commonpath` compares components
#: literally, so `\\?\C:\srv\conf\a.yaml` against the root `C:\srv\conf` would
#: raise `ValueError` although both name the same tree.
_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"
_EXTENDED_PREFIX = "\\\\?\\"

ConfineTo = _ty.Union[None, bool, str, os.PathLike, _ty.Sequence[_ty.Any]]


def confinement_key(path: object) -> str:
    r"""*path* as the string the containment test compares.

    Absolute, ``normcase``-folded and with any ``\\?\`` prefix stripped. Also
    the identity of a local file for include-cycle detection, so a document
    cannot dodge the cycle check by spelling itself ``../conf/self.yaml``.
    """
    text = str(path)
    if text.startswith(_EXTENDED_UNC_PREFIX):
        text = "\\\\" + text[len(_EXTENDED_UNC_PREFIX) :]
    elif text.startswith(_EXTENDED_PREFIX):
        text = text[len(_EXTENDED_PREFIX) :]
    return os.path.normcase(os.path.abspath(text))


def is_anchored_root(text: str) -> bool:
    r"""Whether *text* names a place rather than a place relative to one.

    Not `os.path.isabs` alone: that is `False` for a bare UNC share
    (``\\host\share``) before Python 3.13, and a whole share is a valid root.
    """
    if os.path.isabs(text):
        return True
    drive = os.path.splitdrive(text)[0]
    return drive.startswith("\\\\") or drive.startswith("//")


def root_key(text: str) -> str:
    r"""`confinement_key` for a root: a bare UNC share gets a trailing separator.

    `os.path.commonpath` treats ``\\host\share`` as relative, so without the
    separator every path inside the share would read as "not contained".
    """
    key = confinement_key(text)
    if not os.path.splitdrive(key)[1]:
        key += os.sep
    return key


def within_roots(path: object, roots: "_ty.Sequence[str]") -> bool:
    """True when *path* resolves inside any of *roots*; an empty *roots* allows nothing."""
    target = confinement_key(path)
    for root in roots:
        try:
            if os.path.commonpath([target, root]) == root:
                return True
        except ValueError:
            # A different drive, or a drive/UNC mix: no common root at all.
            continue
    return False


def kind(path: object) -> str:
    """How confinement treats *path*: ``"check"``, ``"exempt"`` or ``"foreign"``.

    By type, never by reading the text. A `MemPath` has no location on disk and
    is exempt; a local path is checked; any other pathapi path (a remote one) is
    inside no local root, so confinement refuses it.
    """
    if isinstance(path, MemPath):
        return "exempt"
    if isinstance(path, LocalPath):
        return "check"
    if isinstance(path, Path):
        # Before the os.PathLike test: a remote path may implement __fspath__,
        # and its URI text must not be checked as if it were a file name.
        return "foreign"
    if isinstance(path, (str, os.PathLike)):
        return "check"
    return "foreign"


def resolve_roots(
    confine_to: ConfineTo, base_dir: "_ty.Optional[object]"
) -> "_ty.Optional[_ty.Tuple[str, ...]]":
    """The roots *confine_to* names, as comparison keys; `None` means no confinement.

    Forms: `True` (*base_dir*, or the working directory when it is unset), a
    `str` split on `os.pathsep` like ``PATH``, a single path object, or a
    sequence of paths (each one root, never split, since a POSIX file name may
    hold ``:``). Empty entries are dropped, so ``[""]`` is an empty allowlist
    that refuses every read, never the working directory. Every root must be
    absolute: `abspath` would quietly turn a typo or a stray space into a root
    under the working directory.

    When *confine_to* is `None`, ``ANYCFG_CONFINE_TO`` is read by the same
    rules. An unset variable means no confinement; an empty one is an empty
    allowlist.
    """
    if confine_to is None:
        env = os.environ.get(CONFINE_TO_ENV)
        if env is None:
            return None
        confine_to = env
    if confine_to is False:
        return None
    if confine_to is True:
        return (root_key(str(base_dir) if base_dir else os.getcwd()),)
    if isinstance(confine_to, str):
        parts: _ty.List[_ty.Any] = [p for p in confine_to.split(os.pathsep) if p]
    elif isinstance(confine_to, os.PathLike):
        parts = [confine_to]
    else:
        try:
            parts = list(confine_to)
        except TypeError:
            raise ConfigTypeError(
                "confine_to= takes True, a path, a "
                f"{os.pathsep!r}-separated string or a sequence of paths, not "
                f"{type(confine_to).__name__}"
            ) from None
        for part in parts:
            if not isinstance(part, (str, os.PathLike)):
                raise ConfigTypeError(
                    f"every confine_to= root must be a path, not {type(part).__name__}"
                )
        parts = [part for part in parts if os.fspath(part)]
    roots = []
    for part in parts:
        text = os.fspath(part)
        if not is_anchored_root(text):
            raise ConfigTypeError(
                f"confine_to= root {text!r} is not an absolute path. Give each "
                "root an absolute path, or pass confine_to=True to confine to "
                "base_dir; a relative root would be resolved against the "
                "working directory, which is not what an allowlist should do."
            )
        roots.append(root_key(text))
    return tuple(roots)
