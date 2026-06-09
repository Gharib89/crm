"""Guard the CLI fast path: `crm --version` must not import command modules or
the D365 backend stack. Run in a subprocess so sys.modules starts clean."""
# pyright: basic

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

import click

# Command modules that must load only when their subcommand is invoked.
LAZY_MODULES = {
    "crm.commands.action", "crm.commands.app", "crm.commands.apply",
    "crm.commands.async_ops",
    "crm.commands.batch", "crm.commands.connection", "crm.commands.data",
    "crm.commands.describe",
    "crm.commands.entity", "crm.commands.form", "crm.commands.init", "crm.commands.metadata",
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


# repo root: this file is crm/tests/test_lazy_imports.py
_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_every_lazy_command_target_resolves():
    """Each _lazy_commands "module:attr" target must import and expose a Click command."""
    from crm.cli import _LazyJsonAwareGroup
    for name, target in _LazyJsonAwareGroup._lazy_commands.items():
        module_name, attr = target.split(":")
        module = importlib.import_module(module_name)
        obj = getattr(module, attr, None)
        assert isinstance(obj, click.Command), (
            f"_lazy_commands[{name!r}] -> {target!r} did not resolve to a Click command"
        )


def _spec_hiddenimports():
    """Extract the literal `hiddenimports` list from the `Analysis(...)` call in
    crm.spec via AST — NOT a loose string scan, so a `crm.commands.*` string that
    appears elsewhere in the spec can't masquerade as a bundled module."""
    spec_src = (_REPO_ROOT / "crm.spec").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(spec_src)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Analysis"):
            for kw in node.keywords:
                if kw.arg == "hiddenimports" and isinstance(kw.value, ast.List):
                    return {
                        el.value for el in kw.value.elts
                        if isinstance(el, ast.Constant) and isinstance(el.value, str)
                    }
    raise AssertionError("could not find Analysis(hiddenimports=[...]) in crm.spec")


def test_lazy_command_modules_are_bundled_in_pyinstaller_spec():
    """Every crm.commands.* module reached via the lazy loader must also be listed in
    crm.spec's Analysis(hiddenimports=...), or the frozen onedir binary will crash when
    that subcommand is invoked (PyInstaller can't follow the runtime import_module call)."""
    from crm.cli import _LazyJsonAwareGroup
    lazy_modules = {
        target.split(":")[0]
        for target in _LazyJsonAwareGroup._lazy_commands.values()
    }
    bundled = _spec_hiddenimports()
    missing = lazy_modules - bundled
    assert not missing, (
        f"crm.spec hiddenimports is missing lazily-loaded modules {sorted(missing)}; "
        f"add them or the frozen binary crashes when those commands run"
    )


def test_keyring_backends_bundled_in_pyinstaller_spec():
    """keyring (a core dependency) resolves its OS backend via entry points, which
    PyInstaller can't follow — so the package and every platform backend must be in
    crm.spec hiddenimports, or `connection set-password` is unreachable in the frozen
    binary (the exact gap that motivated bundling keyring instead of an extra)."""
    bundled = _spec_hiddenimports()
    required = {
        "keyring",
        "keyring.backends.Windows",
        "keyring.backends.macOS",
        "keyring.backends.SecretService",
    }
    missing = required - bundled
    assert not missing, (
        f"crm.spec hiddenimports is missing keyring modules {sorted(missing)}; "
        f"the frozen binary can't store secrets in the OS keyring without them"
    )
