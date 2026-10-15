"""Load examples/advanced.yaml, which shows the `!include` forms.

    python examples/run_advanced.py

Paths resolve against this file's own directory, so the working directory does
not matter. The output is the loaded document, as JSON.
"""

import json
import logging
from pathlib import Path

import yaconfiglib

HERE = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO)


def main():
    # The default policy is the untrusted posture: includes are allowed, but
    # commands and templates are not, and interpolation is sandboxed.
    # confine_to=True keeps every file read inside base_dir.
    loader = yaconfiglib.Loader(
        base_dir=HERE,
        merge="deep",
        interpolate=True,
        policy=yaconfiglib.Policy(confine_to=True),
    )
    config = loader.load("advanced.yaml")
    print(json.dumps(config, indent=2, default=str))


if __name__ == "__main__":
    main()
