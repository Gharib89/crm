# pyright: basic
"""E2E tests for query commands."""
from __future__ import annotations

from crm.tests.e2e.coverage import covers


@covers("query fetchxml")
def test_fetchxml_query_returns_contacts(backend):
    from crm.core.query import fetchxml_query
    fx = (
        "<fetch top='3'>"
        "<entity name='contact'>"
        "<attribute name='fullname'/>"
        "</entity></fetch>"
    )
    result = fetchxml_query(backend, "contacts", fx)
    assert "value" in result
