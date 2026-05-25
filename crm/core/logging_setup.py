"""crm logging setup — wires the `crm` logger tree to stderr.

Two output formats:
- text: `[LEVEL] <event/message> [k=v ...]`
- json-line: one JSON object per record on a single line
"""
# pyright: basic
from __future__ import annotations

import json
import logging
import sys
from typing import Literal

LogFormat = Literal["text", "json-line"]
LogLevel = Literal["debug", "info", "warning", "error"]

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_STRUCT_KEYS = ("event", "method", "url", "status", "ms")


class CrmLogHandler(logging.StreamHandler):
    """StreamHandler that emits either text or json-line per `fmt`."""

    def __init__(self, fmt: LogFormat = "text"):
        super().__init__(stream=sys.stderr)
        self.fmt: LogFormat = fmt

    def format(self, record: logging.LogRecord) -> str:
        struct = {k: getattr(record, k) for k in _STRUCT_KEYS
                  if hasattr(record, k)}
        if self.fmt == "json-line":
            payload = {"level": record.levelname.lower(), **struct}
            if not struct:
                payload["message"] = record.getMessage()
            return json.dumps(payload, default=str)
        # text
        bits: list[str] = [f"[{record.levelname}]"]
        if "event" in struct:
            bits.append(str(struct["event"]))
        if "method" in struct:
            bits.append(str(struct["method"]))
        if "url" in struct:
            bits.append(str(struct["url"]))
        if "status" in struct:
            bits.append(str(struct["status"]))
        if "ms" in struct:
            bits.append(f"({struct['ms']}ms)")
        if not struct:
            bits.append(record.getMessage())
        return " ".join(bits)


def setup_logging(level: LogLevel = "warning", fmt: LogFormat = "text") -> None:
    """Configure the `crm` logger tree. Idempotent."""
    if level not in _LEVEL_MAP:
        raise ValueError(f"Invalid log level {level!r}; expected: {list(_LEVEL_MAP)}")
    if fmt not in ("text", "json-line"):
        raise ValueError(f"Invalid log format {fmt!r}; expected: text, json-line")
    logger = logging.getLogger("crm")
    logger.setLevel(_LEVEL_MAP[level])
    for h in list(logger.handlers):
        if isinstance(h, CrmLogHandler):
            logger.removeHandler(h)
    handler = CrmLogHandler(fmt=fmt)
    handler.setLevel(_LEVEL_MAP[level])
    logger.addHandler(handler)
    logger.propagate = False
