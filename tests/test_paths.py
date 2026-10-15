"""Source resolution: which objects are sources, how globs expand, and de-duplication."""

from __future__ import annotations

import collections.abc
import json
import logging
import os
import pathlib
import subprocess
import sys
import types

import pytest

from yaconfiglib import (
    CommandsDisabledError,
    LoadEvent,
    Loader,
    Policy,
    UnsupportedFormatError,
    sources,
)
from yaconfiglib.sources import Data, Payload, Source

PATHS_LOGGER = "yaconfiglib._paths"


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _FsPathObject:
    """An arbitrary os.PathLike: not a pathlib class."""

    def __init__(self, value):
        self._value = value

    def __fspath__(self):
        return self._value


class _Recorder(Source):
    """A source that records being read."""

    def __init__(self, reads):
        self.reads = reads

    @property
    def name(self):
        return "recorder"

    def read(self, context):
        self.reads.append(self)
        return Payload({"recorded": True}, name="recorder", identity="r", parsed=True)


# ---------------------------------------------------------------------------
# What a source can be
# ---------------------------------------------------------------------------


class TestPathLikeSources:
    def test_stdlib_path_absolute(self, tmp_path):
        _write(tmp_path / "app.json", {"a": 1})
        assert Loader().load(tmp_path / "app.json") == {"a": 1}

    def test_stdlib_path_relative_joins_base_dir(self, tmp_path):
        _write(tmp_path / "conf" / "app.json", {"a": 1})
        assert Loader(base_dir=str(tmp_path)).load(
            pathlib.Path("conf") / "app.json"
        ) == {"a": 1}

    def test_stdlib_path_glob_expands(self, tmp_path):
        _write(tmp_path / "conf" / "a.json", {"a": 1})
        _write(tmp_path / "conf" / "b.json", {"b": 2})
        loader = Loader(base_dir=str(tmp_path), collect="list")
        assert loader.load(pathlib.Path("conf") / "*.json") == [{"a": 1}, {"b": 2}]

    @pytest.mark.parametrize("kind", ["pure_posix_path", "fspath_object"])
    def test_other_pathlike(self, tmp_path, kind):
        _write(tmp_path / "conf" / "app.json", {"a": 1})
        source = (
            pathlib.PurePosixPath("conf/app.json")
            if kind == "pure_posix_path"
            else _FsPathObject("conf/app.json")
        )
        assert Loader(base_dir=str(tmp_path)).load(source) == {"a": 1}

    def test_pathapi_local_path(self, tmp_path):
        from pathapi import LocalPath

        _write(tmp_path / "app.json", {"a": 1})
        assert Loader().load(LocalPath(str(tmp_path / "app.json"))) == {"a": 1}

    def test_pathapi_mempath(self):
        from pathapi.mempath import MemPath

        memory = MemPath("memdir/a.json")
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_text(json.dumps({"a": 1}), encoding="utf-8")
        assert Loader().load(memory) == {"a": 1}


