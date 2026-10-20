"""The platform's standard configuration directories.

Nothing here creates a file or directory.
"""

from __future__ import annotations

import os
import pathlib
import sys
from typing import List, Optional, Union

__all__ = ["default_configs", "system_config_path", "user_config_path"]


def user_config_path(name: Optional[Union[str, os.PathLike]] = None) -> pathlib.Path:
    """The current user's configuration directory, or ``name`` inside it.

    ``%APPDATA%`` on Windows (``~/AppData/Roaming`` when it is unset);
    ``$XDG_CONFIG_HOME`` or ``~/.config`` elsewhere, macOS included. A relative
    ``$XDG_CONFIG_HOME`` is ignored, as the XDG Base Directory specification
    requires.

    ``name`` is joined as :mod:`pathlib` joins, so it may hold subdirectories
    (``"app/config.toml"``)::

        anycfg.user_config_path("app.toml")    # ~/.config/app.toml
    """
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = (
            pathlib.Path(appdata)
            if appdata
            else pathlib.Path.home() / "AppData" / "Roaming"
        )
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = (
            pathlib.Path(xdg)
            if xdg and os.path.isabs(xdg)
            else pathlib.Path.home() / ".config"
        )
    return base / name if name else base


def system_config_path(name: Optional[Union[str, os.PathLike]] = None) -> pathlib.Path:
    """The machine-wide configuration directory, or ``name`` inside it.

    ``%PROGRAMDATA%`` on Windows (``C:\\ProgramData`` when it is unset);
    ``/etc`` elsewhere. ``$XDG_CONFIG_DIRS`` is not consulted: system
    configuration conventionally lives under ``/etc``.

    :func:`default_configs` layers it under the user's file.
    """
    if sys.platform == "win32":
        base = pathlib.Path(os.environ.get("PROGRAMDATA") or "C:\\ProgramData")
    else:
        base = pathlib.Path("/etc")
    return base / name if name else base


def default_configs(name: Union[str, os.PathLike]) -> List[Optional[pathlib.Path]]:
    """The system and user files called ``name``, as layers for :meth:`Loader.load`.

    ``[system_config_path(name), user_config_path(name)]``, in that order so
    the user's file overrides, with ``None`` in place of a file that does not
    exist — the loader skips ``None``, so the list goes straight in::

        config = anycfg.Loader().load(*anycfg.default_configs("app.toml"), default={})

    With neither file present the load returns its ``default``. ``name`` is a
    file name (subdirectories allowed), not a glob pattern.
    """
    if not name:
        raise ValueError("default_configs() needs a file name")
    return [
        path if path.is_file() else None
        for path in (system_config_path(name), user_config_path(name))
    ]
