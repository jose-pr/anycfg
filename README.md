# yaconfiglib

[![Version](https://img.shields.io/pypi/v/yaconfiglib.svg)](https://pypi.org/project/yaconfiglib/)
[![Python versions](https://img.shields.io/pypi/pyversions/yaconfiglib.svg)](https://pypi.org/project/yaconfiglib/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/jose-pr/yaconfiglib/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/yaconfiglib/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/yaconfiglib/test.yml)](https://github.com/jose-pr/yaconfiglib/actions/workflows/test.yml)

**yaconfiglib loads, layers and writes configuration in any registered format
with one `json`-style API** — `load`, `loads`, `dump`, `dumps` — plus a `Loader`
that merges many sources in order, YAML `!include`, typed hydration, and a
trust policy that is **untrusted by default**. Documentation:
<https://jose-pr.github.io/yaconfiglib/>.

## Features

- **One vocabulary.** `load(path)` and `dump(obj, path)` pick the format from
  the file extension, so `dump(x, "a.json")` then `load("a.json")` round-trips;
  `loads(text, format=...)` and `dumps(obj, format=...)` take content. An
  extension no format claims is an error, never a guess.
- **Formats as plugins.** JSON, YAML, TOML, INI and dotenv are built in; a
  `Format` subclass registers in code or through the `yaconfiglib.formats`
  entry-point group. Every format passes the shipped contract suite
  (`yaconfiglib.testing.FormatContract`).
- **Layering.** `Loader(merge="deep").load("base.yaml", "conf.d/*.yaml",
  "local.toml")` merges in order, with deterministic glob ordering, `simple`,
  `substitute` or `deep` strategies (or your own), `DELETE` to remove a key,
  and `collect=` to gather documents instead.
- **Sources beyond files.** Any pathapi `Path`, open files, Python mappings, the
  environment (`sources.Env`), in-memory text (`sources.Text`) and commands
  (`cmd+json://...`, script files).
- **Includes.** `!include` in YAML, file-relative at every depth, with cycle
  detection and a load budget.
- **Typed results.** `load(..., into=Model)` builds a dataclass, a Pydantic
  model or any class, coercing every field without losing data.
- **Templates.** `Loader(interpolate=True)` renders `{{ ... }}` in values;
  `app.yaml.j2` (or any engine [tplx](https://github.com/jose-pr/tplx) knows)
  renders whole files.
- **Safe by default.** Commands and templates are off, interpolation is
  sandboxed, includes can be confined to directories, and errors never carry
  document text.

## Installation

```bash
pip install yaconfiglib
```

[pathapi](https://github.com/jose-pr/pathapi) is installed with it. Extras:

| Extra | Adds | Needed for |
| --- | --- | --- |
| `yaml` | PyYAML 6 | the YAML format and `!include` |
| `toml` | `tomli` (Python < 3.11), `tomli-w` | reading TOML below 3.11; writing TOML |
| `templates` | `tplx[jinja]` | interpolation and template files |
| `all` | every extra above | |

`import yaconfiglib` imports none of them; a feature whose extra is missing
raises `MissingDependencyError` naming it.

## Quick start

One document:

```python
import yaconfiglib

config = yaconfiglib.load("settings.yaml")          # format from the extension
data = yaconfiglib.loads('{"a": 1}', format="json")
yaconfiglib.dump(config, "settings.json")           # written as JSON
```

Layers, with dot access:

```python
from yaconfiglib import Loader

loader = Loader(base_dir="conf", merge="deep", wrap=True)
config = loader.load("base.yaml", "conf.d/*.yaml", "local.toml", None)
print(config.database.host)
```

Typed:

```python
import dataclasses
import yaconfiglib

@dataclasses.dataclass
class Database:
    host: str
    port: int

db = yaconfiglib.load("db.json", into=Database)     # "5432" becomes 5432
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
from yaconfiglib import Loader, Policy
from yaconfiglib.sources import Env

config = Loader(policy=Policy.trusted()).load(
    "base.yaml",
    "cmd+json://vault kv get -format=json secret/app",
    Env("APP_", nested="__"),                       # APP_DB__PORT -> {"DB": {"PORT": ...}}
)
```

## API overview

| Module | Purpose |
| --- | --- |
| `yaconfiglib` | `load`, `loads`, `load_all`, `loads_all`, `dump`, `dumps`; `Loader`, `Policy`; merging (`DELETE`, `register_merge`); `hydrate`, `typed_merge`; `DotAccessibleDict`; interpolation; every exception |
| `yaconfiglib.formats` | the `Format` contract and the registry: `register_format`, `get`, `for_filename`, `names` |
| `yaconfiglib.sources` | `Source`, `Text`, `Data`, `Env`, `Command`, and the scheme registry |
| `yaconfiglib.testing` | `FormatContract`, the suite every format passes |

The installed package carries its API header, `yaconfiglib/AGENTS.md`.

## Trust policy

A load reads only what you named, parses it, and follows `!include`. Anything
else must be allowed by its `Policy`:

| Setting | Default | Allows |
| --- | --- | --- |
| `allow_commands` | off | command sources and script files |
| `allow_templates` | off | template files |
| `allow_includes` | on | `!include` / `!load` |
| `sandbox` | on | sandboxed interpolation and Jinja templates only |
| `confine_to` | none | every file read inside these directories |
| `max_includes`, `max_depth`, `max_bytes` | 1000, 64, 64 MiB | the load's budget |

`Policy.trusted()` turns commands and templates on and the sandbox off, for
configuration you wrote yourself. For configuration you did not write, keep the
default and set `confine_to`.

## Development

```bash
# One venv per interpreter, named <version>-<os>-<arch>:
py -3.9 -m venv .venv/3.9-nt-amd64

# The dev extra installs the tooling and every extra that has tests:
.venv/3.9-nt-amd64/Scripts/pip install -e ".[dev]"

# Put the venv's Scripts (bin on POSIX) first on PATH: command tests start `python`.
export PATH="$PWD/.venv/3.9-nt-amd64/Scripts:$PATH"

python -m pytest -q -rs
```

[`AGENTS.md`](https://github.com/jose-pr/yaconfiglib/blob/main/AGENTS.md) is the
orientation file for a checkout: layout, environments, CI and the release
procedure. Micro-benchmarks live in
[`benchmarks/`](https://github.com/jose-pr/yaconfiglib/tree/main/benchmarks).

### Releasing

The project follows [Semantic Versioning](https://semver.org/) and keeps a
[changelog](https://github.com/jose-pr/yaconfiglib/blob/main/CHANGELOG.md).
Pushing a `v*` tag runs the release workflow: test gate, build, GitHub release,
PyPI publish; the docs site is deployed after it.

## License

MIT — see [LICENSE](https://github.com/jose-pr/yaconfiglib/blob/main/LICENSE).
