"""Check a wheel installed with no extras: it imports, and the core formats work.

    python .github/scripts/bare_install_check.py

Run from a venv holding only the built wheel and its required dependencies.
Fails when importing the package pulls in an optional dependency (or tomli,
required below 3.11 but imported only when TOML is read), when a core format
cannot load, or when a format that needs an extra does not name it.
"""

import json
import sys
import tempfile
from pathlib import Path

import anycfg

OPTIONAL = {"yaml", "jinja2", "tplx", "tomli", "tomli_w", "markupsafe"}


def main() -> int:
    imported = OPTIONAL & {name.split(".")[0] for name in sys.modules}
    if imported:
        print(f"import anycfg imported optional modules: {sorted(imported)}")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.json").write_text('{"a": 1}', encoding="utf-8")
        (root / "b.ini").write_text("[s]\nk = v\n", encoding="utf-8")
        (root / ".env").write_text("K=v\n", encoding="utf-8")
        (root / "c.toml").write_text("t = 1\n", encoding="utf-8")
        (root / "d.cfg").write_text("c = 2\n", encoding="utf-8")
        loader = anycfg.Loader(base_dir=root)
        result = loader.load("a.json", "b.ini", ".env", "c.toml", "d.cfg")
        expected = {"a": 1, "s": {"k": "v"}, "K": "v", "t": 1, "c": 2}
        assert result == expected, result
        anycfg.dump(result, root / "out.json")
        assert json.loads((root / "out.json").read_text(encoding="utf-8")) == result
        try:
            anycfg.loads("a: 1", format="yaml")
        except anycfg.MissingDependencyError as error:
            assert "anycfg[yaml]" in str(error), error
        else:
            print("YAML loaded without PyYAML installed")
            return 1
    print("bare install OK:", anycfg.__version__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
