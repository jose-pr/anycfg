# Sources

A **source** is anything a load reads: a file path, a path object, an open
file, Python data, in-memory text, the environment or a command's output. The
one-document functions take one; `Loader.load()` and `Loader.load_all()` take
any number, possibly nested in lists or tuples, and combine them in order.

```python
from yaconfiglib import Loader
from yaconfiglib.sources import Env, Text

config = Loader(base_dir="conf").load(
    "base.yaml",                          # a path, relative to base_dir
    ["conf.d/*.yaml", "local.toml"],      # a glob and a path, nested
    {"debug": False},                     # Python data
    Text("log_level: info", format="yaml", name="inline"),
    Env("APP_", nested="__"),             # the environment
    None,                                 # an absent optional layer
)
```

## Paths

A `str` or `os.PathLike` is a **local** path. A relative one joins to the
loader's `base_dir` (the working directory when `base_dir` is `None`); an
absolute one is used as it is. The format comes from the file name (see
[Formats](formats.md#how-a-format-is-chosen)) unless `format=` is given.

**A `str` is always a location, never content.** `load("a: 1")` looks for a
file called `a: 1`. Content goes to `loads()` or [`Text`](#in-memory-text).

`None` and `""` are skipped, so an optional layer can be passed
unconditionally:

```python
import os

local = os.environ.get("APP_LOCAL_CONFIG")      # may be unset
config = Loader().load("base.yaml", local)
```

`bytes` is refused with `TypeError`: it is content, not a location.

A file whose name ends in a script suffix — `.sh`, `.bat`, `.cmd`, `.ps1` —
is not parsed but **run**, as a [command](#commands-and-scripts), and so needs
a policy that allows commands.

### Schemes

A string written `<scheme>://...` (or `<scheme>+<format>://...`) is first
looked up as a registered source scheme. The built-in schemes are `cmd`,
`exec` and `sh`, which [run a command](#commands-and-scripts). A scheme that is
not registered is just part of a local file name — a `str` is never parsed as a
URI. To read a remote file, pass a pathapi `Path` object for it.

## Path objects

A [pathapi](https://github.com/jose-pr/pathapi) `Path` — a `LocalPath`, an
in-memory `MemPath`, or a remote path from a pathapi backend — is read through
its own `read_bytes()`, **exactly as given**: it is never joined to `base_dir`.
(So a relative `LocalPath` object resolves against the working directory.)

```python
from pathapi import MemPath
from yaconfiglib import Loader

doc = MemPath("/conf/app.yaml")
doc.parent.mkdir(parents=True, exist_ok=True)
doc.write_text("name: app\n")
print(Loader().load(doc))        # {'name': 'app'}
```

Relative includes inside a path object's document resolve next to it, on the
same filesystem — except inside a `MemPath`, which has no location on disk, so
its includes resolve against `base_dir` like other in-memory content. While
[confinement](security.md#confining-reads) is on, a remote path is refused (it
is inside no local root) and a `MemPath` is exempt.

## Open files

Anything with a `read()` method — a file from `open()`, `codecs.open()`, a
`tempfile.SpooledTemporaryFile`, your own reader — is read once. Its format
comes from `format=` or from its `name`; `sys.stdin` and `io.StringIO` have no
real name, so they need `format=`. It may read `str` or `bytes`.

```python
import io
import yaconfiglib

with open("settings.toml", "rb") as handle:
    config = yaconfiglib.load(handle)

print(yaconfiglib.load(io.StringIO('{"a": 1}'), format="json"))   # {'a': 1}
```

When `name` is a real file, it is checked against confinement and is the
origin of the document's relative includes, just as the path would be.

## Python data

A mapping passed as a source is wrapped in `yaconfiglib.sources.Data` and
merged like any other document. It is **deep-copied when read**, so merging
and interpolation never reach back into your object:

```python
from yaconfiglib.sources import Data

defaults = {"server": {"port": 8080}}
config = Loader(merge="deep").load(defaults, "server.yaml")
config = Loader().load(Data([1, 2, 3], name="numbers"))         # any value, named
```

Use `Data` directly to pass something other than a mapping, or to give the
document a name (for messages and for `collect="hash"`); its default name is
`""`. Any mapping, a read-only one included, is read as a plain `dict`.

## In-memory text

`yaconfiglib.sources.Text(data, *, format, name="")` is content in a named
format — the layered counterpart of `loads()`:

```python
from yaconfiglib.sources import Text

overlay = Text("server:\n  port: 9090\n", format="yaml", name="overlay")
config = Loader(merge="deep").load("server.yaml", overlay)
```

`data` is `str`, or `bytes` decoded with the load's encoding. Relative
includes inside it resolve against the loader's `base_dir`.

## The environment

`yaconfiglib.sources.Env(prefix="", *, nested=None, lowercase=False,
environ=None)` reads environment variables starting with `prefix`, with the
prefix removed:

```python
from yaconfiglib.sources import Env

environ = {"APP_DEBUG": "true", "APP_DB__HOST": "db", "APP_DB__PORT": "5432", "HOME": "/root"}
print(Loader().load(Env("APP_", nested="__", environ=environ)))
# {'DEBUG': 'true', 'DB': {'HOST': 'db', 'PORT': '5432'}}
```

- **`nested`** splits names on a delimiter into nested mappings. A variable that
  is both a value and a parent of nested keys — `APP_DB` beside `APP_DB__PORT`
  — raises `ConfigValueError` naming both, rather than one winning by
  environment order.
- **Keys keep their case**; `lowercase=True` folds them.
- **Values stay strings.** Types come from [hydration](models.md):
  `load(..., into=Settings)` turns `"5432"` into `5432` and `"true"` into
  `True` by the field's annotation.
- On Windows the prefix matches case-insensitively, as the environment does.
- `environ` replaces `os.environ`, for tests and for a captured snapshot.

To override files with the environment, list `Env` last.

## Commands and scripts

A command source runs a program and parses its standard output. **Commands
are refused unless the policy allows them** — `Policy(allow_commands=True)`,
or `Policy.trusted()` — with `CommandsDisabledError`, before anything runs.

```python
from yaconfiglib import Loader, Policy

loader = Loader(policy=Policy.trusted())
secrets = loader.load('cmd+json://python -c "import json; print(json.dumps(dict(token=42)))"')
print(secrets)    # {'token': 42}
```

A string `cmd://...`, `exec://...` or `sh://...` runs everything after the
`://` through the shell, exactly as written. `yaconfiglib.sources.Command`
builds the same source explicitly, and also takes a bare command line:

```python
from yaconfiglib.sources import Command

source = Command("vault kv get -format=json secret/app", format="json", timeout=30)
```

**The output's format must be named** — output is never sniffed. In order:

1. `format=` on `Command`;
2. the `+fmt` of the scheme (`cmd+json://`);
3. a `#!<format>` first line of the output (`#!yaml`), which is then dropped;
4. for a script, the suffix before its own (`secrets.json.sh` prints JSON);
5. the `format=` of the load, or of the include, that reached the command.

Without one, `ConfigValueError` is raised. However it is given, the output's
format must be one documents may select (see
[Formats](formats.md#distributing-a-format)): a command's output is data from
outside your code.

Includes inside a command's output are read with the command's encoding.

The process runs with stdin closed and a timeout — the policy's
`command_timeout`, 60 seconds by default, or `timeout=` on `Command` (`None`
waits indefinitely) — after which it and its child processes are killed and
`CommandTimeoutError` is raised. Its stdout is decoded strictly with
`encoding=` (default: the load's encoding); on Windows, `cmd` and PowerShell
often write the OEM code page, so pass `encoding="oem"` there. Trailing
whitespace and leading blank lines are trimmed; indentation is kept.

A non-zero exit raises `CommandError`, also a
`subprocess.CalledProcessError` with `returncode`, `output` and `stderr`. Its
message ends with the tail of stderr (the last 20 lines, at most 2000
characters), where a tool writes why it failed. **stdout is never in the
message** — it is the payload, often the very secret the command fetches — only
on `.output`. stderr from a command that succeeded is logged at DEBUG.

### Script files

A path ending in `.sh`, `.bat`, `.cmd` or `.ps1` is a script, run through its
interpreter **without a shell**, so a name such as `R&D report.sh` is not shell
syntax. `Command.script(path, ...)` builds the same source explicitly.

- `.bat`/`.cmd` run on Windows only, through `%COMSPEC% /d /v:off /s /c`; a
  path containing `%` is refused, because `cmd` expands `%VAR%` even inside
  quotes.
- `.ps1` runs through `pwsh` (or `powershell`) `-NoProfile -NonInteractive
  -File`, under the machine's execution policy.
- `.sh` runs through `sh` on Windows. On POSIX it runs directly when it is
  executable and starts with `#!`, otherwise under `/bin/sh`.
- The path is made absolute first, never searched on `PATH`.

Name a script after its output: `secrets.json.sh` is parsed as JSON.

## Writing a source

Subclass `yaconfiglib.sources.Source` and return a `Payload` from `read()`:

```python
from yaconfiglib.sources import Payload, Source


class Vault(Source):
    """Secrets fetched from an API client you already have."""

    def __init__(self, client, path):
        self.client, self.path = client, path

    @property
    def name(self):
        return f"vault:{self.path}"

    def read(self, context):
        data = self.client.read(self.path)          # already a mapping
        return Payload(data, name=self.name, identity=self.name, parsed=True)
```

A `Payload` is either text for a format to parse (`data` as `str`/`bytes`, with
`format=` naming it or `None` to let the caller decide) or data already parsed
(`parsed=True`). `identity` is the key for include-cycle detection and
de-duplication; `origin` is where relative includes inside the text resolve;
`encoding` is the codec of `data` when it is bytes, and the default for its
includes.
Set the class attribute `executes = True` on a source that runs a program: the
loader then refuses it unless the policy allows commands, before `read()` is
called. `context` is the [`LoadContext`](../api/policy.md), which carries the
policy and the encoding.

### Schemes of your own

`register_scheme(scheme, factory)` makes `<scheme>://...` strings load through
`factory(text)`, which returns a `Source`:

```python
from yaconfiglib import sources

sources.register_scheme("vault", lambda text: Vault(client, text.split("://", 1)[1]))
config = Loader().load("base.yaml", "vault://secret/app")
```

`sources.schemes()` lists them, `sources.unregister_scheme()` removes one, and
`sources.from_text(text)` returns the source a string names, or `None`.

Another distribution adds schemes through the `yaconfiglib.sources` entry-point
group (`vault = "mypackage.sources:factory"`). Like a plugin format, an
entry-point scheme is usable in your own top-level sources but never from a
document: an `!include 'vault://...'` reaches only built-in and code-registered
schemes.

## Globs

A source whose own components contain `*`, `?` or `[` is a glob pattern, and
expands to the files it matches:

```python
Loader(base_dir="conf", recursive=True).load("defaults.yaml", "**/*.yaml")
```

- **`recursive=True`** makes `**` descend into directories. Expansion is
  pathapi's with `native=False`, so `**` matches the same files on every
  Python version, and `**/*.yaml` selects every YAML file at every depth.
- **Matches load in a fixed order** — sorted by path component — so the same
  tree layers the same way on every machine and filesystem.
- **Directories are skipped**; only files load. Dotfiles are matched.
- **`base_dir` is never pattern text.** A base directory called `proj [v2]`
  works without escaping, and so does an extended-length `\\?\` path.
- **A pattern that names an existing file loads literally**, so a file really
  called `z[1].yaml` is what loads.
- **A pattern that matches nothing contributes nothing**: no error, and
  `load()` returns its `default=` if nothing else loaded either.
- **The same file loads once**, however it is spelled (`conf/app.yaml`,
  `./conf/app.yaml`, and on Windows `conf/App.yaml`). The key is lexical —
  symlinks are not resolved, so two symlinked names stay distinct.
- **A file named explicitly beats a glob match for it**, wherever the two
  appear, and keeps its own position. That makes both layering idioms work:
  `load("base.yaml", "*.yaml")` and `load("*.yaml", "local.yaml")`.
- **A directory that cannot be listed** is offered to `on_error` with
  `phase="glob"` and the directory as `path`; skipping it keeps the rest of the
  pattern. Without `on_error`, its `OSError` is raised.
- **A `**` walk bounds a directory loop** (`bound_loops=True`, the default): a
  Windows junction pointing at one of its own ancestors is descended once, not
  walked until the path is too long. A directory reachable under two names is
  still read under both. A POSIX directory symlink is never descended by `**`
  at all, so name a symlinked layer as its own source.

Glob patterns also work inside an [`!include`](includes.md), relative to the
including file, and are [confined](security.md#glob-patterns) like any other
read.

See the [API reference](../api/sources.md) for every source class and the
scheme registry.
