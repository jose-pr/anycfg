# Formats

A **format** turns document text into data and, if it can write, data back
into text. It never touches a file: the loader reads the bytes, decodes them
and hands the text over, so confinement, encoding and the trust policy apply to
every format alike. JSON, YAML, TOML, INI and dotenv are built in, and a format
of your own is a small class.

## How a format is chosen

In order:

1. **`format=`** — a name (`"yaml"`), an alias (`"yml"`), or a `Format` class.
   Names are case-insensitive.
2. **The file name.** The longest registered suffix wins, compared
   case-insensitively, so `app.env.yaml` is YAML and `settings.env` is dotenv.
3. **`claims_name`** — a format may claim names a suffix cannot describe, which
   is how dotenv reads `.env.production`.

When none of these decides, `UnsupportedFormatError` is raised **before the
file is opened**, listing the registered names. Nothing is sniffed and nothing
falls back to YAML:

```python
import anycfg

try:
    anycfg.load("settings.conf")
except anycfg.UnsupportedFormatError as error:
    print(error)    # no format reads settings.conf; pass format=<name> (registered: ...)

config = anycfg.load("settings.conf", format="ini")
```

`loads()` and `dumps()` take content, which has no name. `dumps()` needs
`format=`; `loads()` takes it, or reads a document that **names its own
format**:

```python
anycfg.loads('#!json\n{"port": 80}')          # a #! first line, removed before parsing
anycfg.loads("%YAML 1.1\n---\nport: 80\n")    # YAML's own directive
```

Every registered format is asked, through its `claims_document(data)`; a
format with a marker of its own (as YAML has `%YAML`) recognises it there.
Nothing is guessed from content that merely parses: a document that names no
format raises `UnsupportedFormatError`. An open file is chosen for by its
`name`; a stream without a real one (`sys.stdin`, `io.StringIO`) needs
`format=`.

## Decoding and errors

Bytes are decoded with `encoding=` (UTF-8 by default), **strictly**: a byte
that does not decode raises `ParseError` naming its offset rather than turning
into U+FFFD. One leading byte-order mark is dropped.

Every malformed document raises `anycfg.ParseError`, whatever the parser
underneath. It carries `path` (the source; `None` for in-memory content),
`lineno` and `colno` (1-based, or `None` where the parser does not say) and
reads as `path:line:col: message`. The message may name document
**structure** — a key, a section, a tag, an alias — but never a **value**, a
line or a snippet, and the parser's own exception is not kept in the chain,
because the line that failed may be a secret.

```python
try:
    anycfg.loads('{"port": }', format="json")
except anycfg.ParseError as error:
    print(error.lineno, error.colno)    # 1 10
```

`ParseError` is also a `ValueError`. A missing file is not a parse failure: it
raises `FileNotFoundError` as itself, so `except (anycfg.ConfigError,
OSError)` covers every load failure.

## Built-in formats

| Format | Names it reads | Extra | Writes | Load options | Dump options |
| --- | --- | --- | --- | --- | --- |
| `json` | `.json` | — | yes | `object_hook`, `object_pairs_hook`, `parse_float`, `parse_int` | `indent`, `sort_keys`, `ensure_ascii`, `separators`, `default` |
| `yaml` (alias `yml`) | `.yaml`, `.yml` | `yaml` | yes | — | `indent`, `width`, `default_flow_style`, `sort_keys`, `allow_unicode`, `explicit_start`, `explicit_end` |
| `toml` | `.toml` | `toml` below 3.11, and to write | yes | — | — |
| `ini` (alias `cfg`) | `.ini`, `.cfg` | — | yes | `interpolation`, `default_section` | `interpolation` |
| `dotenv` | `.env`, `*.env`, `.env.<stage>` | — | yes | `lowercase`, `strict` | — |

Every format is always listed by `anycfg.formats.names()`, whether or not
its extra is installed; using one whose extra is missing raises
`MissingDependencyError` (an `ImportError`) naming the extra.

### JSON

The standard library's `json`. A duplicate key keeps the last value, as `json`
does; `NaN` and `Infinity` are refused in both directions, since they are not
JSON. `dumps` writes UTF-8 text unescaped, indented two spaces, keys in
insertion order.

```python
from decimal import Decimal

anycfg.loads('{"price": 1.10}', format="json", parse_float=Decimal)
print(anycfg.dumps({"a": [1, 2]}, format="json", indent=None))   # {"a": [1, 2]}
```

### YAML

PyYAML's safe loader (`anycfg[yaml]`), so no Python object is ever
constructed. The default dialect is **YAML 1.1** as PyYAML implements it:

- `yes`, `no`, `on` and `off` are booleans;
- `0755` is an octal integer and `1.10` is the float `1.1`;
- `2024-01-02` is a date;
- quote any of them to keep it a string.

