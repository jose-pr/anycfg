"""Check a wheel installed with no extras: it imports, and stdlib formats work.

    python .github/scripts/bare_install_check.py

Run from a venv holding only the built wheel and its required dependencies.
Fails when importing the package pulls in an optional dependency, when a
stdlib format cannot load, or when a format that needs an extra does not name it.
"""

import json
import sys
import tempfile
from pathlib import Path

import yaconfiglib

OPTIONAL = {"yaml", "jinja2", "tplx", "tomli", "tomli_w", "markupsafe"}


def main() -> int:
    imported = OPTIONAL & {name.split(".")[0] for name in sys.modules}
    if imported:
        print(f"import yaconfiglib imported optional modules: {sorted(imported)}")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.json").write_text('{"a": 1}', encoding="utf-8")
        (root / "b.ini").write_text("[s]\nk = v\n", encoding="utf-8")
        (root / ".env").write_text("K=v\n", encoding="utf-8")
        loader = yaconfiglib.Loader(base_dir=root)
        result = loader.load("a.json", "b.ini", ".env")
        assert result == {"a": 1, "s": {"k": "v"}, "K": "v"}, result
        yaconfiglib.dump(result, root / "out.json")
        assert json.loads((root / "out.json").read_text(encoding="utf-8")) == result
        try:
            yaconfiglib.loads("a: 1", format="yaml")
        except yaconfiglib.MissingDependencyError as error:
            assert "yaconfiglib[yaml]" in str(error), error
        else:
            print("YAML loaded without PyYAML installed")
            return 1
    print("bare install OK:", yaconfiglib.__version__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
