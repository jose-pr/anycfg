"""Template files: ``<name>.<format>.<engine suffix>`` rendered, then parsed.

The policy decides: refused by default; ``allow_templates`` with the sandbox renders
Jinja only, in an environment with no template loader; without the sandbox any tplx
engine renders, and one that runs a program also needs ``allow_commands``.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

import anycfg
from _extras import needs_tplx, needs_yaml
from anycfg import (
    CommandsDisabledError,
    ConfigError,
    Loader,
    LoadEvent,
    MissingDependencyError,
    ParseError,
    Policy,
    PolicyError,
    TemplateError,
    TemplatesDisabledError,
    UnsupportedFormatError,
)

pytestmark = pytest.mark.usefixtures("needs_jinja2")

SANDBOXED = Policy(allow_templates=True)


def write(directory, name, text):
    path = pathlib.Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def load(directory, name, policy=SANDBOXED, **settings):
    return Loader(base_dir=directory, policy=policy, **settings).load(name)


def frame_kinds(error):
    return [frame.kind for frame in getattr(error, "config_frames", ()) or ()]


class TestRefusedByDefault:
    def test_loader_refuses(self, tmp_path):
        write(tmp_path, "app.json.j2", '{"v": {{ 1 + 1 }}}')
        with pytest.raises(TemplatesDisabledError) as caught:
            Loader(base_dir=tmp_path).load("app.json.j2")
        error = caught.value
        assert isinstance(error, PolicyError) and isinstance(error, PermissionError)
        assert "allow_templates" in str(error)

    def test_module_load_refuses(self, tmp_path):
        source = write(tmp_path, "app.json.j2", '{"v": {{ 1 + 1 }}}')
        with pytest.raises(TemplatesDisabledError):
            anycfg.load(str(source))

    def test_module_load_renders_with_the_policy(self, tmp_path):
        source = write(tmp_path, "app.json.j2", '{"v": {{ 1 + 1 }}}')
        assert anycfg.load(str(source), policy=SANDBOXED) == {"v": 2}

    @pytest.mark.usefixtures("needs_yaml")
    def test_include_of_a_template_follows_the_loading_policy(self, tmp_path):
        write(tmp_path, "inc.yaml.j2", "v: {{ 3 }}\n")
        write(tmp_path, "app.yaml", "x: !include inc.yaml.j2\n")
        with pytest.raises(TemplatesDisabledError) as caught:
            Loader(base_dir=tmp_path).load("app.yaml")
        assert "include" in frame_kinds(caught.value)
        assert load(tmp_path, "app.yaml") == {"x": {"v": 3}}

    @pytest.mark.parametrize(
        "name, policy, expected",
        [
            pytest.param(
                "missing.json.j2", Policy(), TemplatesDisabledError, id="default"
            ),
            pytest.param(
                "missing.json.mako",
                SANDBOXED,
                TemplatesDisabledError,
                id="sandboxed-mako",
                marks=needs_tplx,
            ),
            pytest.param(
                "missing.json.erb",
                Policy.trusted(allow_commands=False),
                CommandsDisabledError,
                id="erb-without-commands",
                marks=needs_tplx,
            ),
        ],
    )
    def test_refusal_comes_before_reading(self, tmp_path, name, policy, expected):
        # The file does not exist: a refusal raised before reading wins over
        # the FileNotFoundError reading it would raise.
        with pytest.raises(expected):
            load(tmp_path, name, policy=policy)

    def test_refusal_is_offered_to_on_error(self, tmp_path):
        write(tmp_path, "a.json", '{"a": 1}')
        write(tmp_path, "b.json.j2", '{"b": {{ 2 }}}')
        events = []

        def skip(event):
            events.append(event)
            return True

        result = Loader(base_dir=tmp_path, on_error=skip).load("a.json", "b.json.j2")
        assert result == {"a": 1}
        assert len(events) == 1
        assert isinstance(events[0], LoadEvent)
        assert events[0].phase == "load"
        assert isinstance(events[0].error, TemplatesDisabledError)


class TestSandboxed:
    @pytest.mark.parametrize("suffix", [".j2", ".jinja", ".jinja2", ".J2"])
    def test_jinja_suffixes_render(self, tmp_path, suffix):
        write(tmp_path, "app.json" + suffix, '{"v": {{ 1 + 1 }}}')
        assert load(tmp_path, "app.json" + suffix) == {"v": 2}

    @pytest.mark.parametrize(
        "statement",
        [
            "{% include 'part.txt' %}",
            "{% import 'part.txt' as part %}",
            "{% extends 'part.txt' %}",
        ],
    )
    def test_no_template_loader(self, tmp_path, statement):
        write(tmp_path, "part.txt", '{"included": true}')
        write(tmp_path, "app.json.j2", statement)
        with pytest.raises(TemplateError) as caught:
            load(tmp_path, "app.json.j2")
        assert caught.value.__cause__ is not None

    def test_python_internals_are_blocked(self, tmp_path):
        from jinja2.exceptions import SecurityError

        write(tmp_path, "app.json.j2", '{"v": "{{ \'\'.__class__.__mro__ }}"}')
        with pytest.raises(TemplateError) as caught:
            load(tmp_path, "app.json.j2")
        assert isinstance(caught.value.__cause__, SecurityError)

    @pytest.mark.usefixtures("needs_tplx")
    @pytest.mark.parametrize("suffix", [".mako", ".erb", ".cheetah", ".epp"])
    def test_engines_that_cannot_be_sandboxed_are_refused(self, tmp_path, suffix):
        write(tmp_path, "app.json" + suffix, '{"v": 1}')
        with pytest.raises(TemplatesDisabledError, match="sandbox"):
            load(tmp_path, "app.json" + suffix)

    def test_strict_policy_raises_on_undefined(self, tmp_path):
        write(tmp_path, "app.json.j2", '{"v": "{{ nosuch }}"}')
        assert load(tmp_path, "app.json.j2") == {"v": ""}
        with pytest.raises(TemplateError, match="undefined"):
            load(tmp_path, "app.json.j2", policy=SANDBOXED.replace(strict=True))

    def test_without_tplx_a_template_names_the_extra(self, tmp_path):
        write(tmp_path, "app.json.j2", '{"v": {{ 1 + 1 }}}')
        package_root = os.path.dirname(
            os.path.dirname(os.path.abspath(anycfg.__file__))
        )
        code = (
            "import sys\n"
            f"sys.path.insert(0, {package_root!r})\n"
            "sys.modules['tplx'] = None\n"
            "import importlib.util\n"
            "importlib.util.find_spec = lambda name, *a: None if name == 'tplx' "
            "else __import__('importlib').machinery.PathFinder.find_spec(name)\n"
            "import anycfg\n"
            "loader = anycfg.Loader(\n"
            f"    base_dir={str(tmp_path)!r},\n"
            "    policy=anycfg.Policy(allow_templates=True),\n"
            ")\n"
            "try:\n"
            "    loader.load('app.json.j2')\n"
            "except anycfg.MissingDependencyError as error:\n"
            "    print('MISSING', error)\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        assert "MISSING" in done.stdout and "anycfg[templates]" in done.stdout, (
            done.stdout + done.stderr
        )

    def test_liquid_is_sandboxable(self, tmp_path):
        pytest.importorskip("liquid")
        write(tmp_path, "app.json.liquid", '{"v": "{{ filename }}"}')
        assert load(tmp_path, "app.json.liquid") == {"v": "app.json.liquid"}


@pytest.mark.usefixtures("needs_tplx")
class TestTrusted:
    def test_renders_jinja_through_tplx_with_includes(self, tmp_path):
        write(tmp_path, "part.txt", '"included": true')
        write(tmp_path, "app.json.j2", "{ {% include 'part.txt' %} }")
        assert load(tmp_path, "app.json.j2", policy=Policy.trusted()) == {
            "included": True
        }

    def test_includes_resolve_next_to_the_template(self, tmp_path):
        write(tmp_path, "conf/part.txt", '"where": "next to the template"')
        write(tmp_path, "conf/app.json.j2", "{ {% include 'part.txt' %} }")
        write(tmp_path, "part.txt", '"where": "base_dir"')
        result = load(tmp_path, "conf/app.json.j2", policy=Policy.trusted())
        assert result == {"where": "next to the template"}

    def test_includes_resolve_under_the_confinement_roots(self, tmp_path):
        roots = tmp_path / "partials"
        write(roots, "part.txt", '"where": "root"')
        write(tmp_path, "conf/app.json.j2", "{ {% include 'part.txt' %} }")
        policy = Policy.trusted(confine_to=[str(roots), str(tmp_path / "conf")])
        assert load(tmp_path, "conf/app.json.j2", policy=policy) == {"where": "root"}
        # Without roots only the template's own directory is searched.
        with pytest.raises(TemplateError):
            load(tmp_path, "conf/app.json.j2", policy=Policy.trusted())

    def test_unsandboxed_reaches_python_internals(self, tmp_path):
        write(tmp_path, "app.json.j2", '{"v": "{{ \'\'.__class__.__name__ }}"}')
        assert load(tmp_path, "app.json.j2", policy=Policy.trusted()) == {"v": "str"}

    def test_strict_raises_on_undefined(self, tmp_path):
        write(tmp_path, "app.json.j2", '{"v": "{{ nosuch }}"}')
        assert load(tmp_path, "app.json.j2", policy=Policy.trusted()) == {"v": ""}
        with pytest.raises(TemplateError):
            load(tmp_path, "app.json.j2", policy=Policy.trusted(strict=True))

    @pytest.mark.skipif(
        importlib.util.find_spec("mako") is not None,
        reason="mako is installed, so its engine is available",
    )
    def test_engine_without_its_extra_is_missing_dependency(self, tmp_path):
        write(tmp_path, "app.json.mako", '{"v": ${1 + 1}}')
        with pytest.raises(MissingDependencyError) as caught:
            load(tmp_path, "app.json.mako", policy=Policy.trusted())
        assert isinstance(caught.value, ImportError)
        assert "mako" in str(caught.value)

    @pytest.mark.parametrize("suffix", [".erb", ".epp"])
    def test_program_running_engine_needs_allow_commands(self, tmp_path, suffix):
        # Refused before the engine runs, so no ruby or puppet is needed.
        write(tmp_path, "app.json" + suffix, '{"v": 1}')
        with pytest.raises(CommandsDisabledError):
            load(
                tmp_path,
                "app.json" + suffix,
                policy=Policy.trusted(allow_commands=False),
            )


class TestFormatResolution:
    def test_stripped_name_picks_json(self, tmp_path):
        write(tmp_path, "app.json.j2", '{"v": {{ 1 + 1 }}}')
        assert load(tmp_path, "app.json.j2") == {"v": 2}

    @pytest.mark.usefixtures("needs_yaml")
    def test_stripped_name_picks_yaml(self, tmp_path):
        write(tmp_path, "app.yml.j2", "v: {{ 1 + 1 }}\n")
        assert load(tmp_path, "app.yml.j2") == {"v": 2}

    def test_stripped_name_picks_ini(self, tmp_path):
        write(tmp_path, "app.ini.j2", "[s]\nk = {{ 'v' * 2 }}\n")
        assert load(tmp_path, "app.ini.j2") == {"s": {"k": "vv"}}

    def test_stripped_name_picks_dotenv(self, tmp_path):
        write(tmp_path, "app.env.j2", "K={{ 1 + 1 }}\n")
        assert load(tmp_path, "app.env.j2") == {"K": "2"}

    def test_format_options_apply_to_the_rendered_text(self, tmp_path):
        write(tmp_path, "app.json.j2", '{"a": {{ 1.5 }}}')
        result = load(
            tmp_path, "app.json.j2", format_options={"json": {"parse_float": str}}
        )
        assert result == {"a": "1.5"}

    @pytest.mark.usefixtures("needs_yaml")
    def test_load_all_reads_every_rendered_document(self, tmp_path):
        write(
            tmp_path,
            "app.yaml.j2",
            "{% for n in range(3) %}---\nn: {{ n }}\n{% endfor %}",
        )
        loader = Loader(base_dir=tmp_path, policy=SANDBOXED)
        assert list(loader.load_all("app.yaml.j2")) == [{"n": 0}, {"n": 1}, {"n": 2}]

    @pytest.mark.parametrize("name", ["config.j2", "notes.txt.j2", "app.xyz.jinja"])
    def test_name_without_an_inner_format_is_not_a_template(self, tmp_path, name):
        write(tmp_path, name, '{"a": 1}')
        for policy in (Policy(), SANDBOXED, Policy.trusted()):
            with pytest.raises(UnsupportedFormatError):
                load(tmp_path, name, policy=policy)

    def test_unsupported_name_is_refused_before_reading(self, tmp_path):
        # The file does not exist: the name alone decides.
        with pytest.raises(UnsupportedFormatError):
            load(tmp_path, "missing.txt.j2")

    @pytest.mark.usefixtures("needs_yaml")
    def test_a_document_may_select_only_the_document_formats(self, tmp_path):
        write(tmp_path, "inc.json.j2", '{"v": {{ 1 }}}')
        write(tmp_path, "app.yaml", "x: !include inc.json.j2\n")
        assert load(tmp_path, "app.yaml") == {"x": {"v": 1}}
        with pytest.raises(UnsupportedFormatError):
            load(tmp_path, "app.yaml", formats=["yaml"])


class TestRenderContext:
    def test_sandboxed_template_sees_the_path_as_text(self, tmp_path):
        write(
            tmp_path,
            "conf/app.json.j2",
            '{"p": "{{ pathname }}", "f": "{{ filename }}"}',
        )
        result = load(tmp_path, "conf/app.json.j2")
        assert result["f"] == "app.json.j2"
        assert result["p"].endswith("conf/app.json.j2") and "\\" not in result["p"]

    @pytest.mark.usefixtures("needs_tplx")
    def test_pathname_is_a_posix_path(self, tmp_path):
        write(
            tmp_path,
            "conf/app.json.j2",
            '{"name": "{{ pathname.name }}", "suffix": "{{ pathname.suffix }}",'
            ' "stem": "{{ pathname.stem }}", "posix": "{{ pathname.as_posix() }}"}',
        )
        result = load(tmp_path, "conf/app.json.j2", policy=Policy.trusted())
        assert result["name"] == "app.json.j2"
        assert result["suffix"] == ".j2"
        assert result["stem"] == "app.json"
        assert "\\" not in result["posix"]
        assert result["posix"].endswith("conf/app.json.j2")

    @pytest.mark.usefixtures("needs_tplx")
    def test_pathname_type_unsandboxed(self, tmp_path):
        write(tmp_path, "app.json.j2", '{"t": "{{ pathname.__class__.__name__ }}"}')
        result = load(tmp_path, "app.json.j2", policy=Policy.trusted())
        assert result == {"t": "PurePosixPath"}

    def test_env_with_inject_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("YACFG_TPL_VAR", "from-env")
        write(tmp_path, "app.json.j2", '{"e": "{{ env.YACFG_TPL_VAR }}"}')
        assert load(tmp_path, "app.json.j2", inject_env=True) == {"e": "from-env"}

    def test_no_env_without_inject_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("YACFG_TPL_VAR", "from-env")
        write(tmp_path, "app.json.j2", '{"e": "{{ env.YACFG_TPL_VAR }}"}')
        with pytest.raises(TemplateError):
            load(tmp_path, "app.json.j2")

    def test_env_is_read_only(self, tmp_path, monkeypatch):
        monkeypatch.delenv("YACFG_TPL_PWN", raising=False)
        write(
            tmp_path,
            "app.json.j2",
            "{% do env.update({'YACFG_TPL_PWN': 'x'}) %}{\"a\": 1}",
        )
        with pytest.raises(TemplateError):
            load(tmp_path, "app.json.j2", inject_env=True)
        assert "YACFG_TPL_PWN" not in os.environ

    def test_document_keys_are_not_in_scope(self, tmp_path):
        # A template renders before it is parsed: it has no document to read.
        write(tmp_path, "app.json.j2", '{"a": 1, "b": "{{ a }}"}')
        assert load(tmp_path, "app.json.j2") == {"a": 1, "b": ""}


@pytest.mark.usefixtures("needs_yaml")
class TestIncludesInRenderedYaml:
    def test_include_resolves_next_to_the_template(self, tmp_path):
        write(tmp_path, "conf/app.yaml.j2", "n: {{ 2 }}\nchild: !include child.yaml\n")
        write(tmp_path, "conf/child.yaml", "c: 1\n")
        write(tmp_path, "child.yaml", "c: base_dir\n")
        result = load(tmp_path, "conf/app.yaml.j2")
        assert result == {"n": 2, "child": {"c": 1}}

    def test_rendered_include_target(self, tmp_path):
        write(
            tmp_path,
            "app.yaml.j2",
            "{% for name in ['a', 'b'] %}{{ name }}: !include {{ name }}.yaml\n{% endfor %}",
        )
        write(tmp_path, "a.yaml", "v: 1\n")
        write(tmp_path, "b.yaml", "v: 2\n")
        assert load(tmp_path, "app.yaml.j2") == {"a": {"v": 1}, "b": {"v": 2}}


class TestErrors:
    def test_render_failure_is_a_template_error(self, tmp_path):
        from jinja2.exceptions import TemplateSyntaxError

        source = write(tmp_path, "tpl.json.j2", "{{ 1 +\n")
        with pytest.raises(TemplateError) as caught:
            load(tmp_path, "tpl.json.j2")
        error = caught.value
        assert isinstance(error, ConfigError) and isinstance(error, ValueError)
        assert isinstance(error.__cause__, TemplateSyntaxError)
        assert "render" in frame_kinds(error)
        assert error.config_source == str(source)
        assert "tpl.json.j2" in str(error)

    def test_expression_failure_is_a_template_error(self, tmp_path):
        write(tmp_path, "tpl.json.j2", '{"r": {{ 1 / 0 }}}')
        with pytest.raises(TemplateError) as caught:
            load(tmp_path, "tpl.json.j2")
        assert isinstance(caught.value.__cause__, ZeroDivisionError)

    @pytest.mark.usefixtures("needs_tplx")
    def test_unsandboxed_render_failure_is_a_template_error(self, tmp_path):
        write(tmp_path, "tpl.json.j2", "{{ 1 +\n")
        with pytest.raises(TemplateError) as caught:
            load(tmp_path, "tpl.json.j2", policy=Policy.trusted())
        assert caught.value.__cause__ is not None
        assert "render" in frame_kinds(caught.value)

    def test_parse_error_in_rendered_text_carries_a_render_frame(self, tmp_path):
        source = write(tmp_path, "tpl.json.j2", '{"a": [{{ 1 }}')
        with pytest.raises(ParseError) as caught:
            load(tmp_path, "tpl.json.j2")
        error = caught.value
        assert error.path == str(source)
        assert error.lineno == 1
        assert "render" in frame_kinds(error)
        assert "rendered" in str(error)

    @pytest.mark.usefixtures("needs_yaml")
    def test_failure_in_an_include_of_a_template_names_both(self, tmp_path):
        write(tmp_path, "conf/app.yaml.j2", "n: {{ 2 }}\nchild: !include child.yaml\n")
        write(tmp_path, "conf/child.yaml", "c: [unclosed\n")
        with pytest.raises(ParseError) as caught:
            load(tmp_path, "conf/app.yaml.j2")
        message = str(caught.value)
        assert caught.value.path.endswith("child.yaml")
        assert "included from" in message and "line 2" in message
        assert "rendered from" in message and "app.yaml.j2" in message
        assert frame_kinds(caught.value) == ["include", "render"]