class TestSourceTypes:
    @pytest.mark.parametrize("empty", [None, ""])
    def test_none_and_empty_are_skipped(self, tmp_path, empty):
        _write(tmp_path / "base.json", {"a": 1})
        loader = Loader(base_dir=str(tmp_path), collect="list")
        assert loader.load(empty, "base.json", empty) == [{"a": 1}]
        assert loader.load([empty, [empty]]) is None

    def test_skipping_is_logged_at_debug(self, caplog):
        caplog.set_level(logging.DEBUG, logger=PATHS_LOGGER)
        assert Loader().load(None, "") is None
        assert [r for r in caplog.records if "skipping empty source" in r.getMessage()]

    @pytest.mark.parametrize("data", [b"a.json", b"", bytearray(b"{}")])
    def test_bytes_is_type_error(self, data):
        with pytest.raises(TypeError, match="loads"):
            Loader().load(data)

    @pytest.mark.parametrize("bad", [object(), 42, 1.5])
    def test_unsupported_type_is_type_error(self, bad):
        with pytest.raises(TypeError, match="cannot load"):
            Loader().load(bad)

    def test_unsupported_type_raises_before_anything_loads(self, tmp_path):
        reads = []
        _write(tmp_path / "app.json", {"n": 1})
        with pytest.raises(TypeError):
            Loader(base_dir=str(tmp_path)).load(_Recorder(reads), "app.json", object())
        assert reads == []
        with pytest.raises(TypeError):
            list(Loader().load_all(_Recorder(reads), object()))
        assert reads == []

    def test_nested_lists_and_tuples_are_flattened(self, tmp_path):
        for name in ("a", "b", "c", "d"):
            _write(tmp_path / f"{name}.json", {"file": name})
        loader = Loader(base_dir=str(tmp_path), collect="list")
        result = loader.load(["a.json", ("b.json", ["c.json"])], "d.json")
        assert [item["file"] for item in result] == ["a", "b", "c", "d"]

    def test_mappings_become_data(self):
        class Settings(collections.abc.Mapping):
            def __init__(self, **values):
                self._values = values

            def __getitem__(self, key):
                return self._values[key]

            def __iter__(self):
                return iter(self._values)

            def __len__(self):
                return len(self._values)

        result = Loader(collect="list").load({"a": 1}, [Settings(p=1)])
        assert result[0] == {"a": 1}
        assert dict(result[1]) == {"p": 1}

    def test_mapping_proxy_is_a_mapping(self):
        proxy = types.MappingProxyType({"p": 1})
        assert Loader().load(proxy) == {"p": 1}

    def test_source_objects_pass_through(self):
        reads = []
        assert Loader().load(_Recorder(reads), Data({"b": 2})) == {
            "recorded": True,
            "b": 2,
        }
        assert len(reads) == 1


class TestSchemeStrings:
    def test_command_scheme_is_a_command(self):
        # Refused by the default policy: the text was taken as a command, not a file.
        for text in ('cmd://echo "{}"', 'exec+json://echo "{}"', 'sh://echo "{}"'):
            with pytest.raises(CommandsDisabledError):
                Loader().load(text)

    def test_command_text_with_glob_characters_is_not_a_pattern(self, tmp_path):
        command = f'cmd+json://"{sys.executable}" -c "print([1, 2][0])"'
        assert (
            Loader(base_dir=str(tmp_path), policy=Policy.trusted()).load(command) == 1
        )

    def test_scheme_is_case_insensitive(self):
        with pytest.raises(CommandsDisabledError):
            Loader().load('CMD+json://echo "{}"')

    def test_registered_scheme(self):
        def factory(text):
            return Data({"from": text})

        sources.register_scheme("yacltestscheme", factory)
        try:
            assert "yacltestscheme" in sources.schemes()
            assert Loader().load("yacltestscheme://anything") == {
                "from": "yacltestscheme://anything"
            }
        finally:
            assert sources.unregister_scheme("yacltestscheme") is True

    def test_registered_scheme_conflict(self):
        with pytest.raises(ValueError):
            sources.register_scheme("cmd", lambda text: Data({}))

    def test_unknown_scheme_stays_a_local_path(self, tmp_path):
        assert sources.from_text("nosuchscheme://host/x.json") is None
        seen = []

        def record(event):
            seen.append(event)
            return True

        loader = Loader(base_dir=str(tmp_path), on_error=record)
        # Read as a local file, so the failure is the filesystem's, after the
        # base_dir join: there is no URI parsing.
        assert loader.load("nosuchscheme://host/x.json") is None
        (event,) = seen
        assert isinstance(event.error, OSError)
        assert str(event.path).startswith(str(tmp_path))
        assert str(event.path).endswith("x.json")

    @pytest.mark.parametrize("text", ["cmd:echo.json", "C:\\conf\\a.json", "c:/a.json"])
    def test_scheme_needs_both_slashes(self, text):
        assert sources.from_text(text) is None


# ---------------------------------------------------------------------------
# Glob expansion
# ---------------------------------------------------------------------------


