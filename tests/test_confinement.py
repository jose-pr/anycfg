"""`Policy(confine_to=...)`: refusing a local file read outside the allowed roots.

Without confinement a document can read any file the process can, through an
``!include`` with an absolute path or ``..``. These tests pin that the control
works, that it stays off unless asked for, and that no spelling of a root or a
path makes it fail open.
"""

from __future__ import annotations

import io
import logging
import os
import pathlib
import subprocess
import sys
import zipfile

import pytest

import yaconfiglib
from yaconfiglib import (
    ConfigError,
    ConfigTypeError,
    ConfinementError,
    Loader,
    Policy,
    PolicyError,
    sources,
)
from yaconfiglib import _confine

ENV = "YACONFIGLIB_CONFINE_TO"
WIN = sys.platform == "win32"


@pytest.fixture(autouse=True)
def _no_confinement_from_the_environment(monkeypatch):
    # A developer's own YACONFIGLIB_CONFINE_TO must not change these results.
    monkeypatch.delenv(ENV, raising=False)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _tree(tmp_path):
    """``conf/`` holding ``inside.json``, and ``secret.json`` beside it."""
    conf = tmp_path / "conf"
    _write(conf / "inside.json", '{"inside": true}')
    _write(tmp_path / "secret.json", '{"secret": "s3cr3t"}')
    return conf


def _including(conf, target):
    """``conf/app.yaml``, including *target* (a str written into the YAML)."""
    return _write(conf / "app.yaml", f"data: !include '{target}'\n")


def _confined(conf, confine_to, **settings):
    return Loader(base_dir=conf, policy=Policy(confine_to=confine_to), **settings)


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


