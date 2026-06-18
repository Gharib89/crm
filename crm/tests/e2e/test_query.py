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


@covers("query odata")
def test_odata_track_changes_returns_delta_link_and_resumes(backend):
    from urllib.parse import parse_qs, urlsplit

    from crm.core.query import odata_query

    # account has change tracking enabled by default on Dataverse, so --track-changes
    # returns an opaque @odata.deltaLink carrying the $deltatoken resume cursor.
    initial = odata_query(backend, "accounts", select=["name"], track_changes=True)
    assert "value" in initial
    delta_link = initial["@odata.deltaLink"]
    token = parse_qs(urlsplit(delta_link).query)["$deltatoken"][0]

    # Resuming from that token returns only changes since (typically none right
    # after the initial read) plus a fresh delta link to chain from.
    resumed = odata_query(backend, "accounts", select=["name"], delta_token=token)
    assert "value" in resumed
    assert "@odata.deltaLink" in resumed


@covers("query odata")
def test_odata_apply_groups_and_aggregates(backend):
    from crm.core.query import odata_query

    # $apply groupby + aggregate($count) — the validator blocks an inline `$`, so
    # this aggregation can only reach the server through the --apply flag. Each
    # returned row carries the grouped key plus the count measure.
    result = odata_query(
        backend, "contacts",
        apply="groupby((statuscode),aggregate($count as count))",
    )
    assert "value" in result
    for row in result["value"]:
        assert "count" in row
