"""``interpolate=`` names the engine: Jinja, any tplx engine, ``"vars"``, or an object."""

from __future__ import annotations

import pytest

from yaconfiglib import (
    CommandsDisabledError,
    Loader,
    Policy,
    TemplateError,
    TemplatesDisabledError,
    VarsInterpolator,
)
from yaconfiglib.sources import Text


class TestVars:
    """The built-in ``${a.b}`` interpolator: no expressions, no extra."""

    def test_references_and_types(self):
        result = Loader(interpolate="vars").load(
            {
                "db": {"host": "h", "ports": [5432, 6432]},
                "url": "pg://${db.host}:${db.ports.1}/app",
                "port": "${db.ports.0}",
                "copy": "${db}",
            }
        )
        assert result["url"] == "pg://h:6432/app"
        assert result["port"] == 5432
        assert result["copy"] == {"host": "h", "ports": [5432, 6432]}

    def test_chains_resolve_in_any_order(self):
        result = Loader(interpolate="vars").load(
            {"err": "${logs}/err", "logs": "${root}/logs", "root": "/opt"}
        )
        assert result == {"err": "/opt/logs/err", "logs": "/opt/logs", "root": "/opt"}

    def test_escape_and_other_syntax_left_alone(self):
        document = {"a": "$${not} {{ jinja }} $HOME", "b": "x"}
        assert Loader(interpolate="vars").load(document) == {
            "a": "${not} {{ jinja }} $HOME",
            "b": "x",
        }

    def test_undefined_is_empty_or_raises_when_strict(self):
        assert Loader(interpolate="vars").load({"a": "x${nope}y", "b": "${nope}"}) == {
            "a": "xy",
            "b": None,
        }
        with pytest.raises(TemplateError, match="undefined reference"):
            Loader(interpolate="vars", policy=Policy(strict=True)).load(
                {"a": "${nope.deeper}"}
            )

    def test_a_per_call_policy_applies_to_the_engine(self):
        loader = Loader(interpolate="vars")
        assert loader.load({"a": "${nope}"}) == {"a": None}
        with pytest.raises(TemplateError):
            loader.load({"a": "${nope}"}, policy=Policy(strict=True))

    def test_malformed_reference(self):
        with pytest.raises(TemplateError, match="malformed"):
            Loader(interpolate="vars").load({"a": "${a..b}"})

    def test_env_with_inject_env(self, monkeypatch):
        monkeypatch.setenv("YACL_VARS_PROBE", "v")
        result = Loader(interpolate="vars", inject_env=True).load(
            {"a": "${env.YACL_VARS_PROBE}"}
        )
        assert result == {"a": "v"}

    def test_reads_no_attribute(self):
        # Only mapping keys and list indexes: `__class__` is just a missing key.
        assert Loader(interpolate="vars").load({"a": "x", "b": "${a.__class__}"}) == {
            "a": "x",
            "b": None,
        }

    def test_a_document_without_references_is_returned_as_is(self):
        document = {"a": "{{ x }}", "b": [1]}
        assert Loader(interpolate="vars").load(document)["b"] == [1]
        assert VarsInterpolator().references("${a.b} ${c} $${d}") == {"a", "c"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_yaml_document(self):
        text = "base: /srv\nlogs: ${base}/logs\nport: 80\nnum: ${port}\n"
        result = Loader(interpolate="vars").load(Text(text, format="yaml"))
        assert result == {"base": "/srv", "logs": "/srv/logs", "port": 80, "num": 80}


@pytest.mark.usefixtures("needs_tplx")
class TestTplxEngines:
    def test_true_is_jinja(self):
        assert Loader(interpolate=True).load({"a": 1, "b": "{{ a + 1 }}"}) == {
            "a": 1,
            "b": 2,
        }

    def test_an_engine_class(self):
        import tplx

        loader = Loader(interpolate=tplx.JinjaTemplate)
        assert loader.load({"a": 1, "b": "{{ a * 3 }}"}) == {"a": 1, "b": 3}

    def test_envsubst_renders_its_own_syntax_only(self):
        result = Loader(interpolate="envsubst").load(
            {"host": "h", "url": "http://${host}/x", "jinja": "{{ host }}"}
        )
        assert result == {"host": "h", "url": "http://h/x", "jinja": "{{ host }}"}

    def test_an_engine_that_cannot_be_sandboxed_is_refused_when_built(self):
        with pytest.raises(TemplatesDisabledError, match="sandbox"):
            Loader(interpolate="mako")

    def test_an_engine_that_runs_a_program_needs_commands(self):
        with pytest.raises(CommandsDisabledError):
            Loader(interpolate="erb", policy=Policy(sandbox=False))

    def test_an_unknown_engine(self):
        with pytest.raises(ValueError, match="no engine is named"):
            Loader(interpolate="nope")


class TestChoice:
    def test_false_and_none_are_off(self):
        for choice in (False, None):
            assert Loader(interpolate=choice).load({"a": "{{ b }}"}) == {"a": "{{ b }}"}

    def test_anything_else_is_a_type_error(self):
        with pytest.raises(TypeError, match="interpolate="):
            Loader(interpolate=3)

    def test_interpolator_keyword_is_gone(self):
        with pytest.raises(TypeError):
            Loader(interpolator=VarsInterpolator())

    def test_an_object_with_is_template_decides_what_renders(self):
        class Percent:
            def is_template(self, text):
                return text.startswith("%")

            def references(self, text):
                return frozenset()

            def render(self, text, scope):
                return text[1:].upper()

        assert Loader(interpolate=Percent()).load({"a": "%x", "b": "{{ y }}"}) == {
            "a": "X",
            "b": "{{ y }}",
        }
