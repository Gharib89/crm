# pyright: basic
"""E2E tests for query commands."""

from __future__ import annotations

from crm.tests.e2e.coverage import covers


@covers("query fetchxml")
def test_fetchxml_query_returns_contacts(backend):
    from crm.core.query import fetchxml_query

    fx = "<fetch top='3'><entity name='contact'><attribute name='fullname'/></entity></fetch>"
    result = fetchxml_query(backend, "contacts", fx)
    assert "value" in result


@covers("query odata")
def test_odata_all_and_max_records_follow_paging(backend, monkeypatch):
    """page_size=1 forces one row per server page and every followed page repeats it.

    The test org's contacts exceed 5000, so an unbounded --all would cost one GET
    per row: max_records=3 bounds the read to three pages.
    """
    from crm.core.query import odata_query

    gets: list[str] = []
    real_get = backend.get

    def counting_get(path, *a, **kw):
        gets.append(path)
        return real_get(path, *a, **kw)

    monkeypatch.setattr(backend, "get", counting_get)

    merged = odata_query(backend, "contacts", select=["fullname"], page_size=1, max_records=3)
    assert 1 <= len(merged["value"]) <= 3
    assert "@odata.nextLink" not in merged
    assert len(gets) == len(merged["value"]), "every followed page must honour page_size=1"

    single = odata_query(backend, "contacts", select=["fullname"], page_size=1)
    assert len(single["value"]) == 1
    if len(merged["value"]) >= 2:
        assert len(single["value"]) < len(merged["value"])


@covers("query odata")
def test_odata_track_changes_returns_delta_link_and_resumes(backend):
    """Account has change tracking enabled by default on Dataverse.

    So --track-changes returns an opaque @odata.deltaLink carrying the
    $deltatoken resume cursor. Resuming from that token returns only changes
    since (typically none right after the initial read) plus a fresh delta link
    to chain from.
    """
    from urllib.parse import parse_qs, urlsplit

    from crm.core.query import odata_query

    initial = odata_query(backend, "accounts", select=["name"], track_changes=True)
    assert "value" in initial
    delta_link = initial["@odata.deltaLink"]
    token = parse_qs(urlsplit(delta_link).query)["$deltatoken"][0]

    resumed = odata_query(backend, "accounts", select=["name"], delta_token=token)
    assert "value" in resumed
    assert "@odata.deltaLink" in resumed


@covers("query odata")
def test_odata_apply_groups_and_aggregates(backend):
    """A $apply groupby plus aggregate($count) returns the grouped key and the count.

    The validator blocks an inline `$`, so this aggregation can only reach the
    server through the --apply flag.
    """
    from crm.core.query import odata_query

    result = odata_query(
        backend,
        "contacts",
        apply="groupby((statuscode),aggregate($count as count))",
    )
    assert "value" in result
    for row in result["value"]:
        assert "count" in row


@covers("query odata")
def test_odata_track_changes_all_follows_pages_to_the_delta_token(backend):
    """page_size=2000 makes a table above 2000 rows span several server pages (#1289).

    The test org's contacts exceed 5000, and page_size=1 would cost one GET per
    row. The platform sends @odata.deltaLink on the last page only, so a
    page-following tracked read must carry it from there.
    """
    from urllib.parse import parse_qs, urlsplit

    from crm.core.query import odata_query

    first = odata_query(
        backend, "contacts", select=["lastname"], page_size=2000, track_changes=True
    )
    merged = odata_query(
        backend, "contacts", select=["lastname"], page_size=2000, track_changes=True, all_pages=True
    )
    assert first.get("@odata.nextLink"), "contacts must span more than one page"
    assert "@odata.deltaLink" not in first
    assert len(merged["value"]) > len(first["value"])
    assert "@odata.nextLink" not in merged
    token = parse_qs(urlsplit(merged["@odata.deltaLink"]).query)["$deltatoken"][0]
    resumed = odata_query(
        backend, "contacts", select=["lastname"], delta_token=token, all_pages=True
    )
    assert "@odata.deltaLink" in resumed
