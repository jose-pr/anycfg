# Typed results

A load returns plain data — `dict`, `list` and scalars. Pass `into=` to get an
instance of a class instead, with every field coerced through its annotation:
validation, defaults and editor completion instead of string keys on an
untyped mapping.

```python
import dataclasses

import anycfg


@dataclasses.dataclass
class Database:
    host: str
    port: int = 5432


db = anycfg.load("db.json", into=Database)
```

`into=` is accepted by `load()`, `loads()`, `load_all()`, `loads_all()`,
`Loader.load()` and `Loader.load_all()` (the last three apply it to each
document). The same engine is
available on its own as `anycfg.hydrate(cls, data)`:

```python
from anycfg import hydrate

print(hydrate(Database, {"host": "db", "port": "6432"}))   # Database(host='db', port=6432)
```

## The type steers the parse

YAML 1.1 reads a plain (unquoted) `NO` as `False`, `1.10` as the float `1.1`
and `0755` as the octal integer `493` — a classic source of configuration bugs.
**When you load `into=` a type, the type decides instead.** While loading, YAML
keeps every plain scalar it read as a bool, number or date **beside the text
that was written**, and each field takes what its annotation asks for:

```python
import dataclasses
import datetime
from typing import Any

import anycfg

document = """
country: NO
version: 1.10
mode: 0755
enabled: yes
port: 8080
released: 2024-01-02
extra: on
"""


@dataclasses.dataclass
class Release:
    country: str
    version: str
    mode: str
    enabled: bool
    port: int
    released: datetime.date
    extra: Any = None


print(anycfg.loads(document, format="yaml", into=Release))
# Release(country='NO', version='1.10', mode='0755', enabled=True, port=8080, released=datetime.date(2024, 1, 2), extra=True)
```

- A **`str` field gets the text as written**: `NO` stays `"NO"`, `1.10` stays
  `"1.10"`, `0755` stays `"0755"`. A `Decimal` field also starts from the text,
  so `0.10` keeps its digits.
