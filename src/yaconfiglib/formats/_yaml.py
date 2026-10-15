"""YAML through PyYAML, with ``!include``/``!load``."""

from __future__ import annotations

import datetime
import enum
import threading
import typing as _ty

from ..exceptions import (
    ConfigError,
    ConfigTypeError,
    ErrorFrame,
    MissingDependencyError,
    ParseError,
    _add_error_context,
)
from .._scalar import ScalarText
from ._base import Format

from .._context import LoadContext

__all__ = ["YAMLFormat"]

_INCLUDE_TAGS = ("!include", "!load")
#: Plain-scalar types whose text is kept for a typed load.
_TEXT_KEPT = (bool, int, float, datetime.date, datetime.datetime)
_lock = threading.Lock()
_classes: "_ty.Dict[str, type]" = {}


def _yaml() -> _ty.Any:
    try:
        import yaml
    except ImportError:
        raise MissingDependencyError(
            'YAML needs the "yaml" extra: pip install "yaconfiglib[yaml]"'
        ) from None
    return yaml


def _include_call(loader: _ty.Any, node: _ty.Any) -> tuple:
    """Read an ``!include``/``!load`` node as ``(sources, options)``.

    Three forms: a scalar (one source), a sequence (several sources, merged in
    order) and a mapping (``pathname:`` plus options). A malformed form raises
    PyYAML's `ConstructorError`, whose mark names the file and line.
    """
    yaml = _yaml()
    if isinstance(node, yaml.nodes.ScalarNode):
        return (loader.construct_scalar(node),), {}
    if isinstance(node, yaml.nodes.SequenceNode):
        items = loader.construct_sequence(node, deep=True)
        if not items:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                "an !include sequence needs at least one path",
                node.start_mark,
            )
        return tuple(items), {}
    options = loader.construct_mapping(node, deep=True)
    pathname = options.pop("pathname", None)
    if not pathname:
        raise yaml.constructor.ConstructorError(
            None,
            None,
            "an !include mapping needs a non-empty 'pathname' key",
            node.start_mark,
        )
    sources = tuple(pathname) if isinstance(pathname, list) else (pathname,)
    return sources, options


def _construct_include(loader: _ty.Any, node: _ty.Any) -> object:
    yaml = _yaml()
    context = getattr(loader, "_yaconfiglib_context", None)
    if context is None or context._loader is None:
        raise yaml.constructor.ConstructorError(
            None,
            None,
            "!include/!load are only available while a yaconfiglib Loader is loading",
            node.start_mark,
        )
    sources, options = _include_call(loader, node)
    try:
        return context.include(*sources, **options)
    except Exception as error:  # noqa: BLE001 - adds context, re-raises
        # Every type: any failure must say which document included the file.
        _add_error_context(
            error,
            frame=ErrorFrame("include", node.start_mark.name, node.start_mark.line + 1),
        )
        raise


def _construct_delete(loader: _ty.Any, node: _ty.Any) -> object:
    """``!delete``: remove this key from the merged result."""
    from .._merge import DELETE

    context = getattr(loader, "_yaconfiglib_context", None)
    state = getattr(context, "_state", None)
    if state is not None:
        state.deletes = True
    return DELETE


def _classes_for() -> "_ty.Tuple[type, type]":
    """yaconfiglib's own SafeLoader and SafeDumper subclasses, built on first use.

    Tags and representers are registered on these subclasses only, so a plain
    ``yaml.safe_load`` elsewhere in the process never resolves ``!include`` and
    PyYAML's global tables are never touched.
    """
    with _lock:
        if _classes:
            return _classes["loader"], _classes["dumper"]
        yaml = _yaml()
        merge_tag = "tag:yaml.org,2002:merge"

        class _Loader(yaml.SafeLoader):
            _yaconfiglib_keep_text = False

            def construct_mapping(self, node, deep=False):
                if isinstance(node, yaml.nodes.MappingNode):
                    seen = set()
                    for key_node, _value in node.value:
                        # Explicit keys only: a `<<:` merge key brings in keys
                        # an explicit one is allowed to override.
                        if key_node.tag == merge_tag:
                            continue
                        key = self.construct_object(key_node, deep=deep)
                        try:
                            duplicate = key in seen
                        except TypeError:
                            continue  # unhashable; PyYAML reports it itself
                        if duplicate:
                            raise yaml.constructor.ConstructorError(
                                "while constructing a mapping",
                                node.start_mark,
                                "found a duplicate key",
                                key_node.start_mark,
                            )
                        seen.add(key)
                mapping = super().construct_mapping(node, deep=deep)
                if self._yaconfiglib_keep_text:
                    # Keys stay what YAML read them as: no type steers a key.
                    mapping = {
                        (key.value if isinstance(key, ScalarText) else key): value
                        for key, value in mapping.items()
                    }
                return mapping

            def construct_object(self, node, deep=False):
                try:
                    value = super().construct_object(node, deep=deep)
                    if (
                        self._yaconfiglib_keep_text
                        and type(value) in _TEXT_KEPT
                        and isinstance(node, yaml.nodes.ScalarNode)
                        and node.style is None
                    ):
                        # A plain scalar YAML 1.1 read as a non-string: the
                        # target type decides between that and the text.
                        return ScalarText(node.value, value)
                    return value
                except (ConfigError, OSError):
                    raise  # an include's own failure, already attributed
                except (ValueError, KeyError, AttributeError, TypeError, OverflowError):
                    if not node.tag.startswith("tag:yaml.org,2002:"):
                        raise  # yaconfiglib's own tags report for themselves
                    # A scalar its tag cannot hold (`2020-13-45`, `!!int x`).
                    # PyYAML's own message quotes the value, so it is not kept.
                    raise yaml.constructor.ConstructorError(
                        None,
                        None,
                        f"not a valid {node.tag.rsplit(':', 1)[-1]} value",
                        node.start_mark,
                    ) from None

        for tag in _INCLUDE_TAGS:
            _Loader.add_constructor(tag, _construct_include)
        _Loader.add_constructor("!delete", _construct_delete)

        class _Dumper(yaml.SafeDumper):
            pass

        # Every dict and list subclass (a loaded DotAccessibleDict, an
        # OrderedDict) as plain data, a tuple as a list, an enum member as its
        # value: all of them load back. Anything else raises.
        representer = yaml.representer.SafeRepresenter
        _Dumper.add_multi_representer(dict, representer.represent_dict)
        _Dumper.add_multi_representer(list, representer.represent_list)
        _Dumper.add_representer(tuple, representer.represent_list)
        _Dumper.add_representer(frozenset, representer.represent_set)
        _Dumper.add_multi_representer(
            enum.Enum, lambda dumper, member: dumper.represent_data(member.value)
        )
        _classes["loader"] = _Loader
        _classes["dumper"] = _Dumper
        return _Loader, _Dumper


