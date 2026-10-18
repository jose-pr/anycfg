"""Print a pip constraints file pinning every dependency to its declared floor.

    python .github/scripts/floor_constraints.py [--dist NAME] EXTRA...

Reads the *installed* metadata of the distribution, so `pyproject.toml` stays
the single source of the bounds — a hand-written pin in a workflow drifts, and
pip silently upgrades past it. Requirements whose marker does not hold for the
running interpreter or for one of the named extras are skipped, as are
self-references (an `anycfg[jinja2]`-style alias extra).

Exits 1 naming every kept requirement that declares no lower bound: an
unbounded dependency has no floor to test, which is the defect this guards
against.

Standard library plus `packaging`; no network. `.github/` is excluded from the
sdist, so this is a repository tool, not a shipped one.
"""

import argparse
import sys
from importlib.metadata import PackageNotFoundError, requires

from packaging.markers import UndefinedEnvironmentName
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

LOWER_BOUND_OPERATORS = (">=", "~=", "==")


def _wanted(requirement: Requirement, extras: "list[str]") -> bool:
    """Does this requirement apply, for the running interpreter and *extras*?"""
    if requirement.marker is None:
        return True
    for extra in extras:
        try:
            if requirement.marker.evaluate({"extra": extra}):
                return True
        except UndefinedEnvironmentName:
            continue
    # A marker that needs no extra at all (e.g. python_version < "3.11").
    try:
        return requirement.marker.evaluate()
    except UndefinedEnvironmentName:
        return False


def _floor(requirement: Requirement) -> "str | None":
    for specifier in requirement.specifier:
        if specifier.operator in LOWER_BOUND_OPERATORS:
            return specifier.version
    return None


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", default="anycfg", help="distribution to read")
    parser.add_argument("extras", nargs="*", help="extras to include")
    args = parser.parse_args(argv)

    try:
        declared = requires(args.dist) or []
    except PackageNotFoundError:
        print(f"{args.dist} is not installed", file=sys.stderr)
        return 2

    dist_name = canonicalize_name(args.dist)
    pins, unbounded = [], []
    for line in declared:
        requirement = Requirement(line)
        if canonicalize_name(requirement.name) == dist_name:
            continue  # an extra that aliases another extra of this package
        if not _wanted(requirement, args.extras):
            continue
        floor = _floor(requirement)
        if floor is None:
            unbounded.append(requirement.name)
        else:
            pins.append(f"{requirement.name}=={floor}")

    for pin in sorted(set(pins)):
        print(pin)

    if unbounded:
        print(
            "no lower bound declared for: " + ", ".join(sorted(set(unbounded))),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