class TestGlobExpansion:
    def test_relative_pattern(self, tmp_path):
        for name in ("a", "b", "c"):
            _write(tmp_path / f"{name}.json", {"file": name})
        result = Loader(base_dir=str(tmp_path), collect="list").load("*.json")
        assert [item["file"] for item in result] == ["a", "b", "c"]

    def test_absolute_pattern(self, tmp_path):
        _write(tmp_path / "a.json", {"a": 1})
        _write(tmp_path / "b.json", {"b": 2})
        assert Loader().load(str(tmp_path / "*.json")) == {"a": 1, "b": 2}

    def test_absolute_pattern_ignores_base_dir(self, tmp_path):
        _write(tmp_path / "x" / "a.json", {"a": 1})
        (tmp_path / "y").mkdir()
        assert Loader(base_dir=tmp_path / "y").load(str(tmp_path / "x" / "*.json")) == {
            "a": 1
        }

    def test_order_is_by_code_point_not_listing(self, tmp_path):
        for name in ("a.json", "B.json", "c.json"):
            _write(tmp_path / name, {name[0]: name})
        result = Loader(base_dir=str(tmp_path), collect="list").load("*.json")
        # "B" (0x42) sorts before "a" (0x61), whatever order the listing gave.
        assert [next(iter(item)) for item in result] == ["B", "a", "c"]

    def test_recursive_matches_sort_by_path_component(self, tmp_path):
        _write(tmp_path / "00-base.json", {"n": "00-base"})
        _write(tmp_path / "99-local.json", {"n": "99-local"})
        _write(tmp_path / "10-env" / "prod.json", {"n": "10-env/prod"})
        loader = Loader(base_dir=str(tmp_path), collect="list", recursive=True)
        assert [item["n"] for item in loader.load("**/*.json")] == [
            "00-base",
            "10-env/prod",
            "99-local",
        ]

    def test_existing_name_with_glob_characters_loads_literally(self, tmp_path):
        _write(tmp_path / "z1.json", {"which": "z1"})
        _write(tmp_path / "z[1].json", {"which": "bracketed"})
        assert Loader(base_dir=str(tmp_path)).load("z[1].json") == {
            "which": "bracketed"
        }

    def test_missing_name_with_glob_characters_is_a_pattern(self, tmp_path):
        _write(tmp_path / "z1.json", {"which": "z1"})
        assert Loader(base_dir=str(tmp_path)).load("z[1].json") == {"which": "z1"}

    @pytest.mark.parametrize("source", ["app.json", "*.json", "a*.json"])
    def test_base_dir_glob_characters_are_literal(self, tmp_path, source):
        base = tmp_path / "proj [v2]"
        _write(base / "app.json", {"a": 1})
        _write(tmp_path / "proj v" / "app.json", {"decoy": 1})
        assert Loader(base_dir=str(base)).load(source) == {"a": 1}

    def test_absolute_literal_under_bracketed_directory(self, tmp_path):
        base = tmp_path / "proj [v2]"
        _write(base / "app.json", {"a": 1})
        assert Loader().load(str(base / "app.json")) == {"a": 1}

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows path syntax")
    def test_extended_length_anchor_is_literal(self, tmp_path):
        _write(tmp_path / "a.json", {"a": 1})
        _write(tmp_path / "b.json", {"b": 2})
        anchored = "\\\\?\\" + str(tmp_path)
        # The "?" of the anchor is path syntax, not a wildcard.
        assert Loader().load(anchored + "\\a.json") == {"a": 1}
        assert Loader(base_dir=anchored).load("a.json") == {"a": 1}
        assert Loader().load(anchored + "\\*.json") == {"a": 1, "b": 2}
        assert Loader(base_dir=anchored).load("*.json") == {"a": 1, "b": 2}

    def test_directory_matches_are_dropped(self, tmp_path):
        _write(tmp_path / "envs" / "db.json", {"db": 1})
        (tmp_path / "envs" / "sub.json").mkdir(parents=True)
        assert Loader(base_dir=str(tmp_path)).load("envs/*") == {"db": 1}
        assert Loader(base_dir=str(tmp_path)).load("envs/*.json") == {"db": 1}

    def test_no_match_loads_nothing(self, tmp_path):
        _write(tmp_path / "base.json", {"base": 1})
        assert Loader(base_dir=str(tmp_path)).load("base.json", "none-*.json") == {
            "base": 1
        }

    @pytest.mark.parametrize("pattern", ["nodir/*.json", "file.json/*.json"])
    def test_missing_or_file_parent_matches_nothing(self, tmp_path, pattern):
        _write(tmp_path / "base.json", {"base": 1})
        _write(tmp_path / "file.json", {"file": 1})
        assert Loader(base_dir=str(tmp_path)).load("base.json", pattern) == {"base": 1}

    def test_dotfiles_are_matched(self, tmp_path):
        _write(tmp_path / "a.json", {"a": 1})
        _write(tmp_path / ".h.json", {"h": 1})
        loader = Loader(base_dir=str(tmp_path))
        assert loader.load("*.json") == {"a": 1, "h": 1}
        assert loader.load(".*.json") == {"h": 1}

    def test_wildcard_directory_segment(self, tmp_path):
        _write(tmp_path / "envs" / "prod" / "db.json", {"prod": 1})
        _write(tmp_path / "envs" / "dev" / "db.json", {"dev": 1})
        assert Loader(base_dir=str(tmp_path)).load("envs/*/db.json") == {
            "dev": 1,
            "prod": 1,
        }

    def test_mempath_pattern_without_base_dir(self):
        from pathapi.mempath import MemPath

        # One MemPath instance: each constructor call is its own filesystem.
        root = MemPath("memdir")
        root.mkdir(parents=True, exist_ok=True)
        for name in ("b.json", "a.json"):
            (root / name).write_text(json.dumps({"name": name}), encoding="utf-8")
        assert Loader(collect="list").load(root / "*.json") == [
            {"name": "a.json"},
            {"name": "b.json"},
        ]


