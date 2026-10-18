"""`Policy`: the trust posture of a load, and every gate it controls."""

from __future__ import annotations

import dataclasses
import logging
import sys
import time

import pytest

import anycfg
from anycfg import (
    BudgetExceededError,
    CommandsDisabledError,
    CommandTimeoutError,
    ConfigError,
    ConfigValueError,
    ConfinementError,
    Loader,
    Policy,
    PolicyError,
    TemplateError,
    TemplatesDisabledError,
    formats,
    sources,
)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _marker_command(tmp_path, output="{}"):
    """``(cmd+json source, marker)``: the program touches *marker* when it runs."""
    marker = tmp_path / "ran.marker"
    script = _write(
        tmp_path / "mk.py",
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).touch()\n"
        f"print({output!r})\n",
    )
    return f'cmd+json://"{sys.executable}" "{script}"', marker


# ---------------------------------------------------------------------------
# The value
# ---------------------------------------------------------------------------


class TestPolicyValue:
    def test_defaults_are_the_untrusted_posture(self):
        policy = Policy()
        assert policy.allow_commands is False
        assert policy.allow_templates is False
        assert policy.allow_includes is True
        assert policy.sandbox is True
        assert policy.strict is False
        assert policy.confine_to is None
        assert policy.max_includes == 1000
        assert policy.max_depth == 64
        assert policy.max_bytes == 64 * 1024 * 1024
        assert policy.command_timeout == 60.0

    def test_trusted(self):
        policy = Policy.trusted()
        assert policy.allow_commands is True
        assert policy.allow_templates is True
        assert policy.sandbox is False
        # Everything else stays at the default.
        assert (
            policy.replace(allow_commands=False, allow_templates=False, sandbox=True)
            == Policy()
        )

    def test_trusted_with_changes(self):
        policy = Policy.trusted(sandbox=True, max_depth=3)
        assert policy.allow_commands is True
        assert policy.sandbox is True
        assert policy.max_depth == 3

    def test_untrusted(self):
        assert Policy.untrusted() == Policy()
        assert Policy.untrusted(max_depth=3) == Policy(max_depth=3)

    def test_replace_returns_a_copy(self):
        policy = Policy()
        changed = policy.replace(allow_commands=True)
        assert changed.allow_commands is True
        assert policy.allow_commands is False
        assert changed is not policy

    def test_frozen(self):
        policy = Policy()
        with pytest.raises(dataclasses.FrozenInstanceError):
            policy.allow_commands = True  # type: ignore[misc]

    def test_unknown_field_is_a_type_error(self):
        with pytest.raises(TypeError):
            Policy(allow_everything=True)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            Policy().replace(allow_everything=True)

    def test_loader_policy(self):
        assert Loader().policy == Policy()
        trusted = Policy.trusted()
        assert Loader(policy=trusted).policy is trusted
        with pytest.raises(TypeError):
            Loader(policy={"allow_commands": True})  # type: ignore[arg-type]

    def test_per_call_policy_does_not_change_the_loader(self, tmp_path):
        command, _marker = _marker_command(tmp_path)
        loader = Loader()
        assert loader.load(command, policy=Policy(allow_commands=True)) == {}
        assert loader.policy == Policy()
        with pytest.raises(CommandsDisabledError):
            loader.load(command)

    def test_policy_errors_are_permission_errors(self):
        for cls in (
            CommandsDisabledError,
            TemplatesDisabledError,
            ConfinementError,
            BudgetExceededError,
        ):
            assert issubclass(cls, PolicyError)
        assert issubclass(PolicyError, PermissionError)
        assert issubclass(PolicyError, ConfigError)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


class TestAllowCommands:
    def test_top_level_command_refused(self, tmp_path):
        command, marker = _marker_command(tmp_path)
        with pytest.raises(CommandsDisabledError):
            Loader().load(command)
        assert not marker.exists()

    @pytest.mark.usefixtures("needs_yaml")
    def test_included_command_refused(self, tmp_path):
        command, marker = _marker_command(tmp_path)
        doc = _write(tmp_path / "main.yaml", f"app: !include '{command}'\n")
        with pytest.raises(CommandsDisabledError):
            Loader().load(str(doc))
        assert not marker.exists()

    @pytest.mark.usefixtures("needs_yaml")
    def test_per_call_policy_reaches_includes(self, tmp_path):
        command, marker = _marker_command(tmp_path)
        doc = _write(tmp_path / "main.yaml", f"app: !include '{command}'\n")
        loader = Loader(policy=Policy(allow_commands=True))
        with pytest.raises(CommandsDisabledError):
            loader.load(str(doc), policy=Policy())
        with pytest.raises(CommandsDisabledError):
            list(loader.load_all(str(doc), policy=Policy()))
        assert not marker.exists()
        assert loader.load(str(doc)) == {"app": {}}
        assert marker.exists()


