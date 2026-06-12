# pyright: basic
"""E2E tests for relationship commands."""
from __future__ import annotations

from crm.tests.e2e.coverage import covers


@covers("metadata create-one-to-many")
def test_one_to_many_to_stock_account(backend, ephemeral_entity):
    from crm.core import relationships as rel
    info = rel.create_one_to_many(
        backend,
        schema_name=f"new_account_{ephemeral_entity}",
        referenced_entity="account",
        referencing_entity=ephemeral_entity,
        lookup_schema="new_E2EAccountId",
        lookup_display="Account",
        publish=False,
    )
    assert info["created"] is True
