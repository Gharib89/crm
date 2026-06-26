"""Parse a Claude Code ``stream-json`` trace into the skill-efficacy signal (#588, ADR 0016).

The agent under test runs with ``claude -p --output-format stream-json --verbose``, so
its stdout is a JSONL event stream: a ``system`` init event, ``assistant`` message
events (each carrying ``message.content`` content blocks — ``text``, ``thinking``, and
``tool_use``), interleaved ``user`` tool-result events, and a final ``result`` event
with the run metrics (``num_turns`` / ``total_cost_usd`` / ``duration_ms`` / ``usage``).

Two things the skill-efficacy review needs come straight out of that stream and nothing
else does, so they are parsed once here and stored on the run record:

- ``parse_commands`` — the **ordered** ``crm`` invocations the agent ran, the spine of
  the "did it reach the goal efficiently?" question (fewest/most-appropriate commands).
- ``parse_metrics`` — the turn/cost/duration totals from the terminal ``result`` event.

Kept a pure, offline-testable seam (no agent, no org), mirroring ``analyze``/``taskspec``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

#: A ``crm`` invocation as a *shell word*: at line start or after a shell separator
#: (whitespace, ``;``, ``&``, ``|``, ``(``), and followed by whitespace, end-of-string, or
#: a shell terminator — so ``cd x && crm …``, ``echo|crm …``, a bare ``crm``, and a
#: trailing ``… && crm`` all count, but ``scrmble`` / ``crmfoo`` do not. Defensive — the
#: command may be a compound line, and the review only needs to know a crm call happened
#: and in what order.
_CRM_RE = re.compile(r"(?:^|[\s;&|()])crm(?=\s|$|[;&|)])")

#: Run metrics lifted verbatim from the terminal ``result`` event, when present.
_METRIC_KEYS = ("num_turns", "total_cost_usd", "duration_ms")


def iter_events(raw_trace: str) -> Iterator[dict[str, Any]]:
    """Yield each JSON object in a JSONL trace; skip blank or unparseable lines.

    The trace is captured stdout — a crashed/partial run can leave a truncated final
    line — so a malformed line is skipped rather than aborting the parse.
    """
    for line in raw_trace.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def parse_commands(raw_trace: str) -> list[str]:
    """The ordered Bash commands the agent ran that invoked ``crm``.

    Walks ``assistant`` events' ``tool_use`` blocks, keeps the ``Bash`` ones whose
    command contains a ``crm`` invocation (compound lines included, verbatim), in the
    order they appear — that order *is* the efficiency signal the reviewer reads.
    """
    commands: list[str] = []
    for event in iter_events(raw_trace):
        if event.get("type") != "assistant":
            continue
        content = (event.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") != "Bash":
                continue
            command = (block.get("input") or {}).get("command")
            if isinstance(command, str) and _CRM_RE.search(command):
                commands.append(command.strip())
    return commands


def parse_metrics(raw_trace: str) -> dict[str, Any]:
    """The run metrics from the terminal ``result`` event (empty dict if none).

    A trace with no ``result`` event (the agent died before finishing) yields ``{}``
    rather than raising — the review still has the command sequence to judge.
    """
    metrics: dict[str, Any] = {}
    for event in iter_events(raw_trace):
        if event.get("type") == "result":
            metrics = {k: event[k] for k in _METRIC_KEYS if k in event}
    return metrics