class TestAllowTemplates:
    def test_template_file_refused_by_default(self, tmp_path):
        path = _write(tmp_path / "app.yaml.j2", "a: {{ 1 + 1 }}\n")
        with pytest.raises(TemplatesDisabledError) as caught:
            Loader().load(str(path))
        assert isinstance(caught.value, PermissionError)
        assert "allow_templates" in str(caught.value)

    @pytest.mark.usefixtures("needs_yaml")
    def test_included_template_refused_by_default(self, tmp_path):
        _write(tmp_path / "part.yaml.j2", "a: {{ 1 + 1 }}\n")
        doc = _write(tmp_path / "main.yaml", "x: !include part.yaml.j2\n")
        with pytest.raises(TemplatesDisabledError):
            Loader().load(str(doc))

    @pytest.mark.usefixtures("needs_yaml", "needs_jinja2")
    def test_template_renders_when_allowed(self, tmp_path):
        path = _write(tmp_path / "app.yaml.j2", "a: {{ 1 + 1 }}\n")
        assert Loader(policy=Policy(allow_templates=True)).load(str(path)) == {"a": 2}
        assert Loader(policy=Policy.trusted()).load(str(path)) == {"a": 2}


class TestAllowIncludes:
    @pytest.mark.usefixtures("needs_yaml")
    def test_include_refused(self, tmp_path):
        _write(tmp_path / "b.yaml", "b: 1\n")
        doc = _write(tmp_path / "a.yaml", "x: !include b.yaml\n")
        with pytest.raises(PolicyError) as caught:
            Loader(policy=Policy(allow_includes=False)).load(str(doc))
        assert type(caught.value) is PolicyError

    @pytest.mark.usefixtures("needs_yaml")
    def test_document_without_includes_still_loads(self, tmp_path):
        doc = _write(tmp_path / "a.yaml", "x: 1\n")
        assert Loader(policy=Policy(allow_includes=False)).load(str(doc)) == {"x": 1}


class TestSandbox:
    SSTI = "{{ ''.__class__.__mro__ }}"

    @pytest.mark.usefixtures("needs_jinja2")
    def test_interpolation_is_sandboxed_by_default(self):
        with pytest.raises(TemplateError):
            Loader(interpolate=True).load({"x": self.SSTI})

    @pytest.mark.usefixtures("needs_jinja2")
    def test_unsandboxed_when_the_policy_says_so(self):
        result = Loader(interpolate=True, policy=Policy(sandbox=False)).load(
            {"x": self.SSTI}
        )
        assert result["x"] is not None

    @pytest.mark.usefixtures("needs_jinja2")
    def test_strict_raises_on_an_undefined_name(self):
        data = {"x": "a{{ nowhere }}b"}
        assert Loader(interpolate=True).load(data) == {"x": "ab"}
        with pytest.raises(TemplateError):
            Loader(interpolate=True, policy=Policy(strict=True)).load(data)


