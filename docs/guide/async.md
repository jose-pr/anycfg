# Async

Every loading function has an awaitable twin, for configuration that arrives
over the network or from an async file:

| Sync | Async |
| --- | --- |
| `load(source)` | `await aload(source)` |
| `load_all(source)` | `async for doc in aload_all(source)` |
| `dump(obj, target)` | `await adump(obj, target)` |
| `Loader().load(*sources)` | `await Loader().aload(*sources)` |
| `Loader().load_all(*sources)` | `async for doc in Loader().aload_all(*sources)` |

They take the same arguments and return the same results. What they add is the
**async source**:

- an async iterable of `bytes` or `str` chunks — `response.aiter_bytes()` from
  httpx, `resp.content.iter_chunked(n)` from aiohttp, an anyio stream;
- an object with `async def read()` — an aiofiles file, an
  `asyncio.StreamReader`.

```python
import asyncio

import httpx
import yaconfiglib


async def main():
    async with httpx.AsyncClient() as client:
        async with client.stream("GET", "https://example.org/app.json") as response:
            return await yaconfiglib.aload(response.aiter_bytes(), format="json")


config = asyncio.run(main())
```

An async source has no file name unless it carries a `name` (an aiofiles file
does), so pass `format=` for the others.

## How it runs

Gather first, parse second:

1. Each async source is read on the event loop, chunk by chunk. The read
   **stops as soon as it passes `max_bytes`** and raises
   `BudgetExceededError`, so a hostile or runaway stream is not buffered whole.
   A source with a file name is checked against `confine_to` before it is read.
2. The rest — paths, includes, commands, parsing, merging, interpolation,
   `into=` — is the ordinary synchronous load, run in a worker thread with
   `asyncio.to_thread`, so the event loop never waits on a file or a parser.

Async and sync sources mix freely in `Loader.aload`, layered in order:

```python
loader = Loader(base_dir="conf", merge="deep")
config = await loader.aload("base.yaml", remote_overrides, {"debug": True})
```

Cancelling the awaiting task stops the reading of async sources. Work already
handed to the worker thread runs to its end and its result is discarded.

A synchronous `load` given an async source raises `TypeError` naming the async
function to use instead.

## Writing

`adump` serializes the whole document first, so a value the format cannot
write leaves the target untouched. A path or a sync file is written in a worker
thread. An async writer gets the text: an `async def write()` receives `str`
(or bytes in `encoding`, when it refuses `str`); a writer with
`async def drain()`, such as `asyncio.StreamWriter`, receives bytes and is
drained.

## Chunks without async

`yaconfiglib.sources.Chunks` loads a **sync** iterable of chunks — a
`requests` response's `iter_content()`, a generator — with the same early stop:

```python
from yaconfiglib import Loader
from yaconfiglib.sources import Chunks

config = Loader().load(Chunks(response.iter_content(65536), format="json"))
```

A bare iterable passed to a loader still means "several sources"; `Chunks`
says it is one source in pieces.
