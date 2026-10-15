"""Value interpolation: ``Loader(interpolate=True)``, `TemplateInterpolator` and the pass.

The pass renders the merged document once, with its own string keys as the scope.
`interpolate_document` is the function the loader calls; it is used directly
where an observation needs the caller's own object (identity, mutation).
"""

from __future__ import annotations

import collections
import copy
import gc
import logging
import os
import subprocess
import sys
import threading
import time

import pytest

import yaconfiglib
from yaconfiglib import (
    ConfigError,
    ConfigValueError,
    TemplateInterpolator,
    Loader,
    LoadEvent,
    Policy,
    TemplateError,
    sources,
)
from yaconfiglib._templating import interpolate_document

pytestmark = pytest.mark.usefixtures("needs_jinja2")


def render(*documents, **overrides):
    """Load *documents* (Python data) through a loader that interpolates."""
    return Loader(interpolate=True).load(*documents, **overrides)


def yaml_text(text, name=""):
    return sources.Text(text, format="yaml", name=name)


class Recording:
    """An interpolator that records each string it is given and delegates to Jinja."""

    def __init__(self):
        self.inner = TemplateInterpolator()
        self.rendered = []

    def render(self, text, scope):
        self.rendered.append(text)
        return self.inner.render(text, scope)


# ---------------------------------------------------------------------------
# TemplateInterpolator on its own
# ---------------------------------------------------------------------------


class TestTemplateInterpolator:
    def test_defaults_are_sandboxed_and_lenient(self):
        interpolator = TemplateInterpolator()
        assert interpolator.sandbox is True
        assert interpolator.strict is False

    def test_renders_text(self):
        assert (
            TemplateInterpolator().render("Hello {{ name }}!", {"name": "W"})
            == "Hello W!"
        )

    def test_text_without_variables(self):
        assert TemplateInterpolator().render("static {{ 'x' }}", {}) == "static x"

    @pytest.mark.parametrize(
        "text, scope, expected",
        [
            ("{{ 1 + 2 }}", {}, 3),
            ("{{ x * 2 }}", {"x": 5}, 10),
            ("{{ dict(a=1, b=2) }}", {}, {"a": 1, "b": 2}),
            ("{{ [1, 2] }}", {}, [1, 2]),
            ("{{ flag }}", {"flag": False}, False),
        ],
    )
    def test_bare_expression_keeps_its_type(self, text, scope, expected):
        result = TemplateInterpolator().render(text, scope)
        assert result == expected
        assert type(result) is type(expected)

    @pytest.mark.parametrize("text", ["{{ 1 + 1 }}\n", "{{ 1 + 1 }}\r\n"])
    def test_trailing_newline_keeps_the_type(self, text):
        # A YAML `|` block adds a trailing newline to the scalar.
        assert TemplateInterpolator().render(text, {}) == 2

    @pytest.mark.parametrize("text", ["  {{ 5 }}", "{{ 5 }} ", "\n{{ 5 }}"])
    def test_whitespace_around_one_expression_keeps_the_type(self, text):
        # tplx's rule: whitespace around a single expression is not text.
        assert TemplateInterpolator().render(text, {}) == 5

    def test_text_around_an_expression_renders_text(self):
        assert TemplateInterpolator().render("v{{ 5 }}", {}) == "v5"

    def test_two_expressions_render_as_text(self):
        assert TemplateInterpolator().render("{{ 1 }}{{ 2 }}", {}) == "12"

    def test_scope_is_per_call(self):
        interpolator = TemplateInterpolator()
        # Compiled templates are cached by text; the scope must not be.
        assert interpolator.render("{{ s }}", {"s": "first"}) == "first"
        assert interpolator.render("{{ s }}", {"s": "second"}) == "second"
        assert interpolator.render("v={{ s }}", {"s": "first"}) == "v=first"
        assert interpolator.render("v={{ s }}", {"s": "second"}) == "v=second"

    def test_plain_text_comes_back_as_it_is(self):
        text = "no template here"
        assert TemplateInterpolator().render(text, {}) is text

    def test_the_template_sees_only_the_names_it_reads(self):
        class Watched(dict):
            read = []

            def __getitem__(self, key):
                Watched.read.append(key)
                return super().__getitem__(key)

        scope = Watched(a=1, b=2, c=3)
        assert TemplateInterpolator().render("{{ a }}-{{ b }}", scope) == "1-2"
        assert sorted(set(Watched.read)) == ["a", "b"]

    def test_sandbox_blocks_python_internals(self):
        with pytest.raises(TemplateError, match="unsafe"):
            TemplateInterpolator().render("{{ ''.__class__.__mro__ }}", {})
        with pytest.raises(TemplateError, match="unsafe"):
            TemplateInterpolator().render("x {{ ''.__class__.__mro__ }}", {})

    def test_bare_expression_works_sandboxed(self):
        # The result is captured through a render variable, never an attribute
        # the sandbox would refuse.
        result = TemplateInterpolator(sandbox=True).render(
            "{{ base * 2 }}", {"base": 21}
        )
        assert result == 42 and isinstance(result, int)

    def test_without_sandbox_internals_are_reachable(self):
        unsandboxed = TemplateInterpolator(sandbox=False)
        assert unsandboxed.render("{{ ''.__class__.__name__ }}", {}) == "str"

    def test_strict_raises_on_undefined(self):
        strict = TemplateInterpolator(strict=True)
        with pytest.raises(TemplateError, match="undefined"):
            strict.render("{{ nosuch }}", {})
        with pytest.raises(TemplateError, match="undefined"):
            strict.render("x {{ nosuch }}", {})

    def test_lenient_renders_undefined_empty(self):
        assert TemplateInterpolator().render("x{{ nosuch }}y", {}) == "xy"


