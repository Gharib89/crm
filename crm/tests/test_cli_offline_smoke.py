# pyright: basic
"""Offline CLI smoke tests — no live D365 backend required."""

from __future__ import annotations


def test_help():
    from click.testing import CliRunner

    from crm.cli import cli

    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output


class TestDeleteEntityCli:
    def test_delete_entity_requires_confirmation(self):
        from click.testing import CliRunner

        from crm.cli import cli

        runner = CliRunner()
        # Under --json/non-TTY the helper now fail-fasts instead of prompting.
        result = runner.invoke(
            cli,
            ["--json", "metadata", "delete-entity", "new_widget"],
            input="\n",
        )
        # Missing --yes is an operational failure → exit 1 (ADR 0001)
        assert result.exit_code == 1
        assert "Pass --yes to continue" in result.output


class TestAddAttributeBooleanDefaultParsing:
    def test_rejects_unknown_boolean_default(self):
        from click.testing import CliRunner

        from crm.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "metadata",
                "add-attribute",
                "new_widget",
                "--kind",
                "boolean",
                "--schema-name",
                "new_isactive",
                "--display",
                "Active",
                "--default-value",
                "maybe",
                "--solution",
                "TestSol",
            ],
        )
        # Click UsageError → non-zero exit + message routed to stderr
        assert result.exit_code != 0
        assert "must be one of" in (result.output + str(result.exception))


def test_python_m_crm_cli_keeps_global_flags(tmp_path):
    """``python -m crm.cli`` must honour global flags like ``python -m crm`` (#1313).

    Run as ``__main__``, ``crm/cli.py`` used to be imported a second time as
    ``crm.cli`` by the command modules, so ``pass_ctx`` built a default
    ``CLIContext`` and ``--json``/``--dry-run``/``--profile`` were silently dropped.
    """
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "CRM_HOME": str(tmp_path), "PYTHONPATH": str(repo_root)}
    proc = subprocess.run(
        [sys.executable, "-m", "crm.cli", "--json", "profile", "list"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"ok": True, "data": []}