@pytest.mark.usefixtures("needs_yaml")
class TestBudget:
    """``max_includes``, ``max_depth`` and ``max_bytes``: never offered to on_error."""

    @staticmethod
    def _chain(tmp_path):
        _write(tmp_path / "a.yaml", "x: !include b.yaml\n")
        _write(tmp_path / "b.yaml", "y: !include c.yaml\n")
        _write(tmp_path / "c.yaml", "z: 1\n")
        return Loader(base_dir=tmp_path)

    def test_max_depth(self, tmp_path):
        loader = self._chain(tmp_path)
        expected = {"x": {"y": {"z": 1}}}
        assert loader.load("a.yaml", policy=Policy(max_depth=3)) == expected
        with pytest.raises(BudgetExceededError) as caught:
            loader.load("a.yaml", policy=Policy(max_depth=2))
        assert "max_depth=2" in str(caught.value)

    def test_max_includes(self, tmp_path):
        loader = self._chain(tmp_path)
        assert loader.load("a.yaml", policy=Policy(max_includes=2))
        with pytest.raises(BudgetExceededError) as caught:
            loader.load("a.yaml", policy=Policy(max_includes=1))
        assert "max_includes=1" in str(caught.value)

    def test_max_includes_counts_siblings(self, tmp_path):
        _write(tmp_path / "c.yaml", "z: 1\n")
        _write(tmp_path / "s.yaml", "a: !include c.yaml\nb: !include c.yaml\n")
        loader = Loader(base_dir=tmp_path)
        assert loader.load("s.yaml", policy=Policy(max_includes=2))
        with pytest.raises(BudgetExceededError):
            loader.load("s.yaml", policy=Policy(max_includes=1))

    def test_the_budget_is_per_load(self, tmp_path):
        self._chain(tmp_path)
        loader = Loader(base_dir=tmp_path, policy=Policy(max_includes=2))
        for _ in range(3):
            assert loader.load("a.yaml") == {"x": {"y": {"z": 1}}}

    def test_max_bytes_one_source(self, tmp_path):
        _write(tmp_path / "big.yaml", "a: " + "x" * 100 + "\n")
        with pytest.raises(BudgetExceededError) as caught:
            Loader(base_dir=tmp_path, policy=Policy(max_bytes=50)).load("big.yaml")
        assert "max_bytes=50" in str(caught.value)

    def test_max_bytes_counts_every_source_together(self, tmp_path):
        _write(tmp_path / "one.yaml", "a: " + "x" * 30 + "\n")
        _write(tmp_path / "two.yaml", "b: " + "x" * 30 + "\n")
        loader = Loader(base_dir=tmp_path, policy=Policy(max_bytes=50))
        assert loader.load("one.yaml")
        assert loader.load("two.yaml")
        with pytest.raises(BudgetExceededError):
            loader.load("one.yaml", "two.yaml")

    def test_max_bytes_counts_includes(self, tmp_path):
        _write(tmp_path / "inc.yaml", "b: " + "x" * 60 + "\n")
        _write(tmp_path / "top.yaml", "a: !include inc.yaml\n")
        with pytest.raises(BudgetExceededError):
            Loader(base_dir=tmp_path, policy=Policy(max_bytes=50)).load("top.yaml")

    def test_max_bytes_counts_in_memory_text(self):
        source = sources.Text('{"a": "' + "x" * 100 + '"}', format="json")
        with pytest.raises(BudgetExceededError):
            Loader(policy=Policy(max_bytes=50)).load(source)

    def test_max_bytes_counts_command_output(self, tmp_path):
        command, _marker = _marker_command(tmp_path, '{"a": "' + "x" * 100 + '"}')
        policy = Policy(allow_commands=True, max_bytes=50)
        with pytest.raises(BudgetExceededError):
            Loader(policy=policy).load(command)

    @pytest.mark.parametrize(
        "policy",
        [Policy(max_includes=1), Policy(max_depth=2), Policy(max_bytes=20)],
        ids=["includes", "depth", "bytes"],
    )
    def test_never_offered_to_on_error(self, tmp_path, policy):
        loader = self._chain(tmp_path)
        with pytest.raises(BudgetExceededError):
            loader.load("a.yaml", policy=policy, on_error=True)
        events = []
        with pytest.raises(BudgetExceededError):
            loader.load(
                "a.yaml",
                policy=policy,
                on_error=lambda event: events.append(event) or True,
            )
        assert not [e for e in events if isinstance(e.error, BudgetExceededError)]


class TestCommandTimeout:
    def test_policy_timeout_applies(self, tmp_path):
        script = _write(tmp_path / "sleep.py", "import time\ntime.sleep(30)\n")
        policy = Policy(allow_commands=True, command_timeout=1)
        started = time.perf_counter()
        with pytest.raises(CommandTimeoutError):
            Loader(policy=policy).load(f'cmd+json://"{sys.executable}" "{script}"')
        assert time.perf_counter() - started < 15