The load option **`dialect="1.2"`** reads plain scalars by the YAML 1.2 core
schema instead: only `true`/`false` are booleans, `0755` is the decimal 755
(`0o755` is octal), and dates, `yes` and `on` stay strings. It reaches the
document's includes too, and a loader can set it for every YAML document:

```python
anycfg.loads("country: NO\nmode: 0755\n", format="yaml", dialect="1.2")
# {'country': 'NO', 'mode': 755}
Loader(format_options={"yaml": {"dialect": "1.2"}})
```

Or let the type decide: **loading `into=` a type keeps each plain scalar's
text beside the value YAML read**, so a `str` field gets `"NO"`, `"1.10"` or
`"0755"` exactly as written, while a `bool` or `int` field gets the read value
(see [The type steers the parse](models.md#the-type-steers-the-parse)).
Without `into=`, values are exactly what YAML read.

**Duplicate keys raise** `ParseError`. A `<<:` merge key may still be
overridden by an explicit key next to it, which is what merge keys are for.

Three tags are understood: `!include` and `!load` pull in another source (see
[Includes](includes.md)), and `!delete` removes a key when the document is
merged over another (see [Merging](merging.md#none-and-delete)). They live on
anycfg's own `SafeLoader` subclass, so a plain `yaml.safe_load()`
elsewhere in your process never resolves them.

A `---`-separated stream is several documents. `anycfg.load()` refuses one
with `ParseError`; `load_all()` yields each, and `Loader.load()` merges them in
order.

```python
print(list(anycfg.loads_all("a: 1\n---\na: 2\n", format="yaml")))   # [{'a': 1}, {'a': 2}]
```

`dumps` writes plain data only: mappings, sequences, scalars, dates and sets.
A `dict` or `list` subclass (a `DotAccessibleDict`, an `OrderedDict`) is written
as the plain type, a `tuple` as a list and an `Enum` member as its value.
Anything else raises `ConfigTypeError` before anything is written, so the output
always loads back. Keys keep their order and non-ASCII text is written as is.

### TOML

The standard library's `tomllib` on Python 3.11+, its `tomli` backport below
(`anycfg[toml]`) — never the third-party `toml` package, which implements
TOML 0.5 and reads some 1.0 documents differently. TOML 1.0 is the portable
dialect: `tomli` 2.4+ accepts some TOML 1.1 syntax that the 3.11–3.14 `tomllib`
refuses.

Duplicate keys raise, datetimes load as `datetime` objects, and there is no
null. Writing needs `tomli-w` (also in the `toml` extra); the document must be a
table, and a `None` anywhere raises `ConfigTypeError` before anything is
written.

### INI

The standard library's `configparser`. The result is `{section: {option:
value}}` with every value a string. Option names are lower-cased and section
names kept; `key = value` and `key: value` both work; a value may continue on
indented lines; `#` and `;` start a comment only at the start of a line;
duplicate sections and options raise.

`%` interpolation is on by default, as `configparser` has it: `%(name)s`
refers to another option and a literal percent is written `%%`. A logging or
alembic format line is not a reference, so reading such a file needs
interpolation off:

```python
config = anycfg.load("logging.ini", interpolation=None)
# format = %(levelname)s [%(name)s] %(message)s, verbatim
```

`interpolation` takes `"basic"` (the default), `"extended"` for
`${section:option}` references, `"none"`/`None`, or a
`configparser.Interpolation` instance.

Keys in `[DEFAULT]` are inherited by every section rather than returned as a
section of their own, so a file holding nothing else loads as `{}` and logs a
warning. To read those keys as a section, point `default_section` at a name
the file does not use:

```python
config = anycfg.load("defaults.ini", default_section="__none__")
print(config)    # {'DEFAULT': {'timeout': '30'}}
```

`dumps` accepts exactly what `loads` returns — string values and lower-case
option names. Its `interpolation` option names the interpolation the file will
be read with, and values are escaped for it: `"basic"` (the default) writes `%`
as `%%`, `"extended"` writes `$` as `$$`, `"none"` writes values as they are.
The written text is checked to load back unchanged, so what INI cannot hold
raises `ConfigValueError` naming the option instead of being lost: a name
containing `=` or `:`, a value with leading or trailing blanks, a continuation
line starting with `#` or `;`, a `[DEFAULT]` section. Error messages never
quote the offending line or a value.

### dotenv

`KEY=value` assignments, returned as a flat mapping of strings. The format
claims `.env`, `*.env` and staged names such as `.env.local` and
`.env.development.local` — unless the name's **last** suffix belongs to another
format, so `app.env.yaml` is YAML and `.env.j2` is a template rendered first.

- `KEY=value` and `export KEY=value`; spaces around `=` are dropped.
- Keys may contain `.` and `-` after the first character and **keep their
  case**; pass `lowercase=True` to fold them.
- A **double-quoted** value may span lines and decodes `\n`, `\r`, `\t`, `\"`
  and `\\`.
- A **single-quoted** value may span lines and is raw — use it for a Windows
  path (`DIR='C:\new'`).
- An **unquoted** value runs to the end of the line, minus a `#` comment
  preceded by whitespace; quote characters inside it are ordinary.
- Only a newline ends an entry, so a form feed or U+2028 inside a value is data.
- A later duplicate key replaces an earlier one.

A line that is not an assignment logs a warning naming the line number, never
its text, and is skipped; with `strict=True` it raises `ParseError`. An
unterminated quote always raises, since it would otherwise swallow the rest of
the file.

```python
text = 'export HOST=example.org\nNAME="two\\nlines"   # comment\n'
print(anycfg.loads(text, format="dotenv"))   # {'HOST': 'example.org', 'NAME': 'two\nlines'}
```

Values are strings unless the load option **`coerce`** is set: `True` reads
each *unquoted* value by the YAML 1.2 core rules (`PORT=80` an int,
`DEBUG=true` a bool), while a quoted one stays text (`PORT="80"`); a callable
`(text) -> value` decides itself.

`dumps` writes a flat mapping of string values, double-quoted and escaped.

## Format options

A format's options are checked against what it declares. The one-document
functions take them as keywords:

```python
anycfg.load("logging.ini", interpolation=None)
anycfg.dumps({"b": 1, "a": 2}, format="json", sort_keys=True)
```

A `Loader` takes them per format, applied whenever that format reads a source —
including through an include:

```python
from anycfg import Loader

loader = Loader(format_options={"ini": {"interpolation": None}, "dotenv": {"lowercase": True}})
```

An option the format does not declare raises `TypeError` before the format is
called, rather than being ignored.

## Writing

`dumps(obj, format=...)` returns text. `dump(obj, target)` writes it, choosing
the format by the same rule as `load` — `format=`, else the target's file
name — so a value written with `dump` loads back with `load`:

```python
config = {"name": "app", "port": 8080}
anycfg.dump(config, "out.json")
print(anycfg.load("out.json") == config)    # True
```

The target may be:

- a `str` or `os.PathLike` path — written with `encoding=` and **LF** line
  endings on every platform;
- a pathapi `Path` — written with `write_bytes`;
- an open file — a text stream keeps its own codec, a binary one gets the text
  encoded with `encoding=`. A stream without a real name needs `format=`.

`obj` is serialized completely **before** the target is opened, so a value the
format cannot write leaves an existing file untouched. A trailing newline is
ensured. A format that cannot write raises `UnsupportedFormatError`.

Both functions first pass `obj` through `anycfg.dehydrate`, so typed data
writes directly: a dataclass, namespace or `NamedTuple` as a mapping, an `Enum`
member as its value, a path, `Decimal` or `UUID` as its text, and a type with a
`_dump_()` method as what that returns. A load `into=` a type followed by a
`dump` round-trips (see [Typed results](models.md#writing-typed-results-back)).

## Writing a format

Subclass `anycfg.formats.Format`, set its class attributes, override
`loads` (and `dumps` to write), and register it:

```python
import anycfg
from anycfg.exceptions import ConfigTypeError, ParseError
from anycfg.formats import Format, register_format


@register_format
class KeyValueFormat(Format):
    """`key = value` lines; a line starting with `#` is a comment."""

    name = "kv"
    suffixes = (".kv",)
    load_options = frozenset({"lowercase"})

    def loads(self, data, /, *, context=None, lowercase=False, **options):
        path = context.name if context is not None else None
        result = {}
        for lineno, line in enumerate(data.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or not key.strip():
                raise ParseError("expected key = value", path=path, lineno=lineno)
            key = key.strip()
            result[key.lower() if lowercase else key] = value.strip()
        return result

    def dumps(self, obj, /, **options):
        if not isinstance(obj, dict):
            raise ConfigTypeError(f"kv holds a flat mapping, not a {type(obj).__name__}")
        lines = []
        for key, value in obj.items():
            if not isinstance(value, str):
                raise ConfigTypeError(f"kv values are strings; {key} is a {type(value).__name__}")
            lines.append(f"{key} = {value}\n")
        return "".join(lines)


print(anycfg.loads("Host = example.org", format="kv", lowercase=True))   # {'host': 'example.org'}
```

From then on `load("app.kv")`, `format="kv"` and `!include app.kv` all reach
it. The class attributes:

| Attribute | Meaning |
| --- | --- |
| `name` | the registry name, lower-case |
| `aliases` | other names `format=` accepts |
| `suffixes` | suffixes claimed, lower-case with the dot; longest match wins |
| `priority` | breaks a tie when two formats claim one suffix |
| `load_options` / `dump_options` | the keywords `loads` / `dumps` accept; anything else is refused with `TypeError` |
| `binary` | `loads` receives `bytes` instead of decoded text |

The contract a format keeps:

- **Text in, data out.** `loads(data, /, *, context=None, **options)` gets the
  decoded text and returns plain data. It is required.
- **`loads_all`** is optional, for a multi-document format; without it, a
  source is one document. **`dumps(obj, /, **options) -> str`** is optional;
  `anycfg.formats.capabilities(fmt)` reports `"load_all"` and `"dump"`
  from what is overridden.
- **A malformed document raises `ParseError`**, translated from the parser's
  own error and raised *outside* the `except` block, so neither `__cause__` nor
  `__context__` keeps the parser's exception (which may hold the document). The
  message may name structure (a key, a section) but never a value or a line.
- **`dumps` receives plain data**: `dump`/`dumps` have already applied
  `dehydrate`. A direct `Format().dumps()` call does not.
- **A value it cannot write raises `ConfigTypeError`** (or `ConfigValueError`)
  before anything is written.
- **A third-party dependency is imported inside the methods**, so the class
  always imports and stays listed; a missing one raises
  `MissingDependencyError` naming the extra.
- **No state.** One instance may serve every load.

A format that supports includes resolves them through
`context.include(source, ...)`, which applies the policy, confinement, the
budget and cycle detection; `context` is `None` for a direct `Format().loads()`
call, and `include` then raises `ConfigValueError`. `context.origin` is the
path the document came from, and `context.name` the name to put in a
`ParseError`.

A name a suffix cannot describe is claimed with an optional static method:

```python
class ProcfileFormat(Format):
    name = "procfile"

    @staticmethod
    def claims_name(name):          # the base name, lower-cased
        return name == "procfile"
```

`register_format` raises `ValueError` when another class already holds one of
the new format's names, or one of its suffixes at the same `priority`; pass
`replace=True` to take it over. `unregister_format(name)` removes one.

### Testing a format

`anycfg.testing.FormatContract` is the suite every built-in format passes,
shipped so yours runs it too. Subclass it in a pytest module:

```python
from anycfg.testing import FormatContract


class TestKeyValueFormat(FormatContract):
    format = KeyValueFormat
    document = "a = 1\nb = 2\n"
    samples = [{"a": "1"}, {"host": "example.org"}]
    malformed = "{secret}"          # a line with no "="
```

It checks the class attributes, that a byte-order mark is ignored, that an
unknown option raises `TypeError`, that `loads(dumps(x)) == x` for every
sample, that arbitrary text parses or raises `ParseError`, that a value the
format cannot write raises before writing — and that a malformed document's
text (the `{secret}` marker is replaced by a planted value) reaches neither the
error, its chain nor the log. A format is not done until it passes unmodified.

### Distributing a format

Another distribution registers formats through the `anycfg.formats`
entry-point group:

```toml
[project.entry-points."anycfg.formats"]
kv = "mypackage.formats:KeyValueFormat"
```

Entry points are read lazily, on the first registry lookup. One cannot take a
built-in name, and a broken one is logged and skipped rather than breaking
every load.

**A plugin's format is not reachable from documents by default.** Your own
top-level sources can use it — `load("app.kv")`, `format="kv"` — but an
`!include` target's name, an include's `format:` key and a command's
`#!<format>` line select only built-in and code-registered formats, so
installing a package never widens what a hostile document can parse. Name the
formats documents may select in `Loader(formats=...)`, which **replaces** the
default set:

```python
from anycfg import Loader, formats

loader = Loader(formats=[*formats.document_formats(), "kv"])
```

## The registry

| Function | Returns |
| --- | --- |
| `formats.names()` | every registered name, sorted |
| `formats.get(name, *, allowed=None)` | the class for a name or alias; `UnsupportedFormatError` listing the names |
| `formats.for_filename(name, *, allowed=None)` | the class a file name selects, or `UnsupportedFormatError` |
| `formats.find_for_filename(name, *, allowed=None)` | the same, or `None` |
| `formats.document_formats()` | the names documents may select by default |
| `formats.capabilities(fmt)` | `{"load"}`, plus `"load_all"` and `"dump"` when overridden |

```python
from anycfg import formats

print(formats.for_filename("app.env.yaml").name)       # yaml
print(formats.for_filename(".env.production").name)    # dotenv
print(sorted(formats.capabilities(formats.get("yml"))))   # ['dump', 'load', 'load_all']
```

See the [API reference](../api/formats.md) for signatures.
