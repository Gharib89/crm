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
        # No --yes, default Enter on confirm prompt → aborted
        result = runner.invoke(
            cli, ["--json", "metadata", "delete-entity", "new_widget"],
            input="\n",
        )
        # Declined confirmation is an operational failure → exit 1 (ADR 0001)
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output


class TestAddAttributeBooleanDefaultParsing:
    def test_rejects_unknown_boolean_default(self):
        from click.testing import CliRunner
        from crm.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "metadata", "add-attribute", "new_widget",
            "--kind", "boolean",
            "--schema-name", "new_isactive", "--display", "Active",
            "--solution", "MySol",
            "--default-value", "maybe",
        ])
        # Click UsageError → non-zero exit + message routed to stderr
        assert result.exit_code != 0
        assert "must be one of" in (result.output + str(result.exception))
