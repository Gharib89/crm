"""Command-level tests for the trimmed `crm connection` diagnostics group."""
# pyright: basic
from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from crm.cli import cli


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path):
    # Snapshot/restore os.environ around each test and isolate CRM_HOME so no
    # real profile/session leaks in. Default CRM_DOTENV to a noop path so the
    # repo's real ./.env is never autoloaded (cf. #56).
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(tmp_path / ".crm")
    os.environ["CRM_DOTENV"] = str(tmp_path / "noop.env")
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


class TestMissingProfileEnvelope:
    """crm --profile <missing> must emit the standard envelope, not a traceback (#109)."""

    def test_human_mode_clean_error(self):
        # AC: exit 1, error mentions profile name, no raw traceback.
        result = CliRunner().invoke(cli, ["--profile", "does_not_exist", "connection", "whoami"])
        assert result.exit_code == 1
        # Human-mode errors render on stderr; the JSON envelope (other test) on stdout.
        assert "does_not_exist" in result.stderr
        assert "Traceback" not in result.stderr
        assert "FileNotFoundError" not in result.stderr

    def test_json_mode_envelope(self):
        # AC: exit 1, parseable JSON envelope with ok=false and category=validation.
        result = CliRunner().invoke(cli, ["--json", "--profile", "does_not_exist", "connection", "whoami"])
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert "does_not_exist" in payload["error"]
        assert payload["meta"]["category"] == "validation"