# ---------------------------------------------------------------------------
# A document cannot change the policy
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("needs_yaml")
class TestDocumentCannotChangePolicy:
    def test_policy_keys_in_an_include_mapping_are_dropped(self, tmp_path, caplog):
        _write(tmp_path / "child.yaml", "a: 1\n")
        doc = _write(
            tmp_path / "u.yaml",
            "x: !include {pathname: child.yaml, allow_commands: true, "
            "sandbox: false, policy: {allow_commands: true}, allow_templates: true, "
            "confine_to: false, max_depth: 1000, loader: json}\n",
        )
        with caplog.at_level(logging.WARNING):
            assert Loader().load(str(doc)) == {"x": {"a": 1}}
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= 30]
        text = " ".join(warnings)
        for name in (
            "allow_commands",
            "sandbox",
            "policy",
            "allow_templates",
            "confine_to",
            "max_depth",
            "loader",
        ):
            assert name in text

    def test_mapping_cannot_enable_commands(self, tmp_path):
        command, marker = _marker_command(tmp_path)
        doc = _write(
            tmp_path / "u.yaml",
            f"x: !include {{pathname: '{command}', allow_commands: true, "
            "policy: {allow_commands: true}}\n",
        )
        with pytest.raises(CommandsDisabledError):
            Loader().load(str(doc))
        assert not marker.exists()

    def test_mapping_cannot_enable_templates(self, tmp_path):
        _write(tmp_path / "part.yaml.j2", "a: {{ 1 + 1 }}\n")
        doc = _write(
            tmp_path / "u.yaml",
            "x: !include {pathname: part.yaml.j2, allow_templates: true}\n",
        )
        with pytest.raises(TemplatesDisabledError):
            Loader().load(str(doc))

    def test_mapping_cannot_lift_confinement(self, tmp_path):
        conf = tmp_path / "conf"
        secret = _write(tmp_path / "secret.yaml", "secret: 1\n")
        doc = _write(
            conf / "u.yaml",
            f"x: !include {{pathname: '{secret.as_posix()}', confine_to: false}}\n",
        )
        loader = Loader(policy=Policy(confine_to=[str(conf)]))
        with pytest.raises(ConfinementError):
            loader.load(str(doc))

    def test_mapping_cannot_raise_the_budget(self, tmp_path):
        _write(tmp_path / "c.yaml", "z: 1\n")
        _write(tmp_path / "b.yaml", "y: !include {pathname: c.yaml, max_depth: 100}\n")
        doc = _write(tmp_path / "a.yaml", "x: !include b.yaml\n")
        with pytest.raises(BudgetExceededError):
            Loader(policy=Policy(max_depth=2)).load(str(doc))

    def test_allowed_options_still_apply(self, tmp_path):
        _write(tmp_path / "data.txt", '{"a": 1}')
        doc = _write(
            tmp_path / "u.yaml",
            "x: !include {pathname: data.txt, format: json}\n"
            "y: !include {pathname: missing/*.yaml, default: {d: 1}}\n",
        )
        assert Loader().load(str(doc)) == {"x": {"a": 1}, "y": {"d": 1}}


# ---------------------------------------------------------------------------
# Nested loads run under the reaching load's policy
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("needs_yaml")
class TestPolicyReachesNestedLoads:
    @staticmethod
    def _emitting(tmp_path, text):
        script = _write(
            tmp_path / "emit.py", f"import sys\nsys.stdout.write({text!r})\n"
        )
        return f'cmd+yaml://"{sys.executable}" "{script}"'

    def test_command_output_includes_obey_allow_includes(self, tmp_path):
        _write(tmp_path / "sub.yaml", "a: 1\n")
        source = self._emitting(tmp_path, "x: !include sub.yaml\n")
        allowed = Loader(base_dir=tmp_path, policy=Policy(allow_commands=True))
        assert allowed.load(source) == {"x": {"a": 1}}
        refused = Loader(
            base_dir=tmp_path,
            policy=Policy(allow_commands=True, allow_includes=False),
        )
        with pytest.raises(PolicyError):
            refused.load(source)

    def test_command_output_includes_obey_the_budget(self, tmp_path):
        _write(tmp_path / "c.yaml", "z: 1\n")
        _write(tmp_path / "b.yaml", "y: !include c.yaml\n")
        source = self._emitting(tmp_path, "x: !include b.yaml\n")
        policy = Policy(allow_commands=True, max_includes=1)
        with pytest.raises(BudgetExceededError):
            Loader(base_dir=tmp_path, policy=policy).load(source)

    def test_command_output_includes_obey_allow_templates(self, tmp_path):
        _write(tmp_path / "part.yaml.j2", "a: {{ 1 + 1 }}\n")
        source = self._emitting(tmp_path, "x: !include part.yaml.j2\n")
        policy = Policy(allow_commands=True)
        with pytest.raises(TemplatesDisabledError):
            Loader(base_dir=tmp_path, policy=policy).load(source)

    def test_nested_include_obeys_allow_commands(self, tmp_path):
        command, marker = _marker_command(tmp_path)
        _write(tmp_path / "b.yaml", f"y: !include '{command}'\n")
        doc = _write(tmp_path / "a.yaml", "x: !include b.yaml\n")
        with pytest.raises(CommandsDisabledError):
            Loader().load(str(doc))
        assert not marker.exists()


