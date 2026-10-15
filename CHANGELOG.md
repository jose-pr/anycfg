# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.14.0] - 2026-10-16

This release replaces the public API. Every entry point, keyword and backend class
changed; the migration guide in the documentation maps each old name to its
replacement.

### Changed
- **`load`/`dump` choose the format from the file extension; `loads`/`dumps` need
  `format=`.** `dump(obj, "a.json")` writes JSON (it always wrote YAML), so `dump`
  then `load` round-trips. A name no format claims raises `UnsupportedFormatError`
  before the file is opened, where it used to fall back to YAML for streams and
  `loads`. What to do: pass `format="yaml"` to `loads`/`dumps`, and name files by
  their format.
- **A `str` is always a location.** `loads()` takes content; the `"#!name\n..."`
  in-memory form is gone — use `yaconfiglib.sources.Text(data, format=...)` in a
  layered load.
- **`ConfigLoader` is now `Loader`**, keyword-only, and no longer a backend.
  `.load(*sources)` merges; `.load_all(*sources)` yields every document of every
  source, including each document of a `---` YAML stream. Settings are the same
  set for the constructor and per call, and an unknown keyword raises `TypeError`
  instead of being forwarded to a backend and ignored. Format options go in
  `format_options={"json": {...}}`, or as keywords to the one-document functions,
  where they are checked against what the format accepts.
- **The trust policy is a `Policy` object, untrusted by default.** Commands and
  template files are refused unless `Policy(allow_commands=True)` /
  `Policy(allow_templates=True)` (or `Policy.trusted()`); interpolation is
  sandboxed by default. `allow_commands=`, `sandbox=`, `strict=` and `confine_to=`
  move into `Policy`. What to do: configuration you wrote yourself loads with
  `Loader(policy=Policy.trusted())`.
- **Results are plain `dict`/`list`.** Pass `wrap=True` for `DotAccessibleDict`.
- **Merge strategies are names** (`"simple"`, `"substitute"`, `"deep"`, or one added
  with `register_merge`), or any callable. `MergeMethod`, `ConfigLoaderMergeMethod`
  and integer values are gone; `Last`/`List`/`Hash` became `collect="last"`,
  `"list"`, `"hash"` with `key=`.
- **`None` in an overriding document means "no opinion"** in every strategy;
  `simple` used to replace with it. Remove a key with `DELETE` (`!delete` in YAML).
- **`load_as` is `load(..., into=Model)`** and `hydrate(Model, data)`, on the same
  engine as `typed_merge`: `"false"` becomes `False`, `"8080"` becomes `8080`,
  `Model | None` is hydrated on Python 3.10-3.13, and unknown keys are dropped
  (`extra="forbid"` raises). `int` coercion no longer truncates (`1.9` raises), and
  ISO strings become `date`/`datetime`/`time`.
- **Errors.** Every parse failure is `ParseError` (`path`, `lineno`, `colno`),
  translated from the parser; it may name a key, section, tag or alias but never a
  value or a line, and never chains the parser's own error; `except yaml.YAMLError`
  and the like no longer match. `load_error_types()` is gone: catch
  `(ConfigError, OSError)`. Policy refusals are `PolicyError` subclasses (also
  `PermissionError`); `CommandsDisabledError` was a `ValueError`.
  `UnsupportedFormatError` is a `ValueError`, no longer a `NotImplementedError`;
  `UnknownLoaderError` is merged into it. Template and interpolation failures are
  `TemplateError`. `yaconfiglib.errors` is now `yaconfiglib.exceptions`.
- **Command sources need a format** (`cmd+json://`, `format=`, a `#!json` first
  line, or a script's inner suffix like `secrets.json.sh`); output is never sniffed.
  The scheme must be written `cmd://`, not `cmd:`. A command now has a 60-second
  default timeout (`Policy.command_timeout`). Leading indentation of the output is
  kept.
- **Environment and dotenv keys keep their case** (`lowercase=True` folds them), and
  environment values stay strings; `Env(..., coerce=True)` is gone — types come from
  `into=`.
- **Globs use `native=False`**, so `**` matches the same files on every Python; a
  bare `**` pattern loaded nothing on 3.9-3.12.
- **YAML duplicate keys raise `ParseError`.** `dumps` writes plain data only:
  mapping and sequence subclasses as plain ones, `Enum` members as their values,
  and anything else raises `ConfigTypeError` instead of writing a
  `!!python/object` tag that `loads` refused.
- **Templates go through tplx**, for files and for values. A template file renders
  with any engine tplx knows (`app.yaml.j2`, `app.yaml.liquid`, `app.yaml.mako`) when
  the policy allows templates; under the default sandbox only the engines tplx can
  sandbox render (Jinja, Liquid, Mustache, Handlebars, Go, shell, envsubst), and a
  sandboxed template sees plain data (a path or a date as its text) and includes
  files only under the confinement roots. Value interpolation uses
  `TemplateInterpolator`. `interpolate=` names the engine — `True` (Jinja), a tplx
  engine's name or class, `"vars"` (`${a.b}` references, `VarsInterpolator`, no extra
  needed) or an interpolator object — and only strings holding that engine's syntax
  render. `interpolator=` is gone; the choice is checked when the `Loader` is built.
  An `Interpolator` is now just `render(text, scope)`, given every string: tplx's
  `eval` is the only syntax check, and a top-level key renders the first time a
  template reads it.
  `JinjaInterpolator`, `utils.jinja2` and its caches are gone. `transform=` and
  `key=` take callables; their Jinja-string forms are gone.
- **Extras**: `jinja2` and `transform` became `templates` (`tplx[jinja]` 0.2 or
  later); `toml` adds `tomli-w`; `all` installs every extra. Another template engine
  needs its own tplx extra (`tplx[liquid]`).

### Added
- `yaconfiglib.formats`: the `Format` base class and a registry
  (`register_format`, `get`, `for_filename`, `names`), with the
  `yaconfiglib.formats` entry-point group. A plugin's formats are reachable from
  documents only when named in `Loader(formats=...)`.
- `yaconfiglib.sources`: `Source`, `Text`, `Data`, `Env`, `Command`, and the
  `yaconfiglib.sources` entry-point group for schemes.
- `yaconfiglib.testing.FormatContract`, the suite every format passes.
- `load_all`/`loads_all` (both with `into=`), writers for TOML (`tomli-w`), INI and
  dotenv (the INI writer escapes for the interpolation the file will be read with and
  raises `ConfigValueError` for anything that would not load back as written), `DELETE`,
  `register_merge`, `hydrate`, `Interpolator`, `TemplateInterpolator`, `LoadEvent`, `BudgetExceededError`,
  `MissingDependencyError`, `TemplateError`.
- `Policy.max_includes`, `max_depth`, `max_bytes`: a load's budget.
- `Policy` is a class to subclass: `check_path`, `check_command` and
  `check_template` narrow what its fields allow. `yaconfiglib.policies` has
  mixins that stack (`LocalPathsOnly`, `AllowedSuffixes`, `AllowedCommands`,
  `AllowedEngines`), with `Mixin.using(**settings)` and `compose()`; `policy=` also
  takes a list of mixin classes, applied to the default policy in order.
- YAML `dialect="1.2"` (the YAML 1.2 core schema); `Env(coerce=)` and the dotenv
  `coerce=` option read values by the same rules or a callable.
- `INHERIT` (`!inherit`) and the merge option `nulls="replace"`, which makes `None`
  set a key while `INHERIT` keeps the earlier value.
- Async loading: `aload`, `aload_all`, `adump`, `Loader.aload` and
  `Loader.aload_all` read async streams and async iterables of chunks on the event
  loop (stopping past `max_bytes`) and run the rest of the load in a worker thread.
  `sources.Chunks` loads a sync iterable of chunks.
- **The target type steers the parse.** With `into=`, YAML keeps each plain scalar
  it read as a bool, number or date beside its text, and the type decides: a `str`
  or `Decimal` field gets `NO`, `1.10` or `0755` as written, other fields the read
  value, or a value built from the text when the read value does not convert. Only
  the typed build sees the text: merging, includes, `transform` and duplicate keys
  behave as without `into=`. `TypedDict` targets (with `extra=` and required keys)
  and ordinary classes built from a mapping are hydrated.
- `typed_merge`/`hydrate` coerce `Literal[...]`, `NewType`, `Set`/`FrozenSet` and a
  `NamedTuple` written as a mapping. A bool no longer passes as a number, `Decimal`
  takes a float's shortest text, and a failing constructor raises
  `ConfigValueError`/`ConfigTypeError` instead of its own exception.
