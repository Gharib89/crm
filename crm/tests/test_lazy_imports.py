"""Guard the CLI fast path: `crm --version` must not import command modules or
the D365 backend stack. Run in a subprocess so sys.modules starts clean."""
import json
import subprocess
import sys

# Command modules that must load only when their subcommand is invoked.
LAZY_MODULES = {
    "crm.commands.action", "crm.commands.app", "crm.commands.async_ops",
    "crm.commands.batch", "crm.commands.connection", "crm.commands.data",
    "crm.commands.entity", "crm.commands.init", "crm.commands.metadata",
    "crm.commands.query", "crm.commands.repl", "crm.commands.session",
    "crm.commands.skill", "crm.commands.solution", "crm.commands.view",
    "crm.commands.workflow", "crm.utils.d365_backend",
}


def test_version_does_not_import_command_modules_or_backend():
    probe = (
        "import sys, json\n"
        "from click.testing import CliRunner\n"
        "from crm.cli import cli\n"
        "result = CliRunner().invoke(cli, ['--version'])\n"
        f"lazy = {sorted(LAZY_MODULES)!r}\n"
        "leaked = sorted(set(lazy) & set(sys.modules))\n"
        "print(json.dumps({'exit': result.exit_code, "
        "'output': result.output.strip(), 'leaked': leaked}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["exit"] == 0
    assert data["output"].startswith("crm, version"), data["output"]
    assert data["leaked"] == [], f"fast path imported deferred modules: {data['leaked']}"


def test_lazy_group_still_resolves_a_subcommand():
    """The LazyGroup must still resolve a subcommand and import its module on demand."""
    from click.testing import CliRunner
    from crm.cli import cli
    result = CliRunner().invoke(cli, ["entity", "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage: crm entity" in result.output
