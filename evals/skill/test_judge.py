"""Offline tests for the advisory blind L2 judge (issue #894, ADR 0028).

The judge is advisory: its verdict is recorded alongside L1 but never enters pass-rate,
Hake gain, or regression (that isolation is pinned in ``test_paired``). Here we pin the
pure pieces with a fake invoker (the one seam) — command resolution, the blind prompt,
strict-output parsing, and the never-raise closure — with no live model.

    pytest evals/skill/test_judge.py
"""

from __future__ import annotations

import json
import re

import pytest

from evals.skill.judge import (
    DEFAULT_JUDGE_CMD,
    RUBRIC_VERSION,
    JudgeError,
    build_judge_prompt,
    judge_model,
    make_judge,
    parse_judgment,
    resolve_judge_cmd,
)

_GOOD = json.dumps(
    {
        "clarification_quality": {"score": 4, "note": "asked about the target entity"},
        "elegance": {"score": 5, "note": "single create, no flailing"},
    }
)


def test_resolve_judge_cmd_precedence(monkeypatch):
    monkeypatch.delenv("CRM_EVAL_JUDGE_CMD", raising=False)
    assert resolve_judge_cmd() == DEFAULT_JUDGE_CMD.split()
    monkeypatch.setenv("CRM_EVAL_JUDGE_CMD", "my-judge --x")
    assert resolve_judge_cmd() == ["my-judge", "--x"]
    # explicit arg beats the env var
    assert resolve_judge_cmd("other-judge") == ["other-judge"]


def test_resolve_judge_cmd_rejects_malformed():
    # A malformed command (unbalanced quote) is rethrown as JudgeError, not a bare ValueError.
    with pytest.raises(JudgeError):
        resolve_judge_cmd('claude -p "unbalanced')


def test_judge_model_extracts_flag():
    assert judge_model(["claude", "-p", "--model", "opus"]) == "opus"
    # no --model flag → argv[0] is the honest fallback
    assert judge_model(["my-judge", "-x"]) == "my-judge"


def test_build_judge_prompt_is_blind_to_condition():
    prompt = build_judge_prompt(
        "create an account named Foo", "[agent exit 0]\ncrm entity create ..."
    )
    # the task prompt and transcript are carried through
    assert "create an account named Foo" in prompt
    assert "crm entity create" in prompt
    # blind: the template never names the condition, nor asks the judge to guess it.
    # Whole-word match so "elegance" doesn't count as a "leg" leak.
    low = prompt.lower()
    for leak in (
        "with-skill",
        "with skill",
        "no-skill",
        "no skill",
        "skill installed",
        "which leg",
    ):
        assert leak not in low
    assert not re.search(r"\b(bare|leg)\b", low)


def test_parse_judgment_accepts_valid_scores():
    scores = parse_judgment(_GOOD)
    assert scores["clarification_quality"]["score"] == 4
    assert scores["elegance"]["score"] == 5
    assert scores["elegance"]["note"] == "single create, no flailing"


def test_parse_judgment_handles_fenced_json_with_prose():
    raw = "Here is my read:\n```json\n" + _GOOD + "\n```\nThanks."
    assert parse_judgment(raw)["clarification_quality"]["score"] == 4


@pytest.mark.parametrize(
    "raw",
    [
        "no json here",
        json.dumps({"elegance": {"score": 3}}),  # missing a dimension
        json.dumps(
            {"clarification_quality": {"score": 9}, "elegance": {"score": 3}}
        ),  # out of range
        json.dumps(
            {"clarification_quality": {"score": "4"}, "elegance": {"score": 3}}
        ),  # not an int
        json.dumps(
            {"clarification_quality": {"score": True}, "elegance": {"score": 3}}
        ),  # bool ≠ int
    ],
)
def test_parse_judgment_rejects_malformed(raw):
    with pytest.raises(JudgeError):
        parse_judgment(raw)


def test_make_judge_stamps_rubric_and_model():
    j = make_judge("claude -p --model opus", run_cmd=lambda _p: _GOOD)
    verdict = j("do the thing", "[agent exit 0]\n")
    assert verdict["rubric_version"] == RUBRIC_VERSION
    assert verdict["model"] == "opus"
    assert verdict["scores"]["elegance"]["score"] == 5
    assert "error" not in verdict


def test_make_judge_never_raises_on_bad_output():
    # A judge that drifts off-shape must not fail a trial — advisory means non-fatal.
    j = make_judge("claude -p --model opus", run_cmd=lambda _p: "sorry, I cannot comply")
    verdict = j("do the thing", "[agent exit 0]\n")
    assert "scores" not in verdict
    assert "error" in verdict
    # provenance is still recorded even on failure
    assert verdict["rubric_version"] == RUBRIC_VERSION and verdict["model"] == "opus"


@pytest.mark.parametrize("exc", [OSError("binary vanished"), RuntimeError("client blew up")])
def test_make_judge_contains_any_invoker_failure(exc):
    # Advisory means it must NEVER abort a trial — any invoker error (not just OSError) is
    # contained and recorded, including an unexpected one from a custom run_cmd.
    def _boom(_p):
        raise exc

    verdict = make_judge("j", run_cmd=_boom)("p", "t")
    assert "error" in verdict and "scores" not in verdict


def test_make_judge_is_blind_receives_no_leg():
    # Structural proof of blindness: the judge closure is a (prompt, transcript) callable —
    # there is no parameter through which run_pair could pass the leg / condition. Prompt-level
    # blindness is asserted separately in test_build_judge_prompt_is_blind_to_condition.
    j = make_judge("claude -p --model opus", run_cmd=lambda _p: _GOOD)
    assert j.__code__.co_argcount == 2  # type: ignore[attr-defined]