# ---------------------------------------------------------------------------
# Include cycles
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("needs_yaml")
class TestIncludeCycles:
    def test_two_file_cycle(self, tmp_path):
        _write(tmp_path / "a.yaml", "x: !include b.yaml\n")
        _write(tmp_path / "b.yaml", "y: !include a.yaml\n")
        with pytest.raises(ConfigValueError) as caught:
            Loader(base_dir=tmp_path).load("a.yaml")
        message = str(caught.value)
        assert "include cycle" in message
        assert "a.yaml" in message and "b.yaml" in message
        assert " -> " in message

    def test_cycle_through_dotdot_is_detected(self, tmp_path):
        # The canonical path is the identity, so a respelling cannot dodge it.
        doc = _write(tmp_path / "conf" / "self.yaml", "x: !include ../conf/self.yaml\n")
        with pytest.raises(ConfigValueError, match="include cycle"):
            Loader().load(str(doc))

    def test_cycle_through_dot_and_case_is_detected(self, tmp_path):
        if sys.platform == "win32":
            target = "./SELF.yaml"
        else:
            target = "./sub/../self.yaml"
            (tmp_path / "conf" / "sub").mkdir(parents=True)
        doc = _write(tmp_path / "conf" / "self.yaml", f"x: !include '{target}'\n")
        with pytest.raises(ConfigValueError, match="include cycle"):
            Loader().load(str(doc))

    def test_self_include_through_an_absolute_path(self, tmp_path):
        doc = tmp_path / "self.yaml"
        _write(doc, f"x: !include '{doc.as_posix()}'\n")
        with pytest.raises(ConfigValueError, match="include cycle"):
            Loader().load(str(doc))

    def test_same_file_twice_is_not_a_cycle(self, tmp_path):
        _write(tmp_path / "c.yaml", "z: 1\n")
        doc = _write(tmp_path / "s.yaml", "a: !include c.yaml\nb: !include c.yaml\n")
        assert Loader().load(str(doc)) == {"a": {"z": 1}, "b": {"z": 1}}

    def test_same_relative_name_at_each_depth_is_not_a_cycle(self, tmp_path):
        _write(tmp_path / "x.yaml", "top: !include sub/x.yaml\n")
        _write(tmp_path / "sub" / "x.yaml", "mid: !include sub/x.yaml\n")
        _write(tmp_path / "sub" / "sub" / "x.yaml", "leaf: 1\n")
        assert Loader().load(str(tmp_path / "x.yaml")) == {"top": {"mid": {"leaf": 1}}}


# ---------------------------------------------------------------------------
# Robustness against hostile documents
# ---------------------------------------------------------------------------


