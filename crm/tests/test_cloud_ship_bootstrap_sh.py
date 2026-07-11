# pyright: basic
"""Integration tests for scripts/cloud-ship-bootstrap.sh.

Drives the real bootstrap script as a subprocess with stubbed `pip` / `python` /
`crm` binaries on PATH (never touching the network or a real D365 org), then
asserts observable behaviour: does it fail fast on missing env vars, does it
call out to the right tools in the right order, does it ever leak the secret,
and — the point of this PR — does it run to completion **without** `gh`
anywhere on PATH (the gh-install/version-check block was removed in this PR).

Behaviour only — nothing here re-implements how the script does its job.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SH = REPO_ROOT / "scripts" / "cloud-ship-bootstrap.sh"

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="cloud-ship-bootstrap.sh integration test needs a POSIX bash",
)

REQUIRED_VARS = ("D365_URL", "D365_CLIENT_ID", "D365_TENANT_ID", "D365_CLIENT_SECRET")

VALID_ENV = {
    "D365_URL": "https://contoso.crm.dynamics.com",
    "D365_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
    "D365_TENANT_ID": "22222222-2222-2222-2222-222222222222",
    "D365_CLIENT_SECRET": "sUpEr-sEcReT-vAlUe-42",
}


def _write_stub(bin_dir: Path, name: str, log_file: Path, exit_var: str) -> None:
    """A stub that logs `name $*` to log_file then exits $<exit_var> (default 0)."""
    stub = bin_dir / name
    stub.write_text(
        f'#!/bin/sh\necho "{name} $*" >> "{log_file}"\nexit "${{{exit_var}:-0}}"\n'
    )
    stub.chmod(0o755)


def _stub_bin(tmp_path: Path, log_file: Path) -> Path:
    """A PATH entry providing only stub pip/python/crm — deliberately no `gh`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "pip", log_file, "PIP_EXIT_CODE")
    _write_stub(bin_dir, "python", log_file, "PYTHON_EXIT_CODE")
    _write_stub(bin_dir, "crm", log_file, "CRM_EXIT_CODE")
    return bin_dir


