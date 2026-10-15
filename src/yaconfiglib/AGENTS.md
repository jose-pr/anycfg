# `yaconfiglib` — public API header

Header-file-style reference for the `yaconfiglib` package: every public export with
its signature, arguments, contract and gotchas, so the package can be used without
reading its source. It ships inside the package and is self-contained. Development
documentation lives with the source at <https://github.com/jose-pr/yaconfiglib>.

Everything public is importable from `yaconfiglib`, `yaconfiglib.formats`,
`yaconfiglib.sources`, `yaconfiglib.exceptions` (also re-exported at the root) and
`yaconfiglib.testing`. Every other module starts with `_` and is private.

`pip install yaconfiglib` brings pathapi, the path layer. Extras: `yaml` (PyYAML 6),
`toml` (`tomli` below Python 3.11, and `tomli-w` to write), `templates`
(`tplx[jinja]` 0.1.5+: template files and engine interpolation; other engines need
their own tplx extra; `interpolate="vars"` needs none), `all`. `import yaconfiglib`
imports none of them; a feature whose extra is missing raises
`MissingDependencyError` naming it when used.

`yaconfiglib.__version__` is the installed distribution's version.

## One document (`import yaconfiglib`)

The standard Python serialization API, as `json`, `tomllib` and `pickle` spell
it: `loads` takes **content**, `load` takes a **location**. A `str` given to `load`
is always a path, never content. Each has an awaitable twin (see "Async").

- **`load(source, /, *, format=None, encoding="utf-8", policy=None, into=None, **options) -> Any`**
  — one document from a path (`str`, `os.PathLike`), a pathapi `Path`, an open file
  (anything with `read()`), or a `yaconfiglib.sources.Source`. The format is
  `format=` (a name or a `Format` class), else the one the file name's suffix selects;
  when neither decides, `UnsupportedFormatError` is raised **before the file is
  opened**. A multi-document source (YAML `---`) raises `ParseError`; use `load_all`.
  A glob pattern is one location: matching no file or more than one raises
  `ConfigValueError`; a list of sources is `TypeError` (layer them with `Loader`).
  `options` go to the format and are checked against its `load_options` (an unknown
  one is `TypeError`). `into=` hydrates the result (see `hydrate`). `load(None)` is
  `TypeError`, `load("")` is `ValueError`, `load(b"...")` is `TypeError`.
- **`loads(data, /, *, format, encoding="utf-8", policy=None, into=None, **options) -> Any`**
  — `data` is `str`, or `bytes`/`bytearray` decoded with `encoding`. `format=` is
  required. Touches no file except through an `!include` (policy-governed, resolved
  against the working directory).
- **`load_all(source, /, ...)`** / **`loads_all(data, /, *, format, ...)`** `-> Iterator[Any]`
  — every document of a multi-document source; one for a single-document format.
  Both take `into=`, which builds each document.
- **`dumps(obj, /, *, format, **options) -> str`** — `format=` required. `obj` is
  passed through `dehydrate` first (dataclasses, enums, paths, `_dump_()`). Raises
  `UnsupportedFormatError` when the format cannot write, `ConfigTypeError` /
  `ConfigValueError` for a value it cannot represent, `TypeError` for an option
  outside its `dump_options`.
- **`dump(obj, target, /, *, format=None, encoding="utf-8", **options) -> None`** — the
  format is `format=`, else the one `target`'s file name selects (the same rule as
  `load`, so `dump(x, "a.json")` then `load("a.json")` round-trips); no name and no
  format raises `UnsupportedFormatError`. `target`: a `str`/`os.PathLike` path (written
  with `encoding` and **LF** line endings), a pathapi `Path` (`write_bytes`), or a
  writable file (a text stream keeps its own codec; a binary one gets
  `text.encode(encoding)`). `obj` is serialized **before** the target is opened, so a
  failure leaves an existing file untouched. A trailing newline is ensured.

