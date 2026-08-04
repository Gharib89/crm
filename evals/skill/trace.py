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


def parse_invoked(raw_trace: str) -> bool:
    """Whether the agent invoked the ``crm`` skill via the ``Skill`` tool.

    ADR 0028 measures invocation *separately* from success — a skill-leg trial can pass
    without ever loading the skill (found the path another way), or invoke it and still
    fail; the report's invocation-vs-success split needs that signal. Walks ``assistant``
    events for a ``Skill`` ``tool_use`` whose input names ``crm`` (the skill install slug),
    ignoring other skills. The bare leg has no skill installed, so this is ``False`` there.
    """
    for event in iter_events(raw_trace):
        if event.get("type") != "assistant":
            continue
        content = (event.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") != "Skill":
                continue
            skill = (block.get("input") or {}).get("skill")
            if isinstance(skill, str) and skill.strip().lower() == "crm":
                return True
    return False


def parse_api_error(raw_trace: str) -> bool:
    """True when the terminal ``result`` event is an agent-level API failure.

    ``is_error`` on the result event means the *driver* died (e.g. a 529 Overloaded from
    the model API) — the agent never got to work on the task, so scoring the trial as a
    task fail would poison the run (issue #943). A cap-kill leaves **no** result event
    (the process is killed), so this stays False for caps — a cap is a real, scored
    outcome, never retried.
    """
    error = False
    for event in iter_events(raw_trace):
        if event.get("type") == "result":
            error = bool(event.get("is_error"))
    return error


def parse_api_error_detail(raw_trace: str) -> str | None:
    """A short human descriptor of the terminal API failure, or ``None`` when clean.

    Best-effort context for the run-abort message when the per-leg API-error retries are
    exhausted (#943): the terminal ``result`` event's ``subtype`` (e.g.
    ``error_during_execution``) and its ``result`` message (e.g. a usage-limit line),
    joined. Returns ``None`` when the last ``result`` event is not an error, or when it is
    an error but carries neither field — the abort fires on retry *exhaustion*, never on
    the ability to classify the cause, so a missing descriptor is fine.
    """
    detail: str | None = None
    for event in iter_events(raw_trace):
        if event.get("type") != "result":
            continue
        if not event.get("is_error"):
            detail = None
            continue
        parts: list[str] = []
        for key in ("subtype", "result"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        detail = "; ".join(parts) if parts else None
    return detail


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
