# Migrating from the previous API

This release replaced the public API. Every entry point, keyword and backend
class changed; the behaviour you relied on is still there, under the names
below. Three changes affect code that otherwise looks fine:

- **The default policy is untrusted.** Commands and template files used to run
  by default; now they are refused until a `Policy` allows them. Configuration
  you wrote yourself loads as before with `Loader(policy=Policy.trusted())`.
- **Formats are never guessed.** `loads()`/`dumps()` need `format=`, `dump()`
  picks the format from the file name instead of always writing YAML, and a
  name no format claims raises instead of being read as YAML.
- **Results are plain `dict`/`list`.** Pass `wrap=True` for dot access.

## Every removed name

| Before | Now |
| --- | --- |
| `ConfigLoader(base_dir, ...)` | `Loader(base_dir=..., ...)` — keyword-only, and no longer a backend |
| `ConfigLoader.load_as(Model, ...)`, `yaconfiglib.load_as(Model, ...)` | `load(..., into=Model)`, `Loader.load(..., into=Model)`, `hydrate(Model, data)` |
| `DEFAULT_LOADER` | the one-document functions, or a `Loader()` of your own |
| `ConfigLoader.base_dir = ...` (setter) | read-only `Loader.base_dir`; make a new `Loader` |
| `ConfigLoader.confine_to` | `Loader.policy.confine_to` |
| `yaconfiglib.loader` (module) | `yaconfiglib` |
| `yaconfiglib.errors` | `yaconfiglib.exceptions` (also re-exported at the root) |
| `load_error_types()` | `(ConfigError, OSError)` |
| `UnknownLoaderError` | `UnsupportedFormatError` |
| `ConfigBackend` | `yaconfiglib.formats.Format`, registered with `register_format` |
| `ConfigBackend.PATHNAME_REGEX`, `.NAME`, `.can_load_path` | `Format.suffixes`, `Format.name`, `Format.claims_name` |
| `YamlConfig`, `JsonConfig`, `TomlConfig`, `IniConfig`, `DotenvBackend` | the formats `"yaml"`, `"json"`, `"toml"`, `"ini"`, `"dotenv"` (`formats.get(name)`) |
| `EnvVarBackend(prefix, lowercase, nested_delimiter, coerce)` | `sources.Env(prefix, nested=..., lowercase=...)`; types via `into=` |
| `CommandBackend`, `utils.source.CommandSource` | `sources.Command`, `Command.script` |
| `PythonBackend(data)` | the mapping itself as a source, or `sources.Data(data)` |
| `Jinja2ConfigLoader` | template files through tplx, with `Policy(allow_templates=True)` |
| `MergeMethod`, `ConfigLoaderMergeMethod` | strategy names: `"simple"`, `"substitute"`, `"deep"` |
| `ConfigLoaderMergeMethod.Last` / `.List` / `.Hash` | `collect="last"` / `"list"` / `"hash"` |
| `MergeMethod.extend(...)`, `utils.enum.IntEnum` | `register_merge(name, strategy)` |
| `MergeMethod.Deep(a, b)` called directly | `Loader(merge="deep").load(a, b)` |
| `utils.merge.Merge` | `yaconfiglib.Merge` |
| `utils.merge.is_scalar`, `is_array` | removed |
| `utils.jinja2.interpolate`, `compile`, `eval`, `references`, `load_template`, `get_environment`, `DEFAULT_ENV` | `Loader(interpolate=True)`; `TemplateInterpolator().render(text, scope)` and `.references(text)` |
| `utils.source.parse_sources`, `has_glob_pattern` | `Loader.load()` resolves sources itself |
| `utils.trust` | `Policy` |
| `yaconfiglib.utils` (package) | removed; see the rows above |
| the `jinja2` and `transform` extras | the `templates` extra |

| Keyword before | Now |
| --- | --- |
| `loader="yaml"` | `format="yaml"` |
| `loader=<backend instance>`, `loader_factory=` | `format=`, plus `format_options={"<format>": {...}}` for its options |
| `path_factory=` | pass a pathapi `Path` object as the source |
| `allow_commands=` | `Policy(allow_commands=...)` |
| `sandbox=` | `Policy(sandbox=...)` |
| `strict=` | `Policy(strict=...)` |
| `confine_to=` | `Policy(confine_to=...)` |
| `timeout=` (command sources) | `Policy(command_timeout=...)`, or `Command(..., timeout=...)` |
| `ignore_error=` | `on_error=`, with a `LoadEvent` |
| `key_factory=` (callable or `"%<jinja>"`) | `key=`, a callable `(name, document) -> key` |
| `transform=` (a Jinja string) | `transform=`, a callable `(document, name) -> document` |
| `interpolate=True` on `yaconfiglib.load()` | `Loader(interpolate=True).load(...)` |
| `environment=` (a Jinja environment for `.j2`) | removed; `interpolate=<engine or interpolator>` for values |
| `ini_interpolation=` | `interpolation=` (INI format option) |
| `ini_default_section=` | `default_section=` (INI format option) |
| `dotenv_strict=` | `strict=` (dotenv format option) |
| `lowercase=` (dotenv and env, default `True`) | `lowercase=`, default `False` |
| `json_decoder_options=` | the JSON format options as keywords (`parse_float=`, ...) |
| `loader_cls=` (YAML) | removed |
| `coerce=` (env) | `into=` |
| `base_dir=""` | `base_dir=None` |
| `!include {transform: ..., key_factory: ...}` | removed (dropped with a warning); `format:` is new |