class TestRecursiveGlob:
    @staticmethod
    def _tree(tmp_path):
        _write(tmp_path / "a.json", {"a": 1})
        _write(tmp_path / "sub" / "b.json", {"b": 2})
        _write(tmp_path / "sub" / "deeper" / "c.json", {"c": 3})

    def test_double_star_is_identical_on_every_python(self, tmp_path):
        self._tree(tmp_path)
        loader = Loader(base_dir=str(tmp_path), recursive=True)
        everything = {"a": 1, "b": 2, "c": 3}
        assert loader.load("**/*") == everything
        assert loader.load("**/*.json") == everything
        # A bare "**" matches files too, on 3.9 as on 3.13+.
        assert loader.load("**") == everything

    def test_without_recursive_double_star_is_one_level(self, tmp_path):
        self._tree(tmp_path)
        assert Loader(base_dir=str(tmp_path)).load("**/*.json") == {"b": 2}

    def test_per_call_recursive(self, tmp_path):
        self._tree(tmp_path)
        assert Loader(base_dir=str(tmp_path)).load("**/*.json", recursive=True) == {
            "a": 1,
            "b": 2,
            "c": 3,
        }

    def test_per_call_false_overrides_instance_true(self, tmp_path):
        self._tree(tmp_path)
        loader = Loader(base_dir=str(tmp_path), recursive=True)
        assert loader.load("**/*.json", recursive=False) == {"b": 2}

    def test_load_all_honors_recursive(self, tmp_path):
        self._tree(tmp_path)
        loader = Loader(base_dir=str(tmp_path), recursive=True)
        assert list(loader.load_all("**/*.json")) == [{"a": 1}, {"b": 2}, {"c": 3}]
        assert list(Loader(base_dir=str(tmp_path)).load_all("**/*.json")) == [{"b": 2}]

    def test_two_recursive_segments(self, tmp_path):
        _write(tmp_path / "conf" / "a.json", {"a": 1})
        _write(tmp_path / "conf" / "sub" / "c.json", {"c": 1})
        _write(tmp_path / "x" / "conf" / "sub" / "b.json", {"b": 1})
        loader = Loader(base_dir=str(tmp_path), recursive=True)
        assert loader.load("**/conf/**/*.json") == {"a": 1, "b": 1, "c": 1}

    def test_recursive_dotfiles(self, tmp_path):
        _write(tmp_path / ".hidden" / "h.json", {"h": 1})
        loader = Loader(base_dir=str(tmp_path), recursive=True)
        assert loader.load("**/*.json") == {"h": 1}


# ---------------------------------------------------------------------------
# De-duplication
# ---------------------------------------------------------------------------


