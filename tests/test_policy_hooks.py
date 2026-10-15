"""Subclassing `Policy`: ``check_path``, ``check_command`` and ``check_template``."""

from __future__ import annotations

import dataclasses
import sys

import pytest
from pathapi import LocalPath

from yaconfiglib import (
    CommandsDisabledError,
    ConfinementError,
    Loader,
    Policy,
    TemplatesDisabledError,
)
from yaconfiglib.sources import Command


def _json(path, text='{"a": 1}'):
    path.write_text(text, encoding="utf-8")
    return path


class Recording(Policy):
    seen: list = []

    def check_path(self, path):
        Recording.seen.append(path)
        return "hidden" not in path.name


@dataclasses.dataclass(frozen=True)
class Allowlist(Policy):
    commands: tuple = ()

    def check_command(self, command):
        return command in self.commands


class NoMustache(Policy):
    def check_template(self, name, engine):
        return engine.__name__ != "MustacheTemplate"


@pytest.fixture(autouse=True)
def _clear():
    Recording.seen = []


class TestCheckPath:
    def test_sees_the_path_object_read(self, tmp_path):
        _json(tmp_path / "a.json")
        assert Loader(base_dir=tmp_path, policy=Recording()).load("a.json") == {"a": 1}
        assert len(Recording.seen) == 1 and isinstance(Recording.seen[0], LocalPath)
        assert str(Recording.seen[0]) == str(tmp_path / "a.json")

    def test_a_refusal_is_a_confinement_error_offered_to_on_error(self, tmp_path):
        _json(tmp_path / "hidden.json")
        _json(tmp_path / "b.json", '{"b": 2}')
        with pytest.raises(ConfinementError, match="the policy refused it"):
            Loader(base_dir=tmp_path, policy=Recording()).load("hidden.json")
        loader = Loader(base_dir=tmp_path, policy=Recording(), on_error=True)
        assert loader.load("hidden.json", "b.json") == {"b": 2}

    @pytest.mark.usefixtures("needs_yaml")
    def test_includes_are_checked(self, tmp_path):
        _json(tmp_path / "hidden.json")
        (tmp_path / "app.yaml").write_text(
            "x: !include hidden.json\n", encoding="utf-8"
        )
        with pytest.raises(ConfinementError):
            Loader(base_dir=tmp_path, policy=Recording()).load("app.yaml")

    def test_a_pattern_and_its_matches_are_checked(self, tmp_path):
        (tmp_path / "conf").mkdir()
        _json(tmp_path / "conf" / "a.json")
        _json(tmp_path / "conf" / "hidden-name.json", '{"h": 1}')
        loader = Loader(base_dir=tmp_path, policy=Recording(), on_error=True)
        assert loader.load("conf/*.json") == {"a": 1}
        names = [path.name for path in Recording.seen]
        assert "*.json" in names and "a.json" in names
        with pytest.raises(ConfinementError) as caught:
            Loader(base_dir=tmp_path, policy=Recording()).load("conf/*.json")
        assert "hidden-name" not in str(caught.value)

    def test_presets_and_replace_keep_the_subclass(self):
        assert type(Recording.trusted()) is Recording
        assert type(Recording.untrusted()) is Recording
        assert type(Recording().replace(strict=True)) is Recording


class TestCheckCommand:
    command = f'"{sys.executable}" -c "print(1)"'

    def test_narrows_allow_commands(self):
        allowed = Allowlist(allow_commands=True, commands=(self.command,))
        assert Loader(policy=allowed).load(Command(self.command, format="json")) == 1
        refused = Allowlist(allow_commands=True, commands=())
        with pytest.raises(CommandsDisabledError, match="refused"):
            Loader(policy=refused).load(Command(self.command, format="json"))

    def test_cannot_widen_allow_commands(self):
        class Yes(Policy):
            def check_command(self, command):
                return True

        with pytest.raises(CommandsDisabledError, match="does not allow"):
            Loader(policy=Yes()).load(Command(self.command, format="json"))


@pytest.mark.usefixtures("needs_yaml", "needs_tplx")
class TestCheckTemplate:
    def test_narrows_allow_templates(self, tmp_path):
        (tmp_path / "a.yaml.j2").write_text("a: {{ 1 + 1 }}\n", encoding="utf-8")
        (tmp_path / "b.yaml.mustache").write_text("b: 1\n", encoding="utf-8")
        loader = Loader(base_dir=tmp_path, policy=NoMustache(allow_templates=True))
        assert loader.load("a.yaml.j2") == {"a": 2}
        with pytest.raises(TemplatesDisabledError, match="refused"):
            loader.load("b.yaml.mustache")


