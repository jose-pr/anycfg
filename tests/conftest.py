"""Shared pytest fixtures for yaconfiglib tests."""

import pathlib

import pytest

EXAMPLES_DIR = pathlib.Path(__file__).parent.parent / "examples"


@pytest.fixture
def examples_dir():
    """The examples directory."""
    return EXAMPLES_DIR


@pytest.fixture
def needs_yaml():
    """Skip unless PyYAML is installed (yaconfiglib[yaml])."""
    pytest.importorskip("yaml")


@pytest.fixture
def needs_jinja2():
    """Skip unless Jinja2 is installed (yaconfiglib[templates])."""
    pytest.importorskip("jinja2")


@pytest.fixture
def needs_tplx():
    """Skip unless tplx is installed (yaconfiglib[templates])."""
    pytest.importorskip("tplx")


@pytest.fixture
def needs_toml():
    """Skip unless TOML can be read: tomllib (3.11+) or tomli (yaconfiglib[toml])."""
    try:
        import tomllib  # noqa: F401
    except ImportError:
        pytest.importorskip("tomli")


@pytest.fixture
def needs_toml_writer():
    """Skip unless tomli-w is installed (yaconfiglib[toml])."""
    pytest.importorskip("tomli_w")
