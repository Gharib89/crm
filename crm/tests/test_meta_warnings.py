"""Tests for the structured warnings channel: meta.warnings[] (issue #64).

A single advisory channel under the JSON envelope. emit() accepts an optional
warnings list merged (appended, never clobbered) into meta.warnings. The command
helper rolls the solution warning plus any *_lookup_error read-back keys into it,
and a partial optionset failure surfaces completed_steps/failed_stage on the error
envelope while every other error site keeps emitting only {status, code, ...}.
"""
# pyright: basic
from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands._helpers import _emit_with_warning, _handle_d365_error
from crm.utils.d365_backend import D365Error


def _emit_envelope(ctx, capsys, **kw):
    ctx.emit(True, **kw)
    return json.loads(capsys.readouterr().out)


class TestEmitWarnings:
    """Direct unit tests on the single emit chokepoint."""

    def test_warnings_list_becomes_meta_array(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = True
        env = _emit_envelope(ctx, capsys, data={"x": 1}, warnings=["a", "b"])
        assert env["meta"]["warnings"] == ["a", "b"]

    def test_appends_to_existing_meta_warnings(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = True
        env = _emit_envelope(
            ctx, capsys, data={"x": 1}, meta={"warnings": ["x"]}, warnings=["y"]
        )
        assert env["meta"]["warnings"] == ["x", "y"]

    def test_non_list_existing_warning_is_coerced_not_split(self, capsys):
        # A stray scalar under meta["warnings"] must become one warning, never
        # split into characters or raise (Copilot, PR #98).
        ctx = CLIContext()
        ctx.json_mode = True
        env = _emit_envelope(
            ctx, capsys, data={"x": 1}, meta={"warnings": "oops"}, warnings=["y"]
        )
        assert env["meta"]["warnings"] == ["oops", "y"]

    def test_warnings_do_not_clobber_other_meta_keys(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = True
        env = _emit_envelope(
            ctx, capsys, data={"x": 1}, meta={"staged": True}, warnings=["y"]
        )
        assert env["meta"]["staged"] is True
        assert env["meta"]["warnings"] == ["y"]

    def test_does_not_mutate_caller_meta(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = True
        caller_meta = {"staged": True}
        _emit_envelope(ctx, capsys, data={"x": 1}, meta=caller_meta, warnings=["y"])
        assert "warnings" not in caller_meta


class TestEmitWithWarningHelper:
    def _json_ctx(self):
        ctx = CLIContext()
        ctx.json_mode = True
        return ctx

    def test_solution_warning_goes_into_warnings_array(self, capsys):
        ctx = self._json_ctx()
        _emit_with_warning(ctx, {"created": True}, "no solution resolved")
        env = json.loads(capsys.readouterr().out)
        assert env["meta"]["warnings"] == ["no solution resolved"]
        assert "warning" not in env["meta"]

    def test_lookup_error_key_surfaces_as_warning_but_stays_in_data(self, capsys):
        ctx = self._json_ctx()
        data = {"created": True, "optionset_lookup_error": "Read-back failed: boom"}
        _emit_with_warning(ctx, data, None)
        env = json.loads(capsys.readouterr().out)
        assert "Read-back failed: boom" in env["meta"]["warnings"]
        # left in data for back-compat — consumers still keying off it keep working
        assert env["data"]["optionset_lookup_error"] == "Read-back failed: boom"

    def test_solution_warning_and_lookup_error_both_surface(self, capsys):
        ctx = self._json_ctx()
        data = {"created": True, "app_lookup_error": "Read-back failed: nope"}
        _emit_with_warning(ctx, data, "no solution resolved")
        env = json.loads(capsys.readouterr().out)
        assert env["meta"]["warnings"] == [
            "no solution resolved",
            "Read-back failed: nope",
        ]


class TestHandleD365ErrorPartial:
    def _json_ctx(self):
        ctx = CLIContext()
        ctx.json_mode = True
        return ctx

    def test_partial_failure_surfaces_completed_steps_and_failed_stage(self, capsys):
        ctx = self._json_ctx()
        exc = D365Error("value 99 not found", status=400)
        exc.completed_steps = ["insert:7"]
        exc.stage = "update"
        with pytest.raises(click.exceptions.Exit):
            _handle_d365_error(ctx, exc)
        meta = json.loads(capsys.readouterr().out)["meta"]
        assert meta["completed_steps"] == ["insert:7"]
        assert meta["failed_stage"] == "update"

    def test_plain_error_omits_partial_keys(self, capsys):
        ctx = self._json_ctx()
        with pytest.raises(click.exceptions.Exit):
            _handle_d365_error(ctx, D365Error("nope", status=404, code="0x80040217"))
        meta = json.loads(capsys.readouterr().out)["meta"]
        assert "completed_steps" not in meta
        assert "failed_stage" not in meta
        assert meta["status"] == 404
        assert meta["code"] == "0x80040217"


class TestUpdateOptionsetPartialEndToEnd:
    """The real update_optionset attach path surfaced through the command boundary;
    only the HTTP POST is stubbed."""

    class _PartialStub:
        dry_run = False

        def post(self, path, *, json_body=None, solution=None):
            if path == "InsertOptionValue":
                return {}
            raise D365Error("value 99 not found", status=400)

    def test_command_surfaces_partial_optionset_context(self, monkeypatch, tmp_path):
        stub = self._PartialStub()
        monkeypatch.setattr(CLIContext, "backend", lambda _self: stub)
        result = CliRunner().invoke(
            cli,
            ["--json", "metadata", "update-optionset", "new_priority",
             "--insert-option", "7:OK", "--update-option", "99:Bad",
             "--solution", "TestSol", "--no-publish"],
            env={"CRM_HOME": str(tmp_path)},
        )
        assert result.exit_code == 1, result.output
        meta = json.loads(result.output)["meta"]
        assert meta["completed_steps"] == ["insert:7"]
        assert meta["failed_stage"] == "update"

