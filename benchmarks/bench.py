from __future__ import annotations

import argparse
import datetime
import io
import json
import logging
import os
import platform
import re
import statistics
import subprocess
import sys
import sysconfig
import tempfile
import time
from collections.abc import Callable
from pathlib import Path as StdlibPath

# Add src to sys.path so the benchmark can run from a checkout.
sys.path.insert(0, str(StdlibPath(__file__).parent.parent / "src"))

from yaconfiglib import DotAccessibleDict, Loader, TemplateInterpolator
from yaconfiglib._merge import deep
from yaconfiglib._paths import has_glob_pattern, resolve
from yaconfiglib._templating import interpolate_document
from yaconfiglib.sources import Env, Text


def parse_sources(sources, base_dir):
    """Resolve without reading, as the loader does before it opens anything."""
    from pathapi import LocalPath

    return resolve(sources, base_dir=LocalPath(str(base_dir)), recursive=False)


BenchmarkRows = list[tuple[str, object]]


class Timing:
    """One measurement: every sample, and how many calls a sample covers.

    The samples time a whole loop, which is what the printed tables show. The
    JSON reports per-call figures, so *calls* has to travel with them.
    """

    __slots__ = ("samples", "calls")

    def __init__(self, samples: "list[float]", calls: int) -> None:
        self.samples = samples
        self.calls = calls

    @property
    def median(self) -> float:
        return statistics.median(self.samples)

    def per_call_ms(self) -> "dict[str, object]":
        divisor = self.calls or 1
        per_call = sorted(sample / divisor * 1000 for sample in self.samples)
        return {
            "min_ms": per_call[0],
            "median_ms": statistics.median(per_call),
            "max_ms": per_call[-1],
            "calls": self.calls,
            "samples": len(per_call),
        }


def _measure(
    operation: Callable[[], object],
    *,
    calls: int = 1,
    repeat: int = 5,
    warmup: bool = True,
) -> Timing:
    if warmup:
        operation()
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - start)
    return Timing(samples, calls)


def _fmt_metric(value: object) -> str:
    if isinstance(value, Timing):
        value = value.median
    if isinstance(value, float):
        if value < 0.001:
            return f"{value * 1_000_000:.2f}us"
        if value < 1:
            return f"{value * 1000:.2f}ms"
        return f"{value:.4f}s"
    return str(value)


def _print_rows(title: str, rows: BenchmarkRows, *, markdown: bool) -> None:
    if markdown:
        print("| Suite | Benchmark Case | Time / Metric |")
        print("|---|---|---|")
        for name, metric in rows:
            print(f"| {title} | {name} | {_fmt_metric(metric)} |")
        return

    print(f"{title}:")
    for name, metric in rows:
        print(f"   - {name}: {_fmt_metric(metric)}")


def benchmark_sources() -> BenchmarkRows:
    rows: BenchmarkRows = []

    with tempfile.TemporaryDirectory() as tmpdir:
        root = StdlibPath(tmpdir)
        for name in ("a.yaml", "b.yaml", "c.yaml"):
            (root / name).write_text(f"name: {name}\n", encoding="utf-8")

        mixed_sources = [
            "a.yaml",
            ["b.yaml", "c.yaml"],
            "cmd+json://python -c \"print('{}')\"",
            Text("inline: true\n", format="yaml"),
            io.StringIO("stream: true\n"),
        ]

        rows.append(
            (
                "parse mixed sources (500)",
                _measure(
                    lambda: [
                        list(parse_sources(mixed_sources, base_dir=root))
                        for _ in range(500)
                    ],
                    calls=500,
                    repeat=5,
                ),
            )
        )
        rows.append(
            (
                "glob expansion '*.yaml' (200)",
                _measure(
                    lambda: [
                        list(parse_sources(["*.yaml"], base_dir=root))
                        for _ in range(200)
                    ],
                    calls=200,
                    repeat=5,
                ),
            )
        )

        duplicate_count = len(list(parse_sources(["a.yaml", "a.yaml"], base_dir=root)))
        nested_count = len(
            list(parse_sources([["a.yaml", "b.yaml"], "c.yaml"], base_dir=root))
        )
        command_path = next(parse_sources(['cmd+json://echo {"x":[1]}'], base_dir=root))
        rows.append(("duplicate path behavior", f"{duplicate_count} yielded path(s)"))
        rows.append(("nested iterable behavior", f"{nested_count} yielded path(s)"))
        rows.append(("command glob metacharacter behavior", command_path[1].name))

    normal = StdlibPath("normal/path/to/file.yaml")
    globbed = StdlibPath("glob/path/**/*.yaml")
    rows.append(
        (
            "has_glob_pattern pair (20k)",
            _measure(
                lambda: [
                    (has_glob_pattern(normal), has_glob_pattern(globbed))
                    for _ in range(20_000)
                ],
                calls=20000,
                repeat=5,
            ),
        )
    )
    return rows


