from __future__ import annotations

import importlib.util
import io
import sys
from email.message import Message
from http import HTTPStatus
from pathlib import Path

import pytest

SOURCE = Path(__file__).parent.parent / ".basicly/core/kit/board/framing.py"
_SPEC = importlib.util.spec_from_file_location("framing_regressions", SOURCE)
assert _SPEC and _SPEC.loader
framing = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = framing
_SPEC.loader.exec_module(framing)
_SERVER_SPEC = importlib.util.spec_from_file_location(
    "framing_server_regressions", SOURCE.parent / "server.py"
)
assert _SERVER_SPEC and _SERVER_SPEC.loader
server = importlib.util.module_from_spec(_SERVER_SPEC)
sys.modules[_SERVER_SPEC.name] = server
_SERVER_SPEC.loader.exec_module(server)


@pytest.mark.parametrize("value", ["-1", "+1", "1.5", "wat", "", "1000001", "9" * 5000])
def test_invalid_lengths_are_structured_refusals(value: str) -> None:
    with pytest.raises(framing.FramingError) as caught:
        framing.content_length({"Content-Length": value})
    assert caught.value.status in (HTTPStatus.BAD_REQUEST, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)


@pytest.mark.parametrize("first,second", [("1", "1"), ("1", "2")])
def test_duplicate_lengths_never_select_one_of_two_headers(first: str, second: str) -> None:
    headers = Message()
    headers["Content-Length"] = first
    headers["Content-Length"] = second
    with pytest.raises(framing.FramingError, match="exactly one"):
        framing.content_length(headers)


@pytest.mark.parametrize("encoding", ["chunked", "identity", ""])
def test_transfer_encoding_is_refused_even_when_a_length_is_present(encoding: str) -> None:
    with pytest.raises(framing.FramingError, match="Transfer-Encoding"):
        framing.content_length({"Transfer-Encoding": encoding, "Content-Length": "1"})


@pytest.mark.parametrize("length", ["0", "1", " 2 ", "1000000", "00000000000000001"])
def test_valid_lengths_remain_bounded_decimal_counts(length: str) -> None:
    assert framing.content_length({"Content-Length": length}) == int(length)


def test_a_missing_length_remains_an_empty_body() -> None:
    assert framing.content_length({}) == 0


def test_a_declared_body_reads_exactly_its_bound() -> None:
    stream = io.BytesIO(b"{}next request")
    assert framing.read_body(stream, 2) == b"{}"
    assert stream.read() == b"next request"


def test_a_short_body_is_refused_as_incomplete() -> None:
    with pytest.raises(framing.FramingError, match="ended before"):
        framing.read_body(io.BytesIO(b"{"), 2)


def test_a_stalled_body_is_an_explicit_request_timeout() -> None:
    class Stalled(io.BytesIO):
        def read(self, _size: int | None = -1, /) -> bytes:
            raise TimeoutError("stalled")

    with pytest.raises(framing.FramingError) as caught:
        framing.read_body(Stalled(), 2)
    assert caught.value.status == HTTPStatus.REQUEST_TIMEOUT


@pytest.mark.parametrize("length", ["-1", "wat", "1000001"])
def test_bad_framing_is_refused_without_reading_the_tracker_body(length: str) -> None:
    handler = server.Handler.__new__(server.Handler)
    handler.headers = {"Content-Type": "application/json", "Content-Length": length}

    class NeverRead:
        def read(self, size: int) -> bytes:
            pytest.fail(f"an invalid request attempted to read {size} bytes")

    handler.rfile = NeverRead()
    with pytest.raises(server.RequestError):
        handler._body()
