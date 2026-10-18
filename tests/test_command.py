"""Command sources: ``cmd://``/``exec://``/``sh://`` strings, `Command` and `Command.script`.

Every command here runs `sys.executable` on a script written into the test's
own directory, so the same test runs through ``cmd.exe`` and ``/bin/sh`` alike.
"""

from __future__ import annotations

import logging
import os
import pathlib
import pickle
import shutil
import subprocess
import sys
import time

import pytest

import anycfg
from anycfg import (
    CommandError,
    CommandsDisabledError,
    CommandTimeoutError,
    ConfigError,
    ConfigValueError,
    LoadContext,
    Loader,
    ParseError,
    Policy,
    PolicyError,
    UnsupportedFormatError,
    sources,
)

WIN = sys.platform == "win32"
COMMANDS = Policy(allow_commands=True)


def _loader(**settings):
    """A loader that may run commands and nothing else beyond the default."""
    return Loader(policy=COMMANDS, **settings)


def _program(tmp_path, body, name="emit.py"):
    """The command line running *body* as a Python script."""
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def _writes(tmp_path, stdout=b"", *, stderr=b"", status=0, name="emit.py"):
    """The command line of a program writing exactly these bytes, then exiting."""
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8")
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8")
    body = (
        "import sys\n"
        f"sys.stdout.buffer.write({stdout!r})\n"
        f"sys.stderr.buffer.write({stderr!r})\n"
        "sys.stdout.flush()\n"
        "sys.stderr.flush()\n"
        f"sys.exit({status})\n"
    )
    return _program(tmp_path, body, name)


def _marker_program(tmp_path, output='{"ran": true}'):
    """``(command line, marker)``: the program touches *marker* when it runs."""
    marker = tmp_path / "ran.marker"
    body = (
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).touch()\n"
        f"print({output!r})\n"
    )
    return _program(tmp_path, body, "marker.py"), marker


def _script(directory, stem, output='{"ok": true}', suffix=None):
    """A script printing *output*; ``.bat`` on Windows, executable ``.sh`` elsewhere."""
    directory.mkdir(parents=True, exist_ok=True)
    if suffix is None:
        suffix = ".bat" if WIN else ".sh"
    path = directory / f"{stem}{suffix}"
    if suffix in (".bat", ".cmd"):
        lines = "".join(f"echo {line}\n" for line in output.splitlines())
        path.write_text("@echo off\n" + lines, encoding="utf-8")
    else:
        lines = "".join(f"echo '{line}'\n" for line in output.splitlines())
        path.write_text("#!/bin/sh\n" + lines, encoding="utf-8")
        if not WIN:
            path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# The policy gate
# ---------------------------------------------------------------------------