def benchmark_merge() -> BenchmarkRows:
    def make_pair() -> tuple[dict, dict]:
        return (
            {
                "a": 1,
                "b": [1, 2, {"x": "y"}],
                "c": {"d": {"e": "f"}, "g": [1, 2, 3]},
                "h": "hello",
            },
            {
                "b": [3, 4, {"x": "z", "y": "w"}],
                "c": {"d": {"e": "g", "h": "i"}},
                "i": "world",
            },
        )

    def deep_merge_many(mergelists: bool) -> None:
        for _ in range(1_000):
            base, overlay = make_pair()
            deep(base, overlay, mergelists=mergelists)

    result = deep(
        {"items": [{"name": "a", "enabled": False}]},
        {"items": [{"name": "a", "enabled": True}]},
        mergelists=True,
    )

    loader = Loader(merge="deep", merge_options={"mergelists": True})
    docs = (
        Text("items:\n  - name: api\n    enabled: false\n", format="yaml"),
        Text("items:\n  - name: api\n    enabled: true\n", format="yaml"),
    )

    def load_merge_options_many() -> None:
        for _ in range(200):
            loader.load(*docs)

    return [
        (
            "deep merge, append list dicts (1k)",
            _measure(lambda: deep_merge_many(False), calls=1000, repeat=5),
        ),
        (
            "deep merge, positional list dicts (1k)",
            _measure(lambda: deep_merge_many(True), calls=1000, repeat=5),
        ),
        (
            "loader deep merge_options mergelists (200)",
            _measure(load_merge_options_many, calls=200, repeat=5),
        ),
        ("positional merge correctness", result),
    ]


def benchmark_jinja() -> BenchmarkRows:
    interpolator = TemplateInterpolator(sandbox=False)
    data = {
        "user": "{{ env.USER }}",
        "db": {
            "host": "{{ host }}",
            "port": "{{ port }}",
            "url": "postgresql://{{ user }}@{{ host }}:{{ port }}/db",
        },
        "flags": ["{{ flag1 }}", "{{ flag2 }}", "static"],
    }
    context = {
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "flag1": "active",
        "flag2": "debug",
        "env": {"USER": "postgres"},
    }

    docs = [
        Text(f"key_{i}: value_{i}\ninterpolation: '{{{{ key_{i} }}}}'", format="yaml")
        for i in range(50)
    ]
    loader = Loader(interpolate=True)

    def interpolate_many() -> None:
        for _ in range(100):
            interpolate_document(data, interpolator, extra=context)

    return [
        (
            "interpolate nested structure (100)",
            _measure(interpolate_many, calls=100, repeat=5),
        ),
        (
            "load_all inline docs with interpolate (5 x 50 docs)",
            _measure(
                lambda: [list(loader.load_all(*docs)) for _ in range(5)],
                calls=5,
                repeat=3,
            ),
        ),
    ]


def benchmark_dot_access() -> BenchmarkRows:
    data = DotAccessibleDict(
        {
            "database": {"credentials": {"user": "postgres", "password": "secret"}},
            "database.credentials.literal": "literal",
        }
    )

    def dotted_hit() -> None:
        for _ in range(50_000):
            data.get("database.credentials.user")

    def exact_hit() -> None:
        for _ in range(50_000):
            data.get("database.credentials.literal")

    def miss() -> None:
        for _ in range(50_000):
            data.get("database.credentials.missing", "fallback")

    # Conversion happens once, at load, so measure both ends.
    sections = {
        f"section{i}": {
            "nested": {"a": 1, "b": 2},
            "items": [{"n": j} for j in range(5)],
        }
        for i in range(2_000)
    }

    def load_sections() -> None:
        Loader(wrap=True).load(sections)

    loaded = Loader(wrap=True).load(sections)

    def read_every_section() -> None:
        for i in range(2_000):
            getattr(loaded, f"section{i}").nested

    return [
        (
            "load nested mappings (2k sections)",
            _measure(load_sections, calls=2000, repeat=3),
        ),
        (
            "load then read every section by attribute (2k sections)",
            _measure(read_every_section, calls=2000, repeat=5),
        ),
        ("dotted hit (50k)", _measure(dotted_hit, calls=50000, repeat=5)),
        ("exact dotted-key hit (50k)", _measure(exact_hit, calls=50000, repeat=5)),
        ("dotted miss (50k)", _measure(miss, calls=50000, repeat=5)),
        ("exact key outranks traversal", data.get("database.credentials.literal")),
    ]


