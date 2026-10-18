"""The shipped examples run from any working directory, and the documented
untrusted-configuration recipe works exactly as written."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import anycfg

#: The untrusted-configuration checklist recipe, meant to be copied into the
#: security guide verbatim. The test below executes this exact text.
UNTRUSTED_CHECKLIST_SNIPPET = '''\
import anycfg

def load_untrusted(conf_dir, name):
    """Load configuration someone else wrote: parsing and includes, nothing more."""
    # The untrusted posture is the default: no commands, no template files,
    # and interpolation runs sandboxed.
    policy = anycfg.Policy.untrusted(
        confine_to=True,        # every file read stays under conf_dir
        max_includes=100,       # bound what one document can pull in
        max_bytes=1024 * 1024,
    )
    # inject_env stays off, so a document cannot read os.environ.
    loader = anycfg.Loader(base_dir=conf_dir, policy=policy, interpolate=True)
    return loader.load(name)

# One clause catches every refusal and every malformed document:
#     except (anycfg.ConfigError, OSError) as error: ...
'''


def _package_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(anycfg.__file__)))


@pytest.mark.usefixtures("needs_yaml", "needs_jinja2")
@pytest.mark.parametrize("name", ["example.py", "run_advanced.py"])
def test_example_runs_from_any_cwd(examples_dir, tmp_path, name):
    env = dict(os.environ)
    # The child imports the package under test, not whatever copy is installed.
    env["PYTHONPATH"] = os.pathsep.join(
        [_package_root()] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    done = subprocess.run(
        [sys.executable, str(examples_dir / name)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    output = json.loads(done.stdout)
    assert isinstance(output, dict) and output


@pytest.mark.usefixtures("needs_yaml", "needs_jinja2")
def test_example_output(examples_dir, tmp_path):
    done = subprocess.run(
        [sys.executable, str(examples_dir / "example.py")],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=_package_root()),
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = json.loads(done.stdout)
    layered = output["layered load with interpolation"]
    # Interpolated after the merge, so the override layer's root is the one used.
    assert layered["log_dir"] == "/opt/demo/logs"
    assert layered["ini"]["Paths"]["logs"] == "/opt/demo/logs"
    assert layered["workers"] == 4
    assert output["template file"] == {
        "name": "jinja.yaml.j2",
        "ports": [8000, 8001, 8002],
    }
    assert output["typed load"]["server"]["port"] == 8443
    assert output["port type"] == "int"
    assert output["typed_merge"] == {
        "host": "example.com",
        "port": 443,
        "scheme": "https",
    }


@pytest.mark.usefixtures("needs_yaml", "needs_jinja2")
def test_advanced_example_output(examples_dir, tmp_path):
    done = subprocess.run(
        [sys.executable, str(examples_dir / "run_advanced.py")],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=_package_root()),
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = json.loads(done.stdout)
    assert output["welcome_message"] == "Welcome to Anycfg Demo v1.0.0"
    assert output["editor_settings"]["python.testing.pytestEnabled"] is True
    assert output["merged"] == {"include": {"me": True, "local": True}}
    assert output["optional"] == {"enabled": False}


@pytest.mark.usefixtures("needs_yaml", "needs_jinja2")
class TestUntrustedChecklist:
    """The checklist snippet runs as written, and refuses what it claims to."""

    @pytest.fixture
    def load_untrusted(self):
        namespace: dict = {}
        exec(compile(UNTRUSTED_CHECKLIST_SNIPPET, "<checklist>", "exec"), namespace)
        return namespace["load_untrusted"]

    @pytest.fixture
    def conf(self, tmp_path):
        conf = tmp_path / "conf"
        conf.mkdir()
        (conf / "extra.yaml").write_text("tier: prod\n", encoding="utf-8")
        (tmp_path / "secret.yaml").write_text("secret: s3cr3t\n", encoding="utf-8")
        return conf

    def write(self, conf, text):
        (conf / "app.yaml").write_text(text, encoding="utf-8")

    def test_ordinary_configuration_loads(self, load_untrusted, conf):
        self.write(
            conf,
            "name: demo\ngreeting: 'hello {{ name }}'\nextra: !include extra.yaml\n",
        )
        config = load_untrusted(str(conf), "app.yaml")
        assert config == {
            "name": "demo",
            "greeting": "hello demo",
            "extra": {"tier": "prod"},
        }

    def test_include_outside_the_directory_is_refused(self, load_untrusted, conf):
        secret = (conf.parent / "secret.yaml").as_posix()
        self.write(conf, f"stolen: !include '{secret}'\n")
        with pytest.raises(anycfg.ConfinementError):
            load_untrusted(str(conf), "app.yaml")
        self.write(conf, "stolen: !include ../secret.yaml\n")
        with pytest.raises(anycfg.ConfinementError):
            load_untrusted(str(conf), "app.yaml")

    def test_command_is_refused(self, load_untrusted, conf, tmp_path):
        marker = tmp_path / "ran.marker"
        script = tmp_path / "mark.py"
        script.write_text(
            f"import pathlib\npathlib.Path({marker.as_posix()!r}).touch()\nprint(1)\n",
            encoding="utf-8",
        )
        command = f'cmd+json://"{sys.executable}" "{script.as_posix()}"'
        self.write(conf, f"x: !include '{command}'\n")
        with pytest.raises(anycfg.CommandsDisabledError):
            load_untrusted(str(conf), "app.yaml")
        assert not marker.exists()

    def test_template_file_is_refused(self, load_untrusted, conf):
        (conf / "t.yaml.j2").write_text("v: {{ 1 }}\n", encoding="utf-8")
        self.write(conf, "x: !include t.yaml.j2\n")
        with pytest.raises(anycfg.TemplatesDisabledError):
            load_untrusted(str(conf), "app.yaml")

    def test_interpolation_is_sandboxed(self, load_untrusted, conf):
        self.write(conf, "x: \"{{ ''.__class__.__mro__ }}\"\n")
        with pytest.raises(anycfg.TemplateError):
            load_untrusted(str(conf), "app.yaml")

    def test_environment_is_not_in_scope(self, load_untrusted, conf, monkeypatch):
        monkeypatch.setenv("YACFG_CHECKLIST_SECRET", "from-environment")
        self.write(conf, "x: '{{ env.YACFG_CHECKLIST_SECRET }}'\n")
        with pytest.raises(anycfg.TemplateError):
            load_untrusted(str(conf), "app.yaml")

    def test_include_budget_is_bounded(self, load_untrusted, conf):
        self.write(conf, "".join(f"k{n}: !include extra.yaml\n" for n in range(101)))
        with pytest.raises(anycfg.BudgetExceededError):
            load_untrusted(str(conf), "app.yaml")

    def test_one_clause_catches_every_refusal(self, load_untrusted, conf):
        for text in (
            "stolen: !include ../secret.yaml\n",
            "x: !include 'cmd://echo 1'\n",
            "x: [unclosed\n",
            "x: !include missing.yaml\n",
        ):
            self.write(conf, text)
            with pytest.raises((anycfg.ConfigError, OSError)):
                load_untrusted(str(conf), "app.yaml")