- **Any other type is built from the value YAML read** (`enabled: bool` gets
  `True`, `level: 1` an `IntEnum` member, `count: 1.0` the int `1`), and from the
  text only when that value does not convert. A type with a
  [`_load_` hook](#building-a-type-from-a-value) gets the text.
- **Places without type information** — `Any`, `object`, an unannotated `dict`
  or `list`, keys the class does not declare — get the value YAML read.
- **Keys are never affected**. A **union** picks the member holding the read
  value, then the one holding the text, so `Union[int, str]` keeps `yes` as
  `"yes"`.
- A **Pydantic model** gets the text for a field annotated `str` and the read
  value everywhere else, then validates it.

The text is for the type only. **Merging, includes, `transform`, `key=` and the
duplicate-key check see the values YAML read**, exactly as without `into=`, so
adding `into=` never changes which document wins or what an include does.

**Without `into=`, nothing changes**: values are exactly what the format read.
So `anycfg.load("app.yaml")` still returns `{'country': False, ...}` —
quote such values, or load into a type.

## Coercion

Formats such as INI, dotenv and the environment have only strings, and a
command or a JSON file may write a number as text. Hydration coerces each value
to its field's annotation, and **never loses data** doing it: `"8080"` becomes
`8080`, `"false"` becomes `False`, an ISO string becomes a `date`, but `1.9`
for an `int` field raises rather than truncating, `True` for an `int` field
raises rather than becoming `1`, and `"maybe"` for a `bool` raises rather than
becoming `True`. A `Literal[...]` takes only its values, and a `NewType` coerces
as its base type. The full table is in
[Typed merge](merging.md#typed-merge).

That makes the environment usable as typed configuration:

```python
import dataclasses
from typing import Optional

from anycfg import Loader
from anycfg.sources import Env


@dataclasses.dataclass
class Settings:
    debug: bool = False
    workers: int = 1
    region: Optional[str] = None


environ = {"APP_DEBUG": "yes", "APP_WORKERS": "4"}
settings = Loader().load(Env("APP_", lowercase=True, environ=environ), into=Settings)
print(settings)    # Settings(debug=True, workers=4, region=None)
```

## What `into=` accepts

### Dataclasses

Fields are built from the document's keys:

- a key the class does not accept is **dropped** by default; pass
  `extra="forbid"` to `hydrate` to raise `ConfigTypeError` naming it;
- a missing key takes the field's default; a missing required field raises
  `ConfigTypeError`, naming the model;
- a field annotated with another dataclass (or `Optional[...]` of one) is
  built recursively, and so are lists and dicts of them (`List[Host]`,
  `Dict[str, Host]`);
- `field(init=False)` fields are left to `__post_init__`.

```python
from typing import List


@dataclasses.dataclass
class Host:
    name: str
    port: int = 80


@dataclasses.dataclass
class App:
    name: str
    hosts: List[Host] = dataclasses.field(default_factory=list)


app = hydrate(App, {"name": "web", "hosts": [{"name": "a", "port": "8080"}, {"name": "b"}]})
print(app.hosts)   # [Host(name='a', port=8080), Host(name='b', port=80)]
```

### `TypedDict` and mapping types

A `TypedDict` comes back as a `dict` with its declared keys coerced; other
keys are kept with the value the format read. `dict` subclasses and
`Dict[str, int]` are built from the coerced items.

```python
from typing import TypedDict


class Server(TypedDict):
    host: str
    port: int


print(anycfg.loads("host: example.org\nport: '8080'\n", format="yaml", into=Server))
# {'host': 'example.org', 'port': 8080}
```

### Ordinary classes

Any other class is built from a mapping by passing its fields to the
constructor as **keywords**, coerced through the class's annotations or, when
it declares none, its constructor's. A constructor taking `**kwargs` receives
every key; a constructor that refuses the data raises `ConfigTypeError`.

```python
class Endpoint:
    def __init__(self, host: str, port: int = 80):
        self.host, self.port = host, port


endpoint = hydrate(Endpoint, {"host": "example.org", "port": "8443"})
print(endpoint.host, endpoint.port)    # example.org 8443
```

### Namespaces

`argparse.Namespace` and `TypedNamespace` take the keys as attributes; a
`TypedNamespace` applies its `_parse_<field>` hooks (see
[Coercing one field](merging.md#coercing-one-field)).

### Pydantic

A Pydantic model (v1 or v2) is validated by Pydantic itself, so its own
coercion, validators and errors apply — and it receives the scalars' texts:

```python
from pydantic import BaseModel

import anycfg


class DBConfig(BaseModel):
    host: str
    port: int


db = anycfg.load("config.yaml", into=DBConfig)
```

Pydantic stays optional: anycfg never imports it, and recognizes a model
only through a Pydantic that your code has already imported.

## Building a type from a value

A field whose type is not a container, a dataclass or a scalar — an IP
network, a URL, a size with units — is built by its classmethod
**`_load_(cls, value)`**. A scalar arrives as the **text written in the
document** when that is known (otherwise the parsed scalar), a mapping or list
as plain data, and several mapping layers are deep-merged first. The method
**`_dump_(self)`** is the inverse: it returns the instance as plain data, for
writing.

```python
import ipaddress


class Network:
    def __init__(self, cidr):
        self.cidr = ipaddress.ip_network(cidr)

    @classmethod
    def _load_(cls, value):
        return cls(value)

    def _dump_(self):
        return str(self.cidr)


@dataclasses.dataclass
class Lan:
    subnet: Network
    gateway: str


lan = anycfg.loads("subnet: 10.0.0.0/8\ngateway: 10.0.0.1\n", format="yaml", into=Lan)
print(lan.subnet.cidr.num_addresses)    # 16777216
```

Both hooks are looked up on the unwrapped type, so an `Optional[Network]`
field finds them too.

## Writing typed results back

`anycfg.dehydrate(obj)` turns typed data back into plain data a format
can write — the inverse of `hydrate`:

- a type's `_dump_()` result;
- a dataclass, a namespace or a `NamedTuple` as a mapping;
- an `Enum` member as its value;
- a path, a `Decimal` or a `UUID` as its text;
- mappings, lists and tuples walked, aliases and cycles kept;
- anything else as it is, for the format to accept or refuse.

`dump()` and `dumps()` apply it before writing, so a typed configuration
writes directly, and a load into a type followed by a dump round-trips:

```python
anycfg.dump(lan, "lan.yaml")
print(open("lan.yaml").read())
# subnet: 10.0.0.0/8
# gateway: 10.0.0.1
print(anycfg.load("lan.yaml", into=Lan).subnet.cidr)    # 10.0.0.0/8
```

## Errors

A value that does not fit raises `ConfigTypeError` or `ConfigValueError` with
the field's path and the model you asked for — in the message and as
`error.config_key` and `error.config_model`:

```python
try:
    hydrate(App, {"name": "web", "hosts": [{"name": "a", "port": "eighty"}]})
except anycfg.ConfigError as error:
    print(error.config_key, error.config_model)    # ('hosts', 0, 'port') App
```

Hydrating no data at all — every source was optional and none loaded — raises
`ConfigTypeError`; pass `default={}` to `Loader.load()` to build from the
class's defaults instead.

## Merging typed layers

`typed_merge(cls, *layers)` merges several layers into one instance, field by
field — the many-layer form of `hydrate`. It and its hooks (`__merge__`,
`OpaqueMerge`, `opaque`, `TypedNamespace`) are described under
[Typed merge](merging.md#typed-merge).
