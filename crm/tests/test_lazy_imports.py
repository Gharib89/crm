"""Guard the CLI fast path: `crm --version` must not import command modules or
the D365 backend stack. Run in a subprocess so sys.modules starts clean.
"""
# pyright: basic

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

import click
import pytest

# Command modules that must load only when their subcommand is invoked.
LAZY_MODULES = {
    "crm.commands.action",
    "crm.commands.app",
    "crm.commands.apply",
    "crm.commands.async_ops",
    "crm.commands.batch",
    "crm.commands.connection",
    "crm.commands.data",
    "crm.commands.describe",
    "crm.commands.entity",
    "crm.commands.form",
    "crm.commands.metadata",
    "crm.commands.profile",
    "crm.commands.query",
    "crm.commands.repl",
    "crm.commands.session",
    "crm.commands.skill",
    "crm.commands.solution",
    "crm.commands.view",
    "crm.commands.workflow",
    "crm.utils.d365_backend",
}

# HTTP-transport modules that must NOT load until an authenticated session is
# actually built (issue #247). requests + the NTLM chain (requests_ntlm →
# spnego → cryptography) cost ~1.6s at import; local-only commands never need them.
TRANSPORT_MODULES = ("requests", "requests_ntlm", "spnego", "cryptography")

# The NTLM-only sub-chain. An OAuth (Dataverse online) session uses msal alone
# and must never pull these in.
NTLM_CHAIN_MODULES = ("requests_ntlm", "spnego")

# Heavyweight stdlib chains that must load only when the code path that needs
# them runs, not at CLI bootstrap (#702). completion_registry is imported
# eagerly by crm/cli.py (PowerShell completion registration), so its
# subprocess/tempfile use would otherwise be paid on every invocation;
# appmodule's saxutils is needed only by the sitemap-XML builder.
LAZY_STDLIB_MODULES = ("subprocess", "tempfile", "xml.sax.saxutils")


