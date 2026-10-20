# Loading

One document at a time, in the standard Python serialization style that
`json` and `tomllib` use: `load` takes a location, `loads` takes content. For
several layered sources, use `Loader`. Each loading function has an awaitable
twin; see the [Async guide](../guide/async.md).

## One document

::: anycfg.load

::: anycfg.loads

::: anycfg.load_all

::: anycfg.loads_all

::: anycfg.dump

::: anycfg.dumps

## Async

::: anycfg.aload

::: anycfg.aload_all

::: anycfg.adump

## Layers

::: anycfg.Loader

::: anycfg.LoadEvent

## Standard locations

::: anycfg.user_config_path

::: anycfg.system_config_path

::: anycfg.default_configs
