"""What anycfg does, runnable from any directory.

    python examples/example.py

Every path resolves against this file's own directory, so the working directory
does not matter. The output is one JSON document with a section per demo.
"""

import dataclasses
import json
import logging
from pathlib import Path

import anycfg

HERE = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO)


def layered_load():
    """Two layers, deep-merged, then interpolated once as a whole.

    `layered.yaml` includes a JSON and an INI document, and `hiera.yaml`
    overrides a few values. Interpolation runs on the *merged* document, so
    `log_dir` follows the root `hiera.yaml` set, and so does the INI value
    that refers to it.
    """
    loader = anycfg.Loader(base_dir=HERE, merge="deep", interpolate=True)
    return loader.load("layered.yaml", "hiera.yaml")


def template_file():
    """A `.j2` source is rendered first, then parsed as the format it names.

    Templates are off by default; `allow_templates` turns them on, and the
    default sandbox keeps the template away from Python internals and files.
    """
    loader = anycfg.Loader(
        base_dir=HERE, policy=anycfg.Policy(allow_templates=True)
    )
    return loader.load("jinja.yaml.j2")


@dataclasses.dataclass
class Server:
    host: str
    port: int
    scheme: str = "https"


@dataclasses.dataclass
class AppConfig:
    log_dir: str
    workers: int
    server: Server


def typed_load():
    """The same layers, built into a dataclass through its annotations.

    `server.port` is the string "8443" in `hiera.yaml`; the model says `int`,
    so it arrives as an int. Keys the model does not declare are dropped.
    """
    loader = anycfg.Loader(base_dir=HERE, merge="deep", interpolate=True)
    return loader.load("layered.yaml", "hiera.yaml", into=AppConfig)


def typed_merge_demo():
    """Merge several layers into one instance of a type, field by field.

    A `None` is "no opinion": it does not override an earlier value.
    """
    return anycfg.typed_merge(
        Server,
        Server(host="localhost", port=8080),
        {"host": "example.com", "port": "443"},
        {"scheme": None},
    )


def main():
    typed = typed_load()
    merged = typed_merge_demo()
    report = {
        "layered load with interpolation": layered_load(),
        "template file": template_file(),
        "typed load": dataclasses.asdict(typed),
        "typed_merge": dataclasses.asdict(merged),
        "port type": type(typed.server.port).__name__,
    }
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
