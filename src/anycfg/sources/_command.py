"""Command sources: run a program and parse what it prints."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import typing as _ty

from ..exceptions import (
    CommandError,
    CommandTimeoutError,
    ConfigValueError,
    ParseError,
    _stderr_tail,
)
from ._base import Payload, Source

from .._context import LoadContext

__all__ = ["Command", "SCRIPT_SUFFIXES"]

logger = logging.getLogger(__name__)

#: File suffixes that are programs, not documents.
SCRIPT_SUFFIXES = (".sh", ".bat", ".ps1", ".cmd")

_UNSET: _ty.Any = object()


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Kill a process and everything it started.

    Killing only the shell is not enough: a grandchild keeps the output pipes
    open, so the read that follows would still wait for it to finish.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            # The command runs in its own session, so its pid is the group id.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _script_command(path: str) -> "_ty.Union[str, _ty.List[str]]":
    """How to launch the script at *path*, without a shell.

    An argv list, or for ``.bat``/``.cmd`` a command line whose program is
    COMSPEC. Either way it runs with ``shell=False``, so the file's own name is
    never parsed as shell syntax. The path is made absolute first: a relative
    one would be searched on ``PATH``, refused under
    ``NoDefaultCurrentDirectoryInExePath``, or read as an option if it began
    with ``-``.
    """
    path = os.path.abspath(path)
    suffix = os.path.splitext(path)[1].lower()

    if suffix in (".bat", ".cmd"):
        if sys.platform != "win32":
            raise ConfigValueError(
                f"{path!r} is a Windows batch file and cannot run on this platform"
            )
        if "%" in path:
            # cmd expands %VAR% even inside quotes; no escape survives it.
            raise ConfigValueError(
                f"refusing to run {path!r}: a batch file's path cannot contain '%'"
            )
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        # /d skips AutoRun, /v:off disables delayed expansion, /s keeps the
        # outer quotes around the whole command.
        return f'"{comspec}" /d /v:off /s /c ""{path}""'

    if suffix == ".ps1":
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if not exe:
            raise FileNotFoundError(
                f"cannot run {path!r}: neither pwsh nor powershell is on PATH"
            )
        # No -ExecutionPolicy: a locked-down host should still refuse.
        return [exe, "-NoProfile", "-NonInteractive", "-File", path]

    if suffix == ".sh":
        if sys.platform == "win32":
            shell = shutil.which("sh")
            if not shell:
                raise FileNotFoundError(f"cannot run {path!r}: no 'sh' on PATH")
            return [shell, path]
        # Direct execution only when the file is executable (os.access also
        # reports a noexec mount) and carries a #! line; otherwise /bin/sh, so
        # a 0644 script still runs.
        if os.access(path, os.X_OK):
            try:
                with open(path, "rb") as handle:
                    if handle.read(2) == b"#!":
                        return [path]
            except OSError:
                pass
        return ["/bin/sh", path]

    return [path]


def _decode(raw: bytes, codec: str, *, strict: bool) -> str:
    """Decode command output, then apply universal newlines."""
    text = raw.decode(codec, errors="strict" if strict else "replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _run(
    command: "_ty.Union[str, _ty.List[str]]",
    encoding: str,
    timeout: "_ty.Optional[float]",
    shell: bool,
) -> bytes:
    """Run *command* with stdin closed; return its stdout as bytes.

    Raises `CommandError` on a non-zero exit and `CommandTimeoutError` when
    *timeout* elapses, after killing the whole process tree. stderr from a
    successful run is logged at DEBUG.
    """
    with subprocess.Popen(
        command,
        shell=shell,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Binary: a strict decode below needs a real UnicodeDecodeError, which
        # Popen(errors="replace") would hide.
        start_new_session=(os.name != "nt"),
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            process.communicate()
            raise CommandTimeoutError(command, timeout) from None
        except BaseException:
            # Interrupted (KeyboardInterrupt): never leave the command running.
            process.kill()
            raise
    if process.returncode:
        # A failing command's output is a diagnostic, not configuration.
        raise CommandError(
            process.returncode,
            command,
            output=_decode(stdout, encoding, strict=False),
            stderr=_decode(stderr, encoding, strict=False),
        )
    if stderr and stderr.strip():
        logger.debug(
            "command %r wrote to stderr:%s",
            command,
            _stderr_tail(_decode(stderr, encoding, strict=False)),
        )
    return stdout


class Command(Source):
    """Run a command and parse its standard output.

    *command* is either a scheme string — ``cmd://...``, ``exec://...`` or
    ``sh://...``, optionally naming the output format as ``cmd+json://...`` —
    or a bare command line. Both run through the shell. Use
    :meth:`script` for a script file, which runs through its interpreter
    without a shell.

    The output format comes from, in order: *format*, the ``+fmt`` of the
    scheme, a ``#!<format>`` first line of the output (which is then dropped),
    and for a script the suffix before its own (``secrets.json.sh``); then the
    loader's own ``format=``. Output with no format raises: content is never
    sniffed. Includes inside the output are read with the same *encoding*.

    The command runs with stdin closed and a timeout (the policy's
    ``command_timeout`` unless *timeout* is given), after which it and its
    child processes are killed. Output is decoded strictly with *encoding*
    (the load's encoding by default). A non-zero exit raises
    :class:`~anycfg.exceptions.CommandError`, whose message carries the
    tail of stderr and never stdout.
    """

    executes = True

    def __init__(
        self,
        command: str,
        *,
        format: "_ty.Optional[str]" = None,
        timeout: "_ty.Optional[float]" = _UNSET,
        encoding: "_ty.Optional[str]" = None,
    ) -> None:
        from . import SCHEME_REGEX

        self.text = command
        self.format = format
        self.timeout = timeout
        self.encoding = encoding
        self._script: "_ty.Optional[str]" = None
        match = SCHEME_REGEX.match(command)
        if match is not None:
            self.command = command[match.end() :]
            if self.format is None and match.group(2):
                self.format = match.group(2).lower()
        else:
            self.command = command

    @classmethod
    def script(
        cls,
        path: "_ty.Union[str, os.PathLike]",
        *,
        format: "_ty.Optional[str]" = None,
        timeout: "_ty.Optional[float]" = _UNSET,
        encoding: "_ty.Optional[str]" = None,
    ) -> "Command":
        """A script file (``.sh``, ``.bat``, ``.cmd``, ``.ps1``), run without a shell."""
        text = os.fspath(path)
        source = cls(text, format=format, timeout=timeout, encoding=encoding)
        source.command = text
        source._script = text
        return source

    @property
    def name(self) -> str:
        return self.text

    def read(self, context: "LoadContext") -> Payload:
        # A backstop: the loader refuses an executing source before it gets
        # here, but a source can also be read directly.
        from .._loader import check_command

        check_command(context.policy, self.text)
        timeout = (
            context.policy.command_timeout if self.timeout is _UNSET else self.timeout
        )
        codec = self.encoding or context.encoding
        if self._script is not None:
            raw = _run(_script_command(self._script), codec, timeout, shell=False)
        else:
            raw = _run(self.command, codec, timeout, shell=True)
        try:
            text = _decode(raw, codec, strict=True)
        except UnicodeDecodeError as error:
            failure = ParseError(
                f"output is not valid {codec} at byte {error.start}; pass "
                "encoding= naming the program's codec",
                path=self.text,
            )
        else:
            failure = None
        if failure is not None:
            # Outside the except block: the decode error holds the output.
            raise failure
        # Trailing whitespace and leading blank lines only: indentation on the
        # first line is document text (an indented YAML mapping).
        text = text.rstrip()
        while text.startswith("\n"):
            text = text[1:]

        from .. import formats

        named, text = formats.declared(text)
        found = self.format if self.format is not None else named
        if found is None and self._script is not None:
            stem = os.path.splitext(os.path.basename(self._script))[0]
            claimed = formats.find_for_filename(stem)
            found = claimed.name if claimed is not None else None
        # None when nothing named a format: the loader may still have one
        # (its own format=), and otherwise raises naming the ways to give one.
        return Payload(
            text,
            name=self.text,
            identity=f"command:{self.text}",
            format=found,
            encoding=codec,
        )

    def __repr__(self) -> str:
        return f"Command({self.text!r})"
