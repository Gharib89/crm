# pyright: basic
"""E2E tests for solution commands."""
from __future__ import annotations

import uuid

from crm.tests.e2e.coverage import covers


@covers("solution create-publisher", "solution create", "solution export")
def test_e2e_solution_export_with_customization_flag(backend, tmp_path):
    """§3.6: export_customizations=True yields a non-empty zip.

    The **Default** solution is never exportable in D365 — the server
    refuses with "Exporting the default solution is not supported". So seed
    a throwaway publisher + custom solution and a component (a custom entity
    created straight into the solution via the SolutionUniqueName header),
    export THAT, then clean up best-effort.
    """
    from crm.core import metadata as meta_mod
    from crm.core import solution as sol_mod

    suffix = uuid.uuid4().hex[:8]
    prefix = f"e2e{suffix[:4]}"          # 7 chars, starts with a letter
    pub_name = f"new_e2epub_{suffix}"
    sol_name = f"new_e2esol_{suffix}"
    ent_schema = f"{prefix}_ExportSeed"
    out = tmp_path / f"{sol_name}.zip"

    pub_id: str | None = None
    created_solution = False
    created_entity = False
    try:
        pub = sol_mod.create_publisher(
            backend, name=pub_name, prefix=prefix,
            option_value_prefix=10000 + (int(suffix, 16) % 90000),
        )
        pub_id = pub.get("publisherid")
        sol_mod.create_solution(
            backend, name=sol_name, publisher_unique_name=pub_name,
        )
        created_solution = True
        meta_mod.create_entity(
            backend, schema_name=ent_schema,
            display_name=f"E2E Export Seed {suffix}",
            solution=sol_name,
        )
        created_entity = True

        sol_mod.export_solution(
            backend, sol_name, out, export_customizations=True,
        )
        assert out.exists()
        assert out.stat().st_size > 1000
    finally:
        # Best-effort teardown in reverse order: component, then the now-empty
        # solution, then its publisher. Each guarded so one failure doesn't
        # mask the others (artifacts stay for manual cleanup).
        if created_entity:
            try:
                meta_mod.delete_entity(backend, ent_schema.lower())
            except Exception:
                pass
        if created_solution:
            try:
                sol_mod.uninstall_solution(backend, sol_name, force=True)
            except Exception:
                pass
        if pub_id:
            try:
                backend.delete(f"publishers({pub_id})")
            except Exception:
                pass