class TestPolicyGate:
    """A command is refused under the default policy, before it runs."""

    @pytest.mark.parametrize("scheme", ["cmd", "exec", "sh", "cmd+json"])
    def test_scheme_string_refused_before_running(self, tmp_path, scheme):
        command, marker = _marker_program(tmp_path)
        with pytest.raises(CommandsDisabledError) as caught:
            Loader().load(f"{scheme}://{command}")
        assert not marker.exists()
        # A refusal is a PolicyError and so a PermissionError.
        assert isinstance(caught.value, PolicyError)
        assert isinstance(caught.value, PermissionError)

    def test_command_object_refused_before_running(self, tmp_path):
        command, marker = _marker_program(tmp_path)
        with pytest.raises(CommandsDisabledError):
            Loader().load(sources.Command(command, format="json"))
        assert not marker.exists()

    def test_script_object_refused_before_running(self, tmp_path):
        marker = tmp_path / "ran.marker"
        if WIN:
            path = tmp_path / "gen.json.bat"
            path.write_text(
                f'@echo off\ntype nul > "{marker}"\necho {{}}\n', encoding="utf-8"
            )
        else:
            path = tmp_path / "gen.json.sh"
            path.write_text(f"#!/bin/sh\ntouch '{marker}'\necho '{{}}'\n")
            path.chmod(0o755)
        with pytest.raises(CommandsDisabledError):
            Loader().load(sources.Command.script(path))
        assert not marker.exists()

    def test_reading_a_command_directly_is_refused(self, tmp_path):
        # The backstop: a source read outside any loader still obeys the policy.
        command, marker = _marker_program(tmp_path)
        source = sources.Command(command, format="json")
        with pytest.raises(CommandsDisabledError):
            source.read(LoadContext())
        assert not marker.exists()

    def test_allowed_command_runs(self, tmp_path):
        command, marker = _marker_program(tmp_path)
        assert _loader().load(f"cmd+json://{command}") == {"ran": True}
        assert marker.exists()

    def test_per_call_policy_allows_and_refuses(self, tmp_path):
        command, _marker = _marker_program(tmp_path)
        assert Loader().load(f"cmd+json://{command}", policy=COMMANDS) == {"ran": True}
        with pytest.raises(CommandsDisabledError):
            _loader().load(f"cmd+json://{command}", policy=Policy())

    @pytest.mark.usefixtures("needs_yaml")
    def test_include_of_a_command_refused(self, tmp_path):
        command, marker = _marker_program(tmp_path)
        doc = tmp_path / "main.yaml"
        doc.write_text(f"app: !include 'cmd+json://{command}'\n", encoding="utf-8")
        with pytest.raises(CommandsDisabledError):
            Loader().load(str(doc))
        assert not marker.exists()

    @pytest.mark.usefixtures("needs_yaml")
    def test_include_of_a_command_runs_when_allowed(self, tmp_path):
        command = _writes(tmp_path, '{"name": "my-app"}')
        doc = tmp_path / "main.yaml"
        doc.write_text(
            f"config:\n  app: !include 'cmd+json://{command}'\n", encoding="utf-8"
        )
        assert _loader().load(str(doc)) == {"config": {"app": {"name": "my-app"}}}

    def test_command_executes(self):
        assert sources.Command("echo 1").executes is True


# ---------------------------------------------------------------------------
# Scheme strings
# ---------------------------------------------------------------------------


class TestSchemes:
    @pytest.mark.parametrize("scheme", ["cmd", "exec", "sh"])
    def test_every_builtin_scheme_runs_a_command(self, tmp_path, scheme):
        command = _writes(tmp_path, "[1, 2]")
        assert _loader().load(f"{scheme}+json://{command}") == [1, 2]

    def test_builtin_schemes_are_registered(self):
        assert {"cmd", "exec", "sh"} <= set(sources.schemes())

    def test_scheme_is_case_insensitive(self, tmp_path):
        command = _writes(tmp_path, "7")
        assert _loader().load(f"CMD+JSON://{command}") == 7

    @pytest.mark.parametrize(
        "text",
        [
            "cmd:echo 1",  # no "//": a file name
            "C:\\conf\\app.json",
            "C:/conf/app.json",
            "zzunregistered://x.json",
            "app.json",
        ],
    )
    def test_from_text_names_only_registered_schemes(self, text):
        assert sources.from_text(text) is None

    def test_from_text_builds_a_command(self):
        found = sources.from_text("cmd+json://echo 1")
        assert isinstance(found, sources.Command)
        assert found.name == "cmd+json://echo 1"

    def test_unregistered_scheme_is_a_local_file_name(self, tmp_path):
        # No URI parsing: the text is a path, so the load fails as a file would.
        with pytest.raises((OSError, ConfigError)) as caught:
            Loader(base_dir=tmp_path).load("zzunregistered://x.json")
        assert not isinstance(caught.value, CommandsDisabledError)

    def test_register_scheme_routes_strings(self):
        def factory(text):
            return sources.Text('{"via": "scheme"}', format="json", name=text)

        sources.register_scheme("yacltestscheme", factory)
        try:
            assert "yacltestscheme" in sources.schemes()
            assert Loader().load("yacltestscheme://anything") == {"via": "scheme"}
            with pytest.raises(ValueError):
                sources.register_scheme("yacltestscheme", factory)
            sources.register_scheme("yacltestscheme", factory, replace=True)
        finally:
            assert sources.unregister_scheme("yacltestscheme") is True
        assert sources.unregister_scheme("yacltestscheme") is False
        assert sources.from_text("yacltestscheme://anything") is None

    def test_command_text_reaches_the_shell_unchanged(self, tmp_path):
        command = _program(
            tmp_path, "import sys, json\nprint(json.dumps(sys.argv[1:]))\n", "argv.py"
        )
        source = (
            f"cmd+json://{command} "
            "prod/db https://vault.example.com/v1/secret a//b ./rel s/./x/"
        )
        assert _loader(base_dir=tmp_path).load(source) == [
            "prod/db",
            "https://vault.example.com/v1/secret",
            "a//b",
            "./rel",
            "s/./x/",
        ]

    def test_glob_characters_in_a_command_are_not_expanded(self, tmp_path):
        (tmp_path / "a.json").write_text("{}", encoding="utf-8")
        command = _program(
            tmp_path, "import sys, json\nprint(json.dumps(sys.argv[1:]))\n", "argv.py"
        )
        source = f'cmd+json://{command} "*.json" "[ab]"'
        assert _loader(base_dir=tmp_path).load(source) == ["*.json", "[ab]"]


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------


