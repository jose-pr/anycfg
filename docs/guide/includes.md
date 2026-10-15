# Includes

A YAML document can pull another source in at a key with `!include` (or its
alias `!load`). Where several `load()` arguments layer **whole documents**, an
include **nests** one document at a specific place in another — use it when a
value, not the whole configuration, should come from another file.

```yaml
# app.yaml
name: app
database: !include db/settings.toml
```

```python
from yaconfiglib import Loader

config = Loader().load("app.yaml")
print(config["database"]["host"])      # from db/settings.toml
```

The included source may be in any format, chosen by its file name as usual.
Includes need the `yaml` extra (they are YAML tags) and are allowed by the
default policy; `Policy(allow_includes=False)` refuses them with `PolicyError`.

## Forms

```yaml
db: !include db.yaml                                     # one source
all: !include [base.yaml, local.yaml]                    # several, merged in order
opt: !include {pathname: extra.yaml, format: json, merge: deep}
```

The sequence form is a list of **sources merged in order**, not arguments. The
mapping form needs a non-empty `pathname` (a source, or a list of them) and
accepts these options:

| Key | Meaning |
| --- | --- |
| `pathname` | the source or sources |
| `format` | read the sources as this format instead of by name |
| `encoding` | the codec for these sources and, by default, their own includes |
| `merge` | a strategy **name** for merging several sources |
| `merge_options` | options for that strategy (`{mergelists: true}`) |
| `default` | the value when no source loads (a glob that matched nothing) |
| `flatten` | flatten the result by one level, as `load(flatten=True)` |
| `recursive` | let `**` in a glob descend into directories |

Any other key — a policy field, `interpolate`, a callable — is **dropped** with
a WARNING, so a document can never change what its caller allowed (see
[Security](security.md)). A malformed form raises `ParseError` naming the file
and line.

`default` applies only when **nothing** loads. A file named outright that does
not exist still raises `FileNotFoundError`; for an include that may be absent,
use a glob, which matches nothing without an error:

```yaml
local: !include {pathname: "local*.yaml", default: {}}
```

## How include paths resolve

- A **relative path** resolves against the directory of **the file it is
  written in**, at every depth. A file included from a subdirectory writes its
  own includes relative to itself, so a configuration tree can be moved as a
  whole and loaded from any working directory.
- An **absolute path** is used as it is.
- A **glob** expands next to the including file and follows every
  [glob rule](sources.md#globs).
- **Content that is not a file** — `loads()`, `sources.Text`, a stream without
  a real name, command output, a `MemPath` — has no directory of its own, so
  its includes resolve against the loader's `base_dir`, which is the working
  directory by default. `yaconfiglib.loads()` therefore resolves includes
  against the working directory.
- A **template file**'s includes resolve next to the template.

`base_dir` itself applies to the sources you pass to `load()`; it is not the
anchor for an include written in a file.

## Formats a document may select

An include chooses a format by its target's name or by its `format:` key. Both
can select only the built-in formats and those registered in code: a format
installed by another package's entry point is reachable from a document only
when named in `Loader(formats=...)` (see
[Formats](formats.md#distributing-a-format)). Command schemes are reachable
(`!include 'cmd+json://...'`), and refused unless the policy allows commands.

## Encoding

An include that names no `encoding` is read with the encoding of the document
that included it — at the top, the load's `encoding=`. An `encoding:` on the
mapping form applies to that include and is inherited by **its** includes:

```yaml
plain: !include sub.yaml                                  # the load's encoding
legacy: !include {pathname: latin1.yaml, encoding: latin-1}
```

Includes inside a command's output are read with the command's encoding.

## What an include sees

- **Each included document is its own YAML anchor scope**: an alias cannot
  refer to an anchor in the including file.
- **Includes are not interpolated on their own.** With
  `Loader(interpolate=True)` the merged top-level document is rendered once, so
  a template inside an included file sees the whole configuration — and refers
  to the included file's own keys through the key it was included under
  (`{{ database.host }}`, not `{{ host }}`).
- **The policy is the caller's.** An included file, and anything it includes,
  runs under the policy of the load that reached it: confinement, the
  [budget](security.md#the-budget), the sandbox and the command switch all
  carry down.

## Cycles and the budget

An include cycle raises `ConfigValueError`:

```text
include cycle: conf/a.yaml -> conf/b.yaml -> conf/a.yaml
```

Cycles are detected by the **canonical** path, so a document cannot dodge the
check by spelling itself `../conf/a.yaml`. The policy's `max_includes` (1000)
and `max_depth` (64) bound how many includes one load resolves and how deep
they nest; past either, `BudgetExceededError` is raised. Each `!include`
directive counts once toward `max_includes`, however many files it names.

## When an include fails

An error inside an included file keeps its own type — a broken JSON include
still raises `ParseError`, a missing one `FileNotFoundError` — and names the
file it happened in **and** every file that included it, with the line the
`!include` is written on:

```text
conf/sub/bad.json:1:7: Expecting value [included from conf/app.yaml, line 4]
```

The same information is on the exception as `config_source` (the failing
source) and `config_frames` (an `ErrorFrame(kind, source, line)` per step,
innermost first).

Under `on_error`, the failure is offered once per level: first with
`phase="load"` and the included file as `path`, then — if that was not
skipped — with `phase="include"` and `path` each file that included it, outward,
the same exception object each time. Skipping at the `"load"` level makes that
include's value `default` (`None` unless given), and the including document
still loads. Skipping at an `"include"` level drops that including source.

## Includes in your own format

`!include` is YAML's, but any [format](formats.md#writing-a-format) can offer
includes through the context it is given:

```python
def loads(self, data, /, *, context=None, **options):
    ...
    value = context.include("other.yaml", merge="deep")
```

`context.include(source, *more, **options)` loads through the active loader,
with the same rules as a YAML include: the same options, the policy,
confinement, the budget and cycle detection. Without a loader (a direct
`Format().loads()` call), it raises `ConfigValueError`.
