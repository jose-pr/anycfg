# Merging

When `Loader.load()` receives several sources, it loads them in order and
merges each document into the running result with a **merge strategy**. That
is what makes layered configuration work: defaults, then an environment, then
local overrides.

```python
from yaconfiglib import Loader

loader = Loader(merge="deep")
config = loader.load("base.yaml", "production.yaml", "local.yaml")
```

Later sources take precedence. With `base.yaml` holding
`{"server": {"host": "0.0.0.0", "port": 8080}}` and `production.yaml` holding
`{"server": {"port": 443}}`, a `deep` merge gives
`{"server": {"host": "0.0.0.0", "port": 443}}`.

## Strategies

A strategy is chosen by name, or is any callable (see
[Custom strategies](#custom-strategies)):

| Strategy | Mappings | Lists | Leaves |
| --- | --- | --- | --- |
| `simple` (default) | top-level keys update; nested mappings replace | replaced | replaced |
| `substitute` | merged recursively | replaced | replaced |
| `deep` | merged recursively | extended with new items | replaced |

- **`simple`** — for configurations that override whole sections at a time.
- **`substitute`** — nested mappings merge key by key; a list in a later
  document replaces the earlier one.
- **`deep`** — nested mappings merge, and a list is extended with the items of
  the later list that are not already present, in that list's order. An item
  counts as present only when an existing item has the **same type** and is
  equal, so `True` is not a duplicate of `1`; unhashable items (lists inside
  lists) are compared by structure. Mappings in a list are never
  de-duplicated: they are always appended, unless `mergelists` says otherwise.

```python
base = {"server": {"host": "0.0.0.0", "port": 8080}, "features": ["a", "b"]}
prod = {"server": {"port": 443}, "features": ["b", "c"]}

for name in ("simple", "substitute", "deep"):
    print(name, Loader(merge=name).load(base, prod))
# simple {'server': {'port': 443}, 'features': ['b', 'c']}
# substitute {'server': {'host': '0.0.0.0', 'port': 443}, 'features': ['b', 'c']}
# deep {'server': {'host': '0.0.0.0', 'port': 443}, 'features': ['a', 'b', 'c']}
```

With `deep`, `merge_options={"mergelists": True}` also merges two mappings at
the **same index** of two lists when they share a key, instead of appending the
later one:

```python
loader = Loader(merge="deep", merge_options={"mergelists": True})
print(loader.load({"hosts": [{"name": "a", "port": 80}]}, {"hosts": [{"name": "a", "port": 8080}]}))
# {'hosts': [{'name': 'a', 'port': 8080}]}
```

### Rules every strategy follows

- **A leaf replaces.** A leaf is anything that is not a mapping or a list:
  strings, numbers, dates, `Decimal`s, sets, objects a format produced.
- **A shape change replaces.** A mapping overridden by a number, or a string by
  a list, takes the later value. No combination of types raises.
- **A mapping overridden by a non-empty list of mappings folds them in**, in
  order (`substitute` and `deep`). Any other list replaces it, so
  `section: []` clears a section.
- **Copy-on-write.** A strategy never modifies its inputs. That is what makes
  YAML anchors and `<<:` merge keys safe to override: several keys can share one
  mapping, and overriding it for one of them leaves the others alone. A
  self-referencing document merges without recursing forever.

```yaml
defaults: &defaults
  db: {host: shared, pool: 5}

development:
  <<: *defaults        # shares one `db` mapping with production

production:
  <<: *defaults
```

An override layer setting `production.db.host` leaves `development.db.host` as
`shared`.

The result may still **share** unchanged parts with the sources, so deep-copy
it before mutating it if you need the sources intact. Python data passed as a
source is deep-copied when read, so your own objects are never reached.

## `None` and `DELETE`

**`None` in an overriding document means "no opinion"**: the earlier value is
kept, in every strategy. A key the earlier documents did not have gets `None`.

To **remove** a key, set it to `yaconfiglib.DELETE` — in YAML, the `!delete`
tag:

```yaml
# local.yaml
cache: !delete       # removes `cache`, whatever base.yaml set
debug: ~             # keeps base.yaml's `debug`
```

```python
from yaconfiglib import DELETE, Loader

print(Loader().load({"cache": {"size": 10}, "debug": True}, {"cache": DELETE, "debug": None}))
# {'debug': True}
```

`DELETE` markers left in the result — a key deleted that no earlier document
had — are pruned, so they never reach your code.

### Making `None` a value: `nulls="replace"` and `INHERIT`

When `None` should *set* a key, pass the merge option `nulls="replace"`. "No
opinion" is then said explicitly with `yaconfiglib.INHERIT` — `!inherit` in
YAML — which keeps the earlier value in either mode (a key no earlier document
set is left out):

```yaml
# local.yaml
password: ~          # with nulls="replace": password is None
debug: !inherit      # keeps base.yaml's debug
```

```python
loader = Loader(merge="deep", merge_options={"nulls": "replace"})
```

The option also goes in an include's `merge_options:`.

## Collecting instead of merging

`collect=` gathers the documents rather than merging them:

| `collect=` | Result |
| --- | --- |
| `"last"` | the last document, unmerged |
| `"list"` | every document, in load order |
| `"hash"` | `{key(name, document): document}` |

```python
loader = Loader(base_dir="services", collect="hash")
services = loader.load("*.yaml")          # {'api': {...}, 'billing': {...}, 'web': {...}}
```

The default key is the source's file stem. Two sources with the same key do not
both survive — the later replaces the earlier, with a WARNING — and that is
exactly what happens to `services/*/config.yaml`, where every stem is
`config`. Key on something else with `key=`, a callable `(name, document) ->
key`, where `name` is the source's name (a file path, as a string):

```python
from pathlib import PurePath

loader = Loader(collect="hash", key=lambda name, document: PurePath(name).parent.name)
services = loader.load("services/*/config.yaml")   # {'api': ..., 'billing': ..., 'web': ...}
```

With the default key, a source with no name — a mapping passed as data, or
`Text`/`Data` with an empty `name` — cannot be keyed and raises
`ConfigValueError`; give it a `name`, or pass your own `key=`.

## Per-call settings

Every `Loader` setting except `base_dir`, `bound_loops` and `formats` can be
overridden for one call by passing it to `load()` or `load_all()`:

```python
loader = Loader(merge="simple")
result = loader.load("a.yaml", "b.yaml", merge="deep")    # deep, this call only
```

An unknown keyword raises `TypeError` rather than being ignored.
`load()` also takes:

- **`default=`** — returned when **no** source loads: nothing matched, or every
  source failed under `on_error`. It is never merged with a document.
- **`flatten=True`** — flattens the result by one level: a mapping of mappings
  becomes one mapping, a list of lists one list; `None` members are skipped,
  anything else raises `ConfigTypeError`. Useful with `collect="list"`.
- **`transform=`** — `(document, name) -> document`, applied to each document
  before it is combined.

```python
def tag(document, name):
    return {**document, "loaded_from": name}

Loader().load("base.yaml", transform=tag)
```

## When a source fails

By default the first failure raises. `on_error=` decides otherwise:

- `on_error=True` skips every failing source and logs a WARNING naming the
  source, the phase and the error's **type** — never its message, which can
  quote configuration.
- A callable `(LoadEvent) -> bool` decides per failure; returning `True` skips
  it.

```python
from yaconfiglib import LoadEvent, Loader

def skip_missing(event: LoadEvent) -> bool:
    return isinstance(event.error, FileNotFoundError)

config = Loader(on_error=skip_missing).load("base.yaml", "optional.yaml")
```

A `LoadEvent` has `error` (the exception, unchanged), `phase`, `path` and
`key`. `phase` is:

| Phase | What failed | Skipping it |
| --- | --- | --- |
| `"load"` | reading or parsing `path` | drops that source |
| `"include"` | a file `path` includes | see [Includes](includes.md#when-an-include-fails) |
| `"merge"` | merging the document named by `path` | drops that document |
| `"interpolate"` | rendering the value at `key` | keeps that value's template text |
| `"glob"` | listing the directory `path` while expanding a pattern | keeps the rest of the pattern |

`BudgetExceededError` is never offered: a load that went over its budget stops.

## Custom strategies

A strategy is any callable `(a, b, *, memo=None, **options) -> merged`, where
`b` is the later document. Pass it as `merge=`, or register it under a name —
which is also what lets a document select it from an include's `merge:` key:

```python
from collections.abc import Mapping

from yaconfiglib import Loader, register_merge


def concat(a, b, *, memo=None, **options):
    """Like deep, but lists concatenate and keep duplicates."""
    if b is None:
        return a
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        merged = dict(a)
        for key, value in b.items():
            merged[key] = concat(a[key], value, memo=memo, **options) if key in a else value
        return merged
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    return b


register_merge("concat", concat)
print(Loader(merge="concat").load({"tags": ["a"]}, {"tags": ["a", "b"]}))   # {'tags': ['a', 'a', 'b']}
```

Keep the built-in rules where they apply: never modify `a` or `b`, treat a
`None` in `b` as "keep `a`", and leave `DELETE` markers in place (the loader
prunes them). `memo` is internal to the built-in strategies — it keeps aliased
nodes aliased and stops self-referencing documents from recursing — and a
strategy must pass it into every nested call it makes. `merge_options` are
passed as `**options`.

`register_merge` raises `ValueError` for a name that is taken, unless
`replace=True`; `merge_strategies()` lists the names. The built-in strategies'
protocol is `yaconfiglib.Merge`.

## Typed merge

`typed_merge(cls, *layers)` merges layers into one instance of `cls`, field by
field, each field through its own annotation. It is what
[`load(..., into=cls)`](models.md) runs on, with one layer.

```python
import dataclasses
from typing import List

from yaconfiglib import typed_merge


@dataclasses.dataclass
class Server:
    host: str = "localhost"
    port: int = 80
    tags: List[str] = dataclasses.field(default_factory=list)


base = {"host": "example.org", "port": "8080", "tags": ["a"]}
override = {"port": 443, "tags": ["b"], "host": None}
print(typed_merge(Server, base, override))
# Server(host='example.org', port=443, tags=['b'])
```

- **`None` layers and `None` values are skipped** at every level, so a later
  `None` never overrides an earlier value. No layers, or only `None`, give
  `None`.
- **Mappings, dataclasses and namespaces merge field by field.** A layer may be
  a mapping or an object of the target type; a dataclass instance used as a
  layer contributes every field, defaults included.
- **Sequences take the last layer's value**, every item coerced through the
  item type. `Tuple[A, B]` coerces by position and raises `ConfigTypeError` on a
  length mismatch; a `NamedTuple` is rebuilt with its constructor; `Dict[K, V]`
  coerces keys as it collects them, so `"80"` and `80` are one key.
- **A layer of another shape replaces** everything before it. When the **last**
  layer cannot be built into `cls`, `ConfigTypeError` is raised.
- **`extra=`** decides what a key the class does not accept does: `"ignore"`
  (the default) drops it, `"forbid"` raises `ConfigTypeError` naming it. A
  constructor taking `**kwargs` accepts every key.
- **`init=False`** builds instances without calling `__init__`.

Coercion **never loses data**:

| Target | Accepts |
| --- | --- |
| `int` | an `int`, a whole-number float, an integer string — never `1.9` |
| `bool` | a `bool`; the strings `true/yes/on/1` and `false/no/off/0`, case- and space-insensitive — anything else is `ConfigValueError` |
| `float`, `str`, ... | what the type's constructor accepts |
| `date`, `datetime`, `time` | the object, or an ISO 8601 string |
| an `Enum` | a member's value, then a member's name |
| a union | a value one member already accepts, unchanged; otherwise coerced through the first non-`None` member |
| `Any` | the last value, uncoerced |

`Optional[X]`, `X | None` and `Annotated[X, ...]` are unwrapped. When
`typing.get_type_hints` cannot resolve every annotation of a class, each
annotation is resolved on its own, and the ones that resolve are used.

A value that does not fit raises with its location attached — readable as
`error.config_key` and `error.config_model`, and in the message:

```text
cannot interpret 'abc' as an int [at db.port (Settings)]
```

List items appear by index (`hosts[1].port`), and the model named is the
outermost one, the one the key path is relative to.

### Hooks

A type can take part in its own building. Every hook is looked up on the
unwrapped type, so an `Optional[T]` or `Annotated[T, ...]` field finds it; a
type without one gets the coercions above.

| Hook | Does |
| --- | --- |
| classmethod `_load_(cls, value)` | builds the type from one value — see [Building a type from a value](models.md#building-a-type-from-a-value) |
| `_dump_(self)` | returns the instance as plain data, for `dehydrate` and `dump` |
| classmethod `__merge__(cls, *objects, init=True)` | takes over merging the layers entirely |
| `_parse_<field>(value)` | coerces one field — see [below](#coercing-one-field) |

With `_load_`, several mapping layers are deep-merged first and the hook
receives the result as plain data:

```python
class Pair:
    def __init__(self, a, b):
        self.a, self.b = a, b

    @classmethod
    def _load_(cls, value):
        return cls(**value)


pair = typed_merge(Pair, {"a": 1, "b": 2}, {"b": 3})
print(pair.a, pair.b)    # 1 3
```

### Taking over a type's merge

A classmethod `__merge__(cls, *objects, init=True)` replaces `typed_merge`'s
handling of a type entirely. The common case — "this is a fully built object, take the last one" — is
packaged as the `OpaqueMerge` mixin and the `opaque` decorator:

```python
import argparse
import ipaddress

from yaconfiglib import OpaqueMerge, opaque


class Zone(OpaqueMerge, argparse.Namespace):
    """Normalized in its own __init__; never re-merged field by field."""


@opaque
class Network:                     # the same, without changing the bases
    def __init__(self, cidr):
        self.cidr = ipaddress.ip_network(cidr)
```

### Coercing one field

A `_parse_<field>(value)` method on a layer object coerces that field's value
as it is collected. `TypedNamespace`, an `argparse.Namespace`, applies the same
hooks at construction, so a built instance is already normalized:

```python
from yaconfiglib import TypedNamespace, typed_merge


class ServerConfig(TypedNamespace):
    def _parse_ports(self, value):
        return [int(port) for port in value.split(",")]


print(ServerConfig(ports="80,443").ports)                                     # [80, 443]
print(typed_merge(ServerConfig, {"ports": "80"}, {"ports": "8080,8443"}).ports)   # [8080, 8443]
```

Every value is parsed **exactly once**, so a parser need not be idempotent:
merging into a `TypedNamespace` target applies the target's hooks to raw layers
such as a mapping, skips them for a layer that is already a `TypedNamespace`,
and assembles the result without calling `__init__`. Put anything else a
subclass's `__init__` does into a hook or `__merge__`.
