# anycfg

**anycfg** loads, layers and writes configuration in any registered
format with the standard Python serialization API — `load`, `loads`, `dump`,
`dumps`, as `json` and `tomllib` spell it — plus a `Loader` that merges many
sources in order, YAML `!include`, typed hydration, and a trust policy that is
**untrusted by default**. It was published as `yaconfiglib` up to 0.13.1; see
[Migrating from yaconfiglib](guide/migrating.md).

It requires [pathapi](https://github.com/jose-pr/pathapi), the path layer:
glob expansion with a portable `**`, directory-loop bounding, and path objects
for in-memory and remote files — and, below Python 3.11, `tomli`, so TOML
always reads. Everything else format- or feature-specific (PyYAML, `tomli-w`,
tplx and Jinja2, Pydantic) is optional and imported only when it is used.

## Why anycfg

- **One vocabulary.** `load(path)` and `dump(obj, path)` pick the format from
  the file name, so `dump(x, "a.json")` then `load("a.json")` round-trips;
  `loads(text, format=...)` and `dumps(obj, format=...)` take content. A name no
  format claims is an error, never a guess.
- **Formats as plugins.** JSON, YAML, TOML, INI and dotenv are built in. A
  [`Format`](guide/formats.md#writing-a-format) subclass registers in code or
  through an entry point, and every format passes the same shipped contract
  suite.
- **Layering.** `Loader(merge="deep").load("base.yaml", "conf.d/*.yaml",
  "local.toml")` merges in order, with deterministic glob ordering, three
  [merge strategies](guide/merging.md) or your own, `DELETE` to remove a key,
  and `collect=` to gather documents instead of merging them.
- **Sources beyond files.** Any pathapi `Path`, open files, Python mappings,
  the environment, in-memory text and command output — see
  [Sources](guide/sources.md).
- **Includes.** [`!include`](guide/includes.md) in YAML, relative to the
  including file at every depth, with cycle detection and a load budget.
- **Typed results, and the type steers the parse.** `load(..., into=Model)`
  builds a dataclass, a `TypedDict`, a Pydantic model or any class, coercing
  every field without losing data. YAML's `NO`, `1.10` and `0755` reach a `str`
  field exactly as written, while a `bool` or `int` field gets the value YAML
  read. `dump()` writes typed results back, so loading into a type and dumping
  round-trips — see [Typed results](guide/models.md).
- **Templates.** `Loader(interpolate=True)` renders `{{ ... }}` in values, and
  `app.yaml.j2` — or any engine [tplx](https://github.com/jose-pr/tplx) knows,
  `app.toml.liquid` included — renders a whole file first, sandboxed by
  default — see [Templating](guide/templating.md).
- **Safe by default.** Commands and template files are off, interpolation is
  sandboxed, reads can be confined to directories, and errors never carry
  document text — see [Security](guide/security.md).

## Installation

```bash
pip install anycfg
```

That brings pathapi with it. Add the extras for the features you use:

```bash
pip install "anycfg[yaml,toml,templates]"
```

| Extra | Adds | Needed for |
| --- | --- | --- |
| `yaml` | PyYAML 6 | the YAML format and `!include` |
| `toml` | `tomli-w` | writing TOML (reading needs no extra: `tomli` comes with anycfg below Python 3.11) |
| `templates` | `tplx[jinja]` 0.2+ | interpolation and template files, with Jinja |
| `all` | every extra above | |

A template engine other than Jinja needs its own tplx extra
(`pip install "tplx[liquid]"`). JSON, INI and dotenv need nothing beyond the
standard library. `import
anycfg` imports none of the extras; a feature whose extra is missing
raises `MissingDependencyError` naming the extra to install.

## Quick start

One document:

```python
import anycfg

config = anycfg.load("settings.yaml")          # format from the extension
data = anycfg.loads('{"a": 1}', format="json")
anycfg.dump(config, "settings.json")           # written as JSON
```

Layers, with dot access:

```python
from anycfg import Loader

loader = Loader(base_dir="conf", merge="deep", wrap=True)
config = loader.load("base.yaml", "conf.d/*.yaml", "local.toml", None)
print(config.database.host)
```

Later sources override earlier ones, a glob's matches load in a fixed order,
and `None` stands for an optional layer that is absent.

Typed:

```python
import dataclasses
import anycfg

@dataclasses.dataclass
class Database:
    host: str
    port: int

db = anycfg.load("db.json", into=Database)     # "5432" becomes 5432
anycfg.dump(db, "db.yaml")                     # typed data writes back
```

Includes and interpolation, in `app.yaml`:

```yaml
base: /srv/app
logs: "{{ base }}/logs"
database: !include db.yaml
```

```python
config = Loader(interpolate=True).load("app.yaml")
```

The environment and commands, which you opt into:

```python
from anycfg import Loader, Policy
from anycfg.sources import Env

config = Loader(policy=Policy.trusted()).load(
    "base.yaml",
    "cmd+json://vault kv get -format=json secret/app",
    Env("APP_", nested="__"),                       # APP_DB__PORT -> {"DB": {"PORT": ...}}
)
```

## Where to go next

- **Guide** — task-oriented pages: [formats](guide/formats.md),
  [sources](guide/sources.md), [merging](guide/merging.md),
  [includes](guide/includes.md), [templating](guide/templating.md),
  [typed results](guide/models.md) and [security](guide/security.md).
- **[Migrating](guide/migrating.md)** — every name and keyword of the previous
  API, mapped to its replacement.
- **[API Reference](api/reference.md)** — generated from the docstrings,
  organized by module.
- **[Changelog](changelog.md)** — release history.

The installed package also carries its API header, `anycfg/AGENTS.md`:
every public name with its signature, defaults and gotchas, in one file.
