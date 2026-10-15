"""The contract every format must pass, shipped so a third-party format runs it too.

Subclass :class:`FormatContract` in a pytest module and set the class
attributes; pytest collects the inherited tests::

    from yaconfiglib.testing import FormatContract

    class TestMyFormat(FormatContract):
        format = MyFormat
        samples = [{"a": "1"}, {"nested": {"b": "2"}}]
        malformed = "a = = 1"

Needs pytest. A format is not done until it passes this suite unmodified.
"""

from __future__ import annotations

import logging
import typing as _ty

from .exceptions import ConfigTypeError, ConfigValueError, ParseError
from .formats import Format, capabilities

__all__ = ["FormatContract", "SECRET"]

#: Planted in malformed documents; it must never reach an error or a log.
SECRET = "hunter2-yaconfiglib-secret"


class FormatContract:
    """Behaviour every `Format` must have.

    Attributes to set:
        format: The `Format` subclass under test.
        samples: Values ``dumps`` accepts; each must survive
            ``loads(dumps(x)) == x``. Used only when the format can write.
        document: A small valid document, as text.
        malformed: An invalid document holding the marker ``{secret}``, which
            is replaced by :data:`SECRET` before parsing.
    """

    format: _ty.ClassVar[_ty.Type[Format]]
    samples: _ty.ClassVar[_ty.Sequence[_ty.Any]] = ()
    document: _ty.ClassVar[str] = ""
    malformed: _ty.ClassVar[str] = ""

    def _instance(self) -> Format:
        return self.format()

    def test_is_a_format(self) -> None:
        assert isinstance(self.format, type) and issubclass(self.format, Format)
        assert self.format.name and self.format.name == self.format.name.lower()
        for suffix in self.format.suffixes:
            assert suffix.startswith(".") and suffix == suffix.lower()

    def test_loads_text(self) -> None:
        self._instance().loads(self.document)

    def test_ignores_a_byte_order_mark(self) -> None:
        if self.format.binary:
            return
        import yaconfiglib

        plain = yaconfiglib.loads(self.document, format=self.format)
        marked = yaconfiglib.loads(
            ("﻿" + self.document).encode("utf-8"), format=self.format
        )
        assert marked == plain

    def test_refuses_an_unknown_option(self) -> None:
        import pytest
        import yaconfiglib

        with pytest.raises(TypeError):
            yaconfiglib.loads(
                self.document, format=self.format, no_such_option_anywhere=True
            )

    def test_round_trip(self) -> None:
        if "dump" not in capabilities(self.format):
            return
        fmt = self._instance()
        for sample in self.samples:
            assert fmt.loads(fmt.dumps(sample)) == sample

    def test_malformed_raises_parse_error_without_content(self, caplog) -> None:
        import pytest
        import yaconfiglib

        if not self.malformed:
            pytest.skip("no malformed document given")
        text = self.malformed.replace("{secret}", SECRET)
        caplog.set_level(logging.DEBUG)
        with pytest.raises(ParseError) as caught:
            yaconfiglib.loads(text, format=self.format)
        error = caught.value
        seen = [str(error), repr(error), repr(error.args), repr(vars(error))]
        cause = error.__cause__ or error.__context__
        while cause is not None:
            seen.append(repr(cause))
            cause = cause.__cause__ or cause.__context__
        seen.extend(record.getMessage() for record in caplog.records)
        assert not [text for text in seen if SECRET in text]
        # Not even kept as __context__: the parser's error may hold the text.
        assert error.__cause__ is None and error.__context__ is None

    def test_arbitrary_text_parses_or_raises_parse_error(self) -> None:
        fmt = self._instance()
        for text in ("", " ", "\n", "{", "[", "}", "=", ":", "#", "\x00", "a" * 3, " "):
            try:
                fmt.loads(text)
            except ParseError:
                pass

    def test_unrepresentable_value_raises_before_writing(self) -> None:
        import pytest

        if "dump" not in capabilities(self.format):
            return
        with pytest.raises((ConfigTypeError, ConfigValueError)):
            self._instance().dumps({"a": object()})
