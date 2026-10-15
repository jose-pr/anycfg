# Sources

`yaconfiglib.sources`: what a loader reads that is not a file path, and the
registry of `<scheme>://` strings. See the [Sources guide](../guide/sources.md)
for how each kind of source is resolved.

## The contract

::: yaconfiglib.sources.Source

::: yaconfiglib.sources.Payload

## Built-in sources

::: yaconfiglib.sources.Text

::: yaconfiglib.sources.Data

::: yaconfiglib.sources.Env

::: yaconfiglib.sources.Command

## Schemes

::: yaconfiglib.sources.register_scheme

::: yaconfiglib.sources.unregister_scheme

::: yaconfiglib.sources.schemes

::: yaconfiglib.sources.from_text

::: yaconfiglib.sources.SCHEME_REGEX

::: yaconfiglib.sources.ENTRY_POINT_GROUP
