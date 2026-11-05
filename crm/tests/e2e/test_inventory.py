# pyright: basic
"""E2E tests for `crm inventory extract` against a live v9 or Dataverse online org (read-only).

The whole-org read takes about 20 minutes on a Dataverse trial org (one
RetrieveEntity and one ribbon read per entity), so this test scopes the run to
one small unmanaged solution with --solution; the same code reads the whole org.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crm.tests.e2e.coverage import covers

_BUILT_IN = {"Default", "Active", "Basic"}


def _solution(cli) -> str:
    rows = json.loads(cli(["--json", "solution", "list"]).stdout)["data"]
    names = sorted(
        r["uniquename"] for r in rows if not r["ismanaged"] and r["uniquename"] not in _BUILT_IN
    )
    if not names:
        pytest.skip("the org holds no unmanaged solution to scope the inventory to")
    return names[0]


def _extract(cli, out: Path, solution: str, *, quiet: bool = False) -> dict:
    proc = cli(
        ["--json", "inventory", "extract", "--out", str(out), "--solution", solution]
        + (["-q"] if quiet else []),
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Progress goes to stderr only, and -q silences it.
    assert ("[crm] extract stage=source" in proc.stderr) is not quiet, proc.stderr
    assert "[crm] extract" not in proc.stdout
    summary = json.loads(proc.stdout)["data"]
    assert summary["complete"] is True, proc.stdout
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


@covers("inventory extract")
def test_inventory_extract_is_valid_and_repeatable(cli, tmp_path):
    import jsonschema

    from crm.core import connection as conn_mod
    from crm.core import inventory

    solution = _solution(cli)
    first = _extract(cli, tmp_path / "one", solution)
    schema_path = Path(inventory.__file__).parent.parent / "schemas"
    schema = json.loads(
        (schema_path / "inventory-manifest-v1.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(first)
    parsed = inventory.read_inventory(tmp_path / "one")
    assert parsed.source.platform in ("onprem", "cloud")
    assert parsed.scope.solutions == (solution,)
    assert [r.key for r in parsed.records if r.kind == "solution"] == [solution]

    # Each assembly records the registration facts `inventory assess` judges.
    assemblies = [r for r in parsed.records if r.kind == "plugin_assembly"]
    assert all({"source_type", "isolation_mode"} <= set(r.fields) for r in assemblies)
    findings_file = tmp_path / "findings.json"
    proc = cli(
        ["--json", "inventory", "assess", str(tmp_path / "one"), "--target", "online",
         "--out", str(findings_file)],
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    findings = json.loads(findings_file.read_text(encoding="utf-8"))["findings"]
    assert {(r.kind, r.key) for r in parsed.records} == {(f["kind"], f["key"]) for f in findings}

    # Two consecutive runs differ only in the header's extraction time.
    second = _extract(cli, tmp_path / "two", solution, quiet=True)
    assert {k for k in first if first[k] != second[k]} <= {"extracted_at"}

    # No credential reaches any output file (the throwaway e2e profile's secret).
    resolved = conn_mod.resolve_credentials(profile_name="e2e")
    secrets = [v.encode().lower() for v in (resolved.password, resolved.profile.username) if v]
    for path in (tmp_path / "one").rglob("*"):
        if path.is_file():
            data = path.read_bytes().lower()
            # The message names the file only, so a failure never prints a secret.
            assert not any(s in data for s in secrets), f"a credential appears in {path}"