These keep their names and meaning: `encoding`, `merge`, `merge_options`,
`recursive`, `bound_loops`, `inject_env`, `default`, `flatten`, `interpolate`
(on `Loader`), and `typed_merge`, `OpaqueMerge`, `opaque`, `TypedNamespace`,
`DotAccessibleDict`, `ConfigError`, `ConfigValueError`, `ConfigTypeError`,
`ConfinementError`, `CommandsDisabledError`, `CommandError`,
`CommandTimeoutError`, `ErrorFrame`.

## Before and after

### `ConfigLoader` → `Loader`

```python
# before
from yaconfiglib import ConfigLoader, ConfigLoaderMergeMethod

loader = ConfigLoader("conf", merge=ConfigLoaderMergeMethod.Deep, allow_commands=False)
config = loader.load("base.yaml", "local.yaml")
print(config.database.host)
```

```python
# now
from yaconfiglib import Loader

loader = Loader(base_dir="conf", merge="deep", wrap=True)   # commands are off by default
config = loader.load("base.yaml", "local.yaml")
print(config.database.host)
```

Every setting can still be overridden per call, but an unknown keyword now
raises `TypeError` instead of being passed to a backend and ignored. Format
options go in `format_options`.

### `load_as` → `into=`

```python
# before
settings = yaconfiglib.load_as(Settings, "base.yaml", "local.yaml", merge="deep")
```

```python
# now
settings = Loader(merge="deep").load("base.yaml", "local.yaml", into=Settings)
one = yaconfiglib.load("settings.yaml", into=Settings)
```