class TestOutputFormat:
    """``format=``, then ``+fmt``, then a ``#!fmt`` line, then a script's inner suffix."""

    def test_format_argument_wins(self, tmp_path):
        # Valid JSON, invalid TOML: only the format= choice can parse it.
        command = _writes(tmp_path, '#!toml\n{"a": 1}\n')
        source = sources.Command(f"cmd+toml://{command}", format="json")
        assert _loader().load(source) == {"a": 1}

    def test_scheme_format_beats_the_first_line(self, tmp_path):
        command = _writes(tmp_path, '#!toml\n{"a": 1}\n')
        assert _loader().load(f"cmd+json://{command}") == {"a": 1}

    @pytest.mark.parametrize(
        "line", ["#!json", "#! json", "#!/usr/bin/env json", "#!JSON", "#!.json"]
    )
    def test_first_line_names_the_format_and_is_dropped(self, tmp_path, line):
        command = _writes(tmp_path, f'{line}\n{{"a": 1}}\n')
        assert _loader().load(f"cmd://{command}") == {"a": 1}

    def test_first_line_after_leading_blank_lines(self, tmp_path):
        command = _writes(tmp_path, '\n\n#!json\n{"a": 1}\n')
        assert _loader().load(f"cmd://{command}") == {"a": 1}

    def test_script_inner_suffix_names_the_format(self, tmp_path):
        path = _script(tmp_path, "secrets.json", '{"token": 1}')
        assert _loader().load(sources.Command.script(path)) == {"token": 1}

    def test_first_line_beats_the_script_suffix(self, tmp_path):
        path = _script(tmp_path, "secrets.toml", '#!json\n{"token": 1}')
        assert _loader().load(sources.Command.script(path)) == {"token": 1}

    def test_no_format_raises_and_output_is_never_sniffed(self, tmp_path):
        # Unambiguous JSON, still refused: content never picks its own format.
        command = _writes(tmp_path, '{"a": 1}\n')
        with pytest.raises(ConfigValueError) as caught:
            _loader().load(f"cmd://{command}")
        assert "format" in str(caught.value)

    def test_script_without_an_inner_suffix_has_no_format(self, tmp_path):
        path = _script(tmp_path, "plain", '{"a": 1}')
        with pytest.raises(ConfigValueError):
            _loader().load(sources.Command.script(path))

    def test_script_format_argument(self, tmp_path):
        path = _script(tmp_path, "plain", '{"a": 1}')
        assert _loader().load(sources.Command.script(path, format="json")) == {"a": 1}

    def test_loader_format_argument_reads_command_output(self, tmp_path):
        command = _writes(tmp_path, '{"a": 1}\n')
        assert _loader().load(f"cmd://{command}", format="json") == {"a": 1}

    @pytest.mark.usefixtures("needs_yaml")
    def test_include_format_reads_command_output(self, tmp_path):
        command = _writes(tmp_path, '{"a": 1}\n')
        (tmp_path / "main.yaml").write_text(
            f"x: !include {{pathname: 'cmd://{command}', format: json}}\n",
            encoding="utf-8",
        )
        assert _loader(base_dir=tmp_path).load("main.yaml") == {"x": {"a": 1}}

    def test_first_line_naming_an_unknown_format_is_refused(self, tmp_path):
        command = _writes(tmp_path, "#!nosuchformat\n{}\n")
        with pytest.raises(UnsupportedFormatError):
            _loader().load(f"cmd://{command}")

    def test_first_line_naming_a_non_document_format_is_refused(self, tmp_path):
        command = _writes(tmp_path, "#!ini\n[s]\nk = v\n")
        with pytest.raises(UnsupportedFormatError) as caught:
            _loader(formats=["json"]).load(f"cmd://{command}")
        assert "ini" in str(caught.value)
        allowed = _loader(formats=["json", "ini"]).load(f"cmd://{command}")
        assert allowed == {"s": {"k": "v"}}

    def test_empty_output_in_a_format_names_the_command(self, tmp_path):
        command = _writes(tmp_path, "", name="silent.py")
        with pytest.raises(ParseError) as caught:
            _loader().load(f"cmd+json://{command}")
        assert "silent.py" in str(caught.value)