class TestSharedInterpolator:
    """One interpolator serves every load and every thread; tplx caches the templates."""

    def test_concurrent_rendering(self):
        interpolator = TemplateInterpolator()
        failures = []

        def work(offset):
            try:
                for index in range(300):
                    value = (index + offset) % 7
                    assert interpolator.render("v{{ n }}", {"n": value}) == f"v{value}"
                    assert interpolator.render("{{ n + 1 }}", {"n": value}) == value + 1
            except Exception as error:  # noqa: BLE001 - reported below
                failures.append(error)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert failures == []

    def test_loader_reuses_the_default_interpolator(self):
        from yaconfiglib._templating import default_interpolator

        assert default_interpolator("jinja", True, False) is default_interpolator(
            "jinja", True, False
        )
        assert default_interpolator("jinja", True, False) is not default_interpolator(
            "jinja", False, False
        )


# ---------------------------------------------------------------------------
# The pass, called directly
# ---------------------------------------------------------------------------


class TestInterpolateDocument:
    def test_template_free_document_is_returned_as_is(self):
        document = {"a": [1, {"b": "c"}], "n": None, "t": (1, "x"), 404: "plain"}
        assert interpolate_document(document, TemplateInterpolator()) is document

    def test_input_is_never_modified(self):
        document = {
            "a": 1,
            "n": {"k": ["{{ a }}", "x"]},
            "t": ("{{ a }}",),
            "{{ 'key' }}": "v",
        }
        before = copy.deepcopy(document)
        result = interpolate_document(document, TemplateInterpolator())
        assert document == before
        assert result == {"a": 1, "n": {"k": [1, "x"]}, "t": (1,), "key": "v"}

    def test_input_is_never_modified_on_failure(self):
        document = {"db": {"host": "h", "password": "p{% raw", "port": 5432}}
        before = copy.deepcopy(document)
        with pytest.raises(TemplateError):
            interpolate_document(document, TemplateInterpolator())
        assert document == before

    def test_unchanged_parts_are_shared(self):
        document = {"a": "{{ 1 }}", "plain": {"z": [1, 2]}, "lst": [{"k": "v"}]}
        result = interpolate_document(document, TemplateInterpolator())
        assert result is not document
        assert result["plain"] is document["plain"]
        assert result["lst"] is document["lst"]

    def test_containers_keep_their_types(self):
        class Config(dict):
            pass

        document = {
            "a": 1,
            "t": ("{{ a }}", "x"),
            "s": {"{{ a }}"},
            "f": frozenset({"{{ a + 1 }}"}),
            "o": collections.OrderedDict([("k", "{{ a }}")]),
            "c": Config(k="{{ a }}"),
        }
        result = interpolate_document(document, TemplateInterpolator())
        assert result["t"] == (1, "x") and type(result["t"]) is tuple
        assert result["s"] == {1} and type(result["s"]) is set
        assert result["f"] == frozenset({2}) and type(result["f"]) is frozenset
        assert type(result["o"]) is collections.OrderedDict
        assert result["o"] == {"k": 1}
        assert type(result["c"]) is Config and result["c"] == {"k": 1}

    def test_dict_subclass_document_keeps_its_type(self):
        document = collections.OrderedDict([("a", 1), ("b", "{{ a }}")])
        result = interpolate_document(document, TemplateInterpolator())
        assert type(result) is collections.OrderedDict
        assert result == {"a": 1, "b": 1}

    def test_aliases_are_rendered_once_and_stay_aliased(self):
        shared = ["{{ a }}"]
        recording = Recording()
        result = interpolate_document({"a": 5, "x": shared, "y": shared}, recording)
        assert result["x"] == [5]
        assert result["x"] is result["y"]
        # Every string is offered once; each distinct node is visited once.
        assert recording.rendered.count("{{ a }}") == 1

    def test_self_referencing_container_terminates(self):
        looped = ["{{ 1 + 1 }}"]
        looped.append(looped)
        result = interpolate_document({"l": looped}, TemplateInterpolator())
        assert result["l"][0] == 2

    def test_non_mapping_document(self):
        assert interpolate_document(["{{ 1 + 1 }}", "x"], TemplateInterpolator()) == [
            2,
            "x",
        ]
        assert interpolate_document("{{ 3 }}", TemplateInterpolator()) == 3

    def test_extra_names_win_over_document_keys(self):
        document = {"env": "doc", "v": "{{ env }}"}
        result = interpolate_document(
            document, TemplateInterpolator(), extra={"env": "x"}
        )
        assert result == {"env": "doc", "v": "x"}

    def test_on_error_keeps_the_template_text(self):
        seen = []

        def skip(error, keypath):
            seen.append((type(error), keypath))
            return True

        document = {"d": {"p": "p{% raw", "h": "{{ b }}"}, "b": "/s"}
        result = interpolate_document(document, TemplateInterpolator(), on_error=skip)
        assert result == {"d": {"p": "p{% raw", "h": "/s"}, "b": "/s"}
        assert seen == [(TemplateError, ("d", "p"))]


