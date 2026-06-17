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


@covers("query odata")
def test_odata_all_and_max_records_follow_paging(backend):
    from crm.core.query import odata_query
    # page_size=1 forces one row per server page, so any entity with >1 row makes
    # --all follow @odata.nextLink across multiple live GETs.
    merged = odata_query(backend, "contacts", select=["fullname"],
                         page_size=1, all_pages=True)
    assert "value" in merged
    # Following completed → no dangling cursor in the merged envelope.
    assert "@odata.nextLink" not in merged

    capped = odata_query(backend, "contacts", select=["fullname"],
                        page_size=1, max_records=1)
    assert len(capped["value"]) <= 1
    assert len(capped["value"]) <= len(merged["value"])
    if len(merged["value"]) >= 2:
        # A single default page returns fewer rows than the fully-followed set.
        single = odata_query(backend, "contacts", select=["fullname"], page_size=1)
        assert len(single["value"]) < len(merged["value"])