# ---------------------------------------------------------------------------
# Running: timeout, exit status, stdin, stderr
# ---------------------------------------------------------------------------


class TestExecution:
    def test_timeout_kills_the_process_tree(self, tmp_path):
        # The grandchild holds the output pipe: killing only the direct child
        # would leave the read waiting the full 30 seconds.
        command = _program(
            tmp_path,
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "time.sleep(30)\n",
            "tree.py",
        )
        started = time.perf_counter()
        with pytest.raises(CommandTimeoutError) as caught:
            _loader().load(sources.Command(command, format="json", timeout=1))
        assert time.perf_counter() - started < 15
        error = caught.value
        assert isinstance(error, subprocess.TimeoutExpired)
        assert isinstance(error, ConfigError)
        assert error.timeout == 1

    def test_default_timeout_comes_from_the_policy(self, tmp_path):
        command = _program(tmp_path, "import time\ntime.sleep(30)\n", "sleep.py")
        loader = Loader(policy=Policy(allow_commands=True, command_timeout=1))
        started = time.perf_counter()
        with pytest.raises(CommandTimeoutError) as caught:
            loader.load(f"cmd+json://{command}")
        assert time.perf_counter() - started < 15
        assert caught.value.timeout == 1

    def test_policy_default_timeout(self):
        assert Policy().command_timeout == 60.0

    def test_command_timeout_none_overrides_the_policy(self, tmp_path):
        # A policy timeout no interpreter can start within; None waits instead.
        command = _writes(tmp_path, "5")
        loader = Loader(policy=Policy(allow_commands=True, command_timeout=0.001))
        source = sources.Command(command, format="json", timeout=None)
        assert loader.load(source) == 5

    def test_nonzero_exit_raises_command_error_with_stderr(self, tmp_path):
        command = _writes(
            tmp_path,
            "TOPSECRET\n",
            stderr="AccessDeniedException: token expired\n",
            status=3,
        )
        with pytest.raises(CommandError) as caught:
            _loader().load(f"cmd+json://{command}")
        error = caught.value
        assert isinstance(error, subprocess.CalledProcessError)
        assert isinstance(error, ConfigError)
        assert error.returncode == 3
        message = str(error)
        assert "token expired" in message
        # stdout is the payload, often the very secret being fetched.
        assert "TOPSECRET" not in message
        assert "TOPSECRET" in error.output

    def test_stderr_tail_is_bounded(self, tmp_path):
        stderr = "".join(f"L{i:04d}\n" for i in range(500))
        command = _writes(tmp_path, stderr=stderr, status=1)
        with pytest.raises(CommandError) as caught:
            _loader().load(f"cmd+json://{command}")
        message = str(caught.value)
        # The last 20 lines: a tool prints its reason last.
        assert "L0499" in message and "L0480" in message
        assert "L0479" not in message and "L0000" not in message
        assert len(message) < 4000

    def test_stderr_tail_is_capped_in_characters(self, tmp_path):
        stderr = "".join("X" * 400 + f"{i:02d}\n" for i in range(20))
        command = _writes(tmp_path, stderr=stderr, status=1)
        with pytest.raises(CommandError) as caught:
            _loader().load(f"cmd+json://{command}")
        message = str(caught.value)
        assert "19" in message
        assert message.count("X") <= 2000

    def test_exit_status_wins_over_undecodable_output(self, tmp_path):
        command = _writes(tmp_path, bytes([233]), status=3)
        with pytest.raises(CommandError) as caught:
            _loader().load(f"cmd+json://{command}")
        assert caught.value.returncode == 3
        assert isinstance(caught.value.stderr, str)

    def test_command_errors_pickle(self, tmp_path):
        failing = _writes(tmp_path, stderr="boom\n", status=2, name="fail.py")
        with pytest.raises(CommandError) as caught:
            _loader().load(f"cmd+json://{failing}")
        copy = pickle.loads(pickle.dumps(caught.value))
        assert type(copy) is CommandError
        assert copy.returncode == 2

        sleeping = _program(tmp_path, "import time\ntime.sleep(30)\n", "sleep.py")
        with pytest.raises(CommandTimeoutError) as caught_timeout:
            _loader().load(sources.Command(sleeping, format="json", timeout=1))
        copy_timeout = pickle.loads(pickle.dumps(caught_timeout.value))
        assert type(copy_timeout) is CommandTimeoutError
        assert copy_timeout.timeout == 1

    def test_successful_command_stderr_logged_at_debug(self, tmp_path, caplog):
        command = _writes(tmp_path, '{"a": 1}', stderr="deprecation warning\n")
        with caplog.at_level(logging.DEBUG):
            assert _loader().load(f"cmd+json://{command}") == {"a": 1}
        debug = [r for r in caplog.records if "deprecation warning" in r.getMessage()]
        assert debug and all(r.levelno == logging.DEBUG for r in debug)

    def test_command_runs_with_stdin_closed(self, tmp_path):
        # Run in a child whose stdin is a pipe that is never closed: a command
        # inheriting it would wait forever on the read.
        package_root = str(pathlib.Path(anycfg.__file__).resolve().parent.parent)
        inner = _program(
            tmp_path, "import sys\nsys.stdin.read()\nprint(1)\n", "reads_stdin.py"
        )
        code = (
            f"import sys\nsys.path.insert(0, {package_root!r})\n"
            "from anycfg import Loader, Policy\n"
            "print(Loader(policy=Policy(allow_commands=True)).load("
            f"{'cmd+json://' + inner!r}))\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout, _stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            pytest.fail("the command waited on the parent's stdin")
        assert proc.returncode == 0
        assert stdout.strip() == b"1"


# ---------------------------------------------------------------------------
# Decoding and trimming
# ---------------------------------------------------------------------------


class TestDecoding:
    PREFIX = b'{"a": "caf'

    def test_invalid_output_is_a_parse_error_naming_the_byte(self, tmp_path):
        command = _writes(tmp_path, self.PREFIX + bytes([233]) + b'"}')
        with pytest.raises(ParseError) as caught:
            _loader().load(f"cmd+json://{command}")
        error = caught.value
        assert f"byte {len(self.PREFIX)}" in str(error)
        assert "encoding=" in str(error)
        # Raised outside the except block: the decode error holds the output.
        assert error.__cause__ is None and error.__context__ is None

    def test_command_encoding_argument(self, tmp_path):
        command = _writes(tmp_path, self.PREFIX + bytes([233]) + b'"}')
        source = sources.Command(command, format="json", encoding="latin-1")
        assert _loader().load(source) == {"a": "caf\xe9"}

    def test_load_encoding_decodes_the_output(self, tmp_path):
        command = _writes(tmp_path, self.PREFIX + bytes([233]) + b'"}')
        assert _loader().load(f"cmd+json://{command}", encoding="latin-1") == {
            "a": "caf\xe9"
        }
        assert _loader(encoding="cp1252").load(f"cmd+json://{command}") == {
            "a": "caf\xe9"
        }

    def test_command_encoding_beats_the_load_encoding(self, tmp_path):
        command = _writes(tmp_path, '{"a": "caf\xe9"}'.encode("utf-8"))
        source = sources.Command(command, format="json", encoding="utf-8")
        assert _loader(encoding="latin-1").load(source) == {"a": "caf\xe9"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_crlf_output_is_normalized(self, tmp_path):
        output = "\r\n".join(["#!yaml", "x: |", "  l1", "  l2", "y: 2", ""])
        command = _writes(tmp_path, output)
        assert _loader().load(f"cmd://{command}") == {"x": "l1\nl2\n", "y": 2}


class TestTrimming:
    def test_trailing_whitespace_is_trimmed(self, tmp_path):
        command = _writes(tmp_path, '{"a": 1}\n\n   \n\t')
        assert _loader().load(f"cmd+json://{command}") == {"a": 1}

    @pytest.mark.usefixtures("needs_yaml")
    def test_leading_blank_lines_trimmed_and_indentation_kept(self, tmp_path):
        # Stripping the first line's indentation would make this invalid YAML.
        command = _writes(tmp_path, "\n\n  a: 1\n  b: 2\n\n   ")
        assert _loader().load(f"cmd+yaml://{command}") == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# Launching script files
# ---------------------------------------------------------------------------


def _powershell_allows_scripts():
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if not exe:
        return False
    done = subprocess.run(
        [exe, "-NoProfile", "-Command", "Get-ExecutionPolicy"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done.stdout.strip() not in ("Restricted", "AllSigned")


class TestScriptLaunch:
    """A script runs through its interpreter, never a shell parsing its name."""

    MARK = "MARK"

    def test_metacharacters_in_the_name_are_not_shell_syntax(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        stem = "x&copy nul MARK&.json" if WIN else "a;touch MARK;.json"
        path = _script(tmp_path / "conf", stem)
        assert _loader().load(sources.Command.script(path)) == {"ok": True}
        assert not (tmp_path / "conf" / self.MARK).exists()
        assert not (tmp_path / self.MARK).exists()

    def test_directory_with_ampersand_and_space(self, tmp_path):
        path = _script(tmp_path / "R&D dir", "gen.json")
        assert _loader().load(sources.Command.script(path)) == {"ok": True}

    def test_relative_path_is_made_absolute(self, tmp_path, monkeypatch):
        # A leading "-" would read as an option, and a bare name would be
        # searched on PATH, if the path were passed as given.
        path = _script(tmp_path, "-gen.json")
        monkeypatch.chdir(tmp_path)
        source = sources.Command.script(path.name)
        assert _loader().load(source) == {"ok": True}

    def test_path_object_accepted(self, tmp_path):
        path = _script(tmp_path, "gen.json")
        source = sources.Command.script(pathlib.Path(path))
        assert source.name == str(path)
        assert _loader().load(source) == {"ok": True}

    @pytest.mark.skipif(not WIN, reason="Windows batch files only")
    def test_percent_in_a_batch_path_is_refused(self, tmp_path):
        path = _script(tmp_path / "conf", "%OS%.json")
        with pytest.raises(ConfigValueError, match="%"):
            _loader().load(sources.Command.script(path))

    @pytest.mark.skipif(not WIN, reason="Windows batch files only")
    def test_caret_in_a_batch_path_runs(self, tmp_path):
        path = _script(tmp_path / "conf", "a^b.json")
        assert _loader().load(sources.Command.script(path)) == {"ok": True}

    @pytest.mark.skipif(not WIN, reason="Windows .cmd only")
    def test_cmd_suffix_runs(self, tmp_path):
        path = _script(tmp_path, "gen.json", suffix=".cmd")
        assert _loader().load(sources.Command.script(path)) == {"ok": True}

    @pytest.mark.skipif(WIN, reason="POSIX only")
    def test_batch_file_on_posix_is_refused(self, tmp_path):
        path = tmp_path / "gen.json.bat"
        path.write_text('echo {"ok": true}\n', encoding="utf-8")
        with pytest.raises(ConfigValueError, match="batch"):
            _loader().load(sources.Command.script(path))

    @pytest.mark.skipif(WIN, reason="POSIX permissions only")
    def test_non_executable_sh_runs_through_bin_sh(self, tmp_path):
        path = tmp_path / "gen.json.sh"
        path.write_text("echo '{\"ok\": true}'\n", encoding="utf-8")
        path.chmod(0o644)
        assert _loader().load(sources.Command.script(path)) == {"ok": True}

    @pytest.mark.skipif(WIN, reason="POSIX only")
    def test_executable_script_with_shebang_runs_directly(self, tmp_path):
        if " " in sys.executable:
            pytest.skip("a #! line cannot name an interpreter path with a space")
        # Python, not shell: it only works when the #! line is honoured.
        path = tmp_path / "gen.json.sh"
        path.write_text(
            f"#!{sys.executable}\nimport json\nprint(json.dumps({{'py': True}}))\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        assert _loader().load(sources.Command.script(path)) == {"py": True}

    @pytest.mark.skipif(not WIN, reason="Windows .sh-through-sh only")
    def test_sh_on_windows_runs_through_sh(self, tmp_path):
        if not shutil.which("sh"):
            pytest.skip("no 'sh' on PATH")
        path = tmp_path / "gen.json.sh"
        path.write_text("echo '{\"ok\": true}'\n", encoding="utf-8")
        assert _loader().load(sources.Command.script(path)) == {"ok": True}

    @pytest.mark.skipif(not WIN, reason="Windows PowerShell only")
    def test_ps1_runs_through_powershell(self, tmp_path):
        if not _powershell_allows_scripts():
            pytest.skip("PowerShell execution policy forbids running scripts")
        path = tmp_path / "it's R&D.json.ps1"
        path.write_text("Write-Output '{\"ok\": true}'\n", encoding="utf-8")
        assert _loader().load(sources.Command.script(path)) == {"ok": True}


class TestScriptFileSources:
    """A file with a script suffix passed to a loader is a command."""

    def test_script_path_runs_as_a_command(self, tmp_path):
        path = _script(tmp_path, "gen.json")
        assert _loader().load(str(path)) == {"ok": True}

    def test_script_path_refused_by_default(self, tmp_path):
        path = _script(tmp_path, "gen.json")
        with pytest.raises(CommandsDisabledError):
            Loader().load(str(path))

    def test_script_matched_by_a_glob_is_not_shell_syntax(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stem = "x&copy nul MARK&.json" if WIN else "a;touch MARK;.json"
        _script(tmp_path / "conf", stem)
        assert _loader(base_dir=tmp_path).load("conf/*") == {"ok": True}
        assert not (tmp_path / "conf" / "MARK").exists()

    def test_stream_named_like_a_script_is_content(self, tmp_path):
        # A file object's content is data: its name never makes it a program.
        path = tmp_path / "x.sh"
        path.write_text('{"a": 1}\n', encoding="utf-8")
        with open(path, encoding="utf-8") as handle:
            assert Loader().load(handle, format="json") == {"a": 1}


# ---------------------------------------------------------------------------
# Includes inside command output
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("needs_yaml")
class TestIncludesInOutput:
    """Output is in-memory content: its relative includes resolve against ``base_dir``."""

    def test_relative_include_resolves_against_base_dir(self, tmp_path):
        (tmp_path / "sub.yaml").write_text("inner: 1\n", encoding="utf-8")
        command = _writes(tmp_path, "sub: !include sub.yaml\n")
        loader = _loader(base_dir=tmp_path)
        assert loader.load(f"cmd+yaml://{command}") == {"sub": {"inner": 1}}

    def test_missing_include_is_an_error_not_an_empty_document(self, tmp_path):
        command = _writes(tmp_path, "db: !include nope_secret.yaml\n")
        with pytest.raises(FileNotFoundError):
            _loader(base_dir=tmp_path).load(f"cmd+yaml://{command}")

    def test_include_parse_error_carries_an_include_frame(self, tmp_path):
        (tmp_path / "bad.json").write_text('{"a": }\n', encoding="utf-8")
        command = _writes(tmp_path, "db: !include bad.json\n")
        with pytest.raises(ParseError) as caught:
            _loader(base_dir=tmp_path).load(f"cmd+yaml://{command}")
        kinds = [frame.kind for frame in caught.value.config_frames]
        assert "include" in kinds

    def test_load_encoding_reaches_output_includes(self, tmp_path):
        (tmp_path / "sub.yaml").write_bytes("inner: caf\xe9\n".encode("cp1252"))
        command = _writes(tmp_path, "sub: !include sub.yaml\n")
        loader = _loader(base_dir=tmp_path, encoding="cp1252")
        assert loader.load(f"cmd+yaml://{command}") == {"sub": {"inner": "caf\xe9"}}

    def test_command_encoding_reaches_output_includes(self, tmp_path):
        (tmp_path / "sub.yaml").write_bytes("inner: caf\xe9\n".encode("cp1252"))
        command = _writes(tmp_path, "sub: !include sub.yaml\n")
        source = sources.Command(command, format="yaml", encoding="cp1252")
        result = _loader(base_dir=tmp_path).load(source)
        assert result == {"sub": {"inner": "caf\xe9"}}

    def test_output_round_trips_through_the_declared_codec(self, tmp_path):
        command = _writes(tmp_path, "msg: caf\xe9\n".encode("cp1252"))
        loader = _loader(base_dir=tmp_path, encoding="cp1252")
        assert loader.load(f"cmd+yaml://{command}") == {"msg": "caf\xe9"}

    def test_include_mapping_encoding_reaches_command_output_includes(self, tmp_path):
        (tmp_path / "sub.yaml").write_bytes("inner: caf\xe9\n".encode("cp1252"))
        command = _writes(tmp_path, "sub: !include sub.yaml\n")
        source = f"cmd+yaml://{command}".replace("\\", "/")
        (tmp_path / "parent.yaml").write_text(
            f"cmd: !include {{pathname: '{source}', encoding: cp1252}}\n",
            encoding="utf-8",
        )
        result = _loader(base_dir=tmp_path).load("parent.yaml")
        assert result == {"cmd": {"sub": {"inner": "caf\xe9"}}}

    def test_command_output_can_include_a_command(self, tmp_path):
        inner = _writes(tmp_path, '{"deep": 1}', name="inner.py")
        outer = _writes(
            tmp_path, f"x: !include 'cmd+json://{inner}'\n", name="outer.py"
        )
        assert _loader(base_dir=tmp_path).load(f"cmd+yaml://{outer}") == {
            "x": {"deep": 1}
        }


def test_command_name_and_repr():
    source = sources.Command("cmd+json://echo 1")
    assert source.name == "cmd+json://echo 1"
    assert "cmd+json://echo 1" in repr(source)
