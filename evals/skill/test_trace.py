"""Offline tests for the stream-json trace parser (issue #588, ADR 0016).

The parser turns a ``claude -p --output-format stream-json --verbose`` JSONL trace
into the efficiency signal the skill-efficacy review reads: the ordered list of
``crm`` invocations and the run metrics. No agent, no org.

    pytest evals/skill
"""
from __future__ import annotations

import json
from typing import Any

from evals.skill import trace


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj)


def _assistant_tool_use(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": name, "input": tool_input},
        ]},
    }


# A representative trace: a text block, two crm Bash commands, one non-crm Bash
# command, a non-Bash tool, then the final result event.
SAMPLE = "\n".join([
    _line({"type": "system", "subtype": "init", "session_id": "s1"}),
    _line({"type": "assistant", "message": {"content": [{"type": "text", "text": "I'll start."}]}}),
    _line(_assistant_tool_use("Bash", {"command": "crm whoami"})),
    _line(_assistant_tool_use("Read", {"file_path": "/x/SKILL.md"})),
    _line(_assistant_tool_use("Bash", {"command": "echo not-crm"})),
    _line(_assistant_tool_use("Bash", {"command": "cd /tmp && crm query odata accounts --top 1"})),
    _line({"type": "result", "subtype": "success", "is_error": False, "num_turns": 7,
           "total_cost_usd": 0.12, "duration_ms": 4200, "result": "done",
           "usage": {"input_tokens": 100, "output_tokens": 20}}),
])


def test_parse_commands_extracts_ordered_crm_invocations():
    cmds = trace.parse_commands(SAMPLE)
    assert cmds == ["crm whoami", "cd /tmp && crm query odata accounts --top 1"]


def test_parse_commands_skips_non_bash_and_non_crm():
    # the Read tool and the `echo not-crm` Bash call carry no crm invocation.
    cmds = trace.parse_commands(SAMPLE)
    assert "echo not-crm" not in cmds
    assert not any("SKILL.md" in c for c in cmds)


def test_parse_commands_word_boundary_not_substring():
    # `scrmble` / `crmfoo` must not be mistaken for a crm invocation.
    raw = "\n".join([
        _line(_assistant_tool_use("Bash", {"command": "scrmble && crmfoo bar"})),
        _line(_assistant_tool_use("Bash", {"command": "echo x | crm query count accounts"})),
    ])
    assert trace.parse_commands(raw) == ["echo x | crm query count accounts"]


def test_parse_metrics_pulls_run_metrics_from_result_event():
    m = trace.parse_metrics(SAMPLE)
    assert m["num_turns"] == 7
    assert m["total_cost_usd"] == 0.12
    assert m["duration_ms"] == 4200


def test_parse_handles_blank_and_malformed_lines():
    raw = "\n".join(["", "not json at all", "{", _line(_assistant_tool_use("Bash", {"command": "crm test connection"}))])
    assert trace.parse_commands(raw) == ["crm test connection"]
    assert trace.parse_metrics(raw) == {}  # no result event → empty metrics, no crash


def test_parse_metrics_empty_when_no_result_event():
    raw = _line(_assistant_tool_use("Bash", {"command": "crm whoami"}))
    assert trace.parse_metrics(raw) == {}