class TestRobustness:
    @pytest.mark.usefixtures("needs_yaml")
    def test_deep_merge_of_an_alias_bomb_is_fast(self, tmp_path):
        # 9**7 references to one small list: a merge walking every reference
        # instead of every distinct node would not finish.
        lines = ['l0: &l0 ["a", "b", [1, 2], {"k": "v"}]']
        for level in range(1, 8):
            lines.append(
                f"l{level}: &l{level} [" + ", ".join([f"*l{level - 1}"] * 9) + "]"
            )
        lines.append("top: *l7")
        text = "\n".join(lines) + "\n"
        _write(tmp_path / "one.yaml", text)
        _write(tmp_path / "two.yaml", text)
        loader = Loader(base_dir=tmp_path, merge="deep")
        for options in ({}, {"mergelists": True}):
            started = time.perf_counter()
            result = loader.load("one.yaml", "two.yaml", merge_options=options)
            elapsed = time.perf_counter() - started
            assert elapsed < 1, f"deep merge took {elapsed:.2f}s"
        assert result["top"][0] is result["top"][1]

    def test_deep_merge_of_wide_lists_is_fast(self):
        a = {"l": [[i] for i in range(20000)] + [{"k": i} for i in range(5000)]}
        b = {"l": [[i] for i in range(10000, 30000)]}
        started = time.perf_counter()
        result = Loader(merge="deep").load(a, b)
        elapsed = time.perf_counter() - started
        assert len(result["l"]) == 35000
        assert elapsed < 5, f"deep merge took {elapsed:.2f}s"

    def test_long_dotted_file_name_resolves_fast(self):
        name = ".env" * 64000 + ".json"
        started = time.perf_counter()
        found = formats.find_for_filename(name)
        elapsed = time.perf_counter() - started
        assert found is formats.get("json")
        assert elapsed < 0.5, f"format lookup took {elapsed:.2f}s"

    def test_long_dotted_file_name_resolves_correctly(self):
        assert formats.find_for_filename(".env" * 2000 + ".json") is formats.get("json")


class TestTextCodecsOnly:
    """A bytes-to-bytes codec would inflate input past what ``max_bytes`` counted."""

    @pytest.mark.parametrize("codec", ["zlib_codec", "base64_codec", "hex_codec"])
    def test_loads_refuses_a_non_text_codec(self, codec):
        with pytest.raises(anycfg.ConfigValueError, match="not a text encoding"):
            anycfg.loads(b"{}", format="json", encoding=codec)

    def test_loader_refuses_a_non_text_codec(self):
        with pytest.raises(anycfg.ConfigValueError, match="not a text encoding"):
            anycfg.Loader(encoding="zlib_codec")

    @pytest.mark.usefixtures("needs_yaml")
    def test_an_include_cannot_name_a_decompressing_codec(self, tmp_path):
        import zlib

        (tmp_path / "bomb.json").write_bytes(
            zlib.compress(b"[" + b"0," * 100000 + b"0]")
        )
        (tmp_path / "app.yaml").write_text(
            "x: !include {pathname: bomb.json, encoding: zlib_codec}\n",
            encoding="utf-8",
        )
        loader = anycfg.Loader(base_dir=tmp_path, policy=Policy(max_bytes=4096))
        with pytest.raises(anycfg.ConfigValueError, match="not a text encoding"):
            loader.load("app.yaml")

    def test_unknown_codec(self):
        with pytest.raises(anycfg.ConfigValueError, match="unknown encoding"):
            anycfg.loads(b"{}", format="json", encoding="no-such-codec")


class TestBudgetStopsTheRead:
    """``max_bytes`` stops a read one chunk past the budget, not after it."""

    def test_an_io_stream_is_read_in_chunks(self):
        import io

        class Counting(io.BytesIO):
            served = 0

            def read(self, size=-1):
                data = super().read(size)
                Counting.served += len(data)
                return data

        stream = Counting(b"[" + b"0," * 5_000_000 + b"0]")
        with pytest.raises(BudgetExceededError):
            anycfg.Loader(policy=Policy(max_bytes=100_000)).load(stream, format="json")
        assert Counting.served < 300_000

    def test_a_file_is_read_in_chunks(self, tmp_path, monkeypatch):
        from pathapi import LocalPath

        (tmp_path / "big.json").write_bytes(b"[" + b"0," * 5_000_000 + b"0]")
        served = []
        real_open = LocalPath.open

        def spying_open(self, *args, **kwargs):
            handle = real_open(self, *args, **kwargs)
            real_read = handle.read

            class Spy:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    handle.close()

                def read(self, size=-1):
                    data = real_read(size)
                    served.append(len(data))
                    return data

            return Spy()

        monkeypatch.setattr(LocalPath, "open", spying_open)
        loader = anycfg.Loader(base_dir=tmp_path, policy=Policy(max_bytes=100_000))
        with pytest.raises(BudgetExceededError):
            loader.load("big.json")
        assert sum(served) < 300_000
