# Exceptions

`anycfg.exceptions`, every name also importable from `anycfg`.

Every condition the library reports is a `ConfigError`, and each class also
inherits the builtin exception for the same condition — `ParseError` is a
`ValueError`, a `PolicyError` a `PermissionError`. A parser's own exception
never crosses the API: every format translates it into `ParseError`, which
carries a position and may name document structure (a key, a section, a tag,
an alias) but never a value, a line or a snippet. An `OSError` from reading a
file is raised as itself, so one clause covers every load failure:

```python
import anycfg

try:
    config = anycfg.load("app.yaml")
except (anycfg.ConfigError, OSError) as error:
    ...
```

An error a load re-raises keeps its type and identity, and gains where it
happened: `config_source` (the failing source), `config_frames` (an
`ErrorFrame` per include, render, command or merge step, innermost first),
`config_key` and `config_model`, rendered into its message as
`[in <source>; included from <file>, line <n>; at <a.b[0]> (<Model>)]`. A wrong
argument to a function raises a plain `TypeError` or `ValueError`.

::: anycfg.exceptions.ConfigError

::: anycfg.exceptions.ParseError

::: anycfg.exceptions.ConfigValueError

::: anycfg.exceptions.ConfigTypeError

::: anycfg.exceptions.TemplateError

::: anycfg.exceptions.UnsupportedFormatError

::: anycfg.exceptions.MissingDependencyError

## Policy refusals

::: anycfg.exceptions.PolicyError

::: anycfg.exceptions.CommandsDisabledError

::: anycfg.exceptions.TemplatesDisabledError

::: anycfg.exceptions.ConfinementError

::: anycfg.exceptions.BudgetExceededError

## Commands

::: anycfg.exceptions.CommandError

::: anycfg.exceptions.CommandTimeoutError

## Context

::: anycfg.exceptions.ErrorFrame
