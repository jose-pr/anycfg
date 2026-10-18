"""The error hierarchy, and the context a load records on an error it re-raises.

Every condition the library reports is a `ConfigError` that also inherits the
builtin describing the same condition; an `OSError` from reading a file is let
through as itself. So ``except (ConfigError, OSError)`` covers every load failure.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import pickle
import subprocess
import sys
import time
import typing

import pytest

import anycfg
import anycfg.exceptions as exceptions_module
from _extras import needs_jinja2, needs_toml, needs_yaml
from anycfg import (
    BudgetExceededError,
    CommandError,
    CommandsDisabledError,
    CommandTimeoutError,
    ConfigError,
    ConfigTypeError,
    ConfigValueError,
    ConfinementError,
    ErrorFrame,
    Loader,
    MissingDependencyError,
    ParseError,
    Policy,
    PolicyError,
    TemplateError,
    TemplatesDisabledError,
    UnsupportedFormatError,
    sources,
)
from anycfg.testing import SECRET

HIERARCHY = [
    (ConfigError, (Exception,)),
    (ConfigValueError, (ConfigError, ValueError)),
    (ConfigTypeError, (ConfigError, TypeError)),
    (ParseError, (ConfigError, ValueError)),
    (TemplateError, (ConfigError, ValueError)),
    (UnsupportedFormatError, (ConfigError, ValueError)),
    (MissingDependencyError, (UnsupportedFormatError, ImportError)),
    (PolicyError, (ConfigError, PermissionError)),
    (CommandsDisabledError, (PolicyError,)),
    (TemplatesDisabledError, (PolicyError,)),
    (ConfinementError, (PolicyError,)),
    (BudgetExceededError, (PolicyError,)),
    (CommandError, (ConfigError, subprocess.CalledProcessError)),
    (CommandTimeoutError, (ConfigError, subprocess.TimeoutExpired)),
]


@dataclasses.dataclass
class Host:
    port: int


@dataclasses.dataclass
class Service:
    hosts: typing.List[Host] = dataclasses.field(default_factory=list)


def write(directory, name, text, encoding="utf-8"):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    return path


def python_command(code, scheme="cmd+json"):
    return f'{scheme}://"{sys.executable}" -c "{code}"'


def everything_said(error):
    """Every text an error exposes: message, repr, args, attributes, chain."""
    seen = [str(error), repr(error), repr(error.args), repr(vars(error))]
    seen.extend(getattr(error, "__notes__", None) or ())
    cause = error.__cause__ or error.__context__
    while cause is not None:
        seen.extend([str(cause), repr(cause)])
        cause = cause.__cause__ or cause.__context__
    return seen


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


class TestHierarchy:
    @pytest.mark.parametrize(
        "cls, bases", HIERARCHY, ids=[cls.__name__ for cls, _ in HIERARCHY]
    )
    def test_bases(self, cls, bases):
        assert cls.__bases__ == bases

    @pytest.mark.parametrize(
        "cls", [cls for cls, _ in HIERARCHY], ids=lambda c: c.__name__
    )
    def test_every_class_is_a_config_error_exported_twice(self, cls):
        assert issubclass(cls, ConfigError)
        assert getattr(anycfg, cls.__name__) is cls
        assert getattr(exceptions_module, cls.__name__) is cls
        assert cls.__name__ in anycfg.__all__
        assert cls.__name__ in exceptions_module.__all__

    def test_policy_errors_are_os_errors(self):
        for cls in (PolicyError, CommandsDisabledError, ConfinementError):
            assert issubclass(cls, PermissionError) and issubclass(cls, OSError)

    def test_error_frame(self):
        frame = ErrorFrame("include", "a.yaml")
        assert frame.line is None
        assert ErrorFrame("include", "a.yaml", 3) == ("include", "a.yaml", 3)
        assert ErrorFrame._fields == ("kind", "source", "line")
        assert anycfg.ErrorFrame is exceptions_module.ErrorFrame


# ---------------------------------------------------------------------------
# ParseError
# ---------------------------------------------------------------------------


class TestParseError:
    def test_attributes_and_str(self):
        error = ParseError("bad thing", path="app.yaml", lineno=3, colno=4)
        assert (error.msg, error.path, error.lineno, error.colno) == (
            "bad thing",
            "app.yaml",
            3,
            4,
        )
        assert str(error) == "app.yaml:3:4: bad thing"

    def test_str_without_a_position(self):
        assert str(ParseError("bad")) == "bad"
        assert str(ParseError("bad", path="a.json")) == "a.json: bad"
        assert str(ParseError("bad", path="a.ini", lineno=7)) == "a.ini:7: bad"

    def test_defaults_are_none(self):
        error = ParseError("bad")
        assert error.path is None and error.lineno is None and error.colno is None

    def test_pickles(self):
        error = ParseError("bad thing", path="app.yaml", lineno=3, colno=4)
        copy = pickle.loads(pickle.dumps(error))
        assert type(copy) is ParseError
        assert (copy.msg, copy.path, copy.lineno, copy.colno) == (
            "bad thing",
            "app.yaml",
            3,
            4,
        )
        assert str(copy) == str(error)

    @pytest.mark.usefixtures("needs_yaml")
    def test_pickles_with_its_recorded_context(self, tmp_path):
        write(tmp_path, "top.yaml", "a: 1\nchild: !include child.yaml\n")
        write(tmp_path, "child.yaml", "b: [unclosed\n")
        with pytest.raises(ParseError) as caught:
            anycfg.load(str(tmp_path / "top.yaml"))
        copy = pickle.loads(pickle.dumps(caught.value))
        assert copy.config_frames == caught.value.config_frames
        assert str(copy) == str(caught.value)

    @pytest.mark.usefixtures("needs_yaml")
    def test_position_is_one_based(self):
        with pytest.raises(ParseError) as caught:
            anycfg.loads("a: 1\nb: [x\nc: 2\n", format="yaml")
        assert caught.value.lineno >= 2
        with pytest.raises(ParseError) as caught:
            anycfg.loads('{"a": }', format="json")
        assert (caught.value.lineno, caught.value.colno) == (1, 7)

    def test_never_chains_the_parsers_error(self):
        with pytest.raises(ParseError) as caught:
            anycfg.loads('{"a": }', format="json")
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

    def test_decode_failure_names_the_byte_offset(self, tmp_path):
        source = tmp_path / "wide.json"
        source.write_bytes('{"a": 1}'.encode("utf-16"))
        with pytest.raises(ParseError) as caught:
            anycfg.load(str(source))
        assert "byte 0" in str(caught.value)
        assert "encoding" in str(caught.value)
        assert caught.value.path == str(source)
        assert caught.value.__context__ is None


# ---------------------------------------------------------------------------
# Command errors
# ---------------------------------------------------------------------------


class TestCommandError:
    def test_is_a_called_process_error(self):
        error = CommandError(2, "tool --flag", output="PAYLOAD", stderr="it broke\n")
        assert isinstance(error, subprocess.CalledProcessError)
        assert (error.returncode, error.cmd, error.output, error.stderr) == (
            2,
            "tool --flag",
            "PAYLOAD",
            "it broke\n",
        )

    def test_message_carries_stderr_and_never_stdout(self):
        error = CommandError(2, "tool", output="PAYLOAD-" + SECRET, stderr="it broke\n")
        message = str(error)
        assert "returned non-zero exit status 2" in message
        assert "it broke" in message
        assert SECRET not in message

    def test_stderr_tail_is_the_last_twenty_lines(self):
        stderr = "".join(f"line {n}\n" for n in range(50))
        message = str(CommandError(1, "tool", stderr=stderr))
        assert "line 49" in message and "line 30" in message
        assert "line 29" not in message

    def test_stderr_tail_is_capped(self):
        message = str(CommandError(1, "tool", stderr="x" * 10000))
        assert len(message) < 2200
        assert "..." in message

    def test_bytes_stderr_is_decoded(self):
        message = str(CommandError(1, "tool", stderr=b"bytes went wrong\n"))
        assert "bytes went wrong" in message

    def test_no_stderr_no_suffix(self):
        assert "stderr" not in str(CommandError(1, "tool", stderr=""))

    def test_pickles(self):
        error = CommandError(2, "tool", output="out", stderr="err\n")
        copy = pickle.loads(pickle.dumps(error))
        assert type(copy) is CommandError
        assert (copy.returncode, copy.cmd, copy.output, copy.stderr) == (
            2,
            "tool",
            "out",
            "err\n",
        )
        assert str(copy) == str(error)


class TestCommandTimeoutError:
    def test_is_a_timeout_expired(self):
        error = CommandTimeoutError("tool", 1.5, output=b"x", stderr=b"err")
        assert isinstance(error, subprocess.TimeoutExpired)
        assert (error.cmd, error.timeout) == ("tool", 1.5)
        assert "timed out after 1.5 seconds" in str(error)
        assert "err" in str(error)

    def test_pickles(self):
        error = CommandTimeoutError("tool", 1.5, stderr="err")
        copy = pickle.loads(pickle.dumps(error))
        assert type(copy) is CommandTimeoutError
        assert (copy.cmd, copy.timeout) == ("tool", 1.5)
        assert str(copy) == str(error)


class TestCommandFailuresThroughTheLoader:
    def test_non_zero_exit(self):
        # The payload is computed, so the command line itself never holds it.
        command = python_command(
            "import sys; print(str(7 * 191) + 'xyzzy'[::-1]);"
            " sys.stderr.write('boom happened\\n'); sys.exit(3)"
        )
        loader = Loader(policy=Policy(allow_commands=True))
        with pytest.raises(CommandError) as caught:
            loader.load(command)
        error = caught.value
        assert error.returncode == 3
        assert "boom happened" in str(error)
        assert error.output.strip() == "1337yzzyx"
        assert "1337yzzyx" not in str(error)
        assert error.config_source == command

    def test_timeout(self):
        command = python_command("import time; time.sleep(15)")
        loader = Loader(policy=Policy(allow_commands=True, command_timeout=1))
        started = time.perf_counter()
        with pytest.raises(CommandTimeoutError) as caught:
            loader.load(command)
        assert time.perf_counter() - started < 10
        assert isinstance(caught.value, subprocess.TimeoutExpired)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


class TestContextRendering:
    def test_every_part(self):
        error = ConfigValueError("bad value")
        error.config_source = "a.yaml"
        error.config_frames = (
            ErrorFrame("include", "b.yaml", 3),
            ErrorFrame("render", "t.yaml.j2"),
            ErrorFrame("merge", "c.yaml"),
        )
        error.config_key = ("a", "b", 0)
        error.config_model = "Model"
        assert str(error) == (
            "bad value [in a.yaml; included from b.yaml, line 3; rendered from "
            "t.yaml.j2; while merging c.yaml; at a.b[0] (Model)]"
        )

    def test_partial_records(self):
        error = ConfigValueError("bad")
        assert str(error) == "bad"
        error.config_frames = (ErrorFrame("include", "b.yaml"),)
        assert str(error) == "bad [included from b.yaml]"
        error.config_key = ("k",)
        assert str(error) == "bad [included from b.yaml; at k]"
        error.config_key = None
        error.config_model = "Model"
        assert str(error) == "bad [included from b.yaml; (Model)]"

    def test_render_frame_of_the_source_itself(self):
        error = ConfigValueError("bad")
        error.config_source = "t.yaml.j2"
        error.config_frames = (ErrorFrame("render", "t.yaml.j2"),)
        assert str(error) == "bad [in t.yaml.j2; rendered]"

    def test_rendering_follows_the_record(self):
        error = ConfigValueError("bad")
        error.config_key = ("a",)
        assert str(error) == "bad [at a]"
        error.config_key = ("b",)
        assert str(error) == "bad [at b]"
        assert str(error) == str(error)

    @pytest.mark.usefixtures("needs_yaml")
    def test_nested_includes_innermost_first(self, tmp_path):
        top = write(tmp_path, "top.yaml", "a: 1\nmid: !include mid.yaml\n")
        mid = write(tmp_path, "mid.yaml", "x: 1\ny: 2\nleaf: !include leaf.yaml\n")
        leaf = write(tmp_path, "leaf.yaml", "b: [unclosed\n")
        with pytest.raises(ParseError) as caught:
            anycfg.load(str(top))
        error = caught.value
        assert error.config_source == str(leaf)
        assert error.config_frames == (
            ErrorFrame("include", str(mid), 3),
            ErrorFrame("include", str(top), 2),
        )
        assert str(error).endswith(
            f"[included from {mid}, line 3; included from {top}, line 2]"
        )

    @pytest.mark.usefixtures("needs_yaml")
    def test_recorded_once_at_every_level(self, tmp_path):
        write(tmp_path, "top.yaml", "mid: !include mid.yaml\n")
        write(tmp_path, "mid.yaml", "leaf: !include leaf.yaml\n")
        write(tmp_path, "leaf.yaml", "b: [unclosed\n")
        with pytest.raises(ParseError) as caught:
            Loader(base_dir=tmp_path).load("top.yaml")
        message = str(caught.value)
        suffix = message[message.rindex("[") :]
        # The innermost file is the ParseError's own path, so the suffix does
        # not repeat it; each including file appears once.
        assert "leaf.yaml" not in suffix
        assert suffix.count("mid.yaml") == 1
        assert suffix.count("top.yaml") == 1
        assert str(caught.value) == message

    @pytest.mark.usefixtures("needs_yaml")
    def test_same_object_offered_once_per_level(self, tmp_path):
        write(tmp_path, "top.yaml", "mid: !include mid.yaml\n")
        write(tmp_path, "mid.yaml", "leaf: !include leaf.yaml\n")
        write(tmp_path, "leaf.yaml", "b: [unclosed\n")
        events = []

        def record(event):
            events.append((event.phase, str(event.error)))
            return False

        with pytest.raises(ParseError) as caught:
            Loader(base_dir=tmp_path, on_error=record).load("top.yaml")
        assert [phase for phase, _ in events] == ["load", "include", "include"]
        # Each offer re-renders from the record: one more frame, never a copy.
        for _phase, message in events:
            assert message.count("mid.yaml") <= 1
            assert message.count("top.yaml") <= 1
        assert events[-1][1] == str(caught.value)

    @pytest.mark.usefixtures("needs_yaml")
    def test_include_of_another_format_names_the_including_file(self, tmp_path):
        parent = write(tmp_path, "parent.yaml", "secrets: !include secrets.json\n")
        child = write(tmp_path, "secrets.json", '{"a": }\n')
        with pytest.raises(ParseError) as caught:
            anycfg.load(str(parent))
        assert caught.value.path == str(child)
        assert caught.value.config_frames[0] == ErrorFrame("include", str(parent), 1)
        assert f"included from {parent}, line 1" in str(caught.value)

    def test_glob_member_names_itself(self, tmp_path):
        write(tmp_path, "10.json", '{"a": 1}')
        write(tmp_path, "20.json", '{"b": [}')
        with pytest.raises(ParseError) as caught:
            Loader(base_dir=tmp_path).load("*.json")
        assert "20.json" in str(caught.value)

    def test_merge_failure_names_the_source(self, tmp_path):
        write(tmp_path, "a.json", '{"a": 1}')
        write(tmp_path, "b.json", '{"b": 2}')

        def broken(a, b, **options):
            raise TypeError("no merging today")

        with pytest.raises(TypeError) as caught:
            Loader(base_dir=tmp_path, merge=broken).load("a.json", "b.json")
        error = caught.value
        assert type(error) is TypeError
        assert error.config_frames == (ErrorFrame("merge", str(tmp_path / "b.json")),)
        assert str(error) == f"no merging today [while merging {tmp_path / 'b.json'}]"

    def test_key_and_model_from_hydration(self):
        with pytest.raises(ConfigError) as caught:
            Loader().load({"hosts": [{"port": "nope"}]}, into=Service)
        error = caught.value
        assert error.config_key == ("hosts", 0, "port")
        assert error.config_model == "Service"
        assert str(error).endswith("[at hosts[0].port (Service)]")

    def test_type_and_identity_preserved(self, tmp_path):
        write(tmp_path, "bad.json", '{"a": }')
        offered = []

        def record(event):
            offered.append(event.error)
            return False

        with pytest.raises(ParseError) as caught:
            Loader(base_dir=tmp_path, on_error=record).load("bad.json")
        assert offered[-1] is caught.value
        assert type(caught.value) is ParseError


class TestOSErrorIsNotWrapped:
    def test_missing_file(self, tmp_path):
        source = tmp_path / "nope.json"
        with pytest.raises(FileNotFoundError) as caught:
            anycfg.load(str(source))
        error = caught.value
        assert type(error) is FileNotFoundError
        assert not isinstance(error, ConfigError)
        assert error.config_source == str(source)
        assert error.filename == str(source)

    @pytest.mark.usefixtures("needs_yaml")
    def test_missing_include_names_the_including_file(self, tmp_path):
        parent = write(tmp_path, "parent.yaml", "missing: !include nope/x.yaml\n")
        with pytest.raises(FileNotFoundError) as caught:
            anycfg.load(str(parent))
        error = caught.value
        assert type(error) is FileNotFoundError
        assert error.errno is not None
        assert f"included from {parent}, line 1" in str(error)

    @pytest.mark.usefixtures("needs_yaml")
    def test_nested_missing_include_recorded_once(self, tmp_path):
        write(tmp_path, "top.yaml", "mid: !include mid.yaml\n")
        write(tmp_path, "mid.yaml", "x: !include nope.yaml\n")
        with pytest.raises(FileNotFoundError) as caught:
            Loader(base_dir=tmp_path).load("top.yaml")
        message = str(caught.value)
        assert message.count("mid.yaml") == 1
        assert message.count("top.yaml") == 1
        assert str(caught.value) == message


# ---------------------------------------------------------------------------
# One except clause for every load failure
# ---------------------------------------------------------------------------


def _write_and_load(name, text, **settings):
    def run(tmp_path):
        write(tmp_path, name, text)
        return Loader(base_dir=tmp_path, **settings).load(name)

    return run


def _include_cycle(tmp_path):
    write(tmp_path, "a.yaml", "b: !include b.yaml\n")
    write(tmp_path, "b.yaml", "a: !include a.yaml\n")
    return Loader(base_dir=tmp_path).load("a.yaml")


def _confined(tmp_path):
    write(tmp_path, "outside.json", "{}")
    (tmp_path / "inside").mkdir()
    policy = Policy(confine_to=[str(tmp_path / "inside")])
    return Loader(base_dir=tmp_path, policy=policy).load("outside.json")


def _too_many_includes(tmp_path):
    write(tmp_path, "inc.yaml", "v: 1\n")
    write(tmp_path, "app.yaml", "a: !include inc.yaml\nb: !include inc.yaml\n")
    return Loader(base_dir=tmp_path, policy=Policy(max_includes=1)).load("app.yaml")


def _too_deep(tmp_path):
    write(tmp_path, "a.yaml", "b: !include b.yaml\n")
    write(tmp_path, "b.yaml", "c: !include c.yaml\n")
    write(tmp_path, "c.yaml", "v: 1\n")
    return Loader(base_dir=tmp_path, policy=Policy(max_depth=2)).load("a.yaml")


def _directory(tmp_path):
    (tmp_path / "conf.json").mkdir()
    return Loader(base_dir=tmp_path).load("conf.json")


def _utf16(tmp_path):
    (tmp_path / "wide.json").write_bytes('{"a": 1}'.encode("utf-16"))
    return Loader(base_dir=tmp_path).load("wide.json")


def _hydrate(tmp_path):
    return Loader().load({"hosts": [{"port": "not a number"}]}, into=Service)


LOAD_FAILURES = [
    pytest.param(lambda p: Loader(base_dir=p).load("nope.json"), id="missing-file"),
    pytest.param(_directory, id="directory"),
    pytest.param(_write_and_load("a.xyz", "a"), id="unsupported-format"),
    pytest.param(_utf16, id="decode"),
    pytest.param(_write_and_load("a.json", '{"a": }'), id="json"),
    pytest.param(_write_and_load("a.yaml", "a: [x\n"), id="yaml", marks=needs_yaml),
    pytest.param(
        _write_and_load("a.yaml", "a: 1\na: 2\n"), id="yaml-duplicate", marks=needs_yaml
    ),
    pytest.param(
        lambda p: anycfg.load(str(write(p, "a.yaml", "a: 1\n---\nb: 2\n"))),
        id="yaml-multi-document",
        marks=needs_yaml,
    ),
    pytest.param(_write_and_load("a.toml", "a = =\n"), id="toml", marks=needs_toml),
    pytest.param(_write_and_load("a.ini", "k = v\n"), id="ini"),
    pytest.param(
        _write_and_load("a.ini", "[s]\nk = %(nope)s\n"), id="ini-interpolation"
    ),
    pytest.param(_write_and_load("a.env", 'K="open\n'), id="dotenv"),
    pytest.param(_include_cycle, id="include-cycle", marks=needs_yaml),
    pytest.param(
        _write_and_load("a.yaml", "x: !include nope.yaml\n"),
        id="missing-include",
        marks=needs_yaml,
    ),
    pytest.param(
        _write_and_load(
            "a.yaml", "x: !include a.json\n", policy=Policy(allow_includes=False)
        ),
        id="includes-disabled",
        marks=needs_yaml,
    ),
    pytest.param(
        _write_and_load("a.yaml", "x: !include {encoding: utf-8}\n"),
        id="include-without-pathname",
        marks=needs_yaml,
    ),
    pytest.param(_confined, id="confinement"),
    pytest.param(
        _write_and_load(
            "a.json", '{"a": "' + "x" * 100 + '"}', policy=Policy(max_bytes=10)
        ),
        id="max-bytes",
    ),
    pytest.param(_too_many_includes, id="max-includes", marks=needs_yaml),
    pytest.param(_too_deep, id="max-depth", marks=needs_yaml),
    pytest.param(
        lambda p: Loader().load(python_command("print(1)")), id="commands-disabled"
    ),
    pytest.param(
        lambda p: Loader(policy=Policy(allow_commands=True)).load(
            python_command("import sys; sys.exit(3)")
        ),
        id="command-exit",
    ),
    pytest.param(
        lambda p: Loader(policy=Policy(allow_commands=True)).load(
            python_command("print(1)", scheme="cmd")
        ),
        id="command-without-format",
    ),
    pytest.param(
        _write_and_load("a.json.j2", "{}"), id="templates-disabled", marks=needs_jinja2
    ),
    pytest.param(
        _write_and_load("a.json.j2", "{{ 1 +", policy=Policy(allow_templates=True)),
        id="template-render",
        marks=needs_jinja2,
    ),
    pytest.param(
        lambda p: Loader(interpolate=True).load({"a": "{{ 1/0 }}"}),
        id="interpolation",
        marks=needs_jinja2,
    ),
    pytest.param(
        lambda p: Loader(interpolate=True, policy=Policy(strict=True)).load(
            {"a": "{{ b }}", "b": "{{ a }}"}
        ),
        id="interpolation-cycle",
        marks=needs_jinja2,
    ),
    pytest.param(
        lambda p: Loader(interpolate=True).load({"a": 1, "{{ 'a' }}": 2}),
        id="interpolation-key-collision",
        marks=needs_jinja2,
    ),
    pytest.param(lambda p: Loader().load({"a": 1}, flatten=True), id="flatten"),
    pytest.param(
        lambda p: Loader().load(
            sources.Env("P_", nested="__", environ={"P_DB": "1", "P_DB__PORT": "2"})
        ),
        id="env-collision",
    ),
    pytest.param(_hydrate, id="hydrate"),
    pytest.param(
        lambda p: anycfg.loads("not an assignment\n", format="dotenv", strict=True),
        id="dotenv-strict",
    ),
]


@pytest.mark.parametrize("failing_load", LOAD_FAILURES)
def test_one_clause_covers_every_load_failure(tmp_path, failing_load):
    with pytest.raises((ConfigError, OSError)):
        failing_load(tmp_path)


class TestSpecificTypes:
    @pytest.mark.usefixtures("needs_yaml")
    def test_include_cycle_is_a_config_value_error(self, tmp_path):
        with pytest.raises(ConfigValueError, match="include cycle"):
            _include_cycle(tmp_path)

    def test_flatten_member_is_a_config_type_error(self):
        with pytest.raises(ConfigTypeError, match="flatten=True"):
            Loader().load({"a": 1}, flatten=True)

    def test_env_collision_is_a_config_value_error(self):
        source = sources.Env(
            "P_", nested="__", environ={"P_DB": "1", "P_DB__PORT": "2"}
        )
        with pytest.raises(ConfigValueError) as caught:
            Loader().load(source)
        assert "P_DB" in str(caught.value) and "P_DB__PORT" in str(caught.value)

    def test_unsupported_format_lists_the_names_before_opening(self, tmp_path):
        source = tmp_path / "plain.xyz"  # never created
        with pytest.raises(UnsupportedFormatError) as caught:
            anycfg.load(str(source))
        message = str(caught.value)
        assert "plain.xyz" in message
        assert "json" in message and "ini" in message

    @pytest.mark.usefixtures("needs_yaml")
    @pytest.mark.parametrize(
        "text, words",
        [
            ("child: !include {encoding: utf-8}\n", "pathname"),
            ("child: !include []\n", "at least one"),
        ],
    )
    def test_malformed_include_is_a_parse_error_naming_the_line(
        self, tmp_path, text, words
    ):
        parent = write(tmp_path, "parent.yaml", "a: 1\n" + text)
        with pytest.raises(ParseError) as caught:
            anycfg.load(str(parent))
        assert words in str(caught.value)
        assert caught.value.lineno == 2
        assert caught.value.path == str(parent)

    def test_missing_extra_is_a_missing_dependency_error(self, tmp_path):
        source = write(tmp_path, "a.yaml", "a: 1\n")
        package_root = os.path.dirname(
            os.path.dirname(os.path.abspath(anycfg.__file__))
        )
        code = (
            "import sys\n"
            f"sys.path.insert(0, {package_root!r})\n"
            "sys.modules['yaml'] = None\n"
            "import anycfg\n"
            "try:\n"
            f"    anycfg.load({str(source)!r})\n"
            "except (anycfg.ConfigError, OSError) as error:\n"
            "    print(type(error).__name__, isinstance(error, ImportError),\n"
            "          isinstance(error, anycfg.UnsupportedFormatError), error)\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        assert "MissingDependencyError True True" in done.stdout, (
            done.stdout + done.stderr
        )
        assert "anycfg[yaml]" in done.stdout


class TestWrongArgumentsArePlainErrors:
    @pytest.mark.parametrize(
        "call, expected",
        [
            pytest.param(lambda: Loader(collect="bogus"), ValueError, id="collect"),
            pytest.param(
                lambda: Loader(merge="no-such-strategy"), ValueError, id="merge"
            ),
            pytest.param(lambda: Loader(merge=42), TypeError, id="merge-type"),
            pytest.param(lambda: Loader(on_error="yes"), TypeError, id="on_error"),
            pytest.param(lambda: Loader().load(), TypeError, id="no-sources"),
            pytest.param(lambda: Loader().load({"a": 1}, bogus=1), TypeError, id="kw"),
            pytest.param(lambda: anycfg.load(None), TypeError, id="load-none"),
            pytest.param(lambda: anycfg.load(""), ValueError, id="load-empty"),
            pytest.param(lambda: anycfg.load(b"{}"), TypeError, id="load-bytes"),
        ],
    )
    def test_not_config_errors(self, call, expected):
        with pytest.raises(expected) as caught:
            call()
        assert not isinstance(caught.value, ConfigError)


# ---------------------------------------------------------------------------
# No document text in a parse error
# ---------------------------------------------------------------------------

MALFORMED = [
    pytest.param("json", '{"k": "{secret}", }', id="json-trailing-comma"),
    pytest.param("json", '{"k": {secret}}', id="json-bare-word"),
    pytest.param(
        "yaml", "k: {secret}\n  bad: [x\n", id="yaml-mapping", marks=needs_yaml
    ),
    pytest.param("yaml", "k: [{secret}\n", id="yaml-flow", marks=needs_yaml),
    pytest.param("yaml", "k: 1\nk: {secret}\n", id="yaml-duplicate", marks=needs_yaml),
    pytest.param("yaml", "k: \x07{secret}\n", id="yaml-reader", marks=needs_yaml),
    pytest.param("toml", "k = {secret}\n", id="toml-value", marks=needs_toml),
    pytest.param(
        "toml", 'k = "x"\nk = "{secret}"\n', id="toml-duplicate", marks=needs_toml
    ),
    pytest.param("ini", "{secret} = 1\n", id="ini-no-section"),
    pytest.param("ini", "[s]\nk = 1\nk = {secret}\n", id="ini-duplicate-option"),
    pytest.param("ini", "[s]\nk = %({secret})s\n", id="ini-interpolation"),
    pytest.param("dotenv", 'K="{secret}\n', id="dotenv-unterminated"),
]


@pytest.mark.parametrize("format_name, template", MALFORMED)
def test_no_document_text_in_a_parse_error(tmp_path, caplog, format_name, template):
    text = template.replace("{secret}", SECRET)
    suffix = {"dotenv": ".env"}.get(format_name, "." + format_name)
    source = write(tmp_path, "doc" + suffix, text)
    caplog.set_level(logging.DEBUG)
    for attempt in (
        lambda: anycfg.loads(text, format=format_name),
        lambda: anycfg.load(str(source)),
    ):
        with pytest.raises(ParseError) as caught:
            attempt()
        error = caught.value
        assert error.__cause__ is None and error.__context__ is None
        assert not [said for said in everything_said(error) if SECRET in said]
    # A skipped failure is logged; the log must not quote the document either.
    assert Loader(base_dir=tmp_path, on_error=True).load("doc" + suffix) is None
    assert not [r for r in caplog.records if SECRET in r.getMessage()]


def test_no_document_text_from_a_strict_dotenv_line(caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ParseError) as caught:
        anycfg.loads(f"{SECRET}\n", format="dotenv", strict=True)
    assert caught.value.lineno == 1
    assert not [said for said in everything_said(caught.value) if SECRET in said]
    anycfg.loads(f"{SECRET}\n", format="dotenv")
    assert not [r for r in caplog.records if SECRET in r.getMessage()]


@pytest.mark.usefixtures("needs_yaml")
def test_no_document_text_from_an_included_document(tmp_path):
    write(tmp_path, "app.yaml", "x: !include child.json\n")
    write(tmp_path, "child.json", '{"k": ' + SECRET + "}")
    with pytest.raises(ParseError) as caught:
        Loader(base_dir=tmp_path).load("app.yaml")
    assert not [said for said in everything_said(caught.value) if SECRET in said]
