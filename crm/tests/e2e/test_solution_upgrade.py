# pyright: basic
"""E2E test for the single-org managed-upgrade lifecycle:

`solution stage-and-upgrade` (stage a holding solution) + `solution apply-upgrade`
(DeleteAndPromote). The managed-upgrade flow is normally cross-org (author in a
dev org, install the managed solution in a separate test org); this collapses it
into ONE org with the hand-verified recipe:

  create-publisher → create unmanaged source `X` → `export --managed` v1 →
  set-version → `export --managed` v2 → uninstall the unmanaged author copy →
  import v1 managed (installs the base) → `stage-and-upgrade` v2 (stages a holding
  solution) → `apply-upgrade` (DeleteAndPromote replaces the base) → uninstall.

Both managed zips are exported BEFORE the unmanaged author copy is uninstalled —
once the source `X` is gone you can no longer bump+re-export it, and the source
must be dropped before the managed base can be installed under the same unique
name on the same org.

The source solution is intentionally EMPTY (no component): on a single org a
custom-entity component leaves an unmanaged base behind that a managed uninstall
cannot remove, stranding it and dirtying the org. An empty solution exports and
imports managed cleanly and leaves the org provably clean after teardown — the
acceptance criteria require a managed v1+v2 and a clean teardown, not a component.

Proof the promote actually happened: the installed managed solution's version
flips 1.0.0.0 → 2.0.0.0, which only DeleteAndPromote does.
"""
from __future__ import annotations

import json
import uuid

import pytest

from crm.tests.e2e.coverage import covers


@covers("solution stage-and-upgrade", "solution apply-upgrade")
@pytest.mark.slow
def test_managed_upgrade_single_org_lifecycle(cli, backend, tmp_path):
    """Stage then separately promote a managed-solution upgrade on a single org.

    Exercises the two-step path (`stage-and-upgrade` without --promote, then a
    separate `apply-upgrade`) so both verbs are covered, asserting each envelope.
    """
    from crm.core import solution as sol_mod

    suffix = uuid.uuid4().hex[:8]
    prefix = f"e2e{suffix[:4]}"               # 7 chars, starts with a letter
    pub_name = f"new_e2emupub_{suffix}"
    sol_name = f"new_e2emusol_{suffix}"
    v1_zip = tmp_path / f"{sol_name}_v1.zip"
    v2_zip = tmp_path / f"{sol_name}_v2.zip"

    pub_id: str | None = None
    seeded = False                            # a solution named sol_name exists on the org
    try:
        # ── seed publisher + empty unmanaged source solution (v1 = 1.0.0.0) ──
        pub = sol_mod.create_publisher(
            backend, name=pub_name, prefix=prefix,
            option_value_prefix=10000 + (int(suffix, 16) % 90000),
        )
        pub_id = pub.get("publisherid")
        sol_mod.create_solution(
            backend, name=sol_name, publisher_unique_name=pub_name,
        )
        seeded = True

        # ── export v1 managed, bump version, export v2 managed (both before the
        # unmanaged source is dropped — afterwards it can no longer be exported) ──
        sol_mod.export_solution(backend, sol_name, v1_zip, managed=True)
        assert v1_zip.exists() and v1_zip.stat().st_size > 1000, "empty v1 managed export"

        r = cli([
            "--json", "solution", "set-version", sol_name, "--version", "2.0.0.0",
        ])
        assert r.returncode == 0, f"set-version failed:\n{r.stderr}\n{r.stdout}"
        assert json.loads(r.stdout)["ok"], r.stdout

        sol_mod.export_solution(backend, sol_name, v2_zip, managed=True)
        assert v2_zip.exists() and v2_zip.stat().st_size > 1000, "empty v2 managed export"

        # ── drop the unmanaged author copy so the managed base can install under
        # the same unique name on this one org ──
        sol_mod.uninstall_solution(backend, sol_name, force=True)

        # ── install the managed base (v1) ──
        r = cli(["--json", "solution", "import", str(v1_zip), "--yes", "--quiet"])
        assert r.returncode == 0, f"v1 managed import failed:\n{r.stderr}\n{r.stdout}"
        env = json.loads(r.stdout)
        assert env["ok"], env
        assert env["data"].get("status") == "succeeded", env["data"]
        info = sol_mod.solution_info(backend, sol_name)
        assert info.get("version") == "1.0.0.0", f"base not v1: {info.get('version')!r}"

        # ── stage-and-upgrade v2 (holding solution staged, NOT yet promoted) ──
        r = cli([
            "--json", "solution", "stage-and-upgrade", str(v2_zip), "--yes", "--quiet",
        ])
        assert r.returncode == 0, f"stage-and-upgrade failed:\n{r.stderr}\n{r.stdout}"
        env = json.loads(r.stdout)
        assert env["ok"], env
        assert env["data"].get("status") == "succeeded", env["data"]
        # Staging must NOT have promoted yet — the base is still v1 until apply-upgrade.
        info = sol_mod.solution_info(backend, sol_name)
        assert info.get("version") == "1.0.0.0", (
            f"stage-and-upgrade promoted prematurely; version is {info.get('version')!r}"
        )

        # ── apply-upgrade (DeleteAndPromote replaces the base with the holding) ──
        r = cli(["--json", "solution", "apply-upgrade", sol_name, "--yes"])
        assert r.returncode == 0, f"apply-upgrade failed:\n{r.stderr}\n{r.stdout}"
        env = json.loads(r.stdout)
        assert env["ok"], env
        assert env["data"].get("promoted") is True, env["data"]
        assert env["data"].get("solution") == sol_name, env["data"]

        # Proof: only DeleteAndPromote flips the installed base 1.0.0.0 → 2.0.0.0.
        info = sol_mod.solution_info(backend, sol_name)
        assert info.get("version") == "2.0.0.0", (
            f"promote did not upgrade the base; version is {info.get('version')!r}"
        )
    finally:
        # Best-effort teardown: uninstall whatever solution named sol_name exists
        # (unmanaged source, installed managed base, or promoted v2) so the org is
        # left clean, then delete its publisher.
        if seeded:
            try:
                sol_mod.uninstall_solution(backend, sol_name, force=True)
            except Exception:
                pass
        if pub_id:
            try:
                backend.delete(f"publishers({pub_id})")
            except Exception:
                pass