class TestDedup:
    def _layered(self, tmp_path):
        _write(tmp_path / "conf" / "app.json", {"n": 1})
        _write(tmp_path / "conf" / "local.json", {"n": 2})
        return Loader(base_dir=str(tmp_path), collect="list")

    def test_same_file_twice_loads_once(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/app.json", "conf/app.json") == [{"n": 1}]

    def test_two_spellings_load_once(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/app.json", "./conf/app.json") == [{"n": 1}]
        assert loader.load("conf/app.json", "conf/../conf/app.json") == [{"n": 1}]
        absolute = str(tmp_path / "conf" / "app.json")
        assert loader.load("conf/app.json", absolute) == [{"n": 1}]

    def test_duplicate_is_warned(self, tmp_path, caplog):
        loader = self._layered(tmp_path)
        with caplog.at_level(logging.WARNING, logger=PATHS_LOGGER):
            loader.load("conf/app.json", "./conf/app.json")
        assert [r for r in caplog.records if "duplicate" in r.getMessage()]

    @pytest.mark.skipif(
        os.path.normcase("A") != "a", reason="case-insensitive filesystem only"
    )
    def test_case_variant_loads_once(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/app.json", "conf/App.json") == [{"n": 1}]

    def test_explicit_name_after_a_glob_keeps_its_own_position(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/*.json", "conf/local.json") == [{"n": 1}, {"n": 2}]
        _write(tmp_path / "conf" / "zz.json", {"n": 3})
        assert loader.load("conf/*.json", "conf/app.json") == [
            {"n": 2},
            {"n": 3},
            {"n": 1},
        ]

    def test_explicit_name_before_a_glob_keeps_its_position(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/local.json", "conf/*.json") == [{"n": 2}, {"n": 1}]

    def test_explicit_name_nested_in_a_list_beats_the_glob(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load(["conf/*.json"], [["conf/app.json"]]) == [
            {"n": 2},
            {"n": 1},
        ]

    def test_overlapping_globs_yield_each_file_once(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/*.json", "conf/a*.json") == [{"n": 1}, {"n": 2}]

    def test_dedup_is_per_call(self, tmp_path):
        loader = self._layered(tmp_path)
        assert loader.load("conf/app.json") == [{"n": 1}]
        assert loader.load("conf/app.json") == [{"n": 1}]

    def test_symlinked_name_is_a_different_file(self, tmp_path):
        target = _write(tmp_path / "real.json", {"n": 1})
        link = tmp_path / "alias.json"
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError):
            pytest.skip("cannot create a file symlink here")
        # Lexical keys: never resolved, so two names a caller chose stay two.
        result = Loader(base_dir=str(tmp_path), collect="list").load(
            "real.json", "alias.json"
        )
        assert result == [{"n": 1}, {"n": 1}]


# ---------------------------------------------------------------------------
# Directories that cannot be listed
# ---------------------------------------------------------------------------


@pytest.fixture
def locked_tree(tmp_path, monkeypatch):
    """A tree with one directory whose listing raises `PermissionError`.

    Patches pathapi's `_scandir`, which is how its glob lists a directory; a
    monkeypatch rather than real ACLs, so it runs the same on Windows and as
    root in a container.
    """
    from pathapi import LocalPath

    _write(tmp_path / "lock" / "ok" / "a.json", {"a": 1})
    _write(tmp_path / "lock" / "locked" / "b.json", {"b": 2})
    original = LocalPath._scandir

    def patched(self, *args, **kwargs):
        if self.name == "locked":
            raise PermissionError(13, "Permission denied", str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(LocalPath, "_scandir", patched)
    return tmp_path


class TestUnlistableDirectories:
    def test_raises_by_default(self, locked_tree):
        with pytest.raises(PermissionError):
            Loader(base_dir=str(locked_tree)).load("lock/**/*.json", recursive=True)

    def test_on_error_true_skips_only_that_directory(self, locked_tree, caplog):
        loader = Loader(base_dir=str(locked_tree), on_error=True)
        with caplog.at_level(logging.WARNING, logger="yaconfiglib"):
            assert loader.load("lock/**/*.json", recursive=True) == {"a": 1}
        (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert "locked" in warning.getMessage()
        assert "glob" in warning.getMessage()
        assert "PermissionError" in warning.getMessage()

    def test_predicate_is_asked_once_with_glob_phase(self, locked_tree):
        seen = []

        def record(event):
            seen.append(event)
            return True

        loader = Loader(base_dir=str(locked_tree), on_error=record)
        assert loader.load("lock/**/*.json", recursive=True) == {"a": 1}
        (event,) = seen
        assert isinstance(event, LoadEvent)
        assert event.phase == "glob"
        assert isinstance(event.error, PermissionError)
        assert event.path.name == "locked"

    def test_declining_raises_the_listing_error(self, locked_tree):
        loader = Loader(base_dir=str(locked_tree), on_error=lambda event: False)
        with pytest.raises(PermissionError):
            loader.load("lock/**/*.json", recursive=True)

    def test_load_all(self, locked_tree):
        loader = Loader(base_dir=str(locked_tree), on_error=True)
        assert list(loader.load_all("lock/**/*.json", recursive=True)) == [{"a": 1}]

    def test_missing_directory_is_not_offered(self, tmp_path):
        seen = []
        loader = Loader(base_dir=str(tmp_path), on_error=lambda e: seen.append(e))
        assert loader.load("nowhere/**/*.json", recursive=True) is None
        assert seen == []


# ---------------------------------------------------------------------------
# Directory loops
# ---------------------------------------------------------------------------

#: Only a Windows junction is descended by ``**``: it is not reported as a
#: symlink. A POSIX directory symlink is never entered, so it cannot loop.
_LINKS_ARE_DESCENDED = sys.platform == "win32"


def _make_dir_link(link, target):
    """Point *link* at the directory *target*, or skip the calling test."""
    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        if completed.returncode != 0:
            pytest.skip("cannot create a directory junction here")
    else:
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("cannot create a directory symlink here")
    from pathapi import LocalPath

    is_junction = getattr(LocalPath(str(link)), "is_junction", None)
    # A fixture that quietly made a plain directory would prove nothing.
    assert os.path.islink(str(link)) or (is_junction is not None and is_junction())


@pytest.fixture
def looped_tree(tmp_path):
    """A tree whose ``deep/loop`` points back at its own root."""
    conf = tmp_path / "conf"
    _write(conf / "10-base.json", {"a": 1})
    _write(conf / "deep" / "20-extra.json", {"b": 2})
    _make_dir_link(conf / "deep" / "loop", conf)
    return conf


@pytest.fixture
def twice_named_tree(tmp_path):
    """One shared directory reachable under two sibling names: not a loop."""
    conf = tmp_path / "conf"
    _write(conf / "own.json", {"a": 1})
    _write(tmp_path / "shared" / "common.json", {"b": 2})
    _make_dir_link(conf / "site-a", tmp_path / "shared")
    _make_dir_link(conf / "site-b", tmp_path / "shared")
    return conf


@pytest.mark.skipif(not _LINKS_ARE_DESCENDED, reason="only a junction is descended")
class TestJunctionLoops:
    def test_loop_is_bounded_by_default(self, looped_tree):
        loader = Loader(base_dir=str(looped_tree), recursive=True)
        assert loader.load("**/*.json") == {"a": 1, "b": 2}
        assert list(loader.load_all("**/*.json")) == [{"a": 1}, {"b": 2}]

    def test_unbounded_walk_fails(self, looped_tree):
        loader = Loader(base_dir=str(looped_tree), recursive=True, bound_loops=False)
        with pytest.raises(OSError):
            loader.load("**/*.json")

    @pytest.mark.parametrize("bound_loops", [True, False])
    def test_directory_named_twice_is_read_under_both_names(
        self, twice_named_tree, bound_loops
    ):
        loader = Loader(
            base_dir=str(twice_named_tree),
            recursive=True,
            collect="list",
            bound_loops=bound_loops,
        )
        assert loader.load("**/*.json") == [{"a": 1}, {"b": 2}, {"b": 2}]


@pytest.mark.skipif(_LINKS_ARE_DESCENDED, reason="Windows junctions are descended")
class TestPosixSymlinksAreNotDescended:
    def test_symlinked_directory_contributes_nothing(self, twice_named_tree):
        loader = Loader(base_dir=str(twice_named_tree), recursive=True, collect="list")
        assert loader.load("**/*.json") == [{"a": 1}]

    def test_symlinked_loop_neither_raises_nor_repeats(self, looped_tree):
        loader = Loader(base_dir=str(looped_tree), recursive=True)
        assert loader.load("**/*.json") == {"a": 1, "b": 2}


class TestDirectorySource:
    def test_a_literal_directory_has_no_format(self, tmp_path):
        (tmp_path / "conf").mkdir()
        with pytest.raises(UnsupportedFormatError):
            Loader(base_dir=str(tmp_path)).load("conf")