# ---------------------------------------------------------------------------
# Through the loader
# ---------------------------------------------------------------------------


class TestLoaderInterpolation:
    def test_off_by_default(self):
        assert Loader().load({"a": "{{ 1 }}"}) == {"a": "{{ 1 }}"}

    def test_per_call_override(self):
        assert Loader().load({"a": "{{ 1 }}"}, interpolate=True) == {"a": 1}
        loader = Loader(interpolate=True)
        assert loader.load({"a": "{{ 1 }}"}, interpolate=False) == {"a": "{{ 1 }}"}

    def test_one_pass_in_the_merged_scope(self):
        # A template in one layer reads a key another layer defines.
        result = render({"host": "example.com"}, {"url": "http://{{ host }}/api"})
        assert result["url"] == "http://example.com/api"

    def test_a_later_layer_changes_what_a_template_sees(self):
        result = render({"root": "/srv", "logs": "{{ root }}/logs"}, {"root": "/opt"})
        assert result["logs"] == "/opt/logs"

    @pytest.mark.parametrize(
        "document",
        [
            {"base": "/srv", "logs": "{{ base }}/logs", "err": "{{ logs }}/err"},
            {"err": "{{ logs }}/err", "logs": "{{ base }}/logs", "base": "/srv"},
            {"logs": "{{ base }}/logs", "err": "{{ logs }}/err", "base": "/srv"},
        ],
    )
    def test_chains_resolve_in_any_order(self, document):
        assert render(document)["err"] == "/srv/logs/err"

    def test_nested_reference_before_its_mapping(self):
        document = {
            "full": "{{ db.url }}/p",
            "db": {"host": "h", "url": "x://{{ db.host }}"},
        }
        assert render(document)["full"] == "x://h/p"

    def test_rendered_value_is_never_rendered_again(self):
        document = {"x": "SURPRISE", "lit": "{{ '{{ x }}' }}", "ref": "{{ lit }}"}
        result = render(document)
        assert result["lit"] == "{{ x }}"
        assert result["ref"] == "{{ x }}"

    def test_raw_block_stays_literal(self):
        result = render({"x": "S", "lit": "{% raw %}{{ x }}{% endraw %}"})
        assert result["lit"] == "{{ x }}"

    def test_bare_expression_keeps_its_type(self):
        result = render(
            {
                "base": 21,
                "n": "{{ base * 2 }}",
                "d": "{{ dict(a=1) }}",
                "b": "{{ base > 1 }}",
            }
        )
        assert result["n"] == 42 and isinstance(result["n"], int)
        assert result["d"] == {"a": 1}
        assert result["b"] is True

    def test_whitespace_may_surround_a_bare_expression(self):
        result = render(
            {"a": 2, "nl": "{{ a }}\n", "sp": " {{ a }}", "txt": "x{{ a }}"}
        )
        assert result["nl"] == 2
        assert result["sp"] == 2
        assert result["txt"] == "x2"

    def test_non_string_top_level_keys_do_not_break_rendering(self):
        result = render({404: "{{ name }}", "name": "x", 1.5: "y", None: "{{ name }}!"})
        assert result == {404: "x", "name": "x", 1.5: "y", None: "x!"}

    def test_templated_keys_render_after_values(self):
        result = render({"name": "{{ base }}-x", "base": "b", "{{ name }}": 1})
        assert result == {"name": "b-x", "base": "b", "b-x": 1}

    def test_templated_key_collision_raises(self):
        with pytest.raises(ConfigValueError, match="renders to the same key as"):
            render({"a": 1, "{{ 'a' }}": 2})

    def test_skipped_key_collision_keeps_the_template_key(self):
        result = render({"a": 1, "{{ 'a' }}": 2}, on_error=True)
        assert result == {"a": 1, "{{ 'a' }}": 2}

    def test_key_collision_message_does_not_quote_the_rendered_key(self, monkeypatch):
        monkeypatch.setenv("YACFG_COLLIDE_A", "s3cr3t-rendered-key")
        monkeypatch.setenv("YACFG_COLLIDE_B", "s3cr3t-rendered-key")
        document = {"{{ env.YACFG_COLLIDE_A }}": 1, "{{ env.YACFG_COLLIDE_B }}": 2}
        with pytest.raises(ConfigValueError) as caught:
            render(document, inject_env=True)
        assert "s3cr3t-rendered-key" not in str(caught.value)

    def test_reference_cycle_raises_under_strict(self):
        with pytest.raises(
            ConfigValueError, match="reference cycle: a -> b -> a"
        ) as caught:
            render({"a": "{{ b }}", "b": "{{ a }}"}, policy=Policy(strict=True))
        assert caught.value.config_key == ("a",)

    def test_reference_cycle_is_lenient_without_strict(self):
        result = render({"a": "{{ b }}", "b": "{{ a }}"})
        assert set(result) == {"a", "b"}

    def test_sibling_reference_is_not_a_cycle_under_strict(self):
        document = {"db": {"host": "h", "url": "x://{{ db.host }}"}}
        result = render(document, policy=Policy(strict=True))
        assert result["db"]["url"] == "x://h"

    def test_strict_undefined_is_template_error(self):
        with pytest.raises(TemplateError, match="undefined") as caught:
            render({"value": "{{ missing_var }}"}, policy=Policy(strict=True))
        assert caught.value.__cause__ is not None
        assert caught.value.config_key == ("value",)

    def test_lenient_undefined_renders_empty(self):
        assert render({"value": "x{{ missing_var }}"}) == {"value": "x"}

    @pytest.mark.parametrize(
        "document, keypath, rendered_key, cause_name",
        [
            ({"other": "{{ x"}, ("other",), "other", "TemplateSyntaxError"),
            ({"r": {"q": "{{ 1/0 }}"}}, ("r", "q"), "r.q", "ZeroDivisionError"),
            (
                {"servers": [{"host": "{{ x"}]},
                ("servers", 0, "host"),
                "servers[0].host",
                "TemplateSyntaxError",
            ),
            ({"s": "{{ ''.__class__.__mro__ }}"}, ("s",), "s", "SecurityError"),
            ({"u": "{{ nosuch.attr }}"}, ("u",), "u", "UndefinedError"),
            ({"{{ x": 1}, ("{{ x",), "{{ x", "TemplateSyntaxError"),
        ],
    )
    def test_every_failure_is_a_template_error(
        self, document, keypath, rendered_key, cause_name
    ):
        with pytest.raises(TemplateError) as caught:
            render(document)
        error = caught.value
        assert isinstance(error, ConfigError) and isinstance(error, ValueError)
        assert type(error.__cause__).__name__ == cause_name
        assert error.config_key == keypath
        assert f"at {rendered_key}" in str(error)

    def test_on_error_true_skips_one_value_and_keeps_its_text(self, caplog):
        document = {
            "database": {"host": "db.internal", "password": "p{% raw", "port": 5432},
            "log_path": "{{ base }}/logs",
            "base": "/srv",
        }
        caplog.set_level(logging.WARNING, logger="yaconfiglib")
        result = render(document, on_error=True)
        assert result == {
            "database": {"host": "db.internal", "password": "p{% raw", "port": 5432},
            "log_path": "/srv/logs",
            "base": "/srv",
        }
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("interpolate" in m and "TemplateError" in m for m in warnings)
        assert not any("p{% raw" in m for m in warnings)

    def test_on_error_callable_receives_the_interpolate_phase_and_key(self):
        events = []

        def record(event):
            events.append(event)
            return True

        render({"database": {"password": "p{% raw"}}, on_error=record)
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, LoadEvent)
        assert event.phase == "interpolate"
        assert event.path is None
        assert event.key == ("database", "password")
        assert isinstance(event.error, TemplateError)

    def test_declined_failure_raises_the_offered_error(self):
        events = []

        def decline(event):
            events.append(event)
            return False

        with pytest.raises(TemplateError) as caught:
            render({"a": "{{ nosuch }}"}, on_error=decline, policy=Policy(strict=True))
        assert events[-1].error is caught.value

    def test_skipped_key_template_keeps_the_key_as_written(self):
        result = render(
            {"{% raw": "value", "base": "/srv", "kept": "{{ base }}/x"}, on_error=True
        )
        assert result == {"{% raw": "value", "base": "/srv", "kept": "/srv/x"}

    def test_strict_cycle_is_offered_and_a_skip_keeps_the_keys(self):
        events = []

        def skip(event):
            events.append(event)
            return True

        result = render(
            {"a": "{{ b }}", "b": "{{ a }}"}, on_error=skip, policy=Policy(strict=True)
        )
        assert events and events[0].phase == "interpolate"
        assert isinstance(events[0].error, ConfigValueError)
        assert set(result) == {"a", "b"}

    def test_load_all_interpolates_each_document(self):
        loader = Loader(interpolate=True)
        documents = list(loader.load_all({"a": 1, "b": "{{ a }}"}, {"c": "{{ 2 }}"}))
        assert documents == [{"a": 1, "b": 1}, {"c": 2}]

    def test_load_all_skip_yields_the_document(self):
        loader = Loader(interpolate=True, on_error=True)
        documents = list(loader.load_all({"p": "p{% raw", "h": "{{ 1 }}"}))
        assert documents == [{"p": "p{% raw", "h": 1}]

    def test_load_all_declined_failure_raises(self):
        with pytest.raises(TemplateError):
            list(Loader(interpolate=True).load_all({"a": "{{ x"}))

    def test_interpolation_runs_before_hydration(self):
        import dataclasses

        @dataclasses.dataclass
        class Model:
            base: int
            doubled: int

        result = render({"base": 21, "doubled": "{{ base * 2 }}"}, into=Model)
        assert result == Model(base=21, doubled=42)

    def test_interpolation_runs_before_wrapping(self):
        result = render({"db": {"host": "h", "url": "x://{{ db.host }}"}}, wrap=True)
        assert result.db.url == "x://h"


