"""``Loader(path_filter=...)``: the caller vets every path before it is read."""

from __future__ import annotations

import pytest
from pathapi import LocalPath

from yaconfiglib import ConfinementError, Loader, Policy


def _json(path, text='{"a": 1}'):
    path.write_text(text, encoding="utf-8")
    return path


class Recorder:
    def __init__(self, allow=lambda path: True):
        self.seen = []
        self.allow = allow

    def __call__(self, path):
        self.seen.append(path)
        return self.allow(path)


class TestPathFilter:
    def test_sees_each_path_as_the_path_object_read(self, tmp_path):
        _json(tmp_path / "a.json")
        check = Recorder()
        assert Loader(base_dir=tmp_path, path_filter=check).load("a.json") == {"a": 1}
        assert len(check.seen) == 1
        assert isinstance(check.seen[0], LocalPath)
        assert str(check.seen[0]) == str(tmp_path / "a.json")

    def test_a_falsy_answer_refuses_before_reading(self, tmp_path):
        _json(tmp_path / "a.json")
        with pytest.raises(ConfinementError, match="path_filter refused"):
            Loader(base_dir=tmp_path, path_filter=lambda path: False).load("a.json")

    def test_a_refusal_is_offered_to_on_error(self, tmp_path):
        _json(tmp_path / "a.json")
        _json(tmp_path / "b.json", '{"b": 2}')
        loader = Loader(
            base_dir=tmp_path,
            path_filter=lambda path: path.name != "a.json",
            on_error=True,
        )
        assert loader.load("a.json", "b.json") == {"b": 2}

    def test_its_own_exception_propagates(self, tmp_path):
        _json(tmp_path / "a.json")

        def refuse(path):
            raise PermissionError("not this one")

        with pytest.raises(PermissionError, match="not this one"):
            Loader(base_dir=tmp_path, path_filter=refuse).load("a.json")

    @pytest.mark.usefixtures("needs_yaml")
    def test_includes_are_checked(self, tmp_path):
        _json(tmp_path / "secret.json", '{"k": "v"}')
        (tmp_path / "app.yaml").write_text(
            "x: !include secret.json\n", encoding="utf-8"
        )
        loader = Loader(
            base_dir=tmp_path, path_filter=lambda path: path.name != "secret.json"
        )
        with pytest.raises(ConfinementError, match="path_filter"):
            loader.load("app.yaml")

    def test_a_pattern_and_its_matches_are_checked(self, tmp_path):
        (tmp_path / "conf").mkdir()
        _json(tmp_path / "conf" / "a.json")
        _json(tmp_path / "conf" / "hidden.json", '{"h": 1}')
        check = Recorder(allow=lambda path: path.name != "hidden.json")
        loader = Loader(base_dir=tmp_path, path_filter=check, on_error=True)
        assert loader.load("conf/*.json") == {"a": 1}
        names = [path.name for path in check.seen]
        assert "*.json" in names and "a.json" in names and "hidden.json" in names

    def test_a_refused_match_is_named_by_its_pattern(self, tmp_path):
        (tmp_path / "conf").mkdir()
        _json(tmp_path / "conf" / "hidden-name.json")
        loader = Loader(
            base_dir=tmp_path, path_filter=lambda path: "hidden" not in path.name
        )
        with pytest.raises(ConfinementError) as caught:
            loader.load("conf/*.json")
        assert "hidden-name" not in str(caught.value)

    def test_per_call_and_type_checked(self, tmp_path):
        _json(tmp_path / "a.json")
        loader = Loader(base_dir=tmp_path)
        with pytest.raises(ConfinementError):
            loader.load("a.json", path_filter=lambda path: False)
        with pytest.raises(TypeError, match="path_filter"):
            Loader(path_filter="no")

    def test_works_beside_confinement(self, tmp_path):
        _json(tmp_path / "a.json")
        loader = Loader(
            base_dir=tmp_path,
            policy=Policy(confine_to=[str(tmp_path)]),
            path_filter=lambda path: True,
        )
        assert loader.load("a.json") == {"a": 1}
