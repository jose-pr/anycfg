"""Shared pytest fixtures for anycfg tests."""

import pathlib

import pytest

EXAMPLES_DIR = pathlib.Path(__file__).parent.parent / "examples"


@pytest.fixture
def examples_dir():
    """The examples directory."""
    return EXAMPLES_DIR


@pytest.fixture
def needs_yaml():
    """Skip unless PyYAML is installed (anycfg[yaml])."""
    pytest.importorskip("yaml")


@pytest.fixture
def needs_jinja2():
    """Skip unless Jinja2 is installed (anycfg[templates])."""
    pytest.importorskip("jinja2")


@pytest.fixture
def needs_tplx():
    """Skip unless tplx is installed (anycfg[templates])."""
    pytest.importorskip("tplx")


@pytest.fixture
def needs_toml():
    """Skip unless TOML can be read: tomllib (3.11+) or tomli (anycfg[toml])."""
    try:
        import tomllib  # noqa: F401
    except ImportError:
        pytest.importorskip("tomli")


@pytest.fixture
def needs_toml_writer():
    """Skip unless tomli-w is installed (anycfg[toml])."""
    pytest.importorskip("tomli_w")