class TestEnvironment:
    def test_inject_env_puts_env_in_scope(self, monkeypatch):
        monkeypatch.setenv("YACFG_MODE_VAR", "production")
        result = render({"mode": "{{ env.YACFG_MODE_VAR }}"}, inject_env=True)
        assert result == {"mode": "production"}

    def test_env_is_not_in_scope_without_inject_env(self, monkeypatch):
        monkeypatch.setenv("YACFG_MODE_VAR", "production")
        with pytest.raises(TemplateError):
            render({"mode": "{{ env.YACFG_MODE_VAR }}"}, policy=Policy(strict=True))

    @pytest.mark.parametrize(
        "template",
        [
            "{% do env.update({'YACFG_PWN': 'x'}) %}ok",
            "{{ env.pop('PATH') }}",
            "{% do env.__setitem__('YACFG_PWN', 'x') %}ok",
        ],
    )
    def test_env_is_read_only(self, monkeypatch, template):
        monkeypatch.delenv("YACFG_PWN", raising=False)
        path_before = os.environ.get("PATH")
        with pytest.raises(TemplateError):
            render({"v": template}, inject_env=True)
        assert "YACFG_PWN" not in os.environ
        assert os.environ.get("PATH") == path_before

    def test_env_is_read_only_without_the_sandbox(self, monkeypatch):
        monkeypatch.delenv("YACFG_PWN", raising=False)
        with pytest.raises(TemplateError):
            render(
                {"v": "{% do env.update({'YACFG_PWN': 'x'}) %}ok"},
                inject_env=True,
                policy=Policy(sandbox=False),
            )
        assert "YACFG_PWN" not in os.environ

    def test_env_wins_over_a_document_key_named_env(self, monkeypatch):
        monkeypatch.setenv("YACFG_SCOPE_VAR", "from-environment")
        result = render(
            {"env": "doc", "v": "{{ env.YACFG_SCOPE_VAR }}"}, inject_env=True
        )
        assert result == {"env": "doc", "v": "from-environment"}


