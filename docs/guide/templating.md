# Templating

yaconfiglib renders templates in two places, and they are separate features:

- **Interpolation** renders template text inside the *values* of the loaded,
  merged document, so one value can refer to another. It runs at value time,
  after merging, with the engine `interpolate=` names.
- **Template files** — `app.yaml.j2`, `app.toml.liquid` — are rendered as a
  whole *before* they are parsed, so a template can produce the structure
  itself.

Both run on [tplx](https://github.com/jose-pr/tplx) — except
`interpolate="vars"`, which is built in — and both are governed by the load's
[`Policy`](security.md). The `templates` extra
(`pip install "yaconfiglib[templates]"`) installs tplx 0.2+ with Jinja, the
default engine; another engine needs its own tplx extra (`pip install
"tplx[liquid]"`). Without the engine's extra, using it raises
`MissingDependencyError` naming what to install.

## Interpolation

`Loader(interpolate=True)` renders the templates in the merged document's
strings, with the document's own top-level keys as the template's names:

```yaml
# app.yaml
host: localhost
url: "http://{{ host }}:{{ port }}"
port: 8080
```

```python
from yaconfiglib import Loader

config = Loader(interpolate=True).load("app.yaml")
print(config["url"])      # http://localhost:8080
```

Rendering happens **once, after every source has been merged**, so a value
from one layer can refer to a key from another, and a value pulled in with
`!include` sees the whole document. Nothing is rendered twice: a rendered value
that happens to contain `{{` stays as it is.

Interpolation is a `Loader` setting, off by default. The one-document
functions (`yaconfiglib.load()`, `loads()`) never interpolate.

### References between values

A value may refer to any top-level key, in any order. Each top-level key is
rendered after the keys it refers to, so chains resolve completely:

```yaml
base_path: /srv/app
error_log: "{{ log_path }}/error.log"     # -> /srv/app/logs/error.log
log_path: "{{ base_path }}/logs"
```

Nested values are reached through their parent: `{{ database.host }}`. A value
may refer into its own top-level key — `database.url: "{{ database.host }}"` —
and then sees that key's values as loaded, so a sibling that is itself a
template is not rendered yet:

```python
db = {"host": "h", "url": "pg://{{ db.host }}/app", "name": "{{ 'app' }}", "dsn": "{{ db.name }}"}
print(Loader(interpolate=True).load({"db": db})["db"])
# {'host': 'h', 'url': 'pg://h/app', 'name': 'app', 'dsn': "{{ 'app' }}"}
```

Two keys that refer to each other cannot both resolve. By default each then
renders against what the other holds at that moment; with
`Policy(strict=True)` the cycle raises `ConfigValueError`.

### Keeping types

A string that is nothing but one `{{ expression }}` — whitespace around it
allowed — evaluates to the expression's own value, so it keeps its type:

```yaml
port: "{{ 8000 + 80 }}"       # 8080, an int
debug: "{{ false }}"          # False, a bool
hosts: "{{ ['a', 'b'] }}"     # a list
label: "port {{ 8000 + 80 }}" # "port 8080", a string
```

### The environment

With `inject_env=True`, templates also see `env`, a read-only snapshot of
`os.environ`:

```yaml
database_url: "{{ env.DATABASE_URL }}"
```

```python
config = Loader(interpolate=True, inject_env=True).load("app.yaml")
```

`env` wins over a top-level key of the same name.

### Strict mode

By default an undefined name renders as an empty string. With
`Policy(strict=True)` it raises instead, which catches a misspelled key early:

```python
from yaconfiglib import Loader, Policy

loader = Loader(interpolate=True, policy=Policy(strict=True))
```

### Failures

Every interpolation failure — a syntax error, an undefined name under strict
mode, an exception inside an expression such as `{{ 1 / 0 }}` — raises
`yaconfiglib.TemplateError`, with the engine's own exception as `__cause__`
when there is one (tplx's wrapper around it is removed), and the failing
value's key path in the message and on `error.config_key`:

```text
ZeroDivisionError: division by zero [at database.timeout]
```

Under `on_error`, a failure costs only that value: it keeps its template text
as written and the rest of the document still renders. The predicate form is
offered each failure with `phase="interpolate"` and `key` the key path:

```python
def skip_banner(event):
    return event.phase == "interpolate" and event.key == ("ui", "banner")

config = Loader(interpolate=True, on_error=skip_banner).load("app.yaml")
```

Messages and DEBUG logs name the **template**, never the rendered value, since
a template such as `{{ env.DB_PASSWORD }}` exists to produce a secret.

### What interpolation guarantees

- **Copy-on-write.** The loaded data is never modified; containers keep their
  types (a `tuple` stays a `tuple`), and a document with no template marker is
  returned untouched.
- **Templated keys.** A key may be a template too. Keys render after values; a
  key that renders to one that already exists raises `ConfigValueError` naming
  both, and a skip under `on_error` keeps that entry under its template text.
- **Only strings holding a template marker** — `{{`, `{%` or `{#` — are
  rendered; every other value is returned as it is.
- **Aliases are rendered once.** A YAML anchor used in many places is one
  object, rendered once, and still one object afterwards.

### The sandbox

Interpolation runs in tplx's sandbox unless the policy turns the sandbox off
(`Policy(sandbox=False)`, or `Policy.trusted()`). Sandboxed, a value template
cannot reach Python internals — what a value written by someone else needs —
and sees **plain data only**: a mapping or a list as itself, and any other
value that is not a plain scalar (a date, say) as its text:

```python
import datetime

doc = {"released": datetime.date(2024, 1, 2), "label": "v1 ({{ released }})", "db": {"port": 5432}, "port": "{{ db.port }}"}
print(Loader(interpolate=True).load(doc)["label"])    # v1 (2024-01-02)
```

Sandboxed or not, a value template can include no file. The sandbox is the
template engine's, not the operating system's: it does not limit time or
memory.

### Choosing the engine

`interpolate=` names what renders the values:

| `interpolate=` | Renders | Needs |
| --- | --- | --- |
| `False` (default) | nothing | — |
| `True` | Jinja: `{{ db.host }}`, filters, expressions | `templates` extra |
| `"liquid"`, `"go"`, `"mustache"`, `"envsubst"`, ... or a tplx engine class | that engine's syntax | `templates`, plus the engine's tplx extra |
| `"vars"` | `${db.host}`, `${hosts.0}` references only | nothing |
| an object with `render` and `references` | whatever it does | — |

The choice is checked when the `Loader` is built: an unknown engine, a missing
extra, an engine the sandbox cannot hold (mako, Cheetah, ERB, EPP) or one that
runs a program without `allow_commands` raises there, not at the first value.
Only strings holding the engine's own syntax are rendered, so with
`interpolate="envsubst"` a `{{ ... }}` value is left alone.

`"vars"` is for documents that only need to refer to other values. It runs no
code and reads no attribute — each step of a reference is a mapping key or a
list index — so it needs neither tplx nor a sandbox:

```python
from yaconfiglib import Loader

doc = {"db": {"host": "db", "ports": [5432]}, "url": "pg://${db.host}:${db.ports.0}/app", "port": "${db.ports.0}"}
print(Loader(interpolate="vars").load(doc))
# {'db': {'host': 'db', 'ports': [5432]}, 'url': 'pg://db:5432/app', 'port': 5432}
```

A value that is exactly one reference keeps its type, `$${` writes a literal
`${`, and an undefined reference renders empty — or raises with
`Policy(strict=True)`.

An interpolator of your own is any object with `references(text) ->
frozenset[str]` (the top-level names a template reads, used to order
rendering) and `render(text, scope) -> value`; an optional
`is_template(text) -> bool` says which strings it wants, and without it
strings holding `{{`, `{%` or `{#` are offered:

```python
import re

from yaconfiglib import Loader


class NameInterpolator:
    """`{{ name }}` substitution only: no expressions."""

    pattern = re.compile(r"\{\{\s*([A-Za-z_]\w*)\s*\}\}")

    def references(self, text):
        return frozenset(self.pattern.findall(text))

    def render(self, text, scope):
        return self.pattern.sub(lambda match: str(scope[match.group(1)]), text)


loader = Loader(interpolate=NameInterpolator())
print(loader.load({"host": "db", "url": "pg://{{ host }}/app"}))   # {'host': 'db', 'url': 'pg://db/app'}
```

An exception it raises becomes a `TemplateError`, like an engine's.

## Template files

A source named `<name>.<format>.<engine suffix>` is rendered by the tplx
engine that claims the suffix, then the result is parsed as `<format>`:

```text
settings.yaml.j2      ->  rendered by Jinja   ->  parsed as YAML
settings.toml.liquid  ->  rendered by Liquid  ->  parsed as TOML
```

Keep the format suffix in front of the engine's: `settings.yaml.j2`, not
`settings.j2` — or name the rendered format with `format=` on the load, which
for a template file means "parse what it renders as this"; the template still
renders. **Template files are refused unless the policy allows them** —
`Policy(allow_templates=True)`, or `Policy.trusted()` — with
`TemplatesDisabledError`, before the file is even read.

```jinja
{# settings.yaml.j2 #}
replicas: {{ 3 if env.get("STAGE") == "production" else 1 }}
source: {{ filename }}
```

```python
from yaconfiglib import Loader, Policy

loader = Loader(policy=Policy(allow_templates=True), inject_env=True)
config = loader.load("settings.yaml.j2")
```

The template sees:

| Name | Value |
| --- | --- |
| `filename` | the template's file name (`settings.yaml.j2`) |
| `pathname` | its path: a `PurePosixPath` when trusted; its text under the sandbox, which shows a template only plain data |
| `env` | with `inject_env=True`, a read-only snapshot of the environment |

Under the sandbox, write `{{ filename }}` rather than `{{ pathname.name }}`:
`pathname` is text there, and an attribute of it is undefined.

Template files work wherever a source does, including as an `!include` target,
and a YAML template's own `!include`s resolve next to the template.

What renders depends on the sandbox, and every refusal happens before the file
is read:

- **Sandbox on** (the default): every engine tplx can sandbox renders, in
  tplx's sandbox — Jinja, Liquid, Mustache, Handlebars, Go templates, shell
  and envsubst. Mako, Cheetah, ERB and EPP cannot be sandboxed and are refused
  with `TemplatesDisabledError`.
- **Sandbox off** (`Policy.trusted()`): every engine tplx knows renders. One
  that runs a program (ERB, EPP) also needs `allow_commands`, or
  `CommandsDisabledError` is raised.

Either way an engine needs its tplx extra, or `MissingDependencyError` names
it.

A template's own includes (`{% include %}` and the like) search the
[confinement roots](security.md#confining-reads). Without roots, a sandboxed
template includes nothing and a trusted one looks next to itself.

`Policy(strict=True)` makes an undefined name in a template file raise, as it
does for interpolation.

A failure while rendering is a `TemplateError`, whose `__cause__` is the
engine's own exception when there is one. A parse error in the
**rendered** text is the format's `ParseError`, with a `render` frame naming
the template — the line number then refers to the rendered output.
