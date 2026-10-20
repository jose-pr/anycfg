"""Load, merge and write configuration in any registered format.

One document::

    config = anycfg.load("app.yaml")          # format from the suffix
    data = anycfg.loads(text, format="toml")
    anycfg.dump(config, "app.json")           # written as JSON

Layers::

    loader = anycfg.Loader(merge="deep")
    config = loader.load("base.yaml", "conf.d/*.yaml", "local.toml")

Formats live in :mod:`anycfg.formats`, sources that are not files (commands,
the environment, Python data, in-memory text) in :mod:`anycfg.sources`.
What a load may do beyond parsing is its :class:`Policy`, untrusted by default.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _version

from . import formats, policies, sources
from ._api import dump, dumps, load, load_all, loads, loads_all
from ._async import adump, aload, aload_all
from ._context import LoadContext, Policy
from ._dotaccess import DotAccessibleDict, dig
from ._hydrate import (
    OpaqueMerge,
    TypedNamespace,
    dehydrate,
    hydrate,
    opaque,
    typed_merge,
)
from ._loader import LoadEvent, Loader
from ._locations import system_config_path, user_config_path
from ._merge import DELETE, INHERIT, Merge, merge_strategies, register_merge
from ._templating import Interpolator, TemplateInterpolator, VarsInterpolator
from .exceptions import (
    BudgetExceededError,
    CommandError,
    CommandsDisabledError,
    CommandTimeoutError,
    ConfigError,
    ConfigTypeError,
    ConfigValueError,
    ConfinementError,
    ErrorFrame,
    MissingDependencyError,
    ParseError,
    PolicyError,
    TemplateError,
    TemplatesDisabledError,
    UnsupportedFormatError,
)

try:
    __version__ = _version("anycfg")
except _PackageNotFoundError:  # pragma: no cover - a never-installed source tree
    __version__ = "0.0.0"

__all__ = [
    "__version__",
    # one document
    "load",
    "loads",
    "load_all",
    "loads_all",
    "dump",
    "dumps",
    "aload",
    "aload_all",
    "adump",
    # layers
    "Loader",
    "LoadEvent",
    "Policy",
    "LoadContext",
    # standard locations
    "user_config_path",
    "system_config_path",
    # merging and typed results
    "DELETE",
    "INHERIT",
    "Merge",
    "register_merge",
    "merge_strategies",
    "hydrate",
    "dehydrate",
    "typed_merge",
    "OpaqueMerge",
    "opaque",
    "TypedNamespace",
    "DotAccessibleDict",
    "dig",
    # interpolation
    "Interpolator",
    "TemplateInterpolator",
    "VarsInterpolator",
    # registries
    "formats",
    "policies",
    "sources",
    # errors
    "ConfigError",
    "ConfigValueError",
    "ConfigTypeError",
    "ParseError",
    "TemplateError",
    "UnsupportedFormatError",
    "MissingDependencyError",
    "PolicyError",
    "CommandsDisabledError",
    "TemplatesDisabledError",
    "ConfinementError",
    "BudgetExceededError",
    "CommandError",
    "CommandTimeoutError",
    "ErrorFrame",
]