class TestSandbox:
    def test_default_policy_blocks_python_internals(self):
        from jinja2.exceptions import SecurityError

        with pytest.raises(TemplateError) as caught:
            render({"x": "{{ ''.__class__.__mro__ }}"})
        assert isinstance(caught.value.__cause__, SecurityError)

    def test_text_form_is_blocked_too(self):
        with pytest.raises(TemplateError):
            render({"x": "pre {{ ''.__class__.__mro__ }}"})

    def test_ordinary_interpolation_works_sandboxed(self):
        result = render({"greeting": "hello {{ name }}", "name": "world"})
        assert result["greeting"] == "hello world"

    def test_bare_expression_works_sandboxed(self):
        result = render(
            {"name": "world", "greeting": "{{ name }}", "n": "{{ 21 * 2 }}"}
        )
        assert result["greeting"] == "world"
        assert result["n"] == 42 and isinstance(result["n"], int)

    def test_sandbox_off_reaches_internals(self):
        result = render(
            {"x": "{{ ''.__class__.__name__ }}"}, policy=Policy(sandbox=False)
        )
        assert result == {"x": "str"}

    def test_load_all_per_call_policy(self):
        loader = Loader(interpolate=True, policy=Policy(sandbox=False))
        with pytest.raises(TemplateError):
            list(loader.load_all({"x": "{{ ''.__class__.__mro__ }}"}, policy=Policy()))


