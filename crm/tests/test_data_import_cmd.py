# pyright: basic
"""Command-level tests for `crm data import` (#75)."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli

pytestmark = pytest.mark.usefixtures("isolated_home")


@pytest.fixture()
def jsonl_file(tmp_path):
    """A minimal JSONL fixture with two account records."""
    p = tmp_path / "accounts.jsonl"
    p.write_text(
        '{"name": "Acme Corp"}\n{"name": "Globex"}\n',
        encoding="utf-8",
    )
    return p


class _StubBackend:
    """Minimal backend stub: returns one 201-Created BatchResult per op."""

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.calls = []  # records kwargs from every batch() call

    def batch(self, ops, *, transactional, continue_on_error, timeout=None):
        self.calls.append(
            {"transactional": transactional, "continue_on_error": continue_on_error}
        )
        if self.dry_run:
            return [{"method": "POST", "url": "accounts", "status": 0,
                     "headers": {}, "body": None, "error": "dry-run"}
                    for _ in ops]
        return [{"method": "POST", "url": "accounts", "status": 201,
                 "headers": {}, "body": None, "error": None}
                for _ in ops]


class TestGuards:
    def test_continue_on_error_without_no_transaction(self, tmp_path):
        """--continue-on-error without --no-transaction → UsageError (exit 2)."""
        f = tmp_path / "data.jsonl"
        f.write_text('{"name": "x"}\n', encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "data", "import", "accounts", str(f), "--continue-on-error",
        ])
        assert result.exit_code == 2
        assert "--no-transaction" in (result.output + result.stderr)

    def test_upsert_without_id_column(self, tmp_path):
        """--mode upsert without --id-column → UsageError (exit 2)."""
        f = tmp_path / "data.jsonl"
        f.write_text('{"name": "x"}\n', encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "data", "import", "accounts", str(f), "--mode", "upsert",
        ])
        assert result.exit_code == 2
        assert "--id-column" in (result.output + result.stderr)


class TestHappyPath:
    def test_output_summary_keys(self, monkeypatch, jsonl_file):
        """Happy path: JSON output contains imported, failed, chunks."""
        stub = _StubBackend()
        monkeypatch.setattr(CLIContext, "backend", lambda self: stub)
        result = CliRunner().invoke(cli, [
            "--json", "data", "import", "accounts", str(jsonl_file),
        ])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        data = envelope["data"]
        assert "imported" in data
        assert "failed" in data
        assert "chunks" in data

    def test_flags_reach_core(self, monkeypatch, jsonl_file):
        """--no-transaction --continue-on-error → transactional=False, continue_on_error=True."""
        stub = _StubBackend()
        monkeypatch.setattr(CLIContext, "backend", lambda self: stub)
        result = CliRunner().invoke(cli, [
            "--json", "data", "import", "accounts", str(jsonl_file),
            "--no-transaction", "--continue-on-error",
        ])
        assert result.exit_code == 0, result.output
        assert len(stub.calls) >= 1
        call = stub.calls[0]
        assert call["transactional"] is False
        assert call["continue_on_error"] is True

    def test_dry_run(self, monkeypatch, jsonl_file):
        """--dry-run: output shows dry_run true, imported 0."""
        stub = _StubBackend(dry_run=True)
        monkeypatch.setattr(CLIContext, "backend", lambda self: stub)
        result = CliRunner().invoke(cli, [
            "--dry-run", "--json", "data", "import", "accounts", str(jsonl_file),
        ])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        # dry_run appears in meta (root cli --dry-run injects it) or in data
        dry_run_signal = (
            envelope.get("meta", {}).get("dry_run")
            or envelope.get("data", {}).get("dry_run")
        )
        assert dry_run_signal is True
        assert envelope["data"]["imported"] == 0

    def test_csv_format_inferred(self, monkeypatch, tmp_path):
        """--format csv: suffix→format inference wiring locks data["format"] == "csv"."""
        csv_file = tmp_path / "accounts.csv"
        csv_file.write_text("name\nAcme Corp\nGlobex\n", encoding="utf-8")
        stub = _StubBackend()
        monkeypatch.setattr(CLIContext, "backend", lambda self: stub)
        result = CliRunner().invoke(cli, [
            "--json", "data", "import", "accounts", str(csv_file),
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["format"] == "csv"

    def test_failed_records_warning(self, monkeypatch, jsonl_file):
        """When some records fail, ok is still true and meta.warnings contains the count."""
        class _MixedBackend(_StubBackend):
            def batch(self, ops, *, transactional, continue_on_error, timeout=None):
                self.calls.append(
                    {"transactional": transactional, "continue_on_error": continue_on_error}
                )
                results = []
                for i, _ in enumerate(ops):
                    if i % 2 == 0:
                        results.append({"method": "POST", "url": "accounts",
                                        "status": 204, "headers": {}, "body": None, "error": None})
                    else:
                        results.append({"method": "POST", "url": "accounts",
                                        "status": 400, "headers": {}, "body": None,
                                        "error": "Bad Request"})
                return results

        stub = _MixedBackend()
        monkeypatch.setattr(CLIContext, "backend", lambda self: stub)
        result = CliRunner().invoke(cli, [
            "--json", "data", "import", "accounts", str(jsonl_file),
            "--no-transaction",
        ])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        warnings = envelope.get("meta", {}).get("warnings", [])
        assert any("failed" in w for w in warnings)


class _MixedBackend(_StubBackend):
    """Backend stub: even-index ops succeed (204), odd-index ops fail (400)."""

    def batch(self, ops, *, transactional, continue_on_error, timeout=None):
        self.calls.append(
            {"transactional": transactional, "continue_on_error": continue_on_error}
        )
        results = []
        for i, _ in enumerate(ops):
            if i % 2 == 0:
                results.append({"method": "POST", "url": "accounts", "status": 204,
                                "headers": {}, "body": None, "error": None})
            else:
                results.append({"method": "POST", "url": "accounts", "status": 400,
                                "headers": {}, "body": None, "error": "Bad Request"})
        return results


class TestFailuresReporting:
    def test_json_mode_includes_failures_array(self, monkeypatch, jsonl_file):
        """--json: data.failures lists each failed row with index, status, error."""
        stub = _MixedBackend()
        monkeypatch.setattr(CLIContext, "backend", lambda self: stub)
        result = CliRunner().invoke(cli, [
            "--json", "data", "import", "accounts", str(jsonl_file),
            "--no-transaction", "--continue-on-error",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        # jsonl_file has two rows; row 2 (index 2) fails.
        assert data["failures"] == [
            {"index": 2, "status": 400, "error": "Bad Request"},
        ]

    def test_json_mode_no_failures_empty_array(self, monkeypatch, jsonl_file):
        """--json success path: failures present as [] (clone's convention)."""
        stub = _StubBackend()
        monkeypatch.setattr(CLIContext, "backend", lambda self: stub)
        result = CliRunner().invoke(cli, [
            "--json", "data", "import", "accounts", str(jsonl_file),
        ])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["failures"] == []

    def test_human_mode_prints_per_failure_line(self, monkeypatch, jsonl_file):
        """Human mode prints a per-failure line (row + status + error), not just a count."""
        stub = _MixedBackend()
        monkeypatch.setattr(CLIContext, "backend", lambda self: stub)
        result = CliRunner().invoke(cli, [
            "data", "import", "accounts", str(jsonl_file),
            "--no-transaction", "--continue-on-error",
        ])
        assert result.exit_code == 0, result.output
        out = result.output + result.stderr
        assert "row 2" in out
        assert "400" in out
        assert "Bad Request" in out
        # The raw failures list must not be dumped as a status line.
        assert "'index'" not in out