class TestMixins:
    def test_local_paths_only(self, tmp_path):
        from pathapi.mempath import MemPath

        from yaconfiglib import policies

        _json(tmp_path / "a.json")
        policy = policies.LocalPathsOnly()
        assert Loader(base_dir=tmp_path, policy=policy).load("a.json") == {"a": 1}
        assert policy.check_path(MemPath("/x.json"))

        class Remote:
            name = "x.json"

        assert not policy.check_path(Remote())

    def test_stacked_mixins_must_all_agree(self, tmp_path):
        from yaconfiglib import policies

        class Strict(policies.LocalPathsOnly, policies.AllowedSuffixes):
            suffixes = {".JSON"}

        _json(tmp_path / "a.json")
        _json(tmp_path / "b.txt")
        loader = Loader(base_dir=tmp_path, policy=Strict())
        assert loader.load("a.json") == {"a": 1}
        with pytest.raises(ConfinementError):
            loader.load("b.txt", format="json")

    def test_compose_builds_the_same_class(self, tmp_path):
        from yaconfiglib import policies

        Composed = policies.compose(
            policies.AllowedSuffixes, policies.AllowedCommands, suffixes={".json"}
        )
        assert issubclass(Composed, policies.AllowedSuffixes)
        assert Composed.suffixes == {".json"}
        assert type(Composed().replace(strict=True)) is Composed
        _json(tmp_path / "a.json")
        assert Loader(base_dir=tmp_path, policy=Composed()).load("a.json") == {"a": 1}

    def test_compose_refuses_unknown_settings_and_non_policies(self):
        from yaconfiglib import policies

        with pytest.raises(TypeError, match="unknown setting"):
            policies.compose(policies.AllowedSuffixes, commands={"x"})
        with pytest.raises(TypeError, match="Policy subclasses"):
            policies.compose(object)

    def test_allowed_commands(self):
        from yaconfiglib import policies

        command = f'"{sys.executable}" -c "print(2)"'
        Only = policies.compose(policies.AllowedCommands, commands={command})
        assert (
            Loader(policy=Only(allow_commands=True)).load(
                Command(command, format="json")
            )
            == 2
        )
        with pytest.raises(CommandsDisabledError):
            Loader(policy=Only(allow_commands=True)).load(
                Command(command + " ", format="json")
            )

    @pytest.mark.usefixtures("needs_yaml", "needs_tplx")
    def test_allowed_engines(self, tmp_path):
        from yaconfiglib import policies

        (tmp_path / "a.yaml.j2").write_text("a: {{ 2 }}\n", encoding="utf-8")
        (tmp_path / "b.yaml.liquid").write_text("b: 1\n", encoding="utf-8")
        policy = policies.AllowedEngines(allow_templates=True)
        loader = Loader(base_dir=tmp_path, policy=policy)
        assert loader.load("a.yaml.j2") == {"a": 2}
        with pytest.raises(TemplatesDisabledError):
            loader.load("b.yaml.liquid")


class TestMixinList:
    def test_a_list_of_mixins_is_applied_to_the_default_policy(self, tmp_path):
        from yaconfiglib import policies

        _json(tmp_path / "a.json")
        _json(tmp_path / "b.txt")
        loader = Loader(
            base_dir=tmp_path,
            policy=[
                policies.LocalPathsOnly,
                policies.AllowedSuffixes.using(suffixes={".json"}),
            ],
        )
        assert isinstance(loader.policy, policies.LocalPathsOnly)
        assert isinstance(loader.policy, policies.AllowedSuffixes)
        assert loader.policy.allow_commands is False  # the default posture
        assert loader.load("a.json") == {"a": 1}
        with pytest.raises(ConfinementError):
            loader.load("b.txt", format="json")

    def test_in_order(self):
        from yaconfiglib import policies

        calls = []

        class First(Policy):
            def check_path(self, path):
                calls.append("first")
                return super().check_path(path)

        class Second(Policy):
            def check_path(self, path):
                calls.append("second")
                return super().check_path(path)

        Loader(policy=(First, Second)).policy.check_path("x")
        assert calls == ["first", "second"]

    def test_top_level_functions_and_per_call(self, tmp_path):
        import yaconfiglib
        from yaconfiglib import policies

        _json(tmp_path / "b.txt")
        only_json = [policies.AllowedSuffixes.using(suffixes={".json"})]
        with pytest.raises(ConfinementError):
            yaconfiglib.load(str(tmp_path / "b.txt"), format="json", policy=only_json)
        with pytest.raises(ConfinementError):
            Loader(base_dir=tmp_path).load("b.txt", format="json", policy=only_json)

    def test_using_refuses_an_unknown_setting(self):
        from yaconfiglib import policies

        with pytest.raises(TypeError, match="unknown setting"):
            policies.AllowedSuffixes.using(commands={"x"})

    def test_anything_else_is_a_type_error(self):
        with pytest.raises(TypeError, match="Policy subclasses"):
            Loader(policy=[object])