def test_building_oauth_session_does_not_import_ntlm_chain():
    """Building a backend for an OAuth profile must not import the NTLM auth
    chain (requests_ntlm → spnego → cryptography) — msal alone authenticates
    the cloud path. Run in a subprocess so sys.modules starts clean; msal is
    faked so no live creds / network are needed (#247 AC).
    """
    probe = (
        "import sys, types, json, os, tempfile\n"
        "os.environ['CRM_HOME'] = tempfile.mkdtemp()\n"
        # Fake msal: _make_oauth_auth only needs SerializableTokenCache at build
        # time (the ConfidentialClientApplication is built lazily on first request).
        "fake = types.ModuleType('msal')\n"
        "class _Cache:\n"
        "    def __init__(self): self.has_state_changed = False\n"
        "    def serialize(self): return ''\n"
        "    def deserialize(self, blob): pass\n"
        "fake.SerializableTokenCache = _Cache\n"
        "sys.modules['msal'] = fake\n"
        "from crm.utils.d365_backend import D365Backend, ConnectionProfile\n"
        "p = ConnectionProfile(name='t', url='https://contoso.crm.dynamics.com',\n"
        "                      domain='', username='', auth_scheme='oauth',\n"
        "                      tenant_id='tid', client_id='cid', verify_ssl=False)\n"
        "D365Backend(p, password='secret')\n"
        f"chain = {list(NTLM_CHAIN_MODULES)!r}\n"
        "leaked = sorted(m for m in chain if m in sys.modules)\n"
        "print(json.dumps({'leaked': leaked}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["leaked"] == [], f"OAuth session build imported the NTLM chain: {data['leaked']}"


@pytest.mark.parametrize(
    "argv",
    [
        ["--version"],
        ["--json", "profile", "list"],
        ["--help"],
    ],
)
def test_local_commands_do_not_import_transport_stack(argv):
    """Local-only commands (no network I/O) must not import the HTTP-transport
    stack. `--help` is the strict case: it imports EVERY command module to render
    short help, so this proves the laziness holds below the command layer, not
    just around it. Run in a fresh interpreter (subprocess) so sys.modules is
    clean and an unrelated test's `import requests` can't mask a leak (#247 AC).
    """
    probe = (
        "import sys, json, os, tempfile\n"
        "os.environ['CRM_HOME'] = tempfile.mkdtemp()\n"
        "from click.testing import CliRunner\n"
        "from crm.cli import cli\n"
        f"result = CliRunner().invoke(cli, {argv!r})\n"
        f"transport = {list(TRANSPORT_MODULES)!r}\n"
        "leaked = sorted(m for m in transport if m in sys.modules)\n"
        "print(json.dumps({'exit': result.exit_code, 'leaked': leaked}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["exit"] == 0, f"{argv} exited {data['exit']}"
    assert data["leaked"] == [], (
        f"{argv} imported transport modules it never uses: {data['leaked']}"
    )


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
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["exit"] == 0
    assert data["output"].startswith("crm, version"), data["output"]
    assert data["leaked"] == [], f"fast path imported deferred modules: {data['leaked']}"


def test_bootstrap_does_not_import_lazy_stdlib_chains():
    """CLI bootstrap must not import the heavyweight stdlib chains that only rare
    code paths need (#702): subprocess/tempfile (pulled in eagerly by
    completion_registry, imported at crm/cli.py load for PowerShell completion)
    and xml.sax.saxutils (appmodule's sitemap-XML builder). Invoked via
    ``cli.main`` in a fresh interpreter — NOT ``click.testing.CliRunner``, which
    itself imports tempfile and would mask the guard.
    """
    probe = (
        "import sys, json, io\n"
        "from crm.cli import cli\n"
        # Swallow the version banner so stdout carries only our JSON verdict.
        "sys.stdout = io.StringIO()\n"
        "try:\n"
        "    cli.main(['--version'], standalone_mode=True)\n"
        "except SystemExit:\n"
        "    pass\n"
        "sys.stdout = sys.__stdout__\n"
        f"chains = {list(LAZY_STDLIB_MODULES)!r}\n"
        "leaked = sorted(m for m in chains if m in sys.modules)\n"
        "print(json.dumps({'leaked': leaked}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["leaked"] == [], (
        f"CLI bootstrap imported lazy stdlib chains it never uses: {data['leaked']}"
    )


def test_importing_appmodule_does_not_import_saxutils():
    """Appmodule is loaded lazily (only when the `app` command runs), so the
    bootstrap guard above can't catch a top-level saxutils re-import in it.
    Importing the module directly must leave xml.sax.saxutils out of sys.modules
    — it belongs at the sitemap-XML builder's call site, not module scope (#702).
    Fresh interpreter so sys.modules starts clean.
    """
    probe = (
        "import sys, json\n"
        "import crm.core.appmodule\n"
        "print(json.dumps({'leaked': 'xml.sax.saxutils' in sys.modules}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["leaked"] is False, "importing crm.core.appmodule eagerly imported xml.sax.saxutils"


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
    appears elsewhere in the spec can't masquerade as a bundled module.
    """
    spec_src = (_REPO_ROOT / "crm.spec").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(spec_src)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Analysis"
        ):
            for kw in node.keywords:
                if kw.arg == "hiddenimports" and isinstance(kw.value, ast.List):
                    return {
                        el.value
                        for el in kw.value.elts
                        if isinstance(el, ast.Constant) and isinstance(el.value, str)
                    }
    raise AssertionError("could not find Analysis(hiddenimports=[...]) in crm.spec")


def test_lazy_command_modules_are_bundled_in_pyinstaller_spec():
    """Every crm.commands.* module reached via the lazy loader must also be listed in
    crm.spec's Analysis(hiddenimports=...), or the frozen onedir binary will crash when
    that subcommand is invoked (PyInstaller can't follow the runtime import_module call).
    """
    from crm.cli import _LazyJsonAwareGroup

    lazy_modules = {target.split(":")[0] for target in _LazyJsonAwareGroup._lazy_commands.values()}
    bundled = _spec_hiddenimports()
    missing = lazy_modules - bundled
    assert not missing, (
        f"crm.spec hiddenimports is missing lazily-loaded modules {sorted(missing)}; "
        f"add them or the frozen binary crashes when those commands run"
    )


def test_keyring_backends_bundled_in_pyinstaller_spec():
    """Keyring (a core dependency) resolves its OS backend via entry points, which
    PyInstaller can't follow — so the package and every platform backend must be in
    crm.spec hiddenimports, or `connection set-password` is unreachable in the frozen
    binary (the exact gap that motivated bundling keyring instead of an extra).
    """
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
