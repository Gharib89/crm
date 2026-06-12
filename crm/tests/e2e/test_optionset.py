# pyright: basic
"""E2E tests for global optionset commands."""
from __future__ import annotations

import uuid
import warnings

from crm.tests.e2e.coverage import covers


@covers("metadata create-optionset", "metadata update-optionset", "metadata get-optionset", "metadata delete-optionset")
def test_optionset_lifecycle(backend):
    from crm.core import optionsets as os_mod
    name = f"new_e2e_priority_{uuid.uuid4().hex[:8]}"
    try:
        os_mod.create_optionset(
            backend, name=name, display_name="E2E Priority",
            options=[(1, "Low"), (2, "Medium")],
        )
        os_mod.update_optionset(
            backend, name,
            insert=[(7, "Critical")],
            update=[(2, "Mid")],
        )
        os_mod.get_optionset(backend, name)
    finally:
        try:
            os_mod.delete_optionset(backend, name)
        except Exception as exc:
            # Surface the leak without masking the test outcome.
            warnings.warn(
                f"e2e cleanup failed for optionset {name!r}: {exc}", stacklevel=2
            )