def _parse_error(error: _ty.Any, path: "_ty.Optional[str]") -> ParseError:
    """A `ParseError` from a PyYAML error, without PyYAML's source snippet."""
    mark = getattr(error, "problem_mark", None) or getattr(error, "context_mark", None)
    problem = getattr(error, "problem", None)
    context = getattr(error, "context", None)
    if problem:
        msg = f"{context}: {problem}" if context else str(problem)
    else:
        # A ReaderError (a non-printable character) has no problem/mark pair.
        msg = getattr(error, "reason", None) or type(error).__name__
        position = getattr(error, "position", None)
        if position is not None:
            msg = f"{msg} at character {position}"
    lineno = mark.line + 1 if mark is not None else None
    colno = mark.column + 1 if mark is not None else None
    return ParseError(msg, path=path, lineno=lineno, colno=colno)


class YAMLFormat(Format):
    """YAML through PyYAML's safe loader (the ``yaml`` extra).

    Dialect: YAML 1.1 as PyYAML implements it — ``yes``/``no``/``on``/``off``
    are booleans, ``0755`` is an octal integer, ``1.10`` is the float ``1.1``.
    Quote a value to keep it a string. Duplicate keys raise (a ``<<:`` merge key
    may still be overridden). Only safe tags load; no Python object is ever
    constructed.

    ``!include``/``!load`` pull in another source through the loader, which
    applies the policy, confinement, the include budget and cycle detection.
    Each included document is its own anchor scope. ``!delete`` as a value
    removes that key when this document is merged over another. Forms::

        db: !include db.yaml
        all: !include [base.yaml, local.yaml]       # merged in order
        opt: !include {pathname: extra.yaml, default: {}}

    ``loads_all`` reads a ``---``-separated stream. ``dumps`` writes plain
    data only: mappings, sequences, scalars, dates and sets, with mapping and
    sequence subclasses written as plain ones and enum members as their values.
    Anything else raises `ConfigTypeError` before anything is written. Keys keep
    their order and text is written unescaped.
    """

    name = "yaml"
    aliases = ("yml",)
    suffixes = (".yaml", ".yml")
    dump_options = frozenset(
        {
            "indent",
            "width",
            "default_flow_style",
            "sort_keys",
            "allow_unicode",
            "explicit_start",
            "explicit_end",
        }
    )

    def _parser(self, data: _ty.Any, context: "_ty.Optional[LoadContext]") -> _ty.Any:
        yaml = _yaml()
        loader_cls, _dumper = _classes_for()
        path = (context.name if context is not None else None) or None
        try:
            parser = loader_cls(data)
        except yaml.YAMLError as error:
            failure = _parse_error(error, path)
        else:
            parser.name = path or "<string>"
            parser._yaconfiglib_context = context
            parser._yaconfiglib_keep_text = bool(context and context.keep_text)
            return parser
        raise failure

    def _translate(self, read: _ty.Callable[[], _ty.Any], path: _ty.Any) -> _ty.Any:
        yaml = _yaml()
        try:
            return read()
        except yaml.YAMLError as error:
            failure = _parse_error(error, path)
        except RecursionError:
            failure = ParseError("document is nested too deeply", path=path)
        # Outside the except blocks: PyYAML's error holds a source snippet.
        raise failure

    def loads(
        self,
        data: _ty.Any,
        /,
        *,
        context: "_ty.Optional[LoadContext]" = None,
        **options: _ty.Any,
    ) -> _ty.Any:
        parser = self._parser(data, context)
        try:
            return self._translate(
                parser.get_single_data, (context and context.name) or None
            )
        finally:
            parser.dispose()

    def loads_all(
        self,
        data: _ty.Any,
        /,
        *,
        context: "_ty.Optional[LoadContext]" = None,
        **options: _ty.Any,
    ) -> _ty.Iterator[_ty.Any]:
        parser = self._parser(data, context)
        path = (context and context.name) or None
        try:
            while self._translate(parser.check_data, path):
                yield self._translate(parser.get_data, path)
        finally:
            parser.dispose()

    def dumps(self, obj: _ty.Any, /, **options: _ty.Any) -> str:
        yaml = _yaml()
        _loader, dumper_cls = _classes_for()
        options.setdefault("sort_keys", False)
        options.setdefault("allow_unicode", True)
        options.setdefault("default_flow_style", False)
        try:
            return yaml.dump(obj, Dumper=dumper_cls, **options)
        except yaml.representer.RepresenterError as error:
            value = error.args[1] if len(error.args) > 1 else None
            raise ConfigTypeError(
                f"YAML cannot represent a {type(value).__name__}; convert it to "
                "plain data first"
            ) from None
