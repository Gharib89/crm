"""Command-level tests for the trimmed `crm connection` diagnostics group."""

# pyright: basic
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import cli


class TestMissingProfileEnvelope:
    """crm --profile <missing> must emit the standard envelope, not a traceback (#109)."""

    def test_human_mode_clean_error(self):
        """Human-mode errors render on stderr; the JSON envelope (other test) on stdout."""
        result = CliRunner().invoke(cli, ["--profile", "does_not_exist", "connection", "whoami"])
        assert result.exit_code == 1
        assert "does_not_exist" in result.stderr
        assert "Traceback" not in result.stderr
        assert "FileNotFoundError" not in result.stderr

    def test_json_mode_envelope(self):
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "does_not_exist", "connection", "whoami"]
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert "does_not_exist" in payload["error"]
        assert payload["meta"]["category"] == "validation"