- `loads()`/`loads_all()` without `format=` read a document that names its own
  format — a `#!<format>` first line (removed before parsing) or a format's marker
  (YAML's `%YAML`) — through the new `Format.claims_document` and
  `formats.declared`; one that names none raises `UnsupportedFormatError`.
  `sources.Text` takes the same route without `format=`.
- `load()` reads one location: a glob matching no file or several raises
  `ConfigValueError`, and a list of sources is `TypeError`.
- Type hooks: a classmethod `_load_(cls, value)` builds a type from configuration,
  and `_dump_(self)` turns it back into plain data. `dehydrate()` applies them, and
  `dump`/`dumps` call it, so a dataclass, enum, path or `Decimal` writes as plain
  data and `dump(load(path, into=Model), path)` round-trips.

### Removed
- `ConfigLoader`, `ConfigBackend` and every backend class, `loader=`,
  `loader_factory=`, `path_factory=`, `DEFAULT_LOADER`, `load_as`, `MergeMethod`,
  `ConfigLoaderMergeMethod`, `parse_sources`, `has_glob_pattern`, the
  `yaconfiglib.utils` package, `load_error_types`, and backend instances used as
  PyYAML tag constructors.
- Scheme strings no longer reach a URI path layer: a `str` is a local path. Pass a
  pathapi `Path` object (an `HTTPPath`, an `SFTPPath`) to read a remote source.

### Fixed
- `import yaconfiglib` no longer imports PyYAML, Jinja2 or tomllib, and no longer
  fails when `YACONFIGLIB_CONFINE_TO` holds a relative path (the load raises
  instead).
- A pathapi `Path` source is no longer joined to `base_dir`, which broke every
  remote path object.
- Interpolation no longer rewrites the caller's data in place, keeps `tuple` and
  `set` values, renders documents with a non-string top-level key, and raises when
  a templated key collides with an existing one.
- Dotenv drops the spaces after `=`, so `K= "v"` is the quoted value `v`.
- `typed_merge` replaces on a shape change instead of failing with a bare
  `vars()` `TypeError`.

### Security
- An include cycle spelled through `../` is detected; it recursed until
  `RecursionError`, or exponentially with errors ignored.
- `deep` list merging compares unhashable items by structure, once per node: a
  document of aliased lists could make it exponential.
- `typed_merge`/`hydrate` (and `into=`) build a node reached twice once, so an
  aliased document costs its distinct nodes instead of every path through it; data
  that contains itself raises `ConfigValueError` instead of `RecursionError`.
- `max_bytes` stops a file or stream read one 64 KiB chunk past the budget
  instead of after the whole source is in memory.
- An `encoding` must name a text codec; a document's include could name
  `zlib_codec` and inflate past `max_bytes`.
- A glob pattern is checked against `confine_to` before the loader asks whether a
  file of that name exists.
- Format selection is a linear suffix lookup; the dotenv name pattern backtracked
  quadratically on a long, document-controlled name.
- A command's `#!` line can select only a document format; `#!env` returned the
  process environment.
- Any imported backend subclass was reachable from a document under the hardened
  settings; registration is now explicit, and plugin formats need the caller's
  opt-in.


## [0.13.1] - 2026-10-15

### Changed
- **The path layer is `pathapi`, not `pathlib-next`.** `pathlib-next` was renamed and
  split: its core is now the `pathapi` distribution, and `pathlib-next` 0.10.1 is only a
  deprecation shim over it. The dependency is `pathapi>=0.1.0,<0.2`, and yaconfiglib no
  longer installs `pathlib-next`. The paths a load accepts and returns (`Path`,
  `LocalPath`, and the `MemPath` that stands for an in-memory document) are now
  `pathapi` classes. Nothing about how sources resolve, glob or merge changed.

  What to do: if your code imports `pathlib_next` for these types, import them from
  `pathapi` instead (`from pathapi import Path, LocalPath`, `from pathapi.mempath import
  MemPath`), and depend on `pathapi` yourself rather than relying on yaconfiglib to
  bring `pathlib_next`. A path object made by `pathlib-next` 0.9 is not a `pathapi`
  `Path`, so build such paths with `pathapi` (or `pathlib-next` 0.10.1, whose names are
  the `pathapi` classes). A URI source (`sftp://`, `http://`) needs the distribution
  that provides it (`pathapi-uri`, `sftppath`).

## [0.13.0] - 2026-09-20

### Removed
- **The stdlib path fallback.** Every `from pathlib_next import ...` is now unconditional,
  in all eleven modules that had a `try`/`except ImportError` around it, and the
  `HAS_PATHLIB_NEXT` flag, the temp-file materializer it fed and its `atexit` cleanup are
  gone. pathlib-next is a declared dependency, so a missing one now raises `ImportError`
  at import time like any other — where before the package imported and then answered
  *differently*: a `**` source expanded to nothing instead of failing. Measured on the
  current suite with pathlib-next absent: 33 failures and 7 errors, against the 5 on
  record when the fallback was last examined.
- **`ConfigLoader(log_level=...)`.** It accepted a value, warned, and did nothing for one
  release; passing it now raises `TypeError`. Configure the `yaconfiglib` logger through
  the `logging` module.
- **The `envoriment=` alias** on the Jinja2 backend, a historical misspelling kept beside
  `environment=`. Use `environment=`.

Passing `path_factory=pathlib.Path` remains fully supported, and is better than it was:
see Fixed.

### Fixed
- **A glob under `path_factory=pathlib.Path` expands like any other.** Such a source was
  handed to stdlib `glob` as `parent.glob(name)`, which cannot expand `**` — it looks
  inside a directory literally named `**` — so a `recursive=` source expanded to
  **nothing at all**, silently, while `on_error=` and `bound_loops=` were inert. A stdlib
  path is now converted to a `LocalPath` for the walk and every match converted back with
  the caller's own factory, so the factory decides the path *type* a caller receives and
  never which glob runs. Matches are still `pathlib.Path` instances.

## [0.12.0] - 2026-09-17

### Security
- Interpolation DEBUG logs no longer include rendered values, which can be secrets a
  template fetched from the environment. A record now names the key and the template text
  instead.
- `!include`/`!load` are no longer registered on the shared `yaml.SafeLoader`.
  Previously, after any yaconfiglib YAML load, every `yaml.safe_load()` call in the
  same process resolved `!include`, so an untrusted document parsed by unrelated
  code could run `cmd://` commands or read local files. The tags are now registered
  on a private subclass, and only documents loaded through a `ConfigLoader` resolve
  them. Code that relied on `yaml.safe_load` resolving `!include` after a
  yaconfiglib load must load through a `ConfigLoader` instead.
- An untrusted document can no longer weaken the caller's settings through an
  `!include` mapping. Previously a mapping could set `allow_commands: true` or
  `sandbox: false` for the included file, run arbitrary Python through a
  `transform` or `%`-form `key_factory` expression, or call a method on the
  included path through a plain `key_factory` (`key_factory: unlink` deleted the
  file).
- A per-call `load(..., allow_commands=False)` or `load(..., sandbox=True)` now
  also applies to nested `!include` targets. Previously the included files were
  loaded with the instance settings.
- `.j2`/`.jinja2` sources now render in Jinja2's sandbox whenever `sandbox=True` or
  `allow_commands=False` is in effect, including ones reached through `!include`
  (an in-memory `#!name.yaml.j2` document included the same way). Previously
  they always rendered in the non-sandboxed default environment, so a template
  could run arbitrary Python even under both controls. A rendered document that
  is a command source is refused while `allow_commands=False`.

### Added
- `ConfigLoader(confine_to=...)` and `yaconfiglib.ConfinementError`: opt-in confinement of
  file reads to allowed roots. With it set, an `!include` (or a top-level source) that
  resolves outside every root raises `ConfinementError` — a `PermissionError`, and so an
  `OSError` — before the file is opened, which closes the file-disclosure gap the security
  guide documented: an untrusted document could otherwise read any file the process can
  through an absolute path or `..` traversal. It takes a sequence of roots, one
  `os.pathsep`-separated string (so the value can come from an environment variable),
  `True` meaning `base_dir`, or `False`/`None` for off; `YACONFIGLIB_CONFINE_TO` is read
  only when the argument is `None`, and an empty allowlist refuses every local read rather
  than disabling the control. Containment is decided on the **logical** path
  (absolute, `..`-resolved, case-folded where the platform is): symlinks are deliberately
  not resolved, so a link inside a root may point outside it — the boundary is write
  access to a root. Command sources and in-memory `#!` documents are exempt. A path object
  that is not a local filesystem path is refused, being inside no local root — note that a
  `scheme://…` *string* is not one of those with the default `path_factory`, which builds
  local paths: it is checked as the relative filename it becomes. Off by default, so no
  existing load changes — unless `YACONFIGLIB_CONFINE_TO` is set in the environment, which
  confines every loader constructed without an explicit `confine_to=`.
- `bound_loops=` on `ConfigLoader` and `parse_sources`: bound a **Windows junction loop**
  — a junction pointing at one of its own ancestors — which unbounded makes a recursive
  glob walk the loop until the filesystem refuses the path, failing the load with
  `OSError` (or, under `ignore_error`, merging the loop's files many times over).
  **Defaults to `True`** (see Changed); `bound_loops=False` restores pathlib's unbounded
  walk. A POSIX directory *symlink* is never descended by `**` in the first place, so
  nothing changes there.
- `yaconfiglib.CommandError` (also a `subprocess.CalledProcessError`) and
  `yaconfiglib.CommandTimeoutError` (also a `subprocess.TimeoutExpired`), so a command
  failure can be caught either as the stdlib type it always was or as a
  `yaconfiglib.ConfigError`.
- `parse_sources(on_error=...)`: a callback `(error, directory) -> bool` called when glob
  expansion cannot list a directory. Return `True` to skip that directory and keep
  expanding; anything falsy lets the `OSError` propagate. It is asked once per directory,
  and a missing or non-directory parent is not reported at all (it simply matches
  nothing).
- `yaconfiglib.ConfigError` and the subclasses `ConfigValueError`, `ConfigTypeError`,
  `UnsupportedFormatError`, `UnknownLoaderError` and `CommandsDisabledError`. Each is also
  an instance of the builtin exception the same condition raised before, so existing
  `except ValueError` / `except NotImplementedError` clauses keep working; a parser's own
  errors are still raised unwrapped.
- `yaconfiglib.load_error_types()` returns the exception types a load can raise for a
  configuration or I/O reason, for a single `except yaconfiglib.load_error_types():`
  clause. It includes PyYAML's, Jinja2's and TOML's base errors only for parsers already
  imported, so it needs no optional extra.
- `DotAccessibleDict` is importable from `yaconfiglib` (and listed in `__all__`), so the
  type `load()` returns can be named in annotations and `isinstance` checks.
- `del config.key` removes that key, mirroring `config.key = value`.
- `DotAccessibleDict.get()` accepts a **tuple** path — `get(("metadata", "labels",
  "app.kubernetes.io/name"))` — which reaches keys containing dots, integer keys, and
  list indexes without any string parsing.
- `yaconfiglib.utils.source.CommandSource`, the `str` subclass `parse_sources` yields
  for a command URI. It exposes `scheme`, `format` and `command`, and answers `name`,
  `stem` and `as_posix()` with the whole text.
- Python 3.14 is tested in CI and declared in the package classifiers.
- `ini_interpolation` (per call) and `IniConfig(interpolation=...)` choose how `%` is
  handled in an INI file: `"basic"` (the default, unchanged), `"extended"` for
  `${section:key}` references, or `None` to read values verbatim. A logging or alembic
  formatter value such as `%(levelname)-5.5s [%(name)s] %(message)s` could not be loaded
  at all before — it raised `InterpolationSyntaxError` with no way to opt out.
- `DotenvBackend(strict=True)` and the per-call `dotenv_strict=True` make an unparseable
  `.env` line, or a file with no assignment at all, raise `ValueError`.
- `yaconfiglib.load_as(model_cls, *sources, **options)`: the top-level form the README
  and the model guide already showed. It takes several sources, and routes keywords like
  `yaconfiglib.load()`, so `strict`/`base_dir`/`merge` configure the loader.
- `YamlConfig.load(origin=...)`: the document that relative `!include`/`!load` paths
  resolve against. Defaults to the file being parsed; a rendered `.j2` template passes
  its own path.
- `timeout=` for command sources, e.g. `loader.load("cmd://...", timeout=30)`: after
  that many seconds the command and its child processes are killed and
  `subprocess.TimeoutExpired` is raised. There is still no timeout by default.
- `ConfigLoader.load_all()` accepts `sandbox=` and `allow_commands=` per call; they
  also apply to nested `!include` targets.

### Changed
- **A recursive glob now bounds a directory loop by default** (`bound_loops=True`). A
  `**` source that crossed a Windows junction loop used to walk it until the filesystem
  refused the path, so the load failed with `OSError` — or, under `ignore_error`, merged
  the loop's files once per lap. That is now bounded unless you ask for the old walk with
  `bound_loops=False`. The default could not be this until pathlib-next 0.9.9: before it,
  bounding also dropped a directory deliberately reachable under two names, because the
  bound keyed on every directory seen rather than on the current descent path. **The
  floor moves to `pathlib-next>=0.9.9`** for that rule, and a load that relied on a
  looped tree failing now succeeds instead.
- `from yaconfiglib.utils.merge import *` exports only the merge API and the typed-merge
  helpers; it previously also pulled in the module's internals.
- `ConfigBackend` is a regular base class instead of a `typing.Protocol`.
  `issubclass()`/`isinstance()` checks against it now work — they raised `TypeError` — and
  type checkers no longer report `ConfigLoader()`, `EnvVarBackend()`, `PythonBackend()` or
  a custom backend as abstract. Registration was already nominal, so dispatch is
  unchanged; the one behaviour difference is that an object which merely *looks* like a
  backend is no longer an `isinstance` of it — subclass `ConfigBackend` instead.
- `yaconfiglib.utils` no longer re-exports the internal names `T` and `annotations`,
  which a star-import of its `enum` module leaked.
- While sniffing a command's output format, only a parse failure now means "try the next
  format". Anything else — a disabled command, an error from an `!include` inside the
  output, a `TypeError`, a missing file — propagates instead of falling through to
  another format or to the raw output. Fix the reported error, or pass
  `format=`/`cmd+<fmt>://` to choose the format outright.
- With `ConfigLoader(ignore_error=...)`, `load_all()` yields a document with the failing
  value unrendered instead of skipping the document entirely, and an interpolation
  failure is offered to a predicate once per failing value with `phase="interpolate"` and
  `key=` its path. To keep failing on template errors, return False from the predicate
  when `phase == "interpolate"`.
- The `pathlib-next` floor is now `>=0.9.7,<0.10` (was `>=0.9.6`), because glob expansion
  now uses its `glob(on_error=)` hook, added in 0.9.7.
- An `ignore_error` predicate now always receives `phase=`, `path=` and `loader=` by
  keyword, in every phase. `phase` is `"load"`, `"include"`, `"merge"` or
  `"interpolate"`; `load()`'s interpolation offer adds `result=` and `load_all()` adds
  `value=`. The three call sites used to pass different keywords, so a predicate written
  with explicit parameters raised `TypeError` in whichever phase it had not been written
  for. Predicates that accept `**context` are unaffected.
- A failure skipped by `ignore_error=True` is logged at WARNING, naming the source, the
  phase and the error's type. It was logged only at DEBUG, so a blanket
  `ignore_error=True` made failures invisible in a default logging configuration. The
  line never includes the error's message text, which can quote a configuration line;
  pass a predicate instead of `True` to keep chosen skips at DEBUG.
- A malformed `!include`/`!load` mapping with no `pathname`, or an empty `!include []`,
  raises `yaml.constructor.ConstructorError` naming the file and line. They raised a bare
  `KeyError: 'pathname'` and an unpacking `ValueError`, neither of which said where.
- The unknown-format and unknown-loader errors now list the loader names `loader=`
  accepts, and the format error reads `No backend reads <path>` instead of
  `Not reader for <path>`. Code matching the old text should catch
  `yaconfiglib.UnsupportedFormatError` (still a `NotImplementedError`) or
  `yaconfiglib.UnknownLoaderError` (still a `ValueError`) instead.
- `yaconfiglib.dumps()` and `dump()` keep mapping keys in their original order and write
  non-ASCII characters as-is; they sorted keys and escaped every non-ASCII character, so a
  written configuration no longer resembled the one that was read. Pass `sort_keys=True`
  or `allow_unicode=False` for the previous output. `dump()` to a text file whose encoding
  is not a UTF codec (for example `open(path, "w")` on Windows) still escapes non-ASCII,
  since that codec cannot hold it.
- `yaconfiglib.dumps(..., encoding=...)` raises `TypeError`; it returned `bytes` from a
  function documented to return `str`. Call `.encode()` on the result, or use
  `dump(obj, fp, encoding=...)`.
- `yaconfiglib.load(None)` raises `TypeError` and `yaconfiglib.load("")` raises
  `ValueError`; both returned `None`, so an unset config path loaded as an empty
  configuration. Check the path before calling, or pass it among the sources of
  `ConfigLoader().load(...)`, which still skips a falsy source.
- With `merge="hash"`, an open file's key is its file stem (`settings` for
  `settings.toml`) instead of `stream-<n>`. An open `.j2` file is rendered as a template,
  like the same file loaded by path.
- Assigning or deleting an attribute whose name the class defines (`items`, `get`,
  `copy`, `keys`, ...) now raises `AttributeError` instead of silently storing a key that
  attribute reads could never return — reads of those names resolve to the method, as they
  must for a `dict` subclass. Use `config["items"] = ...` for the key, or
  `object.__setattr__` for a real attribute in a subclass.
- Values assigned to a loaded configuration after it is built are stored exactly as
  given: `config["x"] = {...}` keeps a plain dict, rather than that dict becoming
  dot-accessible on the next read. Wrap it yourself (`DotAccessibleDict({...})`) if dot
  access is wanted.
- `.bat`/`.cmd` sources run on Windows only and refuse a path containing `%`; `.ps1`
  requires `pwsh` or `powershell` and obeys the machine's execution policy; `.sh` on
  Windows requires `sh` on `PATH`. Each raises a clear `ValueError`/`FileNotFoundError`
  instead of failing obscurely.
- An in-memory source handed to the command backend must have a script extension
  (`.sh`, `.bat`, `.ps1`, `.cmd`). Use a `cmd://` source to run a shell command.
- A command source's default merge key and its `pathname` in a `key_factory` or
  `transform` expression are now its **full source text** rather than a fragment of it. A
  `Hash` merge keyed on the old value has to use the whole `cmd+json://...` string.
- `CommandBackend.PATHNAME_REGEX` matches script extensions only (`.sh`, `.bat`, `.ps1`,
  `.cmd`); scheme recognition moved to the source type. `CMD+JSON://` (any case) now
  selects the `json` format.
- A source of an unsupported type raises `ValueError` before any source loads, rather
  than after the earlier ones have been read.
- A directory that cannot be listed during glob expansion (a permission error, say) is
  now skipped silently instead of raising: the expansion machinery in `pathlib-next`
  0.9.4+ swallows the error, so it never reaches `ignore_error` either. Name a file
  explicitly if its absence has to be an error.
- Glob matches now merge in a fixed order — path components compared by code point — on
  every operating system and filesystem, where the order used to be whatever the
  directory listing returned. A layered set of globbed files that relied on listing order
  may resolve differently; give such files sortable prefixes (`00-base.yaml`,
  `99-local.yaml`).
- Wildcards match dotfiles, following `pathlib`.
- The `yaml` extra now requires PyYAML `>=6.0,<7`, and the `jinja2` and `transform`
  extras Jinja2 `>=3.0,<4`. An environment pinned to Jinja2 2.x or to PyYAML below 6.0
  no longer resolves: Jinja2 2.x cannot import beside MarkupSafe 2.1 or later, and
  PyYAML below 6.0 publishes no wheels for Python 3.11+. Raise the pin.
- The documentation site is rebuilt from `main` whenever documentation or source files
  change, not only when a release is published. Between releases the published site can
  therefore describe behaviour that is not on PyPI yet.
- The source distribution no longer includes `benchmarks/`, and files matching
  `*.local.*` are excluded from both the source distribution and the wheel.
- `EnvVarBackend` with `nested_delimiter` raises `ValueError` naming both variables when
  one variable is a plain value and another nests keys under it (`APP_DB` alongside
  `APP_DB__PORT`). Which one won previously depended on the order of `os.environ`, so the
  same environment could produce different configuration. Rename or remove one of them.
- On Python 3.9/3.10 the `toml` extra now installs `tomli`, the backport of the standard
  library `tomllib`, instead of the unmaintained `toml` package — which is no longer used
  even when it is installed. TOML 1.0 files therefore parse on 3.9/3.10 exactly as they
  do on 3.11+: mixed-type arrays load instead of raising, a lowercase `z` datetime keeps
  its UTC offset instead of coming back naive, and offset datetimes can be pickled.
  Reinstall with `pip install "yaconfiglib[toml]"` on 3.9/3.10; on 3.11+ the extra
  installs nothing. Note that `tomli` 2.4+ accepts some TOML 1.1 syntax that 3.11-3.14's
  `tomllib` rejects, so a TOML 1.1-only file is not portable across interpreters.
- `.env` double-quoted values decode `\n`, `\r`, `\t`, `\"` and `\\`, so a value that
  needs a literal backslash (a Windows path) belongs in single quotes, which stay raw.
- An unterminated quoted `.env` value raises `ValueError` instead of silently swallowing
  the rest of the file.
- A quoted `.env` value ends at its first matching quote, and only whitespace or a `#`
  comment may follow it. `KEY='it's here'` and `KEY="say "hi""` used to load whole and
  are now skipped with a warning — write `KEY="it's here"` or escape the inner quotes.
- In an unquoted `.env` value quotes are ordinary characters, so `KEY=a "b # c"` gives
  `a "b`. Quote the whole value to keep the `#`.
- A `.env` line that cannot be parsed is skipped with a logged warning rather than in
  silence.
- `typed_merge` coerces mapping **keys** through a `Dict[K, V]` hint's key type, so
  `Dict[int, str]` can be satisfied by JSON, TOML, INI or env sources, which only
  produce string keys. Keys that differ only by type (`"80"` and `80`) now merge into
  one entry.
- `typed_merge` into a `TypedNamespace` subclass assembles the result without calling
  `__init__`, to avoid re-parsing values. If a subclass's `__init__` did more than apply
  `_parse_<field>` hooks, move that work into a hook or into `__merge__`.
- `typed_merge` parses string booleans for a `bool` hint: `true/yes/on/1` and
  `false/no/off/0` (stripped, case-insensitive). Any other string now raises
  `ValueError`, where every non-empty string used to become `True` — including
  `"false"`. Use one of the listed words.
- `typed_merge` raises `TypeError` when a sequence hint is given a string, a mapping or
  a non-iterable, instead of splitting it into characters or keys. Pass a list.
- `typed_merge` raises `TypeError` when a `Tuple[...]` hint's length does not match the
  value's, instead of dropping the extra items.
- `typed_merge`: a `None` source no longer overrides a value from an earlier source. To
  clear a field, pass an explicit empty value (`""`, `[]`, `{}`) instead of `None`.
- `typed_merge`: in a multi-member union, a value that is already an instance of one of
  the members is kept as it is instead of being coerced through the first member —
  `Union[int, str]` fed `"8080"` now returns `"8080"`, and `Union[str, int]` fed `5`
  returns `5`. To force a coercion, narrow the hint to the one type you want (`int`, not
  `Union[int, str]`); reordering the union members does not restore the old result.
- `is_array()` returns `False` for `bytearray` and `memoryview`, matching `bytes`. Binary
  buffers are values, so merging replaces them instead of combining them byte-wise.
- `load_as` builds dataclass fields that are annotated as a dataclass or Pydantic model
  (including `Optional[...]` of one) as instances instead of leaving them as dicts. Code
  that indexed such a field (`cfg.db["host"]`) must use attributes (`cfg.db.host`).
  Containers of models (`List[Model]`) are still left as loaded.
- Options that `ConfigLoader` itself accepts (`merge`, `encoding`, `recursive`,
  `interpolate`, ...) passed to `yaconfiglib.load()`/`loads()` now configure the loader,
  so they also apply to files pulled in with `!include` — a per-call `merge="deep"` now
  deep-merges an included glob, as the same option on `ConfigLoader(...)` always did. A
  keyword that no backend reads is ignored rather than rejected, so check the spelling
  of an option that seems to have no effect.
- With `interpolate=True`, a bare `{{ expr }}` keeps the expression's type only when
  nothing but a trailing newline surrounds it; with leading or trailing spaces the value
  renders as a string. A YAML `|`/`>` block scalar still yields the expression's type.
- With `strict=True`, a reference cycle between two top-level keys raises
  `ValueError: interpolation reference cycle: a -> b -> a` instead of rendering each
  value once against the other's current text.
- An included file's templates now render in the merged document, so a reference to one
  of that file's own keys must go through the key it was included under
  (`{{ svc.name }}` rather than `{{ name }}`).
- A relative `!include`/`!load` path is now resolved relative to the including file —
  that file's own directory, at every depth, `.yaml.j2` templates included. Previously it
  resolved against the loader's `base_dir` (by default the working directory), so a
  file in a subdirectory could not name its neighbours, and an unrelated file with the
  same name could be loaded instead. Documents that are not files (`loads()`, `#!`
  strings, streams, command output) still resolve their includes against `base_dir`,
  and so do the sources you pass to `load()` yourself. An included source's `pathname`,
  as seen by `transform` and `key_factory`, is now absolute. Include paths written
  relative to `base_dir` from a file in a subdirectory must be rewritten relative to
  that file, or made absolute.
- The `!include` mapping form accepts only `pathname`, `encoding`, `transform`,
  `key_factory`, `default`, `flatten`, `merge`, `merge_options` and `recursive`;
  other keys are ignored and logged at `WARNING`. `key_factory` there must use the
  `"%<expr>"` form.
- `transform` and `%`-form `key_factory` expressions are evaluated in Jinja2's
  sandboxed environment whenever `sandbox=True` or `allow_commands=False` is in
  effect.
- `CommandsDisabledError` is now defined in `yaconfiglib.utils.trust`; importing it
  from `yaconfiglib` or `yaconfiglib.loader` keeps working.
- With `strict=True`, an undefined variable in a `.j2`/`.jinja2` source now raises
  `UndefinedError` instead of rendering as an empty string.
- Passing a non-sandboxed `environment=` for a `.j2`/`.jinja2` source while
  `sandbox=True` or `allow_commands=False` is in effect raises `ValueError`; pass a
  `jinja2.sandbox.SandboxedEnvironment` instead.
- With `inject_env=True`, templates receive a read-only snapshot of `os.environ`
  as `env`. Previously `env` was the live `os.environ`, so a template could change
  or delete process environment variables, including ones later command sources
  saw.

### Deprecated
- `ConfigLoader(log_level=...)` (and the same keyword through `yaconfiglib.load()`) has
  no effect and now warns. It has not changed logging since 0.11.0, when setting the
  level on a shared logger was removed. Configure the `yaconfiglib` logger with the
  `logging` module instead.

### Removed
- `yaconfiglib.utils.getLogger` and the `yaconfiglib.utils.Logger` re-export, neither
  documented nor used anywhere. Use `logging.getLogger` and `logging.Logger`.

### Fixed
- Under `confine_to=`, a **glob pattern** is checked before it is expanded, and a refused
  match no longer reports the filename expansion found. Both were disclosure paths rather
  than read paths: a pattern aimed outside the roots used to be expanded anyway — listing
  directories outside them — and the refusal then named a real file, so a document could
  discover filenames by guessing a prefix (`!include '/etc/*.yaml'`). The pattern's
  literal base is now required to be inside a root, each match is checked as well (a
  wildcard can still leave a root after a legal prefix), and a match refusal names the
  **pattern**, with the resolved path logged at DEBUG. A refused stream or pattern can now
  also be skipped by `ignore_error`, which only a refused *path* could before.
- A `confine_to=` root must now be an **absolute** path; anything else raises
  `ConfigTypeError` instead of being resolved against the process working directory. That
  silent resolution was how a typo, a `""` list entry or a stray space after a separator
  in `YACONFIGLIB_CONFINE_TO` became a root under the working directory — `confine_to=[""]`
  allowed the whole of it, and an environment value of `"/a/conf: /b/conf"` dropped the
  second root and added a cwd-relative one in its place. Empty entries are now dropped in
  the sequence form as they always were in the string form, so `[""]` is an empty
  allowlist. A whole **UNC share** is also usable as a root now
  (`\\server\share`): `os.path.commonpath` treats a bare share as a relative path, so
  such a root previously refused every file inside it.
- `confine_to=` no longer fails open in four ways a caller could reach by accident.
  Passing it to an existing loader's `.load()`/`.load_all()` now raises `ConfigTypeError`
  instead of being forwarded to the backend and ignored — it is a constructor setting, and
  a caller who wrote it there had no confinement and no warning (the module-level
  `load`/`loads`/`load_as` route it correctly and are unchanged). Assigning
  `loader.confine_to` after construction is honoured, where it used to change what the
  loader reported without changing what it enforced, in both directions. An open **stream**
  is now checked against the roots by the file its `name` names, so
  `load(open(path), confine_to=...)` refuses a path outside them; a stream with no real
  name (`sys.stdin`, a descriptor, a `StringIO`) stays exempt. And an `!include` inside a
  **command's output** is confined: that output is parsed by a fresh loader, which was
  therefore checking against no roots at all while the same include in a file was refused.
- An `!include` inside the output of a **top-level** command source is read with the
  load's `encoding=`. It was read as UTF-8 whatever the call passed, so a non-UTF-8
  include raised `UnicodeDecodeError` — with a hint that told the caller to pass the very
  encoding they had passed. An include inside a command reached *through* another
  `!include` was already correct, and a mapping-form `encoding:` on a command include now
  reaches that command's output and what the output includes.
- The merge API type-checks as documented: `ConfigLoaderMergeMethod` is seen as a normal
  enum, so `ConfigLoaderMergeMethod.Deep` and `isinstance` checks work (the class is built
  at runtime by `MergeMethod.extend`, typed `type[IntEnum]`, so a checker saw no members);
  `merge="deep"` is accepted wherever a strategy is; `typed_merge()` accepts
  `Optional[...]`, unions and generic hints and is typed as returning `None` when called
  with no objects; `@opaque` keeps the decorated class's type; and importing
  `typed_merge`, `OpaqueMerge`, `opaque` and `TypedNamespace` from
  `yaconfiglib.utils.merge` satisfies a strict checker's re-export rule.
- Backend types say what they actually are: `Jinja2ConfigLoader.load()` is typed as
  returning the parsed document (it claimed `None`), a name-only backend
  (`PATHNAME_REGEX = None`) type-checks, `YamlConfig` accepts
  `loader_cls=yaml.SafeLoader`/`CSafeLoader` and any dumper class (the previous hints
  rejected the backend's own defaults, since `SafeLoader` does not subclass `BaseLoader`),
  backend `dumps()` accepts the data it serializes, and
  `ConfigBackend.get_class_by_name()` is typed — and now explicitly returns — `None` for
  an unknown name.
- `typing.get_type_hints()` now works on Python 3.9 — the supported floor — for the whole
  public API (`ConfigLoader`, `load`/`loads`, `parse_sources`, the Jinja2 helpers, the
  merge methods, every backend). Hints used `X | Y` and `typing.Self`, which 3.9 cannot
  evaluate, so anything introspecting the API there failed on most of it.
- Type checkers now accept the documented calls. Results of
  `load()`/`loads()`/`ConfigLoader.load()`/`load_all()` and `DotAccessibleDict` attribute
  access are typed `Any` rather than `object`, so `config.database.host` checks;
  `loader=` accepts a backend instance or a callable; `key_factory` accepts a string at
  construction and its callable form is the `(path, value)` callable it is actually called
  with; `loader_factory` is typed as the `(path) -> backend` callable it is called as
  (passing a backend class type-checked but never worked); `key_factory`/`loader_factory`
  callables are typed as receiving a path or, for a command URI, a `CommandSource`;
  `ignore_error=` predicates are checked against the keywords they receive, so one that
  accepts only the error — and fails at runtime — is reported; path parameters accept
  `os.PathLike` such as `pathlib.Path`; `load_all()` accepts the same sources as `load()`;
  and parameters defaulting to `None` accept `None`. Code that runs today needs no change,
  and `cast()` / `# type: ignore` workarounds for these can go.
- `typed_merge()` and `load_as()` errors name the field path and the model
  (`... [at db.port (AppConfig)]`, and `ports[1]` for a list item), readable as
  `error.config_key` and `error.config_model`. A coercion failure reported only the
  value, which in a large configuration could have been any field. Exception types are
  unchanged, and `typed_merge`'s own refusals are now `yaconfiglib.ConfigError`
  subclasses as well.
- `load_as()` says when **no source was loaded**, instead of giving a glob that matched
  nothing the same message as a document of the wrong shape. That message now also names
  the model and the type actually loaded: `load_as(AppConfig) needs a mapping, got list`.
- A failing command source's error message now ends with the tail of the command's
  stderr — the reason the tool gave — where before it said only "returned non-zero exit
  status N" and the reason was reachable only by inspecting the exception. stdout is
  never included, since it is the payload. stderr from a command that *succeeded* is
  logged at DEBUG instead of being dropped.
- When no output format is given, an `!include` that fails inside a command's output now
  raises instead of being treated as "this format does not fit". A missing secrets file
  used to be sniffed past, and the document came back empty.
- A parse failure now names the command and every format tried, and chains the parser's
  own error as `__cause__`; it said only `Failed to parse command output as [...]` with
  no cause.
- With `ignore_error`, one failing template no longer deletes the rest of its section.
  Only that value is left unrendered, with its template text as written, and everything
  else in the document still renders; previously the failing key and every sibling in its
  container were dropped, and the load reported success. Interpolation errors also name
  the key they happened at (`... [at database.password]`, and `servers[0].host` for a
  list), readable as `error.config_key`.
- A directory that glob expansion cannot list is no longer skipped in silence: the
  `OSError` is raised, and `ignore_error` is consulted for it with `phase="glob"` and
  `path=` that directory. Previously such a directory vanished from the expansion without
  an error and without reaching `ignore_error`, so a whole layer of configuration could
  disappear — and a glob could not be used to assert that a layer was read. Skipping one
  unreadable directory still loads the rest of the pattern.
- Load errors now name the file that failed. This covers YAML (which reported
  `in "<unicode string>"`, so a failing member of a glob was unidentifiable), JSON, TOML,
  INI (the full path, not just the basename), a file the encoding cannot decode (with a
  hint naming a codec to try), `.j2` templates (which reported `File "<unknown>"`), files
  reached through `!include` (naming the including file and line, at every depth), and a
  failure while merging a source. The exception type and identity are unchanged, so
  `except` clauses and `ignore_error` predicates behave exactly as before; the same
  details are readable as `error.config_source`, `error.config_frames` and
  `error.config_key`.
- Tuples are written as plain YAML lists. They were `!!python/tuple`, which this
  library's own loader refuses — and interpolating a bare `{{ expr }}` can produce one,
  so a config could dump to something it could not read back.
- `yaconfiglib.dump(obj, path, encoding=...)` writes in that encoding; it raised
  `TypeError: write() argument must be str, not bytes`. `dump()` now also accepts binary
  file objects (`open(path, "wb")`, `io.BytesIO`, `gzip.open`) and pathlib-next paths such
  as `MemPath`, which raised `NotImplementedError: __fspath__`. A binary target with
  `encoding="utf-16"` gets a single BOM; it used to get one before every written chunk,
  which did not load back.
- `yaconfiglib.loads()` honors a first line such as `#!settings.toml` that names a
  recognized format, as `ConfigLoader.load()` does; the line was read as a YAML comment
  and the document misparsed. A first line no backend recognizes (a real shebang, a
  script name) is still content.
- `yaconfiglib.loads(data, encoding=...)` accepts bytes in any codec, including
  `utf-8-sig`, `utf-16` and `utf-32`; it raised a `TypeError` about paths. `bytearray` is
  accepted too, and any other type now raises `TypeError` naming what `loads()` expects.
- `yaconfiglib.load(open("settings.toml"))`, binary `"rb"` files, and `.env`, `.ini` and
  `.json` file objects now parse with the backend matching the file's name. Every open
  file was parsed as YAML, so a TOML or `.env` file came back as one string and JSON
  exponents as strings. A file whose name no backend recognizes (`<stdin>`, `.gz`) still
  parses as YAML; pass `loader=` to choose.
- File-like objects that are not `io` classes — `codecs.open()`,
  `tempfile.SpooledTemporaryFile` on Python 3.9/3.10, custom readers — are read as
  streams. They were iterated line by line, and each line was loaded as a file path,
  glob or command, so a file listing other file names loaded those files instead of
  itself.
- YAML strings and text streams holding Windows line endings (`\r\n`) no longer gain a
  blank line per line break on Windows. Block (`|`), folded (`>`) and multi-line quoted
  values now load exactly as they do from a file.
- A string or text-stream source with characters the loader's `encoding=` cannot
  represent (for example Japanese text with `encoding="cp1252"`) now loads instead of
  raising `UnicodeEncodeError`. The document is stored as UTF-8 and read back as UTF-8.
- `parse_sources()` without `encoding=` stores in-memory text as UTF-8 on every platform;
  it used the locale codec (cp1252 on Windows), so a document with non-Latin-1 characters
  failed to load there and could not load at all.
- An in-memory document whose `#!name` line ends in `\r\n` now loads; it raised
  `No backend reads name` (then worded `Not reader for name`).
- `ConfigLoader.load()` of a bytes document in a BOM or UTF-16/UTF-32 `encoding=` now
  loads; it raised `ValueError: not enough values to unpack`.
- A missing attribute on a loaded configuration raises `AttributeError` naming the real
  class, with no chained `KeyError` behind it.
- `copy()`, `|` and `|=` on a loaded configuration keep returning a dot-accessible
  mapping instead of a plain `dict`, and `|` defers to the other operand
  (`NotImplemented`) when it is not a mapping.
- `DotAccessibleDict.get()` returns the default for a missing **non-string** key instead
  of raising `TypeError: argument of type 'int' is not iterable`.
- A dotted `get()` returns an explicit `null` leaf as `None`, matching top-level `get()`
  and attribute access; only a `null` or missing **intermediate** yields the default.
  `get("db.password")` on `password: null` no longer looks like an absent key.
- Dotted paths index lists and tuples: `get("servers.0.host")`. A digit segment on a
  *mapping* is still the string key, so `get("codes.0")` is unambiguous, and a negative
  or out-of-range index returns the default.
- Reading a configuration no longer modifies it. `config.get("missing", {})` used to
  store that default under the missing key, and every attribute or `get()` read of a
  nested mapping replaced the stored value with a new wrapper — so `config["db"] is
  config.db` could be false, a held reference went stale, and two YAML anchor aliases
  stopped being the same object. Nested mappings are now converted once, when the result
  is built, and a miss returns the default without inserting it.
- Mappings inside lists and tuples are dot-accessible, as are `List`-merge results and
  each document from `load_all()`. Previously only a top-level dict was wrapped, so
  `config.services[0].name` raised `AttributeError`.
- Loading no longer aliases the objects a backend returned: writing to a loaded result
  cannot reach back into a `PythonBackend` source or an included document.
- Command output that does not decode with the requested codec (default `utf-8`) raises
  a clear error naming `encoding=`, instead of silently replacing bytes with `U+FFFD`. A
  non-ASCII secret from a tool writing another code page used to arrive corrupted, and the
  failure surfaced only wherever that value was later used. Pass `encoding=` — for
  example `oem` on Windows for `cmd`, `.bat` or PowerShell output. A command that exits
  non-zero still raises `CalledProcessError` with its output attached, decoded leniently.
- Script files run through the right interpreter on each platform: a `.ps1` runs
  PowerShell instead of whatever the file association opens, a POSIX `.sh` needs no
  execute bit (it falls back to `/bin/sh`), and a script path containing a space, `&`,
  `^` or quotes neither breaks nor injects a second command — script files are launched
  with `shell=False`, so their names are no longer shell syntax. A relative script path
  always runs the file in the current directory rather than searching `PATH`.
- An in-memory `#!name.sh` document, and a `gen.sh.j2`/`gen.bat.j2` template, run their
  own (rendered) body. They previously ran whatever program of that name the shell
  happened to resolve.
- `cmd://`, `exec://` and `sh://` text reaches the shell exactly as written. A path
  factory used to rewrite it first: on Windows every `/` became `\`, and on POSIX `//`,
  `/./` and trailing slashes were collapsed — so a secrets-manager URL, a relative path
  argument, or even `print(10/4)` in an inline script arrived corrupted.
- A file whose name merely starts with `sh:`, `cmd:` or `exec:` loads by its extension
  instead of being executed. Only a string source can be a command; a path object is
  always a file.
- A `Hash` merge logs a warning when two sources produce the same key — for example
  `services/*/config.yaml`, whose stems are all `config`, where only the last document
  was kept and nothing said so. The later document still wins; pass `key_factory` (such
  as `lambda path, value: path.parent.name`) to keep every source.
- The same file named two ways now loads once: `conf/app.yaml`, `./conf/app.yaml`,
  `conf/../conf/app.yaml` and, on a case-insensitive filesystem, `conf/App.yaml` are one
  source. Duplicate detection compares normalized absolute paths instead of the string
  each source was spelled with.
- A file named explicitly no longer loads twice when a glob in the same call also matches
  it. The explicit name wins and keeps its position, so both
  `load("base.yaml", "*.yaml")` and `load("*.yaml", "local.yaml")` layer as intended, and
  two overlapping globs yield each file once.
- Glob characters in `base_dir`, in an existing file's name, or in a Windows
  extended-length (`\\?\`) prefix no longer turn a path into a pattern. A `base_dir`
  called `proj [v2]` works, a file really named `z[1].json` loads as itself, and
  `has_glob_pattern()` looks only outside a path's anchor.
- A glob that matches a directory skips it instead of failing with
  `NotImplementedError` for the directory, so `envs/*` loads the files under
  `envs/`.
- Sources given as a `pathlib.Path` (or any other `os.PathLike`, including a `PurePath`
  or an object that just defines `__fspath__`) now load instead of raising
  `ValueError: unable to handle arg ... of type <class 'pathlib.WindowsPath'>`. They get
  the same `base_dir`, `path_factory` and glob handling as the equivalent string, so
  `str(path)` workarounds can be dropped.
- Glob sources work again with current `pathlib-next`. 0.9.4 removed the `glob("")`
  spelling this package used to expand a pattern, for parity with `pathlib`, so every glob
  source and every `recursive=` load raised `ValueError: Unacceptable pattern: ''` on
  0.9.4 and 0.9.5. It now uses `glob(None)`, the supported form, and requires
  `pathlib-next>=0.9.6` — where that form was added. Upgrade `pathlib-next` if a pin holds
  it below 0.9.6.
- Without an importable Jinja2, `transform=`, `key_factory='%...'` and
  `interpolate=True` raise `ImportError` naming `yaconfiglib[jinja2]` and the original
  import error — even under `ignore_error=True`, since the check runs before any source
  is read. The first two previously raised `AttributeError: 'NoneType' object has no
  attribute 'eval'`, or quietly returned `None` when errors were being ignored. Install
  the extra, or catch `ImportError`.
- The security guide now states that `!include` can read any local file the
  process can (absolute paths and `..` are not confined to `base_dir`), even with
  `allow_commands=False` and `sandbox=True`: those controls stop code execution and
  template injection, not file disclosure. Do not return or log an untrusted
  config verbatim.
- The security guide no longer claims the `python` backend executes Python; it
  passes a caller-supplied object through unchanged.
- Corrected the `PythonBackend` examples in the backends guide and the class docstring.
  Passing the backend positionally raised `ValueError`, and `loader=PythonBackend(...)`
  alongside a file silently ignored the file. Load the in-memory object on its own and
  merge it with the file result, as the documentation now shows.
- A lowercase `prefix` now matches on Windows, where environment names are
  case-insensitive and `os.environ` upper-cases them; `prefix="app_"` used to find
  nothing there.
- A variable equal to the prefix (`APP_` with `prefix="APP_"`) no longer produces an
  empty-string key.
- When a format's optional dependency is missing, the "No backend reads ..." and "Unknown
  configuration format/loader" errors name the extra to install and the underlying import
  error, instead of only reporting an unknown format.
- `.cfg` files are detected as INI, which the API reference already stated. A `.cfg`
  file previously raised `NotImplementedError`, so a glob loaded with
  `ignore_error=True` skipped it silently and now merges it in.
- An INI file containing only `[DEFAULT]` logs a warning explaining that those keys are
  inherited by other sections rather than returned, and how to load them as a section.
  It used to load as an empty mapping, indistinguishable from an empty file.
- Command output with no `format=`, `+fmt` or `#!fmt` shebang is sniffed as documented.
  INI output keeps its sections, where it used to be flattened as dotenv and lose keys;
  `KEY=value` output is parsed as dotenv, where it used to come back as one folded
  string; and output no format accepts comes back as the raw stdout string, where it used
  to be an empty mapping. While sniffing, YAML is accepted only for a mapping or a list,
  so output that only YAML read as a scalar — a bare date such as `2026-09-15`, `yes` or
  `~` — now comes back as the raw string; use `cmd+yaml://` to force a YAML scalar.
- A multi-line quoted `.env` value loads whole. A PEM key or any other value spanning
  several lines was truncated at the first line break.
- An apostrophe in an unquoted `.env` value no longer disables comment stripping, so
  `KEY=it's here # comment` gives `it's here`.
- `.env` keys containing `.` or `-` are kept instead of silently dropping the line.
- A `.env` value containing a form feed, vertical tab, `\x1c`-`\x1e`, U+0085, U+2028 or
  U+2029 is no longer cut at that character: only a newline ends an entry. A
  Windows-1252 file read with `encoding="latin-1"` therefore keeps such a value whole —
  though byte `0x85` then arrives as U+0085, so read those files with
  `encoding="cp1252"`.
- JSON, TOML, INI and `.env` files saved with a UTF-8 byte-order mark now load. The
  mark previously dropped the first `.env` variable, prefixed the first TOML key with an
  invisible character, and made JSON and INI raise.
- `TomlConfig`, `JsonConfig`, `IniConfig` and `Jinja2ConfigLoader` accept a `str` path
  and an omitted `encoding`, so a backend instance can be registered directly as a
  PyYAML tag constructor (`!toml`, `!json`, `!ini`), which is how the documentation says
  to use them. They also accept `path_factory=`, like the other file backends.
- Files whose name ends in another format's suffix after `.env` are parsed by that
  format's backend: `app.env.yaml` as YAML, `settings.env.json` as JSON, `x.env.toml` as
  TOML, `x.env.ini` as INI, and `.env.j2` or `config.env.yaml.j2` rendered as a template
  first. They previously loaded as dotenv, which meant an empty or flattened result.
  `.env`, `*.env` and staged names such as `.env.local` and `.env.development.local` are
  still dotenv, and `loader="dotenv"` reads any name as dotenv.
- `typed_merge` builds a mapping target positionally, so **non-string keys** no longer
  fail with "keywords must be strings", an abstract `Mapping[...]` hint no longer raises
  "Can't instantiate abstract class", and a `defaultdict` target keeps its
  `default_factory` instead of losing it.
- `typed_merge` leaves a dataclass's `field(init=False)` names out of the constructor
  call, where passing them raised "unexpected keyword argument".
- `typed_merge` applies a `_parse_<field>` hook exactly once per value. A
  `TypedNamespace` source was parsed again while being collected and a third time by the
  target's constructor, so any parser that is not idempotent — the documented
  comma-splitting example included — failed or corrupted the value.
- `typed_merge` handles the sequence hints that broke in 0.11.1, when the element
  coercion branch first became reachable: abstract `Sequence[...]`/`MutableSequence[...]`
  hints (which raised "Can't instantiate abstract class"), `NamedTuple` hints (rebuilt
  through their field types instead of being handed a generator), `range`, and lists
  holding either.
- `typed_merge` coerces a heterogeneous `Tuple[int, str]` hint by position instead of
  applying the first type argument to every element.
- `typed_merge` skips `None` sources at every level instead of merging them: an
  `Optional[...]` field set to `None` by a later source no longer crashes
  (`vars(None)`) or replaces the earlier value with `None`, `'None'` or `False`.
- `typed_merge` honours a `typing.Any` hint on Python 3.11+, where `Any` became a class
  and reached the coercion path (`isinstance() with typing.Any`). Directly, as a type
  argument, or as a union member, `Any` takes the last object unchanged.
- `typed_merge` strips `Annotated[X, ...]` and coerces through `X`; the annotation used
  to be returned uncoerced.
- `typed_merge` resolves type hints one field at a time when the whole set cannot be
  resolved, so a single unresolvable forward reference no longer leaves every sibling
  field uncoerced.
- `OpaqueMerge`, `opaque` and any `__merge__` classmethod are honoured through
  `Optional[...]`, `Union[...]` and parameterized generics, not only on a direct class
  hint.
- A `Union` hint whose first member is `None` (`Union[None, int]`) no longer merges
  through `NoneType` and fails.
- An error raised inside a custom merge strategy's `init()` propagates instead of being
  swallowed as "this strategy has no `init`", which silently skipped the hook.
- `List` and `Hash` work on an enum built with `ConfigLoaderMergeMethod.extend(...)`.
  The seed value is now chosen by member name, where an identity check against the
  built-in enum failed for every extension (`List` raised `AttributeError`, `Hash`
  returned an unkeyed document).
- `ConfigLoaderMergeMethod` members can be pickled — for example passed to a worker
  process. The generated enum now records the module it is bound in, so `pickle` can
  find it again.
- `load(default=...)` is documented as what the code does: it is returned only when no
  source loads, and is never merged into a loaded document.
- `Deep` list extension no longer treats `True`/`False` as duplicates of `1`/`0`, or
  `1.0` as a duplicate of `1`: an item is already present only if an existing one has the
  same type and compares equal. It also keeps the new source's order when that source
  mixes mappings with other items; mappings used to be moved to the end of the list.
- `Simple`, the default strategy, replaces a list with the later list when two top-level
  list documents (or two lists passed directly) merge, as the strategy table, the module
  docstring and the API header all said. It used to replace positionally and keep the
  earlier list's extra tail, so `[a, b, c]` overridden by `[x]` gave `[x, b, c]`. Lists
  under a mapping key already replaced and are unchanged. Write the full list in the
  override if you relied on the old result.
- `Deep` and `Substitute` no longer raise `TypeError` on ordinary configuration values
  they had no rule for — dates, datetimes, times, `Decimal`, sets, enum members, objects
  a backend produced. Two YAML files with an unquoted `release: 2024-01-01` used to fail
  the whole load, and with `ignore_error=True` the entire override file was discarded
  instead.
- `Deep` list extension keeps items of those types. `holidays: [2024-12-25]` overridden
  by `holidays: [2025-12-25]` now holds both dates; the override used to be dropped.
- A type-changing override replaces instead of raising: a mapping overridden by a number,
  a string by a list, a list by a mapping. A mapping overridden by a list that is not a
  non-empty list of mappings becomes that list, so `section: []` clears it (it used to
  keep the mapping, or raise for `[42]`). A non-empty list of mappings still folds into
  the mapping.
- Overriding a key under a YAML anchor or a `<<:` merge key no longer rewrites the
  sibling keys that share it. `Deep` and `Substitute` merged into their left-hand
  argument, so an override for one environment silently changed every environment that
  shared the mapping. The strategies are now copy-on-write and never modify their
  arguments — use the return value, as `ConfigLoader` always has.
- A self-referencing mapping now merges instead of raising `RecursionError`, and a node
  aliased in both inputs stays one object in the result.
- `Simple` no longer raises `TypeError` when merging into a read-only mapping whose keys
  overlap the override.
- `ConfigLoader(log_level=<any int>)` no longer raises `ValueError` for a value that is
  not one of the `LogLevel` members.
- `flatten=True` skips empty documents instead of failing inside a comprehension — a
  `conf.d` glob may hold an empty file — and reports any other member it cannot flatten
  with a `TypeError` naming that member.
- `yaconfiglib.dumps()`/`dump()` write a loaded configuration as a plain YAML mapping
  that `yaconfiglib.load()` and other YAML tools read back. Previously the result
  carried a `!!python/object/new:...DotAccessibleDict` tag that the library's own safe
  loader refused. Passing your own `Dumper=`/`dumper_cls=` keeps PyYAML's default
  handling.
- `load_as` no longer fails on a document key named `self`, keeps `InitVar` values, and
  no longer imports pydantic when it is not already imported (it probes `sys.modules`
  instead, which is exact: a class can only subclass `BaseModel` if pydantic is loaded).
- `yaconfiglib.load()` and `loads()` pass backend options through instead of raising
  `TypeError`: `json_decoder_options`, `ini_default_section`, `environment=` and any
  other option a backend reads now reach it. They used to be rejected because the
  helpers matched keywords against a hand-written list.
- `env` is available when rendering `.j2`/`.jinja2` sources with `inject_env=True`,
  as the templating guide's example shows. Previously it raised
  `UndefinedError: 'env' is undefined`.
- `utils.jinja2.compile()` and `eval()` honour `globals=` on every call. The first
  call's globals were baked into the cached template, so a later call with different
  globals got the first call's values.
- The compiled-template caches are safe to use from several threads: a lookup can no
  longer fail with `KeyError` when another thread evicts an entry at the same moment.
- A `.j2`/`.jinja2` file with no format extension in front of the suffix (`config.j2`)
  now raises `NotImplementedError` naming the template and the expected
  `name.<format>.j2` form, before the file is read. The error used to name a stripped
  path that does not exist, or an unrelated temporary file.
- Values pulled in with `!include`/`!load` are now rendered together with the document
  that includes them when `interpolate=True`: an included file's templates can refer to
  the including document's keys, and an escaped literal such as `{{ '{{ x }}' }}` in an
  included file is no longer rendered a second time. References between templated values
  resolve fully in any order (`logs: "{{ base }}/logs"`, `err: "{{ logs }}/err"`);
  previously a literal `{{ base }}` could survive into the result.
- An `encoding=` passed to `ConfigLoader.load()`, `ConfigLoader.load_all()` or
  `yaconfiglib.load()` now applies, at every depth, to the files, templates and
  commands pulled in with `!include`/`!load` that do not set their own `encoding`.
  Previously those targets were read with the loader's default, which raised
  `UnicodeDecodeError` or silently garbled non-ASCII text; the same happened to
  includes inside an included command's output under `ConfigLoader(encoding=...)`.
  An include whose real encoding differs from the one passed needs its own
  `encoding:` in the mapping form.
- Interpolating YAML that reuses anchors (`&name`/`*name`) no longer slows down
  exponentially with nesting depth: each shared node is interpolated once, and
  every alias refers to the same result. A seven-level document that took about
  4 seconds now loads in well under a tenth of a second.
- Command sources run with stdin closed. Previously a command that read stdin hung
  the load or consumed the calling process's input.
- An include cycle (`a.yaml` including `b.yaml` including `a.yaml`) now raises
  `ValueError: include cycle: ...` naming the chain. Previously it recursed until
  `RecursionError`, or with `ignore_error=True` returned a deeply nested partial
  result.


## [0.11.2] - 2026-08-16

### Changed
- Bounded the `pathlib-next` dependency to `>=0.9.0,<0.10` (previously
  unversioned). It carried no bounds at all, so a resolver was free to install a
  release predating the `Pathname`/`MemPath` surface that `backends/` and
  `utils/source.py` import, or a future minor that changes it. Nothing in the
  package uses API added after 0.9.0, so the floor sits at the start of the
  series rather than at its newest patch.

## [0.11.1] - 2026-08-16

### Fixed
- **`sandbox=True` no longer breaks bare `{{ expr }}` values.** The
  type-preserving expression path captured its result with
  `_meta.__setitem__(...)`, an underscore attribute access that Jinja2's
  `SandboxedEnvironment` refuses — so every pure-expression config value raised
  `SecurityError` under the 0.11.0 `sandbox` control, while mixed-text templates
  (`"hello {{ name }}"`) worked. The capture is now a plain callable bound as a
  render name; SSTI payloads still raise `SecurityError` as before.
- **The `recursive` option now actually recurses.** `ConfigLoader(recursive=True)`
  and the per-call `load(recursive=...)` override were documented but never
  forwarded to `parse_sources`, making both silent no-ops; `load_all()` now
  honors the instance setting too. Default (`False`) glob behavior is unchanged.
- **Deep merge with `mergelists=True` no longer drops non-overlapping dicts.** A
  positionally-matched dict from the right-hand list that shared no key with its
  counterpart was removed from the pending set before the overlap check, so it
  was neither merged nor appended — `Deep([{"k": 1}], [{"z": 9}], mergelists=True)`
  returned `[{'k': 1}]` instead of `[{'k': 1}, {'z': 9}]`. Silent data loss.
- **`typed_merge` now actually reads parametrized-generic arguments.** Three
  defects shared one root cause — type args were taken from the original `cls`
  instead of the union-unwrapped origin, and the sequence branch tested a class
  object with instance checks so it could never run. `Dict[str, int]` coerced
  nothing (`{"a": "1"}` stayed a string), `List[str]` returned its elements
  uncoerced, and `Optional[Dict[str, int]]` picked `NoneType` as the value type
  and raised `TypeError: NoneType takes no arguments`. Unparametrized `dict`/
  `list` hints and `str`/`bytes` are unaffected.
- **The Jinja2 backend no longer crashes when `pathlib_next` is absent.** Its
  ImportError fallback set `MemPath = None` and `load()` called it anyway, so
  every `.j2`/`.jinja2` source raised `TypeError: 'NoneType' object is not
  callable` — despite the class docstring promising "a real temp file when
  `pathlib_next` is unavailable". It now materializes a tracked temp file,
  reusing the mechanism `utils/source.py` already used for in-memory sources,
  and keeps the rendered basename so backend auto-detection still resolves
  `settings.yaml.j2` to the YAML backend.

## [0.11.0] - 2026-07-20

### Changed
- Narrowed `typed_merge`'s `get_type_hints` fallback to
  `(TypeError, NameError, AttributeError)`; the loader/command broad error
  boundaries that feed the `ignore_error` predicate now log the swallowed error
  at DEBUG and are documented as deliberate.
- **Faster interpolation.** A string with no Jinja delimiter (`{{`/`{%`/`{#`) —
  the common case for config values — now returns unchanged without a cache
  lookup or `Template.render`. The template/expression caches also switched from
  clear-everything-at-capacity to LRU eviction, and their env keys are
  weakref-guarded so a recycled `id(env)` can't return a render bound to a dead
  environment. `parse_sources` duplicate detection is O(1) per source (set)
  instead of O(n²) (list).

### Added
- **`allow_commands` control.** `ConfigLoader(allow_commands=False)` (or per
  `load()` call) refuses to execute a command source — `cmd://`, `exec://`,
  `sh://`, `*+fmt://`, script-extension files, and any reached via `!include` —
  raising `CommandsDisabledError` instead. Use it when loading configuration you
  do not fully trust. Defaults to permissive (commands allowed).
- **`sandbox` control.** `ConfigLoader(sandbox=True)` runs interpolation in
  Jinja2's `SandboxedEnvironment`, blocking attribute-traversal (SSTI) attacks
  from untrusted config values. Defaults off.
- New `docs/guide/security.md` documenting the trust model and both controls.

### Fixed
- **`!include` now routes through the loader driving the current parse.** The
  include constructor is registered once per YAML loader class, capturing the
  first `ConfigLoader`; nested `!include`/`!load` previously inherited that first
  loader's settings (base_dir, merge, and — critically — `allow_commands`),
  leaking state across loaders. Includes now use the loader actually performing
  the load.
- `ConfigLoader.load(merge_options=...)` no longer permanently overwrites the
  instance's `merge_options` — the documented per-call override was leaking into
  every subsequent `load()`.
- Backend resolution is now deterministic: recursive backend discovery returns a
  definition-ordered list instead of a `set`, so when two backends' patterns both
  match a path, the first-registered one reliably wins (previously hash-order
  dependent).
- Constructing a `ConfigLoader` (including the import-time default instance) no
  longer calls `setLevel()` on the module logger — library code no longer mutates
  global logging state.
- Stream and unnamed in-memory sources each get a unique virtual path; previously
  every stream materialized to the same `MemPath("stream")`, so resolving sources
  up front left all of them holding the last stream's content.
- `CommandBackend` decodes command output as UTF-8 (or an explicit `encoding=`)
  instead of the locale codec, which mangled UTF-8 output on Windows; temp-file
  fallback sources are cleaned up at exit and their suffixes sanitized.

## [0.10.0] - 2026-07-18

### Added
- `OpaqueMerge` mixin, `opaque` class decorator, and `TypedNamespace` base for
  customizing `typed_merge`, all exported from the package root.
  `OpaqueMerge`/`opaque` mark a type opaque (last object wins, no field
  introspection — for a fully-built config object or one with factory-function
  field hints); `TypedNamespace` applies `_parse_<field>` coercers at
  construction. The `__merge__` and `_parse_<field>` extension hooks are now
  documented in the merging guide.

### Changed
- YAML `!include`/`!load` auto-registration now logs a `WARNING` when it
  overrides a constructor already registered on the loader class, so a
  redundant manual `yaml.add_constructor("!include", ...)` is visible rather
  than silently replaced.

## [0.9.8] - 2026-07-18

### Added
- `typed_merge` is now exported from the top-level `yaconfiglib` package
  (`from yaconfiglib import typed_merge`), alongside `MergeMethod` /
  `ConfigLoaderMergeMethod`. It was previously only reachable via the internal
  `yaconfiglib.utils.merge` path.

### Fixed
- `typed_merge` no longer raises `TypeError: issubclass() arg 1 must be a class`
  when a field's resolved type hint is a **non-class callable** (e.g. an
  `ipaddress`-style factory function such as `netutils.IPNetwork`). Such a hint is
  now treated as an opaque coercer: the last value wins, coerced through the
  callable when it accepts the value, otherwise returned unchanged. Class hints are
  unaffected.

## [0.9.7] - 2026-07-14

### Documentation
- Added docstrings across `ConfigBackend` and every built-in backend (YAML, TOML, JSON, INI, dotenv, env, command, python, jinja2), and filled in `ConfigLoader`'s constructor, `load`, `load_all`, and `parse_sources`.
- Rebuilt the docs site: `docs/index.md` is now a full landing page instead of a README redirect stub; added task-oriented guide pages (`backends`, `merging`, `templating`, `includes`, `models`) under `docs/guide/`; split the flat API reference into per-module pages (`api/loader.md`, `api/backends.md`, `api/utils.md`).

## [0.9.6] - 2026-07-14

### Added
- Added optional nested environment variable loading and scalar coercion to `EnvVarBackend` via `nested_delimiter` and `coerce`.
- Expanded benchmarks with focused suites for source parsing, merge behavior, Jinja/load interpolation, dot-access lookup, and env backend loading.

### Changed
- Optimized `DotAccessibleDict.get()` dotted traversal to avoid exception-converting attribute lookup on hot paths.

### Fixed
- Fixed `.env` parsing so whitespace-delimited inline comments are stripped while hashes inside quoted or unquoted values are preserved.
- Defined the missing `ConfigLoader.load_as` type variable so runtime type-hint introspection succeeds.
- Fixed `Jinja2ConfigLoader` so `environment=` is honored, the backend is discoverable as `jinja2`, and rendered in-memory configs are parsed without rejoining the parent base directory.
- Fixed `ConfigLoader.load()` so `merge_options` are passed to merge implementations, including `mergelists=True`.
- Added an explicit `TypeError` for `flatten=True` on scalar merged results instead of leaving an unbound local failure path.
- Fixed duplicate path tracking, nested iterable option propagation, and stdlib `base_dir` glob fallback behavior in `parse_sources()`.

## [0.9.5] - 2026-07-13

### Changed
- **Documentation**: Replaced AGENTS-specific development and release references in `README.md` with concrete local install/test commands and a concise release workflow summary for contributors.

## [0.9.4] - 2026-07-11

### Changed
- **Performance**: Optimized `utils/source.py` by precompiling command matching regex at the module level and avoiding path splitting allocations in `has_glob_pattern` (yielding a ~58% speedup in glob checks).
- **Performance**: Optimized `loader.py` and `utils/jinja2.py` by caching compiled templates/expressions and reusing the Jinja Environment in `load_all` (up to 30x faster template interpolation and 8x faster `load_all` sequential loads).
- **Performance**: Optimized `utils/merge.py` by fast-pathing standard collection types in `is_array` and bypassing positional dictionary comprehensions in `_deep_lists` when positional merging is disabled (yielding a ~19-35% speedup in deep merges).

### Fixed
- Fixed `UnboundLocalError` inside `load_all` error handling path when a load failure occurred before the local variable `value` was bound.

## [0.9.3] - 2026-07-11

### Fixed
- Fixed false-positive glob pattern detection for `cmd://` and `exec://` sources whose command arguments contained glob metacharacters (e.g. `[`, `]`). Paths identified as command sources now bypass the glob check and are yielded directly.

## [0.9.2] - 2026-07-11

### Fixed
- Replaced Python 3.10+ `match`/`case` statement in `loader.py` with `if`/`elif` for Python 3.9 compatibility.
- Replaced runtime `|` pipe union syntax in `utils/source.py` (`SourceLike`) with `typing.Union` for Python 3.9 compatibility.

## [0.9.1] - 2026-07-11

### Fixed
- Corrected dependency declaration from `pathlib_next` to canonical PyPI name `pathlib-next`.

## [0.9.0] - 2026-07-11

### Added
- Pluggable backend registry with custom backends: `DotenvBackend` (parsing `.env` files), `EnvVarBackend` (querying `os.environ` with prefix filtering), and `PythonBackend` (direct dict injection).
- New `CommandBackend` supporting command and shell script execution (`cmd://`, `exec://`, `.sh`, `.bat`, etc.) with format overrides and shebang-based routing (`#!json` / `#!yaml`).
- Top-level standard library API parity (`load`, `loads`, `dump`, `dumps`) with the newly implemented `dump` function.
- Optional Pydantic model validation (`load_as`) with fallback support to dataclasses.
- Dot-notation dictionary access wrapper (`DotAccessibleDict`) for deep configuration values.
- Jinja2 environment features: environment auto-injection (`env.KEY`) and strict interpolation mode (`strict=True`).
- Comprehensive unit tests in `tests/test_v2_features.py`.

### Changed
- **API Modernization**: Renamed `configloader` argument to `loader` across the public API surface (`load`, `loads`, and backends).
- **YAML Inclusion**: Idempotent auto-registration of `!include` and `!load` constructor tags in SafeLoader, now dynamically delegating to the parent `ConfigLoader` instance to support nested TOML/JSON/command loads.
- **Compatibility**: Replaced Python 3.10+ specific features to support Python 3.9+, and updated pyproject.toml / CI workflows.
- **pathlib_next**: Made `pathlib_next` an optional dependency with standard library pathlib, tempfile, and glob fallback behaviors.
- **Code Organization**: Decoupled `typed_merge` logic into its own `utils/typing_merge.py` module.
- **License**: Changed the license from GNU GPLv3 to MIT License.
- **Refactoring**: Completely rewrote `utils/merge.py` and `utils/jinja2.py` in a clean-room implementation, removing all legacy code dependencies (GPL3 taint from `hiyapyco` and `yamlinclude`).
- **Error Handling**: Improved error handling across `loader.py` and `utils/source.py`, replacing bare `except Exception:` blocks with targeted exception capturing.
- **Logging**: Adopted idiomatic module-level loggers instead of passing logger instances down the call stack.
- **Typing**: Adopted modern PEP 604 union types and improved static analysis compatibility in merge functions.

### Removed
- Legacy references and code blocks tied to `hiyapyco`.

[Unreleased]: https://github.com/jose-pr/yaconfiglib/compare/v0.14.0...HEAD
[0.14.0]: https://github.com/jose-pr/yaconfiglib/compare/v0.13.1...v0.14.0
[0.13.1]: https://github.com/jose-pr/yaconfiglib/compare/v0.13.0...v0.13.1
[0.13.0]: https://github.com/jose-pr/yaconfiglib/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/jose-pr/yaconfiglib/compare/v0.11.2...v0.12.0
[0.11.2]: https://github.com/jose-pr/yaconfiglib/compare/v0.11.1...v0.11.2
[0.11.1]: https://github.com/jose-pr/yaconfiglib/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/jose-pr/yaconfiglib/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.8...v0.10.0
[0.9.8]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.7...v0.9.8
[0.9.7]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.6...v0.9.7
[0.9.6]: https://github.com/jose-pr/yaconfiglib/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.5
[0.9.4]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.4
[0.9.3]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.3
[0.9.2]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.2
[0.9.1]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.1
[0.9.0]: https://github.com/jose-pr/yaconfiglib/releases/tag/v0.9.0
