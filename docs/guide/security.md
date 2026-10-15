# Security

A configuration document can do more than hold values: it can include other
files, and — when you allow it — run commands and render templates. What a load
may do beyond parsing what you named is its **policy**, a
`yaconfiglib.Policy`, and **the default policy is the untrusted posture**.

## The trust model

With the default policy, a load:

- reads the sources you named and parses them;
- follows `!include`, so a document can read other files the process can read
  — unless you [confine](#confining-reads) it;
- interpolates values, when you ask for interpolation, in tplx's sandbox;
- refuses to run a command or render a template file.

Configuration you wrote yourself can do more, and says so explicitly:

```python
from yaconfiglib import Loader, Policy

loader = Loader(policy=Policy.trusted())      # commands and templates on, sandbox off
```

## The policy

`Policy` is a frozen dataclass. Every field and its default:

| Field | Default | Allows |
| --- | --- | --- |
| `allow_commands` | `False` | command sources and script files; template engines that run a program |
| `allow_templates` | `False` | template files (`app.yaml.j2`) |
| `allow_includes` | `True` | `!include` / `!load` |
| `sandbox` | `True` | interpolation and template files run in tplx's sandbox; engines that cannot be sandboxed refused |
| `strict` | `False` | an undefined template name raises; an interpolation reference cycle raises |
| `confine_to` | `None` | every local file read inside these roots |
| `max_includes` | `1000` | includes one load may resolve |
| `max_depth` | `64` | how deep includes may nest |
| `max_bytes` | 64 MiB | bytes one load may read, all sources together |
| `command_timeout` | `60.0` | seconds before a command and its children are killed (`None`: no limit) |

```python
Policy()                                    # the untrusted default
Policy.untrusted(confine_to=True)           # the same, with changes
Policy.trusted(command_timeout=10)          # commands and templates on, sandbox off, with changes
Policy().replace(max_bytes=1_000_000)       # a copy with changes
```

A policy is given to a `Loader` (`policy=`, also per call) or to a
one-document function (`yaconfiglib.load(path, policy=...)`).

**A document can never change the policy.** An include mapping cannot carry a
policy field — any key outside the [include options](includes.md#forms) is
dropped with a WARNING — and every nested load, an include or a command's
output, runs under the policy of the load that reached it.

Every refusal is a `PolicyError` subclass — `CommandsDisabledError`,
`TemplatesDisabledError`, `ConfinementError`, `BudgetExceededError` — raised
**before** anything is read or run. `PolicyError` is also a `PermissionError`,
and so an `OSError`.

### Your own rules

`Policy` is a class to subclass. Three hooks are asked **after** the fields
have allowed something, so a subclass can only narrow; returning a falsy value
refuses:

| Hook | Asked about | Refusal |
| --- | --- | --- |
| `check_path(path)` | every path before it is read: a source, an include, a script, a glob pattern and each match — the pathapi `Path` that would be read | `ConfinementError` |
| `check_command(command)` | a command's text or a script's path, once `allow_commands` is on | `CommandsDisabledError` |
| `check_template(name, engine)` | a template file and its tplx engine class, once `allow_templates` allows it | `TemplatesDisabledError` |

`yaconfiglib.policies` has ready-made ones. Each overrides one hook and calls
`super()`, so they stack and nothing has to implement every check:

| Mixin | Narrows to | Setting |
| --- | --- | --- |
| `LocalPathsOnly` | local and in-memory paths (no remote pathapi `Path`) | — |
| `AllowedSuffixes` | file names ending in one of `suffixes` | `suffixes = {".yaml", ".json"}` |
| `AllowedCommands` | exactly the listed commands | `commands = {"vault read ..."}` |
| `AllowedEngines` | template files rendered by the listed tplx engines | `engines = {"jinja"}` |

The simplest way to use them is a list, applied to the default policy in that
order; `Mixin.using(...)` gives a mixin its setting:

```python
from yaconfiglib import Loader, policies

loader = Loader(
    policy=[policies.LocalPathsOnly, policies.AllowedSuffixes.using(suffixes={".yaml"})]
)
```

A class statement combines them with your own fields, and `policies.compose()`
does the same without one:

```python
class Production(policies.LocalPathsOnly, policies.AllowedCommands):
    commands = {"vault read -field=password secret/db"}

    def check_path(self, path):          # your own rule, on top of the mixins'
        return "secrets" not in path.parts and super().check_path(path)


loader = Loader(policy=Production(allow_commands=True))
Strict = policies.compose(policies.LocalPathsOnly, policies.AllowedSuffixes, suffixes={".json"})
```

`trusted()`, `untrusted()` and `replace()` keep the subclass.

## Commands

A command source — `cmd://`, `exec://` and `sh://` strings, `sources.Command`,
and any file ending in `.sh`, `.bat`, `.cmd` or `.ps1` — runs a program as a
side effect of loading. With `allow_commands` off, every one of them raises
`CommandsDisabledError` naming it, on every route: a top-level source, a glob
match, an `!include 'cmd://...'`, a script file in a directory you load with a
glob.

Allowed commands run with stdin closed and the policy's `command_timeout`;
a script's own path is passed to its interpreter, never parsed by a shell, so a
name containing `&`, `;` or a space cannot inject a second command. See
[Sources](sources.md#commands-and-scripts).

## Templates and the sandbox

- **Template files** render only with `allow_templates`, and a refused template
  is not even read. Under the sandbox, only engines tplx can sandbox render —
  Jinja, Liquid, Mustache, Handlebars, Go templates, shell and envsubst — in
  tplx's sandbox, where a template cannot reach Python internals and sees only
  plain data; Mako, Cheetah, ERB and EPP are refused. Without the sandbox any
  tplx engine renders, and one that runs a program (ERB, EPP) also needs
  `allow_commands`.
- **A template file's own includes** search only the confinement roots. Without
  roots, a sandboxed template includes nothing at all; a trusted one looks
  next to itself.
- **Interpolation** renders in tplx's sandbox while `sandbox` is on, which
  blocks the attribute traversal used for template injection and shows a value
  template only plain data. A value template can include no file, sandboxed or
  not.

The sandbox is the template engine's, not the operating system's: it does not
limit CPU time or memory. With `inject_env=True`, templates can read every
environment variable — including secrets you may not want a third-party
document to see — though not change one.

## Includes

`!include` is allowed by default because composing files is the common case,
and **by default it is not confined**: an absolute path or a `..` traversal
reaches any file the process can read, and a document can pull in any file
whose name a format claims:

```yaml
stolen: !include /home/app/.config/service/credentials.yaml
```

For a document you did not write, set `confine_to` — or, if it has no reason
to include anything, `Policy(allow_includes=False)`, which refuses every
include with `PolicyError`.

## Confining reads

With `confine_to` set, **every local file read** — top-level sources, includes
at any depth, glob matches, an open file's underlying file, an include inside
a command's output — must fall inside one of the roots, or `ConfinementError`
is raised **before the file is opened**:

```python
from pathlib import Path

from yaconfiglib import Loader, Policy

conf = Path("conf").resolve()
loader = Loader(base_dir=conf, policy=Policy(confine_to=[conf]))
```

`confine_to` takes:

- a sequence of roots — a file inside **any** of them is allowed;
- one string split on `os.pathsep`, like `PATH` (a string without a separator
  is one root);
- a single path object;
- `True` — the loader's `base_dir`, or the working directory when `base_dir`
  is `None`;
- `False` or `None` — off.

**Every root must be absolute**, or `ConfigTypeError` is raised when the load
runs: a relative root would silently mean "under whatever the working directory
is". Use `confine_to=True` for "wherever `base_dir` points". A whole UNC share
(`\\server\share`) is a valid root. Empty entries are dropped, so `[""]` is an
**empty allowlist**, which refuses every read rather than meaning "off".

When `confine_to` is `None`, the `YACONFIGLIB_CONFINE_TO` environment variable
is read by the same rules: unset means no confinement, and set but empty
refuses every local read. An explicit `confine_to` ignores the variable, so the
environment can never widen an allowlist your code set.

How the check works:

- **It is lexical.** The path is made absolute, `..` removed, case folded where
  the platform folds it, a `\\?\` prefix stripped, and containment tested with
  `os.path.commonpath` — never a string prefix, which would accept
  `/srv/confidential` under the root `/srv/conf`.
- **Symlinks are not resolved.** A link inside a root may point outside it:
  whoever can write into a root could equally put the configuration itself
  there. The trust boundary is write access to the roots; what confinement
  closes is a hostile **document**.
- **What has no location is exempt**: in-memory text, Python data, a
  `MemPath`, a stream without a real file name. A command source is governed
  by `allow_commands`, not by the roots.
- **A non-local path object** (a remote pathapi `Path`) is inside no local root
  and is refused while confinement is on. A document cannot produce one — every
  `str` it names is a local path — so remote paths come only from the caller,
  who can vet them, and anything else, with a [policy hook](#your-own-rules)
  (`policies.LocalPathsOnly`).
- **It confines reads only.** `dump()` writes wherever it is told.

### Glob patterns

A pattern is checked twice, and neither check waits for a file to be read: the
directory before its first wildcard must be inside a root, so a pattern aimed
elsewhere is refused **without listing anything**; and each match is checked
before it is read, because a wildcard can leave a root after a legal prefix
(`*/../../elsewhere/*`).

A refused match is reported by its **pattern**, never by the file the
expansion found — that is a name the document did not write, and reporting it
would turn a pattern into a way to discover file names. The path is logged at
DEBUG only. One residual: for a pattern whose wildcards leave a root after a
legal prefix, whether anything matched is observable (a refusal versus an
empty result), though no name is disclosed and nothing outside the roots is
read.

### Confinement needs the sandbox

`confine_to` confines the reads yaconfiglib performs **for** a document. It
cannot contain code the document gets to run: a non-sandboxed template
expression can open files with no path for the check to see, and a command can
read anything. Keep `sandbox` on and commands off for any document you confine
— which the default policy does.

## The budget

`max_includes`, `max_depth` and `max_bytes` bound one load, all sources and
includes together; going past one raises `BudgetExceededError`, which
`on_error` is never offered. Include cycles are detected separately, by the
canonical path, and raise `ConfigValueError`. Interpolation visits each
distinct node once, so a document of aliased YAML anchors costs time in its
distinct nodes, not in its expanded size; `deep` list merging likewise
compares items once per node.

## Logging and secrets

- **Errors never carry document values.** A `ParseError` holds a position and
  a message that may name structure (a key, a section, a tag, an alias) but
  never a value, a line or a snippet; the parser's own exception is not kept in
  its chain.
- **Rendered values are never logged.** Interpolation's DEBUG records name the
  key and the template (`{{ env.DB_PASSWORD }}`), not what it produced.
- **`on_error=True`'s WARNING** names the source, the phase and the error's
  type — never its message — so it is safe in a shipped log.
- **A failing command's stderr tail is in its exception message**, so it
  reaches tracebacks and error reports; a tool that prints credentials to
  stderr when it fails exposes them there. Its stdout is only on `.output`.
- DEBUG records may contain source paths, template text and full error
  messages: treat DEBUG logs from this library as sensitive as the
  configuration.

## Untrusted configuration checklist

```python
from pathlib import Path

from yaconfiglib import Loader, Policy

conf = Path("conf").resolve()                 # roots must be absolute

loader = Loader(
    base_dir=conf,
    policy=Policy(confine_to=[conf]),         # the untrusted defaults, plus confinement
    interpolate=True,                         # sandboxed, by the default policy
)
config = loader.load("tenant.yaml", format="yaml")
```

That covers, including through nested includes and per-call overrides:

- **commands** — refused on every route, including an include and a script
  file a glob matches;
- **template files** — refused;
- **template injection** in interpolated values — sandboxed;
- **include options** — a document cannot re-enable commands, turn off the
  sandbox or change the roots;
- **file reads** — an absolute path, a `..` traversal or a glob that leaves
  `conf` raises `ConfinementError` before anything is opened;
- **size** — includes, nesting depth and bytes read are bounded;
- **format selection** — `format="yaml"` stops a top-level file name from
  choosing another parser, and documents can select only built-in and
  code-registered formats.

Still up to you:

- a symlink inside `conf` pointing outside it is followed, so anyone who can
  write into `conf` can still point a configuration elsewhere;
- `env` is only available to templates with `inject_env=True` — leave it off
  for documents that should not see the environment;
- `Policy(allow_includes=False)` if the documents have no reason to include
  anything, and a lower `max_bytes` if they are small;
- do not return, echo or log a loaded result verbatim, and run the process with
  only the file permissions it needs.
