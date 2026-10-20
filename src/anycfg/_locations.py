"""The platform's standard configuration directories.

Both functions only compute a path: nothing is created, and nothing is
checked for existence.
"""

from __future__ import annotations

import os
import pathlib
import sys
from typing import Optional, Union

__all__ = ["system_config_path", "user_config_path"]


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

    Layer the two with the user's file last, so it overrides::

        layers = [anycfg.system_config_path("app.toml"), anycfg.user_config_path("app.toml")]
        config = anycfg.Loader().load(*(p for p in layers if p.is_file()))
    """
    if sys.platform == "win32":
        base = pathlib.Path(os.environ.get("PROGRAMDATA") or "C:\\ProgramData")
    else:
        base = pathlib.Path("/etc")
    return base / name if name else base
