# pyright: basic
"""Command-level tests for `crm data delete` (issue 372)."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands import data as data_cmd

pytestmark = pytest.mark.usefixtures("isolated_home")

_FETCH = '<fetch><entity name="contact"/></fetch>'


@pytest.fixture()
def spy_core(monkeypatch):
    """Replace the bulk_delete core with a spy returning a canned submitted result."""
    calls: list[dict] = []

    def _fake(backend, entity_set, fetch_xml, *, job_name=None, wait=False, timeout=None):
        calls.append({"entity_set": entity_set, "fetch_xml": fetch_xml,
                      "job_name": job_name, "wait": wait, "timeout": timeout})
        return {"job_id": "job-1", "job_name": job_name or "x", "status": "submitted",
                "match_count": 2}

    monkeypatch.setattr(data_cmd.bulk_delete_mod, "bulk_delete", _fake)
    monkeypatch.setattr(CLIContext, "backend", lambda self: object())
    return calls


class TestGuards:
    def test_requires_a_query(self, spy_core):
        result = CliRunner().invoke(cli, ["data", "delete", "contacts", "--yes"])
        assert result.exit_code == 2
        assert "--fetchxml" in (result.output + result.stderr)
        assert spy_core == []

    def test_both_query_sources_rejected(self, spy_core, tmp_path):
        f = tmp_path / "q.xml"
        f.write_text(_FETCH, encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "data", "delete", "contacts", "--yes",
            "--fetchxml", _FETCH, "--fetchxml-file", str(f),
        ])
        assert result.exit_code == 2
        assert spy_core == []

    def test_decline_aborts_without_submitting(self, spy_core):
        result = CliRunner().invoke(
            cli, ["--json", "data", "delete", "contacts", "--fetchxml", _FETCH],
            input="n\n",
        )
        assert spy_core == []
        # A non-TTY abort writes the confirm prompt to stdout ahead of the JSON
        # envelope (shared-helper behavior), so slice from the first brace.
        env = json.loads(result.output[result.output.index("{"):])
        assert env == {"ok": False, "error": "aborted by user"}


class TestSubmit:
    def test_yes_submits_and_emits_job(self, spy_core):
        result = CliRunner().invoke(cli, [
            "--json", "data", "delete", "contacts", "--yes", "--fetchxml", _FETCH,
        ])
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["job_id"] == "job-1"
        assert spy_core[0]["entity_set"] == "contacts"
        assert spy_core[0]["wait"] is False

    def test_wait_and_job_name_forwarded(self, spy_core):
        CliRunner().invoke(cli, [
            "--json", "data", "delete", "contacts", "--yes", "--wait",
            "--job-name", "nightly", "--fetchxml", _FETCH,
        ])
        assert spy_core[0]["wait"] is True
        assert spy_core[0]["job_name"] == "nightly"
