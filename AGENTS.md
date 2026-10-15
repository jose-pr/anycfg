# Working on yaconfiglib

Orientation for a checkout: layout, environments, how to run things, and what CI
and a release actually do. Not the API reference — that is
[`src/yaconfiglib/AGENTS.md`](src/yaconfiglib/AGENTS.md), which ships inside the
installed package.

## Layout

| Path | Holds |
| --- | --- |
| `src/yaconfiglib/` | the package. `_api.py` holds `load`/`loads`/`dump`/`dumps`; `_loader.py` the layered `Loader`; `formats/` the `Format` contract, its registry and one private module per built-in format; `sources/` the `Source` contract, `Command`/`Env`/`Data`/`Text` and the scheme registry; `_context.py` the `Policy`; `_paths.py` source resolution and globs; `_confine.py` confinement; `_merge.py`, `_hydrate.py`, `_templating.py`, `_dotaccess.py`; `exceptions.py`; `testing.py` the shipped `FormatContract` |
| `src/yaconfiglib/AGENTS.md` | the shipped API header: every public name with its signature, defaults and gotchas. Keep it current in the same commit as an API change |
| `tests/` | pytest suite. `conftest.py` has the shared fixtures, `_extras.py` the skip marks for optional dependencies |
| `docs/` | MkDocs sources for the published site |
| `examples/` | runnable scripts; see below |
| `benchmarks/` | micro-benchmarks and committed results; see [`benchmarks/README.md`](benchmarks/README.md) |
| `.github/workflows/` | `test.yml`, `release.yml`, `docs.yml` |

`README.md` and `src/yaconfiglib/AGENTS.md` ship inside the wheel; this file and
`benchmarks/` are excluded from both artifacts.

## Environments

One virtualenv per interpreter, named `.venv/<version>-<os>-<arch>`:

```bash
py -3.9 -m venv .venv/3.9-nt-amd64          # the supported floor
py -3.14 -m venv .venv/3.14-nt-amd64        # the newest supported
```

On a Windows ARM64 machine, use its native interpreters
(`py -V:3.9-arm64`, `py -V:3.14-arm64`) and name the venvs `-nt-arm64`.

The `dev` extra installs the tooling **and every extra that has tests**:

```bash
.venv/3.9-nt-amd64/Scripts/pip install -e ".[dev]"
```

Put the venv's `Scripts` (or `bin`) directory first on `PATH`, or activate it:
the command-backend tests start `python` as a subprocess, and they fail if the
wrong one is found.

Windows on ARM64: PyYAML and MarkupSafe publish no cp39 `win_arm64` wheels, so a
native 3.9 venv builds them from their sdists (PyYAML without libyaml, which the
library does not use); `pip install -e ".[dev]"` works there as is. 3.14 ARM64
installs wheels throughout, including the docs extra.

Build the docs from a 3.14 venv with the `docs` extra installed
(`python -m mkdocs build --strict`); mkdocstrings imports the package.

## Tests

```bash
python -m pytest -q -rs
```

With every extra installed only platform-specific tests skip. `-rs` lists what
skipped, which is how you tell a missing extra from a real skip. A test that needs
an extra uses a `needs_*` fixture from `tests/conftest.py` or a mark from
`tests/_extras.py`, so an install without it skips rather than fails.

A new format runs `yaconfiglib.testing.FormatContract` unmodified
(`tests/formats/`).

## Formatting

black, with `target-version = ["py39"]` — that is the **output** target and is
independent of the interpreter running black:

```bash
black src/ tests/          # fix
black --check src/ tests/  # verify
```

Run it from the **3.14 venv**. black 26 requires Python 3.10+, so the 3.9 floor
venv can only install black 25, and the two releases format one thing
differently: 25 leaves two blank lines before a comment that follows an import
block, 26 leaves one. The tree is formatted with 26, which 25 also accepts — so
either venv's `--check` passes today, but only the 3.14 one will keep the tree
that way.

There is **no format job in CI**; formatting is verified locally. Adding one
would mean a job on Python 3.10+, since that is where a current black installs.

## Examples and benchmarks

```bash
python examples/example.py
python examples/run_advanced.py
```

Both resolve their paths against their own directory, so they run from
anywhere; a test asserts that. Benchmarks:

```bash
python benchmarks/bench.py            # every suite
python benchmarks/bench.py all --save # write a result JSON
```

Local benchmark numbers are a smoke test, never evidence for a claim — see
[`benchmarks/README.md`](benchmarks/README.md).

## CI

Three workflows:

- **`test.yml`** — the matrix: Linux on 3.9 through 3.14, Windows and macOS on
  3.9 and 3.14; a **bare-wheel** job that builds the wheel, installs it with no
  extra and runs `.github/scripts/bare_install_check.py`; and a job with real
  `pydantic`. Triggered by `workflow_dispatch` or by pushing a throwaway
  `ci-*` tag. The tag form exists so a run can be started and polled without
  dashboard access: push `ci-<something-unique>`, watch the run, then delete the
  tag locally and on the remote. A `ci-*` tag uploads every commit it reaches,
  and the run log is public.
- **`test-floors`** — a job in both `test.yml` and `release.yml` (and a
  prerequisite of `build`, so it gates a release). It installs the **lowest**
  version each dependency declares, rather than the newest, because an
  unexercised lower bound is a guess. `.github/scripts/floor_constraints.py`
  derives the pins from the installed metadata at job time, so `pyproject.toml`
  stays the only place a bound is written; the script exits non-zero if any
  dependency declares no lower bound at all. Ubuntu, Python 3.9 — a floor has to
  hold at the `requires-python` floor.
- **`release.yml`** — triggered by a `v*` tag: test gate → build → GitHub
  release → PyPI publish (OIDC trusted publishing, `skip-existing` so a partial
  publish can be re-run). A strict docs build runs as a *gate*: it can redden the
  run, but it never blocks the publish and never deploys. Because a release
  created with the workflow token starts no `release: published` run, the last
  job explicitly dispatches `docs.yml` for the tag.
- **`docs.yml`** — the only workflow that deploys Pages: on pushes to `main`
  touching `docs/`, `mkdocs.yml`, `src/` or the changelog, on a published
  release, and on manual dispatch.

The `github-pages` environment needs a deployment branch policy for `main` as
well as the `v*` tag policy, or a push-triggered deploy is rejected.

## Release

1. Version bump and the `CHANGELOG.md` section in **one** commit.
2. The changelog entry says what changed, what the old and new behaviour are, and
   what a user must do. No process narration, no rationale for the version.
3. Clean the repo root first: hatchling packs untracked, un-ignored root files
   into the sdist. Then `python -m build` and `twine check dist/*`, and list the
   artifact members before trusting them.
4. The owner pushes the `v*` tag. Verify afterwards by the workflow's jobs and by
   what actually appears on PyPI.

## Dependency bounds

A runtime dependency is bounded to the series that works: floor at the series'
`.0` and ceiling at the next incompatible series (`>=6.0,<7`, or `>=0.9.0,<0.10`
for a 0.x package). Raise a floor above `.0` **only** when the code needs an API
a later release added, and say which API and which release. `dev` and `docs` stay
unbounded — they cannot affect what a user installs — and the floor script
ignores them.

## Versioning

Pre-1.0 (`0.y.z`): **MINOR means the documented API broke, and nothing else.**
New API, new optional keyword arguments and bug fixes are all PATCH, so a
`~=0.11.0` dependant gets additions without re-reading the docs and a minor bump
stays a real signal. The released version is always the one the maintainer names.
