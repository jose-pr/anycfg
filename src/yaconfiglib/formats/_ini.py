"""INI, through the standard library's configparser."""

from __future__ import annotations

import configparser
import io
import logging
import typing as _ty

from ..exceptions import ConfigTypeError, ConfigValueError, ParseError
from ._base import Format

from .._context import LoadContext

__all__ = ["INIFormat"]

logger = logging.getLogger(__name__)

DEFAULT_SECTION = "DEFAULT"


def _interpolation(choice: object) -> "_ty.Optional[configparser.Interpolation]":
    """Turn an ``interpolation`` option into a configparser interpolation."""
    if choice is None or isinstance(choice, configparser.Interpolation):
        return choice
    if isinstance(choice, str):
        name = choice.lower()
        if name == "basic":
            return configparser.BasicInterpolation()
        if name == "extended":
            return configparser.ExtendedInterpolation()
        if name == "none":
            return None
    raise ConfigValueError(
        f"invalid interpolation {choice!r}: expected 'basic', 'extended', 'none', "
        "None, or a configparser.Interpolation instance"
    )


def _parse_error(error: configparser.Error, path: "_ty.Optional[str]") -> ParseError:
    """A `ParseError` for a configparser error, without the line's text.

    configparser quotes the offending line in its message, and that line may be
    a secret, so the message is rebuilt from the error's type and position.
    """
    lineno = getattr(error, "lineno", None)
    if isinstance(error, configparser.ParsingError) and getattr(error, "errors", None):
        lineno = error.errors[0][0]
    if isinstance(error, configparser.MissingSectionHeaderError):
        msg = "a value appears before any [section] header"
    elif isinstance(error, configparser.DuplicateSectionError):
        msg = f"duplicate section [{error.section}]"
    elif isinstance(error, configparser.DuplicateOptionError):
        msg = f"duplicate option {error.option!r} in [{error.section}]"
    elif isinstance(error, configparser.InterpolationError):
        msg = (
            f"interpolation failed for {error.option!r} in [{error.section}] "
            f"({type(error).__name__}); pass interpolation=None to read values "
            "verbatim"
        )
    elif isinstance(error, configparser.ParsingError):
        msg = "a line is neither a section header nor a key/value pair"
    else:
        msg = type(error).__name__
    return ParseError(msg, path=path, lineno=lineno)


class INIFormat(Format):
    """INI through :class:`configparser.ConfigParser`.

    Returns ``{section: {option: value}}`` with every value a string. Dialect,
    which is configparser's: option names are lower-cased and section names are
    kept; ``key = value`` and ``key: value`` both work; a value may continue on
    indented lines; ``#`` and ``;`` start a comment only at the start of a line;
    duplicate sections and options raise.

    ``%`` interpolation is on by default, as configparser has it, so
    ``%(name)s`` references resolve and a literal percent is written ``%%``.
    Load options: ``interpolation`` (``"basic"``, ``"extended"``, ``"none"`` /
    `None`, or a :class:`configparser.Interpolation`) and ``default_section``.
    Keys in ``[DEFAULT]`` are inherited by sections rather than returned; a
    file holding nothing else logs a warning.

    ``dumps`` writes a mapping of sections whose values are strings and whose
    option names are lower-case — exactly what ``loads`` returns, so the round
    trip is exact. With the default interpolation a ``%`` is written ``%%``.
    """

    name = "ini"
    aliases = ("cfg",)
    suffixes = (".ini", ".cfg")
    load_options = frozenset({"interpolation", "default_section"})
    dump_options = frozenset({"interpolation"})

    def loads(
        self,
        data: _ty.Any,
        /,
        *,
        context: "_ty.Optional[LoadContext]" = None,
        interpolation: object = "basic",
        default_section: "_ty.Optional[str]" = None,
        **options: _ty.Any,
    ) -> _ty.Any:
        path = (context.name if context is not None else None) or None
        parser = configparser.ConfigParser(
            default_section=default_section or DEFAULT_SECTION,
            interpolation=_interpolation(interpolation),
        )
        failure = None
        try:
            parser.read_string(data, path or "<string>")
            result = {}
            for section in parser.sections():
                values = parser[section]
                result[section] = {key: values[key] for key in values}
        except configparser.Error as error:
            failure = _parse_error(error, path)
        if failure is not None:
            # Outside the except block: configparser's error quotes the line,
            # so it must not stay attached as __context__ either.
            raise failure
        if parser.defaults() and not result:
            # Defaults are inherited by sections rather than returned, so a
            # file holding nothing else would otherwise look empty.
            logger.warning(
                "%s: only the %r section is present, and its keys are inherited "
                "by other sections rather than returned; pass "
                'default_section="<a name the file does not use>" to load them '
                "as a section",
                path or "<string>",
                parser.default_section,
            )
        return result

    def dumps(
        self, obj: _ty.Any, /, *, interpolation: object = "basic", **options: _ty.Any
    ) -> str:
        if not isinstance(obj, _ty.Mapping):
            raise ConfigTypeError(
                f"INI holds a mapping of sections, not a {type(obj).__name__}"
            )
        escape = _interpolation(interpolation) is not None
        parser = configparser.ConfigParser(interpolation=None)
        for section, values in obj.items():
            if not isinstance(section, str) or not isinstance(values, _ty.Mapping):
                raise ConfigTypeError(
                    f"INI section {section!r} must be a str name mapping to a "
                    "mapping of options"
                )
            parser.add_section(section)
            for key, value in values.items():
                if not isinstance(key, str) or key != key.lower():
                    raise ConfigTypeError(
                        f"INI option names are lower-case strings; {key!r} in "
                        f"[{section}] would not load back as written"
                    )
                if not isinstance(value, str):
                    raise ConfigTypeError(
                        f"INI values are strings; [{section}] {key} is a "
                        f"{type(value).__name__}"
                    )
                parser.set(section, key, value.replace("%", "%%") if escape else value)
        out = io.StringIO()
        parser.write(out)
        return out.getvalue()
