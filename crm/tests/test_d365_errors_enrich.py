"""Direct unit tests for the `d365_errors` enrich(exc) callback and the
reserved-key guard on `_handle_d365_error` (#533).

The seam translates a `D365Error` from one core call into the standard failure
envelope. `enrich(exc)` lets a verb derive a hint / extra `meta` *from* the caught
exception without hand-rolling its own `try/except`. These tests pin the contract:

1. The pure error envelope ({status, code, category, retryable} + raw str) is
   always built first; enrich is strictly additive.
2. A reserved key in extra_meta raises ValueError (you cannot overwrite the pure
   error) — no natural command-level trigger, so it must be unit-tested here.
3. enrich-hint wins over the static hint= when non-None.
4. enrich returning (None, None) is a clean no-op.
"""
# pyright: basic

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

from crm.cli import CLIContext
from crm.utils.d365_backend import D365Error


def _envelope(run):
    """Invoke `run` inside a click command and return the parsed emit envelope.

    `_handle_d365_error`/`d365_errors` emit `ok=False` and raise `Exit(1)`, so the
    call must happen inside a Click command for the runner to capture the output.
    """
    @click.command()
    def _cmd():
        run()

    result = CliRunner().invoke(_cmd, catch_exceptions=False)
    return json.loads(result.output)


# ── reserved-key guard ──────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["status"])
def test_extra_meta_reserved_key_raises_valueerror(key):
    """extra_meta naming a pure-error key raises ValueError before emit — the
    enrich callback can never overwrite the reserved error envelope."""
    from crm.commands._helpers import _handle_d365_error

    ctx = CLIContext()
    ctx.json_mode = True
    exc = D365Error("boom", status=400, code="0x1")

    with pytest.raises(ValueError):
        _handle_d365_error(ctx, exc, extra_meta={key: "hijacked"})


# ── enrich(exc) via the d365_errors seam ────────────────────────────────────


def test_enrich_lands_hint_and_extra_meta_additively():
    """enrich returning (hint, extra_meta) adds both atop the pure error
    envelope, which stays intact."""
    from crm.commands._helpers import d365_errors

    ctx = CLIContext()
    ctx.json_mode = True

    def run():
        with d365_errors(ctx, enrich=lambda exc: ("derived hint", {"did_you_mean": "account"})):
            raise D365Error("not found", status=404, code="0x1")

    env = _envelope(run)
    assert env["ok"] is False
    # Pure error reserved + intact.
    assert env["meta"]["status"] == 404
    assert env["meta"]["code"] == "0x1"
    assert env["meta"]["category"] == "not_found"
    assert "retryable" in env["meta"]
    # Additive enrichment.
    assert env["meta"]["did_you_mean"] == "account"
    assert env["meta"]["hint"] == "derived hint"
    assert "derived hint" in env["error"]


def test_enrich_hint_wins_over_static_hint():
    """A non-None derived hint takes precedence over the static hint=."""
    from crm.commands._helpers import d365_errors

    ctx = CLIContext()
    ctx.json_mode = True

    def run():
        with d365_errors(ctx, hint="static hint",
                         enrich=lambda exc: ("derived wins", None)):
            raise D365Error("boom", status=400, code="0x1")

    env = _envelope(run)
    assert env["meta"]["hint"] == "derived wins"


def test_enrich_none_hint_falls_back_to_static():
    """When enrich returns hint=None, the static hint= is used (entity-create
    shape: derived meta, no derived hint)."""
    from crm.commands._helpers import d365_errors

    ctx = CLIContext()
    ctx.json_mode = True

    def run():
        with d365_errors(ctx, hint="static hint",
                         enrich=lambda exc: (None, {"did_you_mean": "x"})):
            raise D365Error("boom", status=400, code="0x1")

    env = _envelope(run)
    assert env["meta"]["hint"] == "static hint"
    assert env["meta"]["did_you_mean"] == "x"


def test_enrich_none_none_is_clean_noop():
    """enrich returning (None, None) adds nothing — only the pure error
    envelope, identical to a bare `with d365_errors(ctx):`."""
    from crm.commands._helpers import d365_errors

    ctx = CLIContext()
    ctx.json_mode = True

    def run():
        with d365_errors(ctx, enrich=lambda exc: (None, None)):
            raise D365Error("boom", status=500, code="0xfff")

    env = _envelope(run)
    assert env["error"] == "boom"
    assert set(env["meta"]) == {"status", "code", "category", "retryable"}