Hydration now coerces every field — `"false"` becomes `False`, `"8080"`
becomes `8080`, ISO strings become dates — drops unknown keys (or raises with
`hydrate(..., extra="forbid")`), and never truncates (`1.9` for an `int` field
raises). A YAML scalar such as `NO` or `1.10` now reaches a `str` field as
written instead of as `False` or `1.1`, and `dump()` writes a typed result back
(see [Typed results](models.md#the-type-steers-the-parse)).

### `loader=` → `format=`

```python
# before
config = yaconfiglib.load("settings.conf", loader="ini", ini_interpolation=None)
config = ConfigLoader(loader_factory=lambda path: IniConfig(interpolation=None)).load("*.ini")
```

```python
# now
config = yaconfiglib.load("settings.conf", format="ini", interpolation=None)
config = Loader(format_options={"ini": {"interpolation": None}}).load("*.ini")
```

### `loads()` no longer defaults to YAML

```python
# before
data = yaconfiglib.loads("a: 1")
text = yaconfiglib.dumps(data)               # always YAML
yaconfiglib.dump(data, "out.json")           # YAML, despite the name
```

```python
# now
data = yaconfiglib.loads("a: 1", format="yaml")
text = yaconfiglib.dumps(data, format="yaml")
yaconfiglib.dump(data, "out.json")           # JSON, by the name
```

A stream with no real name (`sys.stdin`) needs `format=` too.

### `"#!name"` strings → `sources.Text`

```python
# before
config = loader.load("base.yaml", "#!override.yaml\nport: 9090\n")
```

```python
# now
from yaconfiglib.sources import Text

config = Loader().load("base.yaml", Text("port: 9090\n", format="yaml", name="override"))
```

A `str` source is always a location now; content is `Text` (or `loads()` for a
single document).

### `PythonBackend` → a mapping or `Data`

```python
# before
override = loader.load(loader=PythonBackend({"debug": True}))
config = ConfigLoaderMergeMethod.Deep(base, override)
```

```python
# now
config = Loader(merge="deep").load("base.yaml", {"debug": True})
```

A mapping is wrapped in `sources.Data`, deep-copied when read; use `Data`
directly for other values or to name the document.

### `EnvVarBackend` → `sources.Env`

```python
# before
config = ConfigLoader().load(loader=EnvVarBackend(prefix="APP_", nested_delimiter="__", coerce=True))
```

```python
# now
from yaconfiglib.sources import Env

config = Loader().load(Env("APP_", nested="__", lowercase=True))     # values stay strings
settings = Loader().load(Env("APP_", nested="__", lowercase=True), into=Settings)
```

Keys keep their case unless `lowercase=True`, and values stay strings: types
come from `into=`.

### Command sniffing → an explicit format

```python
# before: the output's format was guessed
secrets = loader.load("cmd:vault kv get secret/app")
```

```python
# now
from yaconfiglib import Policy

loader = Loader(policy=Policy.trusted())
secrets = loader.load("cmd+json://vault kv get -format=json secret/app")
```

Name the format with `cmd+<format>://`, `Command(..., format=...)`, a
`#!<format>` first line of output, or a script name such as `secrets.json.sh`.
The scheme is written `cmd://`, not `cmd:`. A command now has a 60-second
default timeout (`Policy.command_timeout`) and keeps its output's leading
indentation.

### `MergeMethod` → names and `collect=`

```python
# before
ConfigLoader(merge=ConfigLoaderMergeMethod.Hash, key_factory="%pathname.parent.name")
```

```python
# now
from pathlib import PurePath

Loader(collect="hash", key=lambda name, document: PurePath(name).parent.name)
```

`None` in an overriding document now means "no opinion" in every strategy
(`simple` used to replace with it); remove a key with `DELETE` (`!delete` in
YAML).

### `ignore_error=` → `on_error=` with `LoadEvent`

```python
# before
def predicate(error, *, phase, path, **context):
    return phase == "load" and isinstance(error, FileNotFoundError)

ConfigLoader(ignore_error=predicate)
```

```python
# now
def predicate(event):
    return event.phase == "load" and isinstance(event.error, FileNotFoundError)

Loader(on_error=predicate)
```

`LoadEvent` has `error`, `phase`, `path` and `key`. `on_error=True` still skips
every failure with a WARNING.

### Trust settings → `Policy`

```python
# before
config = yaconfiglib.load(
    "untrusted.yaml", base_dir="conf", allow_commands=False, sandbox=True, strict=True, confine_to=True
)
```

```python
# now
from yaconfiglib import Policy

loader = Loader(base_dir="conf", policy=Policy(strict=True, confine_to=True))
config = loader.load("untrusted.yaml")
```

The untrusted settings are the default now, so only what differs from it needs
saying. `CommandsDisabledError` and `ConfinementError` are `PolicyError`s, and
so `PermissionError`s; `CommandsDisabledError` used to be a `ValueError`.

### `transform` and `key_factory` strings → callables

```python
# before
ConfigLoader(key_factory="%pathname.name").load("*.yaml", transform="value.services")
```

```python
# now
from pathlib import PurePath

Loader(key=lambda name, document: PurePath(name).name).load(
    "*.yaml", transform=lambda document, name: document["services"]
)
```

An include mapping can no longer carry `transform` or `key_factory`; a
document selects only data, never code.

### Interpolation is a `Loader` setting

```python
# before
config = yaconfiglib.load("app.yaml", interpolate=True, inject_env=True)
```

```python
# now
config = Loader(interpolate=True, inject_env=True).load("app.yaml")
```

The one-document functions never interpolate. Interpolation is sandboxed
unless the policy turns the sandbox off — and sandboxed, a value template sees
plain data only, so a date reads as its text — and every failure is a
`TemplateError`.

### Template files

```python
# before: a .j2 file rendered by default, unsandboxed, with a custom environment
config = yaconfiglib.load("settings.yaml.j2", environment=my_env)
```

```python
# now
from yaconfiglib import Policy

config = Loader(policy=Policy(allow_templates=True), inject_env=True).load("settings.yaml.j2")
```

Template files are refused unless the policy allows them, and render through
tplx — any engine it knows, by suffix (`.j2`, `.liquid`, `.mustache`, ...).
Under the default sandbox, engines that cannot be sandboxed (Mako, Cheetah,
ERB, EPP) are refused, `pathname` is the path's text — write `{{ filename }}`
for `{{ pathname.name }}` — and a template includes files only from the
confinement roots. There is no `environment=` for template files; for values,
`interpolate=` takes an engine name (`"liquid"`, `"vars"`) or your own
interpolator.

### `DotAccessibleDict` → `wrap=True`

```python
# before: every mapping result was dot-accessible
config = yaconfiglib.load("app.yaml")
print(config.server.port)
```

```python
# now
config = Loader(wrap=True).load("app.yaml")
print(config.server.port)
```

`wrap=` also takes a callable applied to the result. To reach a nested value
without wrapping, `yaconfiglib.dig` follows Puppet's `dig`:

```python
yaconfiglib.dig(config, "server", "port", default=8080)   # the default when a step is missing
```

### Errors

```python
# before
try:
    config = yaconfiglib.load("app.yaml")
except (yaml.YAMLError, *yaconfiglib.load_error_types()) as error:
    ...
```

```python
# now
try:
    config = yaconfiglib.load("app.yaml")
except (yaconfiglib.ConfigError, OSError) as error:
    ...
```

Every parse failure is `ParseError` (also a `ValueError`) with `path`,
`lineno` and `colno`, whatever the parser — `except yaml.YAMLError` and
`except json.JSONDecodeError` no longer match. `UnsupportedFormatError` is a
`ValueError`, no longer a `NotImplementedError`. Template and interpolation
failures are `TemplateError`.

### Extras

```bash
# before
pip install "yaconfiglib[yaml,jinja2]"
```

```bash
# now
pip install "yaconfiglib[yaml,templates]"
```

`templates` installs tplx 0.2+ with Jinja; another template engine needs its
own tplx extra (`tplx[liquid]`). `toml` now also installs `tomli-w`, to write
TOML, and `all` installs every extra.
