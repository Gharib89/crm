# pyright: basic
"""Offline e2e for `crm solution pack` / `extract` (the Power Platform CLI wrappers).

Unlike the rest of the e2e suite these verbs are **offline local-file transforms**:
they shell out to `pac solution pack`/`unpack` and never touch a D365 org, profile,
connection, or secret. The `@pytest.mark.offline` marker exempts them from the live
opt-in/reachability gate (see conftest `_enforce_capability`), so they run in plain
CI with `D365_E2E` unset — the one e2e pair that needs no live target (#529).

They still need the real `pac` binary; when it is absent the tests skip with an
install hint, so a local run without pac stays green.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from crm.tests.e2e.coverage import covers

pytestmark = pytest.mark.offline

# Committed minimal unpacked-solution tree (placeholder ids only — no real-org
# GUIDs/fingerprints). `pac solution pack` reads it; the test never writes here.
FIXTURE_SRC = Path(__file__).parent / "fixtures" / "solution_src"

_PAC_HINT = (
    "pac (Power Platform CLI) not found — `crm solution pack`/`extract` shell out "
    "to `pac solution pack`/`unpack`. Install it with `dotnet tool install --global "
    "Microsoft.PowerApps.CLI.Tool` (pac >= 2.8 needs .NET 10). https://aka.ms/PowerPlatformCLI"
)


def _skip_without_pac() -> None:
    """Skip with an install hint unless a `pac` binary is resolvable the same way
    the command does: CRM_PAC / CRM_SOLUTIONPACKAGER env (a file) → PATH."""
    candidate = os.environ.get("CRM_PAC") or os.environ.get("CRM_SOLUTIONPACKAGER")
    if candidate:
        if not Path(os.path.expanduser(os.path.expandvars(candidate))).is_file():
            pytest.skip(_PAC_HINT)
        return
    if shutil.which("pac") is None:
        pytest.skip(_PAC_HINT)


@covers("solution pack", "solution extract")
def test_solution_pack_extract_roundtrip(cli, tmp_path):
    """pack the committed source tree into a zip, then extract it back and confirm
    the solution's UniqueName survived the roundtrip (real pac, both verbs)."""
    _skip_without_pac()
    built = tmp_path / "built.zip"
    restored = tmp_path / "restored"

    pack = cli(["--json", "solution", "pack",
                "--zipfile", str(built), "--folder", str(FIXTURE_SRC)])
    env = json.loads(pack.stdout)
    assert env["ok"], env
    assert env["data"]["action"] == "Pack"
    assert env["data"]["exit_code"] == 0
    assert built.is_file() and built.stat().st_size > 0

    extract = cli(["--json", "solution", "extract",
                   "--zipfile", str(built), "--folder", str(restored)])
    env = json.loads(extract.stdout)
    assert env["ok"], env
    assert env["data"]["action"] == "Extract"
    assert env["data"]["exit_code"] == 0

    solution_xml = (restored / "Other" / "Solution.xml").read_text(encoding="utf-8")
    assert "<UniqueName>crmclitestsolution</UniqueName>" in solution_xml


def test_solution_extract_bad_zip_fails(cli, tmp_path):
    """A non-solution zip makes pac exit non-zero; the command must fail (ADR 0001)
    with an error naming the real `pac solution unpack` subcommand, not succeed."""
    _skip_without_pac()
    bogus = tmp_path / "bogus.zip"
    bogus.write_bytes(b"PK\x03\x04 not a real solution zip")

    result = cli(["--json", "solution", "extract",
                  "--zipfile", str(bogus), "--folder", str(tmp_path / "out")],
                 check=False)
    assert result.returncode != 0
    env = json.loads(result.stdout)
    assert env["ok"] is False
    assert "pac solution unpack failed" in env["error"]
