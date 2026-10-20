# anycfg

[![Version](https://img.shields.io/pypi/v/anycfg.svg)](https://pypi.org/project/anycfg/)
[![Python versions](https://img.shields.io/pypi/pyversions/anycfg.svg)](https://pypi.org/project/anycfg/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/jose-pr/anycfg/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/anycfg/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/anycfg/test.yml)](https://github.com/jose-pr/anycfg/actions/workflows/test.yml)

**anycfg loads, layers and writes configuration in any registered format
with the standard Python serialization API** — `load`, `loads`, `dump`, `dumps`,
as `json` and `tomllib` spell it — plus a `Loader` that merges many sources in
order, YAML `!include`, typed hydration, and a trust policy that is **untrusted by
default**. Documentation: <https://jose-pr.github.io/anycfg/>.

anycfg was published as `yaconfiglib` up to 0.13.1; see
[Migrating from yaconfiglib](https://jose-pr.github.io/anycfg/guide/migrating/).

## Features

- **One vocabulary.** `load(path)` and `dump(obj, path)` pick the format from
  the file extension, so `dump(x, "a.json")` then `load("a.json")` round-trips;
  `loads(text, format=...)` and `dumps(obj, format=...)` take content. An
  extension no format claims is an error, never a guess.
- **Formats as plugins.** JSON, YAML, TOML, INI and dotenv are built in; a
  `Format` subclass registers in code or through the `anycfg.formats`
  entry-point group. Every format passes the shipped contract suite
  (`anycfg.testing.FormatContract`).
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
- **Templates.** `Loader(interpolate=True)` renders `{{ ... }}` in values after
  merging — or `interpolate="liquid"` (any [tplx](https://github.com/jose-pr/tplx)
  engine), or `interpolate="vars"` for `${a.b}` references with no extra; a file
  named `app.yaml.j2` (any engine's suffix) is rendered whole before parsing.
- **Async.** `await aload(...)`, `aload_all`, `adump` and `Loader.aload` read
  async streams and chunk iterators (stopping past the byte budget) and run the
  rest of the load off the event loop; no extra needed.
- **Safe by default.** Commands and templates are off, interpolation is
  sandboxed, includes can be confined to directories, and errors never carry
  document text.

## Installation

```bash
pip install anycfg
```

[pathapi](https://github.com/jose-pr/pathapi) is installed with it. Extras:

| Extra | Adds | Needed for |
| --- | --- | --- |
| `yaml` | PyYAML 6 | the YAML format and `!include` |
| `toml` | `tomli-w` | writing TOML (reading needs no extra: `tomli` comes with anycfg below Python 3.11) |
| `templates` | `tplx[jinja]` | interpolation and template files |
| `all` | every extra above | |

`import anycfg` imports none of them; a feature whose extra is missing
raises `MissingDependencyError` naming it.

## Quick start

One document:

```python
import anycfg

config = anycfg.load("settings.yaml")          # format from the extension
data = anycfg.loads('{"a": 1}', format="json")
anycfg.dump(config, "settings.json")           # written as JSON
```

A few layers, later over earlier, `None` for one that is absent:

```python
config = anycfg.load("base.toml", "local.toml", merge="deep")
config = anycfg.load(*anycfg.default_configs("app.toml"), default={})
```

Layers, with dot access:

```python
from anycfg import Loader

loader = Loader(base_dir="conf", merge="deep", wrap=True)
config = loader.load("base.yaml", "conf.d/*.yaml", "local.toml", None)
print(config.database.host)
```

Typed:

```python
import dataclasses
import anycfg

@dataclasses.dataclass
class Database:
    host: str
    port: int

db = anycfg.load("db.json", into=Database)     # "5432" becomes 5432
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

## API overview

| Module | Purpose |
| --- | --- |
| `anycfg` | `load`, `loads`, `load_all`, `loads_all`, `dump`, `dumps`; `aload`, `aload_all`, `adump`; `Loader`, `Policy`; `user_config_path`, `system_config_path`, `default_configs`; merging (`DELETE`, `register_merge`); `hydrate`, `typed_merge`; `DotAccessibleDict`; interpolation; every exception |
| `anycfg.formats` | the `Format` contract and the registry: `register_format`, `get`, `for_filename`, `names` |
| `anycfg.sources` | `Source`, `Text`, `Chunks`, `Data`, `Env`, `Command`, and the scheme registry |
| `anycfg.testing` | `FormatContract`, the suite every format passes |

The installed package carries its API header, `anycfg/AGENTS.md`.

## Trust policy

A load reads only what you named, parses it, and follows `!include`. Anything
else must be allowed by its `Policy`:

| Setting | Default | Allows |
| --- | --- | --- |
| `allow_commands` | off | command sources and script files |
| `allow_templates` | off | template files |
| `allow_includes` | on | `!include` / `!load` |
| `sandbox` | on | interpolation and template files in tplx's sandbox; engines it cannot hold refused |
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

[`AGENTS.md`](https://github.com/jose-pr/anycfg/blob/main/AGENTS.md) is the
orientation file for a checkout: layout, environments, CI and the release
procedure. Micro-benchmarks live in
[`benchmarks/`](https://github.com/jose-pr/anycfg/tree/main/benchmarks).

### Releasing

The project follows [Semantic Versioning](https://semver.org/) and keeps a
[changelog](https://github.com/jose-pr/anycfg/blob/main/CHANGELOG.md).
Pushing a `v*` tag runs the release workflow: test gate, build, GitHub release,
PyPI publish; the docs site is deployed after it.

## License

MIT — see [LICENSE](https://github.com/jose-pr/anycfg/blob/main/LICENSE).