class TestLogging:
    def test_debug_logs_name_the_template_never_the_value(self, caplog, monkeypatch):
        monkeypatch.setenv("YACFG_LOG_SECRET", "pa55-secret-value")
        caplog.set_level(logging.DEBUG)
        result = render({"password": "{{ env.YACFG_LOG_SECRET }}"}, inject_env=True)
        assert result == {"password": "pa55-secret-value"}
        messages = [record.getMessage() for record in caplog.records]
        assert not [m for m in messages if "pa55-secret-value" in m]
        assert any("{{ env.YACFG_LOG_SECRET }}" in m for m in messages)

    def test_error_message_names_the_template_not_the_value(self, monkeypatch):
        monkeypatch.setenv("YACFG_LOG_SECRET", "pa55-secret-value")
        document = {"a": "{{ env.YACFG_LOG_SECRET }}", "b": "{{ a ~ 1/0 }}"}
        with pytest.raises(TemplateError) as caught:
            render(document, inject_env=True)
        assert "pa55-secret-value" not in str(caught.value)


class TestCustomInterpolator:
    def test_custom_interpolator_is_used(self):
        class Shout:
            """Renders what it recognises; any other text comes back as it is."""

            def __init__(self):
                self.seen = []

            def render(self, text, scope):
                if "{" not in text:
                    return text
                self.seen.append(text)
                return text.upper()

        shout = Shout()
        result = Loader(interpolate=shout).load(
            {"a": "{{ x }}", "b": "plain", "c": ["{% y %}"]}
        )
        assert result == {"a": "{{ X }}", "b": "plain", "c": ["{% Y %}"]}
        assert sorted(shout.seen) == ["{% y %}", "{{ x }}"]

    def test_a_name_read_renders_first(self):
        class Lookup:
            """Reads the scope; a name that is a template renders first."""

            def render(self, text, scope):
                if not text.startswith("{{"):
                    return text
                return f"<{scope[text[3:-3]]}>"

        result = Loader(interpolate=Lookup()).load(
            {"c": "{{ b }}", "b": "{{ a }}", "a": "v"}
        )
        assert result == {"c": "<<v>>", "b": "<v>", "a": "v"}

    def test_custom_failure_is_a_template_error(self):
        class Broken:
            def references(self, text):
                return frozenset()

            def render(self, text, scope):
                raise KeyError("nope")

        with pytest.raises(TemplateError) as caught:
            Loader(interpolate=Broken()).load({"k": {"v": "{{ x }}"}})
        assert isinstance(caught.value.__cause__, KeyError)
        assert caught.value.config_key == ("k", "v")

    def test_custom_config_error_passes_through(self):
        class Refusing:
            def references(self, text):
                return frozenset()

            def render(self, text, scope):
                raise ConfigValueError("refused")

        with pytest.raises(ConfigValueError, match="refused") as caught:
            Loader(interpolate=Refusing()).load({"k": "{{ x }}"})
        assert caught.value.config_key == ("k",)


