"""Async loading: ``aload``, ``aload_all``, ``adump``, ``Loader.aload`` and ``Chunks``.

The tests drive coroutines with `asyncio.run`, so no async test plugin is needed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

import yaconfiglib
from yaconfiglib import (
    BudgetExceededError,
    ConfinementError,
    Loader,
    Policy,
    UnsupportedFormatError,
)
from yaconfiglib.sources import Chunks


class AsyncChunks:
    """An async iterable of chunks, like ``response.aiter_bytes()``."""

    def __init__(self, chunks, name=None):
        self.chunks = list(chunks)
        self.taken = 0
        if name is not None:
            self.name = name

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self.chunks:
            self.taken += 1
            await asyncio.sleep(0)
            yield chunk


class AsyncReader:
    """An object with ``async def read(n)``, like aiofiles or StreamReader."""

    def __init__(self, data, name=None, sized=True):
        self.data = data
        self.sized = sized
        self.calls = 0
        if name is not None:
            self.name = name

    async def read(self, *args):
        self.calls += 1
        if not self.sized:
            if args:
                raise TypeError("read() takes no size")
            data, self.data = self.data, self.data[:0]
            return data
        size = args[0] if args else len(self.data)
        data, self.data = self.data[:size], self.data[size:]
        return data


class AsyncWriter:
    def __init__(self, name=None, binary=False):
        self.parts = []
        self.binary = binary
        if name is not None:
            self.name = name

    async def write(self, data):
        if self.binary and isinstance(data, str):
            raise TypeError("binary")
        self.parts.append(data)


@dataclasses.dataclass
class Conf:
    port: int


class TestAload:
    def test_async_iterable_of_bytes(self):
        source = AsyncChunks([b'{"a"', b": 1}"])
        assert asyncio.run(yaconfiglib.aload(source, format="json")) == {"a": 1}

    def test_async_reader_named_by_its_file(self):
        source = AsyncReader(b'{"port": "80"}', name="conf.json")
        assert asyncio.run(yaconfiglib.aload(source, into=Conf)) == Conf(80)

    def test_reader_whose_read_takes_no_size(self):
        source = AsyncReader('{"a": 2}', sized=False)
        assert asyncio.run(yaconfiglib.aload(source, format="json")) == {"a": 2}

    def test_text_chunks(self):
        source = AsyncChunks(['{"a": ', "3}"])
        assert asyncio.run(yaconfiglib.aload(source, format="json")) == {"a": 3}

    def test_a_path_still_works(self, tmp_path):
        (tmp_path / "a.json").write_text('{"a": 1}', encoding="utf-8")
        assert asyncio.run(yaconfiglib.aload(str(tmp_path / "a.json"))) == {"a": 1}

    def test_no_name_no_format(self):
        with pytest.raises(UnsupportedFormatError):
            asyncio.run(yaconfiglib.aload(AsyncChunks([b"{}"])))

    def test_stops_reading_past_max_bytes(self):
        source = AsyncChunks([b"x" * 1000] * 50)
        with pytest.raises(BudgetExceededError):
            asyncio.run(
                yaconfiglib.aload(source, format="json", policy=Policy(max_bytes=2500))
            )
        assert source.taken == 3

    def test_mixed_chunks_are_refused(self):
        with pytest.raises(TypeError, match="mixes"):
            asyncio.run(yaconfiglib.aload(AsyncChunks([b"{", "}"]), format="json"))

    def test_a_named_async_file_outside_the_roots_is_not_read(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        source = AsyncReader(b"{}", name=str(tmp_path / "outside.json"))
        with pytest.raises(ConfinementError):
            asyncio.run(
                yaconfiglib.aload(source, policy=Policy(confine_to=[str(root)]))
            )
        assert source.calls == 0

    def test_sync_load_points_to_aload(self):
        with pytest.raises(TypeError, match="aload"):
            yaconfiglib.load(AsyncChunks([b"{}"]))
        with pytest.raises(TypeError, match="aload"):
            Loader().load(AsyncReader(b"{}"))


@pytest.mark.usefixtures("needs_yaml")
class TestAloadAll:
    def test_each_document_of_a_stream(self):
        source = AsyncChunks([b"a: 1\n--", b"-\na: 2\n"])

        async def collect():
            return [doc async for doc in yaconfiglib.aload_all(source, format="yaml")]

        assert asyncio.run(collect()) == [{"a": 1}, {"a": 2}]

    def test_loader_aload_all_with_into(self):
        async def collect():
            loader = Loader()
            return [
                doc
                async for doc in loader.aload_all(
                    AsyncChunks([b"port: '1'\n---\nport: 2\n"]),
                    format="yaml",
                    into=Conf,
                )
            ]

        assert asyncio.run(collect()) == [Conf(1), Conf(2)]

    def test_loader_aload_all_checks_its_arguments_when_called(self):
        with pytest.raises(TypeError):
            Loader().aload_all(AsyncChunks([]), nope=1)


class TestLoaderAload:
    def test_layers_async_and_sync_sources(self, tmp_path):
        (tmp_path / "base.json").write_text('{"a": 1, "b": 1}', encoding="utf-8")
        loader = Loader(base_dir=tmp_path)
        result = asyncio.run(
            loader.aload(
                "base.json",
                [AsyncChunks([b'{"b": 2}'], name="over.json")],
                {"c": 3},
            )
        )
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_a_refused_async_source_is_skipped_under_on_error(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = AsyncReader(b'{"x": 1}', name=str(tmp_path / "x.json"))
        loader = Loader(policy=Policy(confine_to=[str(root)]), on_error=True)
        assert asyncio.run(loader.aload(outside, {"a": 1})) == {"a": 1}
        assert outside.calls == 0

    def test_needs_a_source(self):
        with pytest.raises(TypeError):
            asyncio.run(Loader().aload())

    def test_interpolation_and_merge_options_reach_the_load(self):
        result = asyncio.run(
            Loader(interpolate="vars").aload(
                AsyncChunks([b'{"a": "x", "b": "${a}y"}'], name="c.json")
            )
        )
        assert result == {"a": "x", "b": "xy"}


class TestAdump:
    def test_async_text_writer(self):
        writer = AsyncWriter(name="out.json")
        asyncio.run(yaconfiglib.adump({"a": 1}, writer))
        assert json.loads("".join(writer.parts)) == {"a": 1}

    def test_async_binary_writer(self):
        writer = AsyncWriter(binary=True)
        asyncio.run(yaconfiglib.adump({"a": 1}, writer, format="json"))
        assert writer.parts[0].endswith(b"}\n")

    def test_stream_writer_with_drain(self):
        class StreamWriter:
            def __init__(self):
                self.parts = []
                self.drained = False

            def write(self, data):
                self.parts.append(data)

            async def drain(self):
                self.drained = True

        writer = StreamWriter()
        asyncio.run(yaconfiglib.adump({"a": 1}, writer, format="json"))
        assert writer.drained and json.loads(writer.parts[0]) == {"a": 1}

    def test_a_path(self, tmp_path):
        target = tmp_path / "o.json"
        asyncio.run(yaconfiglib.adump({"a": 1}, str(target)))
        assert yaconfiglib.load(str(target)) == {"a": 1}

    def test_nothing_written_when_serializing_fails(self):
        writer = AsyncWriter(name="out.json")
        with pytest.raises(yaconfiglib.ConfigTypeError):
            asyncio.run(yaconfiglib.adump({"a": object()}, writer))
        assert writer.parts == []

    def test_an_unnamed_writer_needs_a_format(self):
        with pytest.raises(UnsupportedFormatError):
            asyncio.run(yaconfiglib.adump({}, AsyncWriter()))


class TestChunks:
    def test_sync_chunks(self):
        assert Loader().load(Chunks(iter([b'{"a"', b": 1}"]), format="json")) == {
            "a": 1
        }

    def test_named_chunks_pick_the_format(self):
        assert Loader().load(Chunks(['{"a": 1}'], name="x.json")) == {"a": 1}

    def test_stops_past_max_bytes(self):
        taken = []

        def chunks():
            for index in range(100):
                taken.append(index)
                yield b"x" * 100

        with pytest.raises(BudgetExceededError):
            Loader(policy=Policy(max_bytes=250)).load(Chunks(chunks(), format="json"))
        assert len(taken) == 3

    def test_text_is_not_chunks(self):
        with pytest.raises(TypeError):
            Chunks("not chunks", format="json")
