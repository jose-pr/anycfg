# API Reference

Generated from the docstrings. Everything public is importable from
`anycfg`, `anycfg.formats`, `anycfg.sources`,
`anycfg.exceptions` (also re-exported at the root) and
`anycfg.testing`; every other module starts with `_` and is private.

- **[Loading](loading.md)** — `load`, `loads`, `load_all`, `loads_all`,
  `dump`, `dumps`, the layered `Loader` and the `LoadEvent` its `on_error`
  receives.
- **[Policy and context](policy.md)** — `Policy`, the trust settings of a
  load, and `LoadContext`, what a format or source sees of it.
- **[Formats](formats.md)** — the `Format` base class and the registry.
- **[Sources](sources.md)** — `Source`, `Payload`, `Text`, `Data`, `Env`,
  `Command` and the scheme registry.
- **[Merging and typed results](merging.md)** — `DELETE`, `register_merge`,
  `hydrate`, `dehydrate`, `typed_merge` and its hooks, `DotAccessibleDict`.
- **[Interpolation](templating.md)** — the `Interpolator` protocol,
  `TemplateInterpolator` and `VarsInterpolator`.
- **[Exceptions](exceptions.md)** — the `ConfigError` family.
- **[Testing](testing.md)** — `FormatContract`, the suite every format
  passes.

The installed package also carries `anycfg/AGENTS.md`, a one-file header
of every public name with its signature, defaults and gotchas.