def benchmark_env() -> BenchmarkRows:
    rows: BenchmarkRows = []
    original = os.environ.copy()
    try:
        for i in range(100):
            os.environ[f"YACFG_FLAT_{i}"] = str(i)
            os.environ[f"YACFG_NESTED_SERVICE__ITEM_{i}"] = str(i)
        os.environ["YACFG_NESTED_FEATURES__CACHE"] = "true"
        os.environ["YACFG_NESTED_LIMITS__RATE"] = "1.5"
        os.environ["YACFG_NESTED_ITEMS"] = '["a", 2]'

        flat_source = Env("YACFG_FLAT_")
        nested_source = Env("YACFG_NESTED_", nested="__")
        loader = Loader()

        rows.append(
            (
                "flat env scan (200)",
                _measure(
                    lambda: [loader.load(flat_source) for _ in range(200)],
                    calls=200,
                    repeat=5,
                ),
            )
        )
        rows.append(
            (
                "nested env scan (200)",
                _measure(
                    lambda: [loader.load(nested_source) for _ in range(200)],
                    calls=200,
                    repeat=5,
                ),
            )
        )
        result = loader.load(nested_source)
        rows.append(
            (
                "nested correctness",
                {
                    "cache": result["FEATURES"]["CACHE"],
                    "rate": result["LIMITS"]["RATE"],
                    "items": result["ITEMS"],
                },
            )
        )
    finally:
        os.environ.clear()
        os.environ.update(original)
    return rows


def _metric_key(suite: str, label: str) -> str:
    """A stable `<suite>.<snake_case>` key for a row label."""
    cleaned = re.sub(r"[^0-9a-z]+", "_", label.lower()).strip("_")
    return f"{suite}.{cleaned}"


def _default_result_path(version: str) -> StdlibPath:
    """benchmarks/results/<version>-<impl><major.minor>-<os>-<arch>.json"""
    impl = platform.python_implementation().lower()
    major_minor = ".".join(platform.python_version_tuple()[:2])
    arch = sysconfig.get_platform().split("-")[-1]
    name = f"{version}-{impl}{major_minor}-{os.name}-{arch}.json"
    return StdlibPath(__file__).parent / "results" / name


def _benchmarked_version() -> str:
    """Read the version from the pyproject.toml beside the benchmarked src/.

    Deliberately not importlib.metadata: that reports whatever is installed,
    while this harness benchmarks the tree it sits in.
    """
    pyproject = StdlibPath(__file__).parent.parent / "pyproject.toml"
    match = re.search(
        r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M
    )
    return match.group(1) if match else "unknown"


def _benchmarked_commit() -> "str | None":
    """Short HEAD of the benchmarked tree, or None (e.g. a git archive export)."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=StdlibPath(__file__).parent.parent,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout.strip() or None


def save_results(
    suites: "list[tuple[str, BenchmarkRows]]", path: "StdlibPath | None"
) -> StdlibPath:
    """Write one result file. Nothing machine-identifying goes in it."""
    version = _benchmarked_version()
    target = StdlibPath(path) if path else _default_result_path(version)
    target.parent.mkdir(parents=True, exist_ok=True)

    metrics: "dict[str, object]" = {}
    checks: "dict[str, object]" = {}
    for suite, rows in suites:
        for label, metric in rows:
            key = _metric_key(suite, label)
            if isinstance(metric, Timing):
                metrics[key] = metric.per_call_ms()
            else:
                checks[key] = metric

    payload = {
        "name": target.stem,
        "version": version,
        "commit": _benchmarked_commit(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        # platform.processor()/platform.platform(), never platform.node(): these
        # files are committed, so no machine or user name may appear.
        "processor": platform.processor(),
        "platform": platform.platform(),
        "source": "ci" if os.environ.get("CI") else "local",
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "metrics": metrics,
        "checks": checks,
    }
    # The newline is pinned: these files are committed, and the repo is LF-only.
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2) + "\n")
    return target


def collect_rows(command: str) -> list[tuple[str, BenchmarkRows]]:
    suites = {
        "sources": benchmark_sources,
        "merge": benchmark_merge,
        "jinja": benchmark_jinja,
        "dot": benchmark_dot_access,
        "env": benchmark_env,
    }
    if command == "all":
        return [(name, benchmark()) for name, benchmark in suites.items()]
    return [(command, suites[command]())]


def cli(argv: list[str] | None = None) -> None:
    logging.getLogger("yaconfiglib.utils.source").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description="Run yaconfiglib benchmarks.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["all", "sources", "merge", "jinja", "dot", "env"],
        default="all",
        help="benchmark suite to run",
    )
    parser.add_argument("--markdown", action="store_true", help="print markdown tables")
    parser.add_argument(
        "--save",
        nargs="?",
        const="",
        metavar="PATH",
        help="also write the results as JSON (default: benchmarks/results/<name>.json)",
    )
    args = parser.parse_args(argv)

    suites = collect_rows(args.command)
    for title, rows in suites:
        _print_rows(title, rows, markdown=args.markdown)

    if args.save is not None:
        target = save_results(suites, StdlibPath(args.save) if args.save else None)
        print(f"\nsaved {target}")


if __name__ == "__main__":
    cli()