Decoding is strict UTF-8 by default, one leading BOM is dropped, and a decode failure
is a `ParseError` naming the byte offset. An `encoding` must name a **text** codec:
`zlib_codec`, `base64_codec` and other bytes-to-bytes codecs raise `ConfigValueError`
(from a document's include as well), since they would inflate what `max_bytes`
counted.

## Async (`aload`, `aload_all`, `adump`, `Loader.aload`)

Gather first, parse second, no new dependency. An **async source** is an async
iterable of `str`/`bytes` chunks (`response.aiter_bytes()`) or an object with
`async def read()` (aiofiles, `asyncio.StreamReader`; asked for 64 KiB at a time,
or once when `read` takes no size). It is read on the event loop and stops as soon
as it passes `max_bytes` (`BudgetExceededError`); a named one is checked against
`confine_to` **before** it is read. Everything else — paths, includes, commands,
parsing, merging, hydration — is the sync pipeline in a worker thread
(`asyncio.to_thread`). Cancelling stops the async reads; work handed to the thread
finishes. A sync `load`/`Loader.load` given an async source raises `TypeError`
pointing here.

- **`await aload(source, /, *, format=None, encoding="utf-8", policy=None, into=None, **options)`**
  — `load`, plus async sources. The format is `format=`, else the source's `name`.
- **`aload_all(source, /, ...) -> AsyncIterator[Any]`** — `load_all`'s documents.
- **`await adump(obj, target, /, *, format=None, encoding="utf-8", **options)`** — a
  path or sync file goes to `dump` in a thread; an async writer gets the text
  (`async def write`: `str`, or bytes in `encoding` when it refuses `str`; a writer
  with `async def drain()`: bytes, then `drain()`). Serialized completely first.
- **`await Loader.aload(*sources, ...)`** / **`Loader.aload_all(*sources, ...)`** —
  `load`/`load_all` with the same arguments; async sources may sit anywhere among
  the others, nested lists included. `aload_all` checks its arguments when called.
  A confinement refusal of an async source is offered to `on_error`.

## Loader (`yaconfiglib.Loader`)

`Loader(*, base_dir=None, policy=None, encoding="utf-8", merge="simple",
merge_options=None, collect=None, key=None, recursive=False, bound_loops=True,
path_filter=None,
interpolate=False, inject_env=False, wrap=None, on_error=False,
format_options=None, formats=None)`

- **`.load(*sources, format=None, into=None, default=None, flatten=False, transform=None, **overrides) -> Any`**
  — read every source, combine them in order, finish the result.
- **`.load_all(*sources, format=None, into=None, transform=None, **overrides) -> Iterator[Any]`**
  — every document of every source, each finished on its own (interpolated, wrapped,
  hydrated); a `---` stream yields each of its documents.
- `**overrides`: any of `policy`, `encoding`, `merge`, `merge_options`, `collect`,
  `key`, `recursive`, `path_filter`, `interpolate`, `inject_env`, `wrap`,
  `on_error`, `format_options` — for that call only (`policy=None` keeps the
  loader's). Anything else is `TypeError` (format options go in
  `format_options={"json": {...}}`). No sources is `TypeError`.
- With `into=`, only the typed build sees a scalar's text: merging, includes,
  `transform`, `key=`, a custom merge strategy and the duplicate-key check get the
  values the format read, exactly as without `into=`. `flatten=True` with nothing
  loaded returns `default`.
- Properties: `.policy`, `.base_dir` (a pathapi `Path` or `None`).

Settings:

- `base_dir` — relative `str`/`os.PathLike` sources join to it; `None` means the
  working directory. Also the anchor for includes inside in-memory content. A pathapi
  `Path` **source is used exactly as given**, never joined.
- `merge` — `"simple"`, `"substitute"`, `"deep"`, a name added with `register_merge`,
  or a callable `(a, b, *, memo=None, **options) -> merged`. Resolved at construction:
  an unknown name is `ValueError`, a non-callable `TypeError`.
- `merge_options` — keywords every merge call gets (`{"mergelists": True}` for deep).
- `collect` — instead of merging: `"last"` (the last document), `"list"` (all, in
  order), `"hash"` (`{key(name, doc): doc}`; a repeated key replaces the earlier
  document with a WARNING; an unnamed source raises `ConfigValueError`).
- `key` — `(name, document) -> key` for `collect="hash"`; default: the source's file
  stem, and an unnamed source (a mapping, unnamed `Text`) then raises.
- `recursive` — `**` in a glob descends into directories. `bound_loops` — bound a `**`
  walk through a Windows junction loop (on by default; a POSIX directory symlink is
  never descended).
- `path_filter` — `(path) -> bool`, asked about every path before it is read: a
  top-level source, an include, a script, a glob pattern before it is expanded and
  each match, as the pathapi `Path` that would be read. A falsy answer refuses it
  with `ConfinementError` (offered to `on_error`; a refused match is named by its
  pattern); an exception it raises propagates. A `str` source — top-level or from a
  document — always becomes a `LocalPath`; any other `Path` (remote, in-memory)
  only ever comes from the caller, so this is where a caller vets those.
- `interpolate`, `inject_env` — see "Interpolation".
- `wrap` — `None` returns plain data; `True` (or `DotAccessibleDict`) returns
  dot-accessible mappings; a callable is applied to the result. Ignored with `into=`.
- `on_error` — `False` raises; `True` skips a failing source (WARNING naming source,
  phase and error **type**, never its text); a callable `(LoadEvent) -> bool` decides
  per failure (DEBUG). `LoadEvent(error, phase, path, key)`; `phase` is `"load"`,
  `"include"`, `"merge"`, `"interpolate"` or `"glob"` (a directory that could not be
  listed; skipping it keeps the rest of the pattern). A failure inside an included
  file is offered once per level: `"load"` for the file itself, then `"include"` for
  each file that included it — the same exception object. `BudgetExceededError` is
  never offered.
- `format_options` — `{"<format>": {...}}`, applied whenever that format reads a
  source in this loader; checked against the format's options.
- `formats` — the format names a **document** may select (by an include's file name, an
  include's `format:`, or a command's `#!<format>` line). Default: built-in and
  code-registered formats; a format from an installed plugin must be named here.

`load()` arguments: `format` reads every source as that format; `default` is returned
only when **no** source loads (nothing matched, or every one failed under
`on_error`), never merged; `flatten=True` flattens a mapping of mappings or a list
of lists by one level, skipping `None` members (anything else is `ConfigTypeError`);
`transform(document, name) -> document` runs on each document before combining.

### Sources

Accepted by `Loader.load`/`load_all`, possibly nested in lists or tuples:

- a `str` or `os.PathLike` — a **local** path (`pathapi.LocalPath`); glob patterns
  expand (see "Globs"). A `str` written `<scheme>://...` is looked up as a source
  scheme first (`cmd://`, `exec://`, `sh://`, plus registered ones); an unregistered
  scheme is just a local file name — there is no URI parsing.
- a pathapi `Path` (a `MemPath`, an `HTTPPath`, ...) — read as given.
- an open file object — read once; the format comes from `format=` or its `name`.
  When `name` is a real file it is checked against confinement and is the origin of
  relative includes.
- a mapping — Python data (wrapped in `sources.Data`, deep-copied).
- a `yaconfiglib.sources.Source`.
- `None` and `""` — skipped, so an optional layer can be passed unconditionally.
- `bytes` — `TypeError` (content belongs in `loads` or `sources.Text`).
- A file with a script suffix (`.sh`, `.bat`, `.cmd`, `.ps1`) is a **command** (see
  `Command.script`).

### Globs

Expansion is pathapi's `glob(..., native=False)`, so `**` means the same on every
Python. A source is a pattern only by its **own** components (wildcards in `base_dir`
or in a `\\?\` anchor are literal); a pattern naming an existing path loads
literally; directory matches are dropped; matches are **sorted by path component**,
so layering is deterministic. Duplicates load once by a lexical key (absolute,
normalized, case-folded on Windows — never `resolve()`); a file named explicitly
anywhere in the call beats a glob match for it and keeps its own position. A
directory that cannot be listed is offered to `on_error` (`phase="glob"`).

### Includes

`!include`/`!load` in YAML (other formats may support includes through
`LoadContext.include`):

```yaml
db: !include db.yaml                       # one source
all: !include [base.yaml, local.yaml]      # several, merged in order
opt: !include {pathname: x.yaml, format: json, merge: deep, default: {}}
```

A relative path resolves against the **including file's** directory, at every depth
(in-memory content: `base_dir`). Mapping keys allowed: `pathname`, `encoding`,
`default`, `flatten`, `merge` (a strategy **name**), `merge_options`, `recursive`,
`format`; anything else is dropped with a WARNING. A path that is not non-empty text,
or an option of the wrong type (`flatten`/`recursive` take booleans, `encoding`,
`format` and `merge` text, `merge_options` a mapping), is a `ConfigError`. Option
values are what YAML read, even under `into=`. An include's encoding is inherited
by its own includes. Include cycles raise `ConfigValueError("include cycle: a -> b ->
a")`, detected by the **canonical** path, so `../conf/self.yaml` cannot dodge it.
Each included document is its own YAML anchor scope. Includes are not interpolated on
their own: the merged top-level document is.

## Policy (`yaconfiglib.Policy`)

`Policy(allow_commands=False, allow_templates=False, allow_includes=True,
sandbox=True, strict=False, confine_to=None, max_includes=1000, max_depth=64,
max_bytes=64*1024*1024, command_timeout=60.0)` — frozen dataclass. **The default is
the untrusted posture.** `Policy.trusted(**changes)` = commands and templates on,
sandbox off. `Policy.untrusted(**changes)`, `.replace(**changes)`.

A document can never change the policy; includes and command output run under the
policy of the load that reached them. Refusals are `PolicyError` subclasses (also
`PermissionError`), raised before anything is read or run:

- `allow_commands` — command sources and script files; and template engines that run
  a program. Else `CommandsDisabledError`.
- `allow_templates` — template files; else `TemplatesDisabledError`.
- `allow_includes` — `!include`; else `PolicyError`.
- `sandbox` — interpolation and template files run in tplx's sandbox; an engine
  tplx cannot sandbox (mako, Cheetah, ERB, EPP) is refused (`TemplatesDisabledError`).
- `strict` — an undefined template name raises; a reference cycle in interpolation
  raises.
- `confine_to` — every **local** file read (top-level sources, includes, glob
  matches, a stream's file) must be inside one of the roots, else `ConfinementError`
  before the file is opened. Forms: a sequence of absolute paths, one
  `os.pathsep`-separated string, a single path, `True` (= the loader's `base_dir`, or
  the working directory), `False`/`None` (off). Each root must be absolute
  (`ConfigTypeError` otherwise; empty entries are dropped, so `[""]` is an empty
  allowlist that refuses everything). With `None`, `YACONFIGLIB_CONFINE_TO` is read by
  the same rules (unset = off, empty = refuse everything); an explicit value ignores
  it. The comparison is `commonpath` over `normcase(abspath(...))` with `\\?\`
  stripped; symlinks are not resolved. A non-local pathapi `Path` is refused while
  confinement is on; a `MemPath` is exempt. A glob pattern's literal base is checked
  before expansion and each match before it is read, and a refused match is reported
  by its **pattern**, not the file found.
- `max_includes` (each `!include` directive counts once, however many files it
  names), `max_depth`, `max_bytes` — the load's budget; exceeding one is
  `BudgetExceededError`. Files, io streams, `Chunks` and async sources are read in
  64 KiB chunks, so `max_bytes` stops a read one chunk past the budget; a reader that
  is not an `io` stream is called once with `read()`.
- `command_timeout` — seconds before a command and its children are killed.

## Formats (`yaconfiglib.formats`)

- **`Format`** — base class. Class attributes `name` (lower-case), `aliases`,
  `suffixes` (lower-case, with the dot), `priority`, `load_options`,
  `dump_options`, `binary`. Methods: `loads(data, /, *, context=None, **options)`
  (required), `loads_all(...)` (optional), `dumps(obj, /, **options) -> str`
  (optional). Never touches files. Raise `ParseError` for a malformed document,
  translated and raised **outside** the `except` block so neither `__cause__` nor
  `__context__` keeps the parser's error, and with no document text. An optional `claims_name(name) -> bool`
  static method claims names a suffix cannot describe. A format's third-party
  dependency is imported inside its methods, so the class always imports and stays
  listed; a missing one raises `MissingDependencyError` naming the extra.
- **`capabilities(fmt) -> frozenset`** — `{"load"}` plus `"load_all"`/`"dump"` when
  overridden.
- **`register_format(cls=None, *, replace=False)`** — bare or as a decorator.
  `ValueError` when another class holds one of its names, or one of its suffixes at the
  same priority, unless `replace=True`, which moves only what collides: a format
  that loses an alias keeps its other names, and is replaced only when the new class
  takes its name. Lookups never lock and see one consistent registry while another
  thread registers. **`unregister_format(name) -> bool`**.
- **`names()`**, **`get(name, *, allowed=None)`** (aliases, case-insensitive;
  `UnsupportedFormatError` listing the names), **`for_filename(name, *, allowed=None)`**
  / **`find_for_filename(...)`** (longest suffix wins, case-insensitive, then
  `claims_name`; linear in the name), **`document_formats()`**.
- Entry-point group **`yaconfiglib.formats`**: `name = "pkg.module:FormatClass"`.
  Read lazily; an entry point cannot take a built-in name; a broken one is logged
  and skipped. Its formats are not reachable from documents unless named in
  `Loader(formats=...)` (names or `Format` classes). A format registered in code
  after a `Loader` was built is reachable from its documents. An entry point may
  claim a built-in suffix at a higher `priority`: it then reads the caller's own
  files of that name (`load("app.json")`), while a document's include of the same
  name still gets the highest-priority format documents may select.

Built-in formats and their dialects:

- **`json`** (`.json`) — stdlib. Duplicate keys: last wins. `NaN`/`Infinity` refused
  both ways. Load options: `object_hook`, `object_pairs_hook`, `parse_float`,
  `parse_int`. Dump options: `indent` (2), `sort_keys`, `ensure_ascii` (False),
  `separators`, `default`.
- **`yaml`** (`.yaml`, `.yml`; alias `yml`; extra `yaml`) — PyYAML's safe loader,
  YAML 1.1 (`yes`/`no`/`on`/`off` are booleans, `0755` is octal, `1.10` is `1.1`).
  **Duplicate keys raise** (a `<<:` merge key may still be overridden). Tags:
  `!include`, `!load`, `!delete`. Tags live on yaconfiglib's own `SafeLoader`
  subclass, so a plain `yaml.safe_load` never resolves them. `loads_all` reads `---`
  streams. `dumps` is `SafeDumper`-based: mappings, sequences, scalars, dates, sets;
  `dict`/`list` subclasses as plain ones, `tuple` as a list, an `Enum` member as its
  value; anything else raises `ConfigTypeError` before writing. Dump options:
  `indent`, `width`, `default_flow_style` (False), `sort_keys` (False),
  `allow_unicode` (True), `explicit_start`, `explicit_end`.
- **`toml`** (`.toml`; extra `toml`) — `tomllib` (3.11+) or `tomli`; never the `toml`
  package (TOML 0.5). TOML 1.0 is the portable dialect. No null. `dumps` needs
  `tomli-w` (extra `toml`); the document must be a table.
- **`ini`** (`.ini`, `.cfg`; alias `cfg`) — `configparser`. `{section: {option:
  str}}`; option names lower-cased, sections kept; `[DEFAULT]` keys are inherited,
  not returned (a DEFAULT-only file warns). Load options: `interpolation` (`"basic"`
  default, `"extended"`, `"none"`/`None`, or a `configparser.Interpolation`),
  `default_section`. `dumps` accepts exactly what `loads` returns (string values,
  lower-case option names) and escapes for its `interpolation` option, the one the
  file will be read with: `"basic"` (default) writes `%` as `%%`, `"extended"` `$` as
  `$$`, `"none"` nothing; a custom `Interpolation` is `ConfigValueError`. It checks the
  text loads back unchanged, so a name holding `=`/`:`, a value with leading or
  trailing blanks, a continuation line starting with `#`/`;`, or a `DEFAULT` section
  raises `ConfigValueError` naming the option. Errors never quote the offending line
  or a value.
- **`dotenv`** (`.env`, `*.env`, and `.env.<stage>` unless the name's last suffix
  belongs to another format) — `KEY=value`, `export KEY=value`; spaces around `=`
  dropped; keys keep their case (load option `lowercase=True` to fold); double quotes
  decode `\n \r \t \" \\` and may span lines, single quotes are raw; an unquoted value
  ends at a `#` preceded by whitespace; only `\n` ends an entry (a form feed or U+2028
  in a value is data). A non-assignment line warns (naming the line number, never its
  text) or raises with `strict=True`; an unterminated quote always raises. `dumps`
  writes flat string values, double-quoted and escaped.

## Sources (`yaconfiglib.sources`)

- **`Source`** — base class: `read(context) -> Payload`, `name` property,
  `executes: ClassVar[bool]` (an executing source is refused unless
  `allow_commands`).
- **`Payload(data, name, identity, format=None, origin=None, parsed=False,
  trusted_format=False, encoding=None)`** — `encoding` is the codec of `data` and the
  default for its includes — `identity` is the cycle/dedup key; `parsed` means `data`
  is already data; a `format` a source read from data (not `trusted_format`) is
  limited to the document formats.
- **`Text(data, *, format, name="")`** — in-memory content.
- **`Chunks(chunks, *, format=None, name="")`** — content arriving in pieces: any
  iterable of `str` or `bytes` (not mixed), joined as read and stopped past
  `max_bytes`. Format: `format`, else `name`'s suffix. An iterator loads once.
- **`Data(data, name="")`** — Python data, deep-copied on read (any mapping,
  read-only ones included, becomes a `dict`).
- **`Env(prefix="", *, nested=None, lowercase=False, environ=None)`** — environment
  variables starting with `prefix`, prefix removed; `nested="__"` splits
  `DB__PORT` into `{"DB": {"PORT": ...}}` and raises `ConfigValueError` naming both
  variables when one is a value and another nests under it. Values stay strings.
  Prefix matching is case-insensitive on Windows.
- **`Command(command, *, format=None, timeout=<policy>, encoding=None)`** — runs
  `cmd://...`, `exec://...`, `sh://...` (or `cmd+json://...`) or a bare command line
  through the shell. **`Command.script(path, ...)`** runs a script file without a
  shell: `.bat`/`.cmd` via `%COMSPEC% /d /v:off /s /c` (Windows only; a `%` in the
  path is refused), `.ps1` via `pwsh`/`powershell -NoProfile -NonInteractive -File`,
  `.sh` via `sh` on Windows and directly on POSIX when executable with `#!` (else
  `/bin/sh`); the path is made absolute first. Output format, in order: `format=`,
  the `+fmt` of the scheme, a `#!<format>` first output line (dropped), a script's
  inner suffix (`secrets.json.sh`), then the loader's or include's `format=`; none
  raises `ConfigValueError` — output is never sniffed. A command's output format,
  however given, is limited to the formats documents may select (the output is
  data). Includes inside the output are read with the command's `encoding`. Stdin is closed; the timeout kills the process tree
  (`CommandTimeoutError`); stdout is decoded strictly (`ParseError` naming the byte
  offset); a non-zero exit is `CommandError` whose message carries the stderr tail,
  never stdout. Trailing whitespace and leading blank lines are trimmed, indentation
  kept.
- **`register_scheme(scheme, factory, *, replace=False)`**,
  **`unregister_scheme(scheme)`**, **`schemes()`**, **`from_text(text, *,
  allow_plugins=True) -> Optional[Source]`**, `SCHEME_REGEX`. Entry-point group
  **`yaconfiglib.sources`**: `scheme = "pkg.module:factory"`, where
  `factory(text) -> Source`; not reachable from documents.

## Load context (`yaconfiglib.LoadContext`)

Frozen; what a format or source sees. `policy`, `encoding`, `origin` (the path the
current document came from, or `None`), `name`, `chain`/`trail` (identities/names
of the documents being loaded). **`include(source, *more, **options)`** loads through
the active loader — `ConfigValueError` without one (a direct `Format.loads` call).
**`derive(**changes)`**.

## Merging (`yaconfiglib`)

- **`DELETE`** — set a key to it in an overriding document to remove the key
  (YAML: `key: !delete`); markers left in a result are pruned.
- Built-in strategies (by name): `simple` (top-level keys update; everything else
  replaces), `substitute` (mappings recurse; lists replace), `deep` (mappings
  recurse; lists extend with items not already present — the same **type** and value,
  so `True` is not a duplicate of `1`; unhashable items compare by structure, linear
  in distinct nodes; `mergelists=True` merges mappings at the same index that share a
  key; mappings in a list are never de-duplicated).
- Rules for all: a leaf (not a mapping or list) replaces; a shape change replaces;
  **`None` means "no opinion"** (the earlier value is kept; a new key gets `None`); a
  mapping overridden by a non-empty list of mappings folds them in (`substitute`,
  `deep`). **Copy-on-write**: inputs are never modified; the result may share
  unchanged parts with them. `memo` is internal (aliasing and cycles); a custom
  strategy must thread it into nested calls.
- **`register_merge(name, strategy, *, replace=False)`**, **`merge_strategies()`**,
  **`Merge`** (a `typing.Protocol` for the callable).

## Typed results (`yaconfiglib.hydrate`, `yaconfiglib.typed_merge`)

- **`hydrate(cls, data, *, extra="ignore") -> cls`** — what `load(..., into=cls)`
  uses. A dataclass, a namespace, a mapping type (a `TypedDict` included), a Pydantic
  model (validated by Pydantic, detected through `sys.modules` — never imported) or
  any other class built from a mapping: its fields go to the constructor as
  keywords, coerced through the class's annotations or else its constructor's.
  `extra="forbid"` raises `ConfigTypeError` naming unknown keys; `"ignore"` drops
  them (a `**kwargs` constructor receives every key). `None` data is
  `ConfigTypeError`; a constructor refusing the data (a missing field) is
  `ConfigTypeError` too.
- **The type steers the parse.** While loading `into=` a type, YAML keeps each plain
  (unquoted) scalar it read as a bool, number or date **beside its text**, and the
  target decides: a `str` field gets the text as written (`NO` stays `"NO"`, `1.10`
  stays `"1.10"`, `0755` stays `"0755"`) and so does a `Decimal` (`0.10` keeps its
  digits); any other type is built from the read value first (`level: 1` into an
  `IntEnum`, `1.0` into an `int`, `0x10` into a `float`) and from the text only when
  that fails. Anything without type information (`Any`, `object`, an unannotated
  `dict`/`list`, extra keys) gets the read value. Keys are never affected. A union
  picks the member holding the read value, then the one holding the text
  (`Union[int, str]` keeps `yes` as `"yes"`). A Pydantic model gets the text for a
  field annotated `str` and the read value everywhere else. Without `into=`, values
  are exactly what the format read.
- **`dehydrate(obj) -> plain data`** — the inverse, which `dump`/`dumps` apply
  before writing: a type's `_dump_()` result, a dataclass/namespace/`NamedTuple` as a
  mapping, an `Enum` member as its value, a path, `Decimal` or `UUID` as its text,
  mappings and
  lists/tuples walked (aliases and cycles kept); anything else is left for the format
  to accept or refuse. So `dump(load("a.yaml", into=App), "a.yaml")` round-trips.
- **`typed_merge(cls, *objects, init=True, extra="ignore")`** — merge layers into one
  `cls`, field by field through each annotation. `None` layers are skipped at every
  level; no or all-`None` layers give `None`. A layer of another shape replaces
  everything before it; when the **last** layer cannot be built into `cls`,
  `ConfigTypeError`. Sequences take the last layer, each item coerced; `Tuple[A, B]`
  coerces by position (`ConfigTypeError` on a length mismatch); a `NamedTuple` is
  rebuilt with its constructor from a sequence or a mapping of its fields (how
  `dehydrate` writes it); `Set`/`FrozenSet` coerce each item; `Dict[K, V]` coerces
  keys while collecting (so `"80"` and `80` group as one); a `TypedDict` applies
  `extra=` and raises `ConfigTypeError` for a missing required key. Coercion never
  loses data: `int` takes whole floats and integer strings (not `1.9`, not a bool);
  no number takes a bool; `float` refuses an int it cannot hold exactly; `Decimal`
  takes a float's shortest text; `bool` reads `true/yes/on/1` and `false/no/off/0`
  (else `ConfigValueError`); `date`/`datetime`/`time` read ISO strings; `Enum` takes a
  value, then a member name; `Literal[...]` takes one of its values (else
  `ConfigValueError`); a `NewType` coerces as its base type. Any other constructor's
  failure is `ConfigValueError`/`ConfigTypeError`. A union keeps a value it already
  accepts, else coerces through its first non-`None` member; `Optional`,
  `X | None` and `Annotated` are unwrapped; `Any` keeps the last value. A node
  reached twice (a YAML anchor) is built once and stays one object, so an aliased
  document costs its distinct nodes; data that contains itself, or nests past the
  recursion limit, is `ConfigValueError`. Annotations resolve per entry when
  `get_type_hints` fails (3.14+: through `annotationlib`). Errors carry
  `config_key` (`at db.hosts[0].port`) and `config_model` (the outermost model).
- Hooks, looked up on the unwrapped type (so `Optional[T]` finds them); without
  one the basic coercions above apply:
  - classmethod **`_load_(cls, value)`** builds the type from one value: a scalar
    arrives as the text written in the document when that is known (else the parsed
    scalar), a mapping or list as plain data, and several mapping layers are
    deep-merged first.
  - **`_dump_(self)`** returns the instance as plain data, for `dehydrate`/`dump`.
  - classmethod **`__merge__(cls, *objects, init=True)`** takes over merging layers
    entirely.
  - **`_parse_<field>(value)`** on a layer object (not a mapping) or on a
    `TypedNamespace` target coerces that field.
- A dataclass instance used as a layer contributes every field, defaults included.
- **`OpaqueMerge`** (mixin) / **`opaque(cls)`** (decorator) — the last layer wins,
  unmerged. **`TypedNamespace`** — an `argparse.Namespace` applying `_parse_<field>`
  at construction; merged into without calling `__init__`, so each value is parsed
  once.

## Dot access (`yaconfiglib.DotAccessibleDict`)

From `wrap=True`. Nested mappings are converted once, at construction (copied, never
mutated; aliasing and cycles kept): `cfg["db"] is cfg.db`. `get(key, default=None,
dig=True)`: the exact key wins; then a dotted string walks mappings (an ASCII-digit
segment indexes a `list`/`tuple`; on a mapping it is the string key) and a tuple path
uses each segment as given (ints index sequences, `bool` excluded); a non-traversable
value with segments left gives `default`; the final value is returned as stored,
`None` included. Reads never write. Attribute writes and deletes of a name the class
defines (`items`, `get`, ...) raise `AttributeError`; use `cfg[name]`. `copy()`,
`|`, `|=` keep the class; values assigned later are stored as given.

## Interpolation (`Loader(interpolate=...)`)

Value interpolation and template files are two features. **Interpolation** runs at
value time: after every source is loaded and merged, the strings of the result that
hold the engine's syntax are rendered, once, with the document's string keys as the
scope (`env`, a read-only `os.environ` snapshot, too with `inject_env=True`).
`Loader.load` interpolates the merged document, `Loader.load_all` each document;
includes are not interpolated on their own, and the top-level `yaconfiglib.load()`
never interpolates. **Template files** (next section) are chosen by file suffix and
rendered whole before parsing.

`interpolate=` picks the renderer, and is checked when the `Loader` is built (or the
per-call override is applied):

- `False`/`None` (default): off. `True`: Jinja.
- a tplx engine's name or class (`"liquid"`, `"envsubst"`, `tplx.JinjaTemplate`):
  that engine, sandboxed and strict as the policy says. Under the sandbox an engine
  tplx cannot sandbox raises `TemplatesDisabledError`; an engine that runs a program
  (ERB, EPP) needs `allow_commands` (`CommandsDisabledError`); a missing engine
  extra is `MissingDependencyError`; an unknown name `ValueError`.
- `"vars"`: `VarsInterpolator`, no extra needed.
- an object with `render` and `references` (an `Interpolator`): used as it is.
- anything else: `TypeError`.

Which strings render is the engine's syntax: `{{`/`{%`/`{#` for Jinja, `{{` for
Mustache, Handlebars and Go, `$` for envsubst and shell, `${` for `"vars"`, `<%` for
ERB/EPP; every string for an engine not in that table. A custom interpolator decides
with an optional `is_template(text) -> bool`, else Jinja's markers. Top-level keys render after the
keys they reference, so chains resolve in any order; a rendered value is never
rendered again. A bare expression (`{{ expr }}`, `${name}`; only a trailing
newline around it) keeps its type. Copy-on-write: the input is never modified, containers keep their types, and a
document with no template marker is returned untouched. A templated key rendering to
a key that already exists raises `ConfigValueError` naming both templates (a skip
under `on_error` keeps the entry under its template text). A value may refer into
its own top-level key (`db.url: "{{ db.host }}"`); it sees that key's values as
loaded, so a sibling that is itself a template is not yet rendered. Every failure is a
**`TemplateError`** (the engine's exception as `__cause__`, `config_key` set); a
skip under `on_error` keeps that value's template text. Messages and DEBUG logs name
the template, never the rendered value.

- **`Interpolator`** — protocol: `references(text) -> frozenset[str]`,
  `render(text, scope) -> Any`, optionally `is_template(text) -> bool`.
- **`VarsInterpolator(*, strict=False)`** — `${name}` and `${a.b.0}` references:
  each step a mapping key or a list index, no expressions, no attribute access, so
  no sandbox is needed. A value that is exactly one reference keeps its type; inside
  text a value is written with `str` (`None` as nothing). `$${` is a literal `${`.
  Undefined: empty (`None` alone), or `TemplateError` when strict. A malformed
  reference (`${a..b}`) is `TemplateError`.
- **`TemplateInterpolator(engine="jinja", *, sandbox=True, strict=False)`** — a tplx
  engine by name or class; `.engine_class` is the class it resolved to, and
  `is_template(text)` its syntax test. Sandboxed, a value cannot
  reach Python internals and sees plain data: a mapping or list as itself, any other
  non-plain value (a date) as its text; a container an expression builds comes back
  as plain `dict`/`list`/`tuple`. A sandboxed render is bounded by tplx's defaults
  (1,000,000 characters of output, 100,000 loop iterations, depth 100); going over is
  a `TemplateError`. A value template can include no file. Compiled templates are
  cached per instance. Needs the `templates` extra (tplx 0.1.5+).

## Template files

A source named `<name>.<format>.<engine suffix>` (`app.yaml.j2`, `app.toml.liquid`)
is rendered by the tplx engine that claims the suffix, then parsed as `<format>`
through the loader's own format resolution. The context holds `pathname` (a
`PurePosixPath`; its text under the sandbox, which shows a template only plain data),
`filename` (its last component) and, with `inject_env`, `env`. A loader's `format=` names the format
the template renders to; the template still renders. Refusals happen before the file
is read: refused unless `allow_templates`; under `sandbox` only engines tplx can
sandbox (Jinja, Liquid, Mustache, Handlebars, Go, shell, envsubst) render, in tplx's
sandbox — mako, Cheetah, ERB and EPP are refused; an engine that runs a program
(`tplx.SubprocessTemplate`: ERB, EPP) also needs `allow_commands`. Includes inside a
template search the confinement roots; without them a sandboxed template includes
nothing and a trusted one looks next to itself. An engine whose tplx extra is missing
raises `MissingDependencyError`. Errors are `TemplateError`; a parse error in the
rendered text carries a `render` frame.

## Exceptions (`yaconfiglib.exceptions`, re-exported at the root)

- **`ConfigError`** — base. Catch `(ConfigError, OSError)` for every load failure;
  an `OSError` from reading a file is not wrapped.
- **`ParseError(ConfigError, ValueError)`** — `path` (the source, `None` for
  in-memory content), `lineno`, `colno` (1-based or `None`), `msg`; `str()` is
  `path:line:col: msg`. It may name document **structure** — a key, a section, a tag,
  an alias — but never a **value**, a line or a snippet, and it never chains the
  parser's own error (not even as `__context__`).
- **`TemplateError(ConfigError, ValueError)`**,
  **`ConfigValueError(ConfigError, ValueError)`**,
  **`ConfigTypeError(ConfigError, TypeError)`**.
- **`UnsupportedFormatError(ConfigError, ValueError)`** — no format for a name, or a
  format that cannot write; lists the registered names.
- **`MissingDependencyError(UnsupportedFormatError, ImportError)`** — names the extra.
- **`PolicyError(ConfigError, PermissionError)`** and its subclasses
  **`CommandsDisabledError`**, **`TemplatesDisabledError`**, **`ConfinementError`**,
  **`BudgetExceededError`**.
- **`CommandError(ConfigError, subprocess.CalledProcessError)`**,
  **`CommandTimeoutError(ConfigError, subprocess.TimeoutExpired)`** — both pickle;
  stderr tail in the message (last 20 lines, 2000 characters), stdout never.
- **`ErrorFrame(kind, source, line=None)`** — `kind` is `"include"`, `"render"`,
  `"command"` or `"merge"`.
- Context, recorded in place on any error a load re-raises — type and identity
  unchanged: `config_source`, `config_frames` (innermost first), `config_key`,
  `config_model`, rendered as `[in <source>; included from <src>, line <n>; rendered
  from <src>; while merging <src>; at <a.b[0]> (<Model>)]`. A wrong argument raises a
  plain `TypeError`/`ValueError`.

## Testing a format (`yaconfiglib.testing`)

- **`FormatContract`** — subclass it in a pytest module and set the class attributes
  `format` (the `Format` subclass), `document` (a small valid document, text),
  `samples` (values `dumps` accepts; each must survive `loads(dumps(x)) == x`, used
  only when the format writes) and `malformed` (an invalid document holding the
  marker `{secret}`). The inherited tests check the class attributes, a BOM being
  ignored, an unknown option raising `TypeError`, the round trip, that arbitrary
  short text parses or raises `ParseError`, that an unwritable value raises
  `ConfigTypeError`/`ConfigValueError`, and that the malformed document's
  `ParseError` — message, args, attributes, cause chain and DEBUG logs — never holds
  the secret. Needs pytest.
- **`SECRET`** — the text `{secret}` is replaced with.

## Environment variables

- `YACONFIGLIB_CONFINE_TO` — confinement roots, read only when a load's
  `Policy.confine_to` is `None`, by the same rules (an `os.pathsep`-separated list of
  absolute paths). Unset means no confinement; set but empty refuses every local read.
  A relative root raises `ConfigTypeError` when a load runs, never at import.

## Gotchas

- A `str` given to `load` or `Loader.load` is always a location. Content goes to
  `loads` or `sources.Text`.
- A pathapi `Path` source is used as given and never joined to `base_dir`; a
  relative `LocalPath` object therefore resolves against the working directory.
- `None` in an overriding document keeps the earlier value. Use `DELETE`
  (`!delete`) to remove a key.
- YAML is 1.1: quote `yes`, `no`, `on`, `off`, `0755` and `1.10` to keep them strings.
- Includes are on by default but not confined. For a document you did not write,
  set `Policy(confine_to=...)` and keep the default sandbox.
- `Loader.load` merges every document of a multi-document YAML file in order;
  `yaconfiglib.load` refuses one with `ParseError` (use `load_all`).