@pytest.mark.usefixtures("needs_yaml")
class TestYamlDocuments:
    @staticmethod
    def write(tmp_path, name, text):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_include_sees_the_parent_scope(self, tmp_path):
        self.write(tmp_path, "svc.yaml", 'url: "http://{{ host }}/api"\n')
        self.write(tmp_path, "app.yaml", "host: example.com\nsvc: !include svc.yaml\n")
        result = Loader(base_dir=tmp_path, interpolate=True).load("app.yaml")
        assert result["svc"]["url"] == "http://example.com/api"

    def test_include_parent_reference_under_strict(self, tmp_path):
        # Includes are not interpolated on their own, so a name only the
        # including document defines is not undefined.
        self.write(tmp_path, "svc.yaml", 'url: "http://{{ host }}/api"\n')
        self.write(tmp_path, "app.yaml", "host: example.com\nsvc: !include svc.yaml\n")
        loader = Loader(base_dir=tmp_path, interpolate=True, policy=Policy(strict=True))
        assert loader.load("app.yaml")["svc"]["url"] == "http://example.com/api"

    def test_escaped_literal_in_an_include_is_rendered_once(self, tmp_path):
        self.write(tmp_path, "inc.yaml", "lit: \"{{ '{{ x }}' }}\"\n")
        self.write(tmp_path, "app.yaml", "x: SURPRISE\ninc: !include inc.yaml\n")
        result = Loader(base_dir=tmp_path, interpolate=True).load("app.yaml")
        assert result["inc"]["lit"] == "{{ x }}"

    def test_yaml_block_scalar_keeps_the_type(self):
        text = "base: 20\nn: |\n  {{ base + 1 }}\n"
        assert render(yaml_text(text)) == {"base": 20, "n": 21}

    def test_aliased_yaml_interpolates_in_linear_time(self):
        # 7 levels, each a list of 9 aliases of the previous: walking every
        # reference is ~4.8M nodes, walking each shared container once is 7.
        lines = ['l0: &l0 ["{{ 1 + 1 }}", "plain"]']
        for level in range(1, 7):
            aliases = ", ".join([f"*l{level - 1}"] * 9)
            lines.append(f"l{level}: &l{level} [{aliases}]")
        lines.append("top: *l6")
        started = time.perf_counter()
        result = render(yaml_text("\n".join(lines) + "\n"))
        elapsed = time.perf_counter() - started
        assert elapsed < 2, f"interpolation took {elapsed:.1f}s"
        assert result["top"][0] is result["top"][1]
        assert result["l0"] == [2, "plain"]

    def test_merged_files_share_one_scope(self, tmp_path):
        self.write(tmp_path, "base.yaml", 'root: /srv\nlogs: "{{ root }}/logs"\n')
        self.write(tmp_path, "local.yaml", "root: /opt\n")
        result = Loader(base_dir=tmp_path, interpolate=True).load(
            "base.yaml", "local.yaml"
        )
        assert result == {"root": "/opt", "logs": "/opt/logs"}


