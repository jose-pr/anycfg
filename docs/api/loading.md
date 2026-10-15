# Loading

One document at a time, in the standard Python serialization style that
`json` and `tomllib` use: `load` takes a location, `loads` takes content. For
several layered sources, use `Loader`. Each loading function has an awaitable
twin; see the [Async guide](../guide/async.md).

## One document

::: yaconfiglib.load

::: yaconfiglib.loads

::: yaconfiglib.load_all

::: yaconfiglib.loads_all

::: yaconfiglib.dump

::: yaconfiglib.dumps

## Async

::: yaconfiglib.aload

::: yaconfiglib.aload_all

::: yaconfiglib.adump

## Layers

::: yaconfiglib.Loader

::: yaconfiglib.LoadEvent