class TestConfinement:
    """One rule: a local file read must resolve inside one of the roots."""

    def test_off_by_default(self, tmp_path):
        conf = _tree(tmp_path)
        loader = Loader(base_dir=conf)
        assert loader.load(str(tmp_path / "secret.json")) == {"secret": "s3cr3t"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_off_by_default_for_includes(self, tmp_path):
        conf = _tree(tmp_path)
        _including(conf, (tmp_path / "secret.json").as_posix())
        assert Loader(base_dir=conf).load("app.yaml") == {"data": {"secret": "s3cr3t"}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_include_inside_a_root_loads(self, tmp_path):
        conf = _tree(tmp_path)
        _including(conf, "inside.json")
        loader = _confined(conf, [str(conf)])
        assert loader.load("app.yaml") == {"data": {"inside": True}}

    @pytest.mark.usefixtures("needs_yaml")
    def test_absolute_include_outside_refused(self, tmp_path):
        conf = _tree(tmp_path)
        _including(conf, (tmp_path / "secret.json").as_posix())
        with pytest.raises(ConfinementError):
            _confined(conf, [str(conf)]).load("app.yaml")

    @pytest.mark.usefixtures("needs_yaml")
    def test_dotdot_escape_refused(self, tmp_path):
        conf = _tree(tmp_path)
        # Relative to the including file, so this names <tmp>/secret.json.
        _including(conf, "../secret.json")
        with pytest.raises(ConfinementError):
            _confined(conf, True).load("app.yaml")

    def test_top_level_source_outside_refused(self, tmp_path):
        conf = _tree(tmp_path)
        with pytest.raises(ConfinementError):
            _confined(conf, True).load(str(tmp_path / "secret.json"))

    def test_relative_top_level_escape_refused(self, tmp_path):
        conf = _tree(tmp_path)
        with pytest.raises(ConfinementError):
            _confined(conf, True).load("../secret.json")

    def test_refused_before_the_file_is_opened(self, tmp_path):
        # A missing file outside the roots is a refusal, not FileNotFoundError.
        conf = _tree(tmp_path)
        with pytest.raises(ConfinementError):
            _confined(conf, True).load(str(tmp_path / "does-not-exist.json"))

    def test_sibling_with_a_common_prefix_refused(self, tmp_path):
        # "<tmp>/confidential/x.json" starts with "<tmp>/conf" as a string but
        # lies outside it: containment is commonpath, never a prefix test.
        conf = _tree(tmp_path)
        leaked = _write(tmp_path / "confidential" / "x.json", '{"leaked": 1}')
        with pytest.raises(ConfinementError):
            _confined(conf, [str(conf)]).load(str(leaked))

    def test_any_of_several_roots_accepts(self, tmp_path):
        conf = _tree(tmp_path)
        shared = _write(tmp_path / "shared" / "extra.json", '{"extra": 1}')
        loader = _confined(conf, [str(conf), str(shared.parent)])
        assert loader.load(str(shared)) == {"extra": 1}
        assert loader.load("inside.json") == {"inside": True}

    def test_pathsep_string_is_split(self, tmp_path):
        conf = _tree(tmp_path)
        shared = _write(tmp_path / "shared" / "extra.json", '{"extra": 1}')
        # os.pathsep, never ":": a literal colon would split "C:".
        roots = os.pathsep.join([str(conf), str(shared.parent)])
        assert _confined(conf, roots).load(str(shared)) == {"extra": 1}
        with pytest.raises(ConfinementError):
            _confined(conf, roots).load(str(tmp_path / "secret.json"))

    def test_a_single_path_object_is_one_root(self, tmp_path):
        conf = _tree(tmp_path)
        loader = _confined(conf, pathlib.Path(conf))
        assert loader.load("inside.json") == {"inside": True}
        with pytest.raises(ConfinementError):
            loader.load(str(tmp_path / "secret.json"))

    def test_true_means_base_dir(self, tmp_path):
        conf = _tree(tmp_path)
        assert _confined(conf, True).load("inside.json") == {"inside": True}

    def test_true_without_base_dir_means_the_working_directory(
        self, tmp_path, monkeypatch
    ):
        conf = _tree(tmp_path)
        monkeypatch.chdir(conf)
        loader = Loader(policy=Policy(confine_to=True))
        assert loader.load("inside.json") == {"inside": True}
        with pytest.raises(ConfinementError):
            loader.load(str(tmp_path / "secret.json"))

    @pytest.mark.parametrize("value", [None, False])
    def test_none_and_false_are_off(self, tmp_path, value):
        conf = _tree(tmp_path)
        loader = _confined(conf, value)
        assert loader.load(str(tmp_path / "secret.json")) == {"secret": "s3cr3t"}

    def test_empty_allowlist_refuses_everything(self, tmp_path):
        conf = _tree(tmp_path)
        with pytest.raises(ConfinementError) as caught:
            _confined(conf, []).load("inside.json")
        assert "allowlist is empty" in str(caught.value)

    def test_load_all_is_confined(self, tmp_path):
        conf = _tree(tmp_path)
        _write(conf / "second.json", '{"second": 1}')
        loader = _confined(conf, [str(conf)])
        assert list(loader.load_all("inside.json", "second.json")) == [
            {"inside": True},
            {"second": 1},
        ]
        with pytest.raises(ConfinementError):
            list(loader.load_all(str(tmp_path / "secret.json")))

    def test_per_call_policy_confines(self, tmp_path):
        conf = _tree(tmp_path)
        loader = Loader(base_dir=conf)
        with pytest.raises(ConfinementError):
            loader.load(
                str(tmp_path / "secret.json"), policy=Policy(confine_to=[str(conf)])
            )
        assert loader.load(str(tmp_path / "secret.json")) == {"secret": "s3cr3t"}

    def test_module_level_load_is_confined(self, tmp_path):
        conf = _tree(tmp_path)
        with pytest.raises(ConfinementError):
            yaconfiglib.load(
                str(tmp_path / "secret.json"), policy=Policy(confine_to=[str(conf)])
            )

    def test_refusal_names_target_and_roots(self, tmp_path):
        conf = _tree(tmp_path)
        other = tmp_path / "shared"
        other.mkdir()
        with pytest.raises(ConfinementError) as caught:
            _confined(conf, [str(conf), str(other)]).load(str(tmp_path / "secret.json"))
        message = os.path.normcase(str(caught.value))
        assert os.path.normcase(str(tmp_path / "secret.json")) in message
        for root in (conf, other):
            assert os.path.normcase(str(root)) in message
        error = caught.value
        assert isinstance(error, PolicyError)
        assert isinstance(error, ConfigError)
        assert isinstance(error, PermissionError)

    def test_symlink_inside_a_root_to_outside_loads(self, tmp_path):
        # Lexical by design: a link inside a root was put there by whoever
        # administers the root.
        conf = _tree(tmp_path)
        outside = tmp_path / "secret.json"
        link = conf / "linked.json"
        try:
            os.symlink(str(outside), str(link))
        except (OSError, NotImplementedError):
            completed = subprocess.run(
                ["cmd", "/d", "/c", "mklink", str(link), str(outside)],
                capture_output=True,
            )
            if completed.returncode != 0 or not os.path.islink(str(link)):
                pytest.skip("cannot create a file symlink here")
        assert _confined(conf, True).load("linked.json") == {"secret": "s3cr3t"}


# ---------------------------------------------------------------------------
# on_error
# ---------------------------------------------------------------------------


class TestRefusalAndOnError:
    def test_refusal_is_offered_to_on_error(self, tmp_path):
        conf = _tree(tmp_path)
        events = []
        loader = _confined(
            conf, [str(conf)], on_error=lambda event: events.append(event) or True
        )
        result = loader.load("inside.json", str(tmp_path / "secret.json"))
        assert result == {"inside": True}
        assert len(events) == 1
        assert isinstance(events[0].error, ConfinementError)
        assert events[0].phase == "load"

    def test_skip_warning_names_no_configuration_text(self, tmp_path, caplog):
        # Source, phase and error type, never the message, which lists every
        # allowed root.
        conf = _tree(tmp_path)
        loader = _confined(conf, [str(conf)], on_error=True)
        with caplog.at_level(logging.WARNING):
            result = loader.load("inside.json", str(tmp_path / "secret.json"))
        assert result == {"inside": True}
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        text = warnings[0].getMessage()
        assert "secret.json" in text
        assert "ConfinementError" in text
        assert "load" in text
        assert "refusing to read" not in text
        assert "confine_to=" not in text

    def test_declined_refusal_raises(self, tmp_path):
        conf = _tree(tmp_path)
        loader = _confined(conf, [str(conf)], on_error=lambda event: False)
        with pytest.raises(ConfinementError):
            loader.load(str(tmp_path / "secret.json"))


# ---------------------------------------------------------------------------
# Every kind of source
# ---------------------------------------------------------------------------


class TestSources:
    def test_stream_on_an_outside_file_refused(self, tmp_path):
        conf = _tree(tmp_path)
        with open(tmp_path / "secret.json", encoding="utf-8") as handle:
            with pytest.raises(ConfinementError):
                _confined(conf, [str(conf)]).load(handle)

    def test_stream_inside_a_root_loads(self, tmp_path):
        conf = _tree(tmp_path)
        with open(conf / "inside.json", encoding="utf-8") as handle:
            assert _confined(conf, [str(conf)]).load(handle) == {"inside": True}

    def test_binary_stream_on_an_outside_file_refused(self, tmp_path):
        conf = _tree(tmp_path)
        with open(tmp_path / "secret.json", "rb") as handle:
            with pytest.raises(ConfinementError):
                _confined(conf, [str(conf)]).load(handle)

    def test_refused_stream_is_skippable(self, tmp_path):
        conf = _tree(tmp_path)
        loader = _confined(conf, [str(conf)], on_error=True)
        with open(tmp_path / "secret.json", encoding="utf-8") as handle:
            assert loader.load("inside.json", handle) == {"inside": True}

    def test_nameless_streams_are_exempt(self, tmp_path):
        conf = _tree(tmp_path)
        loader = _confined(conf, [str(conf)])
        assert loader.load(io.StringIO('{"k": "v"}'), format="json") == {"k": "v"}

        class _Pipe(io.StringIO):
            name = "<stdin>"

        assert loader.load(_Pipe('{"k": "v"}'), format="json") == {"k": "v"}

    def test_sources_that_are_not_files_are_exempt(self, tmp_path):
        loader = _confined(tmp_path, [])
        assert loader.load(sources.Text('{"t": 1}', format="json")) == {"t": 1}
        assert loader.load(sources.Data({"d": 1})) == {"d": 1}
        assert loader.load({"m": 1}) == {"m": 1}
        env = sources.Env("APP_", environ={"APP_E": "1"})
        assert loader.load(env) == {"E": "1"}

    def test_command_source_is_exempt(self, tmp_path):
        loader = Loader(
            base_dir=tmp_path, policy=Policy(allow_commands=True, confine_to=[])
        )
        source = f'cmd+json://"{sys.executable}" -c "print(1)"'
        assert loader.load(source) == 1

    def test_mempath_is_exempt(self):
        from pathapi.mempath import MemPath

        path = MemPath("/yacltest-confine/x.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"m": 1}')
        assert Loader(policy=Policy(confine_to=[])).load(path) == {"m": 1}

    def test_non_local_path_refused_while_confined(self, tmp_path):
        from pathapi import LocalPath
        from pathapi.archive import ZipPath

        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("x.json", '{"z": 1}')
        member = ZipPath("x.json", archive=LocalPath(str(archive)))
        assert Loader().load(member) == {"z": 1}
        # Inside no local root, even with the archive itself inside one.
        with pytest.raises(ConfinementError) as caught:
            Loader(policy=Policy(confine_to=[str(tmp_path)])).load(member)
        assert "only local files" in str(caught.value)

    @pytest.mark.usefixtures("needs_yaml")
    def test_includes_in_in_memory_text_are_confined(self, tmp_path):
        conf = _tree(tmp_path)
        loader = _confined(conf, [str(conf)])
        inside = sources.Text("x: !include inside.json\n", format="yaml")
        assert loader.load(inside) == {"x": {"inside": True}}
        outside = sources.Text("x: !include ../secret.json\n", format="yaml")
        with pytest.raises(ConfinementError):
            loader.load(outside)

    @pytest.mark.usefixtures("needs_yaml")
    def test_includes_in_command_output_are_confined(self, tmp_path):
        conf = _tree(tmp_path)
        secret = (tmp_path / "secret.json").as_posix()
        emit = _write(tmp_path / "emit.py", f"print(\"data: !include '{secret}'\")\n")
        loader = Loader(
            base_dir=conf,
            policy=Policy(allow_commands=True, confine_to=[str(conf)]),
        )
        with pytest.raises(ConfinementError):
            loader.load(f'cmd+yaml://"{sys.executable}" "{emit}"')

    @pytest.mark.usefixtures("needs_yaml")
    def test_includes_in_command_output_inside_a_root_load(self, tmp_path):
        conf = _tree(tmp_path)
        inside = (conf / "inside.json").as_posix()
        emit = _write(tmp_path / "emit.py", f"print(\"data: !include '{inside}'\")\n")
        loader = Loader(
            base_dir=conf,
            policy=Policy(allow_commands=True, confine_to=[str(conf)]),
        )
        assert loader.load(f'cmd+yaml://"{sys.executable}" "{emit}"') == {
            "data": {"inside": True}
        }

    @pytest.mark.usefixtures("needs_yaml")
    def test_nested_includes_are_confined(self, tmp_path):
        conf = _tree(tmp_path)
        _write(conf / "mid.yaml", "deep: !include ../secret.json\n")
        _write(conf / "top.yaml", "mid: !include mid.yaml\n")
        with pytest.raises(ConfinementError):
            _confined(conf, True).load("top.yaml")


# ---------------------------------------------------------------------------
# Glob patterns
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("needs_yaml")
class TestGlobPatterns:
    """A pattern must not become a filename oracle."""

    def _tree(self, tmp_path):
        conf = tmp_path / "conf"
        _write(conf / "sub" / "ok.yaml", "ok: 1\n")
        _write(tmp_path / "outside" / "secret_token.yaml", "p: SECRET\n")
        return conf

    def _load(self, conf, pattern):
        _write(conf / "app.yaml", f"x: !include '{pattern}'\n")
        return _confined(conf, [str(conf)], recursive=True).load("app.yaml")

    def test_pattern_outside_the_roots_refused_without_naming_a_match(self, tmp_path):
        conf = self._tree(tmp_path)
        outside = (tmp_path / "outside" / "secret_tok*").as_posix()
        with pytest.raises(ConfinementError) as caught:
            self._load(conf, outside)
        assert "secret_token.yaml" not in str(caught.value)

    def test_a_right_and_a_wrong_guess_are_indistinguishable(self, tmp_path):
        # The literal base is checked before expansion, so the directory is
        # never listed and whether a file exists cannot be observed.
        conf = self._tree(tmp_path)
        messages = []
        for guess in ("secret_tok*", "nothing_her*"):
            with pytest.raises(ConfinementError) as caught:
                self._load(conf, (tmp_path / "outside" / guess).as_posix())
            messages.append(str(caught.value).replace(guess, "<guess>"))
        assert messages[0] == messages[1]

    def test_wildcard_escaping_after_a_legal_prefix_names_only_the_pattern(
        self, tmp_path
    ):
        # The prefix is inside the root, so each match is checked instead, and
        # a refused match is reported by the pattern the document wrote.
        conf = self._tree(tmp_path)
        with pytest.raises(ConfinementError) as caught:
            self._load(conf, "*/../../outside/secret_tok*")
        message = str(caught.value)
        assert "secret_token.yaml" not in message
        assert "secret_tok*" in message

    def test_pattern_inside_the_roots_expands(self, tmp_path):
        conf = self._tree(tmp_path)
        assert self._load(conf, "sub/*.yaml") == {"x": {"ok": 1}}

    def test_top_level_pattern_outside_refused(self, tmp_path):
        conf = self._tree(tmp_path)
        with pytest.raises(ConfinementError):
            _confined(conf, [str(conf)]).load(
                (tmp_path / "outside" / "*.yaml").as_posix()
            )

    def test_refused_pattern_is_skippable(self, tmp_path):
        conf = self._tree(tmp_path)
        _write(conf / "own.yaml", "own: 1\n")
        loader = _confined(conf, [str(conf)], recursive=True, on_error=True)
        assert loader.load(
            "own.yaml", (tmp_path / "outside" / "*.yaml").as_posix()
        ) == {"own": 1}


# ---------------------------------------------------------------------------
# Root forms
# ---------------------------------------------------------------------------


class TestRootForms:
    """What counts as a root, and what is refused rather than guessed at."""

    def test_relative_root_refused(self, tmp_path):
        conf = _tree(tmp_path)
        with pytest.raises(ConfigTypeError) as caught:
            _confined(conf, ["conf"]).load("inside.json")
        message = str(caught.value)
        assert "absolute" in message
        assert "confine_to=True" in message

    @pytest.mark.parametrize(
        "root", [".", "conf", "~/conf", pytest.param("C:conf", id="drive_relative")]
    )
    def test_unanchored_roots_refused(self, tmp_path, root):
        if root == "C:conf" and not WIN:
            pytest.skip("drive-relative paths are a Windows spelling")
        conf = _tree(tmp_path)
        with pytest.raises(ConfigTypeError):
            _confined(conf, [root]).load("inside.json")

    @pytest.mark.parametrize("value", [[""], "", os.pathsep, os.pathsep * 3], ids=repr)
    def test_empty_entries_make_an_empty_allowlist(self, tmp_path, value):
        # Never the working directory: [""] is what splitting an unset
        # variable produces in a caller's own code.
        conf = _tree(tmp_path)
        with pytest.raises(ConfinementError) as caught:
            _confined(conf, value).load("inside.json")
        assert "allowlist is empty" in str(caught.value)

    def test_empty_entries_beside_a_root_are_dropped(self, tmp_path):
        conf = _tree(tmp_path)
        roots = os.pathsep + str(conf) + os.pathsep
        assert _confined(conf, roots).load("inside.json") == {"inside": True}
        assert _confined(conf, ["", str(conf)]).load("inside.json") == {"inside": True}

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(5, "confine_to="), ([5], "every confine_to= root")],
        ids=["wrong_type", "wrong_element_type"],
    )
    def test_a_non_path_value_rejected(self, tmp_path, value, expected):
        conf = _tree(tmp_path)
        with pytest.raises(ConfigTypeError) as caught:
            _confined(conf, value).load("inside.json")
        message = str(caught.value)
        assert expected in message
        assert "int" in message

    @pytest.mark.skipif(WIN, reason="':' cannot appear in a Windows file name")
    def test_sequence_entries_are_never_split(self, tmp_path):
        root = tmp_path / "a:b"
        _write(root / "x.json", '{"x": 1}')
        loader = Loader(policy=Policy(confine_to=[str(root)]))
        assert loader.load(str(root / "x.json")) == {"x": 1}

    @pytest.mark.skipif(not WIN, reason="extended-length paths are a Windows spelling")
    @pytest.mark.parametrize("side", ["target", "root"])
    def test_extended_length_prefix_stripped_on_either_side(self, tmp_path, side):
        conf = _tree(tmp_path)
        extended = "\\\\?\\" + str(conf)
        if side == "target":
            loader = _confined(conf, [str(conf)])
            target = extended + "\\inside.json"
        else:
            loader = _confined(conf, [extended])
            target = str(conf / "inside.json")
        assert loader.load(target) == {"inside": True}

    @pytest.mark.skipif(not WIN, reason="extended-length paths are a Windows spelling")
    def test_extended_length_target_outside_still_refused(self, tmp_path):
        conf = _tree(tmp_path)
        target = "\\\\?\\" + str(tmp_path / "secret.json")
        with pytest.raises(ConfinementError):
            _confined(conf, [str(conf)]).load(target)

    @pytest.mark.skipif(not WIN, reason="UNC paths are a Windows spelling")
    def test_a_bare_unc_share_root_contains_its_files(self):
        # Lexical, so no share has to exist. commonpath treats a bare share as
        # relative; the root key gives it the separator it needs.
        roots = (_confine.root_key(r"\\srv\share"),)
        assert _confine.within_roots(r"\\srv\share\conf\app.yaml", roots)
        assert _confine.within_roots(r"\\srv\share\app.yaml", roots)
        assert not _confine.within_roots(r"\\srv\other\app.yaml", roots)

    @pytest.mark.skipif(not WIN, reason="UNC paths are a Windows spelling")
    def test_a_bare_unc_share_is_an_accepted_root(self, tmp_path):
        conf = _tree(tmp_path)
        # Accepted as absolute (not ConfigTypeError); a local file is outside it.
        with pytest.raises(ConfinementError):
            _confined(conf, r"\\srv\share").load("inside.json")

    def test_commonpath_not_prefix(self):
        root = os.path.abspath(os.path.join(os.sep, "srv", "conf"))
        roots = (_confine.root_key(root),)
        assert _confine.within_roots(os.path.join(root, "a.yaml"), roots)
        assert not _confine.within_roots(
            os.path.join(os.path.dirname(root), "confidential", "a.yaml"), roots
        )
        assert not _confine.within_roots(
            os.path.join(root, "..", "confidential", "a.yaml"), roots
        )


# ---------------------------------------------------------------------------
# YACONFIGLIB_CONFINE_TO
# ---------------------------------------------------------------------------


class TestEnvironmentVariable:
    def test_read_when_the_policy_leaves_it_unset(self, tmp_path, monkeypatch):
        conf = _tree(tmp_path)
        monkeypatch.setenv(ENV, str(conf))
        loader = Loader(base_dir=conf)
        assert loader.load("inside.json") == {"inside": True}
        with pytest.raises(ConfinementError):
            loader.load(str(tmp_path / "secret.json"))

    def test_read_at_load_time(self, tmp_path, monkeypatch):
        conf = _tree(tmp_path)
        loader = Loader(base_dir=conf)
        assert loader.load(str(tmp_path / "secret.json")) == {"secret": "s3cr3t"}
        monkeypatch.setenv(ENV, str(conf))
        with pytest.raises(ConfinementError):
            loader.load(str(tmp_path / "secret.json"))

    def test_pathsep_separated(self, tmp_path, monkeypatch):
        conf = _tree(tmp_path)
        shared = _write(tmp_path / "shared" / "extra.json", '{"extra": 1}')
        monkeypatch.setenv(ENV, os.pathsep.join([str(conf), str(shared.parent)]))
        assert Loader(base_dir=conf).load(str(shared)) == {"extra": 1}

    def test_explicit_roots_ignore_it(self, tmp_path, monkeypatch):
        # The variable names a root that would accept the read; an explicit
        # value wins, so the environment can never widen an in-code allowlist.
        conf = _tree(tmp_path)
        monkeypatch.setenv(ENV, str(tmp_path))
        with pytest.raises(ConfinementError):
            _confined(conf, [str(conf)]).load(str(tmp_path / "secret.json"))

    def test_explicit_false_ignores_it(self, tmp_path, monkeypatch):
        conf = _tree(tmp_path)
        monkeypatch.setenv(ENV, str(conf))
        loader = _confined(conf, False)
        assert loader.load(str(tmp_path / "secret.json")) == {"secret": "s3cr3t"}

    def test_unset_is_off(self, tmp_path):
        conf = _tree(tmp_path)
        assert ENV not in os.environ
        loader = Loader(base_dir=conf)
        assert loader.load(str(tmp_path / "secret.json")) == {"secret": "s3cr3t"}

    def test_empty_refuses_everything(self, tmp_path, monkeypatch):
        conf = _tree(tmp_path)
        monkeypatch.setenv(ENV, "")
        with pytest.raises(ConfinementError) as caught:
            Loader(base_dir=conf).load("inside.json")
        assert "allowlist is empty" in str(caught.value)

    def test_relative_root_refused(self, tmp_path, monkeypatch):
        conf = _tree(tmp_path)
        monkeypatch.setenv(ENV, "conf")
        with pytest.raises(ConfigTypeError):
            Loader(base_dir=conf).load("inside.json")

    def test_stray_space_after_the_separator_refused(self, tmp_path, monkeypatch):
        # " /other" is not anchored: quietly resolving it under the working
        # directory would drop the root the operator named.
        conf = _tree(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv(ENV, f"{conf}{os.pathsep} {other}")
        with pytest.raises(ConfigTypeError):
            Loader(base_dir=conf).load("inside.json")

    def test_import_never_fails_on_a_relative_value(self, tmp_path):
        # A bad value must not break importing the package; the first load
        # that consults it raises instead.
        package_root = str(pathlib.Path(yaconfiglib.__file__).resolve().parent.parent)
        code = (
            "import sys\n"
            f"sys.path.insert(0, {package_root!r})\n"
            "import yaconfiglib\n"
            "print('imported')\n"
            "try:\n"
            "    yaconfiglib.loads('{}', format='json')\n"
            "except yaconfiglib.ConfigTypeError:\n"
            "    print('ConfigTypeError')\n"
        )
        environ = dict(os.environ)
        environ[ENV] = os.path.join("relative", "dir")
        done = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=environ,
            timeout=60,
        )
        assert done.returncode == 0, done.stderr
        assert done.stdout.split() == ["imported", "ConfigTypeError"]
