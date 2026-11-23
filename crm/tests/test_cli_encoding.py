"""Unit tests for force_utf8_output — Windows cp1252 console fix (#146a)."""

# pyright: basic
from __future__ import annotations

import io

from crm.cli import force_utf8_output


def test_reconfigures_cp1252_stream_to_utf8():
    s = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    force_utf8_output(s)
    assert s.encoding.lower() == "utf-8"


def test_box_chars_encodable_after_reconfigure():
    """The box characters raise UnicodeEncodeError under cp1252; they must not after reconfigure."""
    s = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    force_utf8_output(s)
    s.write("│─")
    s.flush()


def test_noop_on_stream_without_reconfigure():
    class Dummy:
        encoding = "cp1252"

    d = Dummy()
    force_utf8_output(d)
    assert d.encoding == "cp1252"


def test_noop_on_stream_with_none_encoding():
    """A stream whose encoding is None must not raise AttributeError on None.lower()."""

    class Dummy:
        encoding = None

    d = Dummy()
    force_utf8_output(d)


def test_noop_on_already_utf8_stream():
    s = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    force_utf8_output(s)
    assert s.encoding.lower() == "utf-8"