def _run_bootstrap(
    env_extra: dict[str, str], path: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for var in REQUIRED_VARS:
        env.pop(var, None)
    env.update(env_extra)
    env["PATH"] = path
    return subprocess.run(
        ["bash", str(BOOTSTRAP_SH)],
        env=env,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_bash_syntax_is_valid():
    """A basic sanity guard: the script must at least parse under bash -n."""
    result = subprocess.run(
        ["bash", "-n", str(BOOTSTRAP_SH)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("missing_var", REQUIRED_VARS)
def test_missing_required_var_aborts_before_any_command(tmp_path: Path, missing_var: str):
    """Fails fast on the first missing var — never reaches pip/python/crm."""
    log_file = tmp_path / "calls.log"
    bin_dir = _stub_bin(tmp_path, log_file)

    env = {k: v for k, v in VALID_ENV.items() if k != missing_var}
    result = _run_bootstrap(env, f"{bin_dir}:{os.environ['PATH']}")

    assert result.returncode != 0
    assert missing_var in result.stderr
    assert not log_file.exists(), f"a tool was invoked before {missing_var} was checked"


def test_happy_path_invokes_pip_python_crm_in_order(tmp_path: Path):
    """All vars present -> pip install, cffi self-heal, profile add, whoami — in order."""
    log_file = tmp_path / "calls.log"
    bin_dir = _stub_bin(tmp_path, log_file)

    result = _run_bootstrap(VALID_ENV, f"{bin_dir}:{os.environ['PATH']}")

    assert result.returncode == 0, result.stderr
    lines = log_file.read_text().splitlines()
    assert len(lines) == 4, lines

    assert lines[0].startswith("pip ")
    assert "install" in lines[0] and ".[dev,docs]" in lines[0]

    assert lines[1].startswith("python ")
    assert "-m pip install --force-reinstall cffi" in lines[1]

    assert lines[2].startswith("crm ")
    assert "profile add" in lines[2]
    assert "--name agent-cloud" in lines[2]
    assert VALID_ENV["D365_URL"] in lines[2]

    assert lines[3].startswith("crm ")
    assert "--profile agent-cloud connection whoami" in lines[3]


def test_never_echoes_secret(tmp_path: Path):
    """The script's own stdout/stderr must never surface the raw client secret."""
    log_file = tmp_path / "calls.log"
    bin_dir = _stub_bin(tmp_path, log_file)

    result = _run_bootstrap(VALID_ENV, f"{bin_dir}:{os.environ['PATH']}")

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert VALID_ENV["D365_CLIENT_SECRET"] not in combined


def test_pip_failure_stops_before_python_and_crm(tmp_path: Path):
    """set -euo pipefail: a failing pip install must short-circuit the rest."""
    log_file = tmp_path / "calls.log"
    bin_dir = _stub_bin(tmp_path, log_file)

    env = dict(VALID_ENV)
    env["PIP_EXIT_CODE"] = "1"
    result = _run_bootstrap(env, f"{bin_dir}:{os.environ['PATH']}")

    assert result.returncode != 0
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("pip ")


def test_crm_profile_add_failure_stops_before_whoami(tmp_path: Path):
    """A failing `crm profile add` must abort before the whoami sanity check runs."""
    log_file = tmp_path / "calls.log"
    bin_dir = _stub_bin(tmp_path, log_file)

    env = dict(VALID_ENV)
    env["CRM_EXIT_CODE"] = "1"
    result = _run_bootstrap(env, f"{bin_dir}:{os.environ['PATH']}")

    assert result.returncode != 0
    lines = log_file.read_text().splitlines()
    # pip + python succeeded, then the first (failing) crm call — no second crm call.
    assert len(lines) == 3
    assert lines[2].startswith("crm ")
    assert "profile add" in lines[2]


def test_runs_to_completion_without_gh_anywhere_on_path(tmp_path: Path):
    """Regression for this PR: the script must not require `gh` at all.

    PATH here is stripped down to just the stubbed pip/python/crm plus whatever
    directory provides the `bash` interpreter itself — no `gh` is exposed via the
    stub dir, so any lingering `command -v gh` / `gh --version` dependency would
    surface as a failure to find gh (which the (removed) old code treated as
    "not installed yet" and tried to download, requiring network access this
    test environment doesn't have).
    """
    log_file = tmp_path / "calls.log"
    bin_dir = _stub_bin(tmp_path, log_file)
    assert shutil.which("gh", path=str(bin_dir)) is None

    bash_path = shutil.which("bash")
    assert bash_path is not None
    bash_dir = str(Path(bash_path).parent)

    result = _run_bootstrap(VALID_ENV, f"{bin_dir}:{bash_dir}")

    assert result.returncode == 0, result.stderr
    lines = log_file.read_text().splitlines()
    assert len(lines) == 4, "pip/python/crm should all have run despite no gh on PATH"


def test_script_source_has_no_gh_install_logic():
    """Static regression guard: the removed gh-download/install block must stay gone.

    (`gh auth login` still appears once, but only inside the updated comment
    explaining that it is *not* needed — see test_script_source_documents_mcp_replacement.)
    """
    text = BOOTSTRAP_SH.read_text(encoding="utf-8")
    for removed in (
        "GH_VERSION",
        "cli/cli/releases/download",
        "release-assets.githubusercontent.com",
        "command -v gh",
        "gh --version",
        "install -m 0755",
    ):
        assert removed not in text, f"stale gh-install artifact still present: {removed!r}"


def test_script_source_documents_mcp_replacement():
    """The updated header comment must explain the new GitHub access story."""
    text = BOOTSTRAP_SH.read_text(encoding="utf-8")
    assert "mcp__" in text or "MCP connector" in text
    assert "no `gh` install or `gh auth login` is needed" in text


def test_script_still_installs_editable_package_with_dev_docs_extras():
    """Unrelated-to-gh behaviour that must be preserved by the refactor."""
    text = BOOTSTRAP_SH.read_text(encoding="utf-8")
    assert 'pip install -e ".[dev,docs]"' in text