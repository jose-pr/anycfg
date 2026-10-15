"""Skip marks for the optional extras, usable inside ``pytest.param``.

The ``needs_*`` fixtures in ``conftest.py`` cover classes and functions; a single
parameter of a parametrized test cannot use a fixture (``pytest.param`` rejects
``pytest.mark.usefixtures``), so these marks are the equivalent there.
"""

import importlib.util

import pytest


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


needs_yaml = pytest.mark.skipif(not _has("yaml"), reason="requires yaconfiglib[yaml]")
needs_jinja2 = pytest.mark.skipif(
    not _has("jinja2"), reason="requires yaconfiglib[templates]"
)
needs_tplx = pytest.mark.skipif(
    not _has("tplx"), reason="requires yaconfiglib[templates]"
)
needs_toml = pytest.mark.skipif(
    not (_has("tomllib") or _has("tomli")), reason="requires yaconfiglib[toml]"
)
needs_toml_writer = pytest.mark.skipif(
    not _has("tomli_w"), reason="requires yaconfiglib[toml]"
)
