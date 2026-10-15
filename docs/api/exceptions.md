# Exceptions

`yaconfiglib.exceptions`, every name also importable from `yaconfiglib`.

Every condition the library reports is a `ConfigError`, and each class also
inherits the builtin exception for the same condition — `ParseError` is a
`ValueError`, a `PolicyError` a `PermissionError`. A parser's own exception
never crosses the API: every format translates it into `ParseError`, which
carries a position and may name document structure (a key, a section, a tag,
an alias) but never a value, a line or a snippet. An `OSError` from reading a
file is raised as itself, so one clause covers every load failure:

```python
import yaconfiglib

try:
    config = yaconfiglib.load("app.yaml")
except (yaconfiglib.ConfigError, OSError) as error:
    ...
```

An error a load re-raises keeps its type and identity, and gains where it
happened: `config_source` (the failing source), `config_frames` (an
`ErrorFrame` per include, render, command or merge step, innermost first),
`config_key` and `config_model`, rendered into its message as
`[in <source>; included from <file>, line <n>; at <a.b[0]> (<Model>)]`. A wrong
argument to a function raises a plain `TypeError` or `ValueError`.

::: yaconfiglib.exceptions.ConfigError

::: yaconfiglib.exceptions.ParseError

::: yaconfiglib.exceptions.ConfigValueError

::: yaconfiglib.exceptions.ConfigTypeError

::: yaconfiglib.exceptions.TemplateError

::: yaconfiglib.exceptions.UnsupportedFormatError

::: yaconfiglib.exceptions.MissingDependencyError

## Policy refusals

::: yaconfiglib.exceptions.PolicyError

::: yaconfiglib.exceptions.CommandsDisabledError

::: yaconfiglib.exceptions.TemplatesDisabledError

::: yaconfiglib.exceptions.ConfinementError

::: yaconfiglib.exceptions.BudgetExceededError

## Commands

::: yaconfiglib.exceptions.CommandError

::: yaconfiglib.exceptions.CommandTimeoutError

## Context

::: yaconfiglib.exceptions.ErrorFrame