def test_missing_jinja2_names_the_extra(tmp_path):
    """Without the extra, asking for Jinja raises naming it when the loader is built.

    ``interpolate="vars"`` needs neither tplx nor Jinja2.
    """
    package_root = os.path.dirname(
        os.path.dirname(os.path.abspath(yaconfiglib.__file__))
    )
    code = (
        "import sys\n"
        f"sys.path.insert(0, {package_root!r})\n"
        "sys.modules['jinja2'] = None\n"
        "sys.modules['tplx'] = None\n"
        "import yaconfiglib\n"
        "try:\n"
        "    loader = yaconfiglib.Loader(interpolate=True, on_error=True)\n"
        "    print('RESULT', loader.load({'a': '{{ 1 }}'}))\n"
        "except yaconfiglib.MissingDependencyError as error:\n"
        "    print('MISSING', isinstance(error, ImportError), error)\n"
        "plain = yaconfiglib.Loader(interpolate='vars')\n"
        "print('VARS', plain.load({'a': 1, 'b': '${a}'}))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
    )
    assert "MISSING True" in done.stdout, done.stdout + done.stderr
    assert "yaconfiglib[templates]" in done.stdout
    assert "RESULT" not in done.stdout
    assert "VARS {'a': 1, 'b': 1}" in done.stdout


@pytest.mark.usefixtures("needs_tplx")
class TestExpressionsBuildPlainData:
    def test_a_container_built_by_an_expression_holds_no_views(self):
        result = Loader(interpolate=True).load(
            {
                "db": {"host": "h", "ports": [1, 2]},
                "b": "{{ {'x': db} }}",
                "c": "{{ [db] }}",
                "f": "{{ (db, 1) }}",
            }
        )
        assert result["b"] == {"x": {"host": "h", "ports": [1, 2]}}
        assert type(result["b"]["x"]) is dict
        assert type(result["c"][0]) is dict
        assert type(result["f"][0]) is dict
        assert type(result["b"]["x"]["ports"]) is list
