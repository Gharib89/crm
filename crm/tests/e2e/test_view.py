# pyright: basic
"""E2E tests for view verbs: list, create, edit-columns, set-order."""
from __future__ import annotations

import json
from xml.etree import ElementTree

import pytest

from crm.tests.e2e.coverage import covers


def _resolve_otc(backend, logical: str) -> int:
    """Fetch the ObjectTypeCode for a standard entity; skip if unavailable."""
    from crm.utils.d365_backend import as_dict, D365Error
    try:
        rb = as_dict(backend.get(
            f"EntityDefinitions(LogicalName='{logical}')",
            params={"$select": "ObjectTypeCode"},
        ))
    except D365Error as exc:
        pytest.skip(f"Could not resolve OTC for {logical!r}: {exc}")
    otc = rb.get("ObjectTypeCode")
    if not isinstance(otc, int) or otc <= 0:
        pytest.skip(f"OTC for {logical!r} not available: {rb}")
    return otc


# ── view list ───────────────────────────────────────────────────────────────


@covers("view list")
def test_view_list_contact(cli):
    """Every D365 org ships public views for 'contact'; assert non-empty + shape."""
    result = cli(["--json", "view", "list", "contact"])
    assert result.returncode == 0, (
        f"view list failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list), f"expected list, got {type(items)}: {env}"
    assert len(items) > 0, "view list returned empty list for 'contact'"
    first = items[0]
    assert "savedqueryid" in first, f"savedqueryid missing from first view: {first}"
    assert "name" in first, f"name missing from first view: {first}"
    assert "isdefault" in first, f"isdefault missing from first view: {first}"
    assert "querytype" in first, f"querytype missing from first view: {first}"


# ── view create ───────────────────────────────────────────────────────────────


@covers("view create")
@pytest.mark.slow
def test_view_create_on_contact(backend, cli, request, unique):
    """Create a public system view on 'contact', assert created, clean up."""
    view_name = f"E2E View {unique}"
    otc = _resolve_otc(backend, "contact")

    created_id: list[str] = []

    def _cleanup():
        if created_id:
            try:
                backend.delete(f"savedqueries({created_id[0]})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    result = cli([
        "--json", "view", "create", "contact",
        "--name", view_name,
        "--otc", str(otc),
        "--column", "contactid",
        "--column", "firstname",
        "--column", "lastname",
        "--no-publish",
    ])
    assert result.returncode == 0, (
        f"view create failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data.get("created") is True, f"expected created=True: {data}"
    sqid = data.get("savedqueryid")
    assert sqid, f"savedqueryid missing from response: {data}"
    created_id.append(sqid)

    # Verify the record exists via direct GET.
    from crm.utils.d365_backend import as_dict
    rb = as_dict(backend.get(
        f"savedqueries({sqid})",
        params={"$select": "name,savedqueryid"},
    ))
    assert rb.get("name") == view_name, (
        f"view name mismatch: expected {view_name!r}, got {rb.get('name')!r}"
    )


@covers("view create")
@pytest.mark.slow
def test_view_create_query_type_and_description(backend, cli, request, unique):
    """--query-type + --description persist on the created savedquery."""
    view_name = f"E2E QF {unique}"
    description = f"E2E description {unique}"
    otc = _resolve_otc(backend, "contact")

    created_id: list[str] = []

    def _cleanup():
        if created_id:
            try:
                backend.delete(f"savedqueries({created_id[0]})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    result = cli([
        "--json", "view", "create", "contact",
        "--name", view_name,
        "--otc", str(otc),
        "--column", "contactid",
        "--column", "fullname",
        "--query-type", "quick-find",
        "--description", description,
        "--no-publish",
    ])
    assert result.returncode == 0, (
        f"view create failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    sqid = env["data"].get("savedqueryid")
    assert sqid, f"savedqueryid missing from response: {env}"
    created_id.append(sqid)

    # Verify querytype + description persisted via direct GET.
    from crm.utils.d365_backend import as_dict
    rb = as_dict(backend.get(
        f"savedqueries({sqid})",
        params={"$select": "querytype,description,isquickfindquery"},
    ))
    assert rb.get("querytype") == 4, f"expected quick-find querytype 4: {rb}"
    assert rb.get("isquickfindquery") is True, (
        f"expected isquickfindquery True: {rb}"
    )
    assert rb.get("description") == description, (
        f"description mismatch: {rb.get('description')!r}"
    )


@covers("view create")
@pytest.mark.slow
def test_view_filter_active_and_order_desc_round_trip(backend, cli, request, unique):
    """Create a public view with --filter-active and a descending sort, then
    confirm export-spec emits filter_active/order_desc (#597) — the export half
    of a lossless round-trip. Cleans up."""
    view_name = f"E2E Active {unique}"
    otc = _resolve_otc(backend, "contact")

    created_id: list[str] = []

    def _cleanup():
        if created_id:
            try:
                backend.delete(f"savedqueries({created_id[0]})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    result = cli([
        "--json", "view", "create", "contact",
        "--name", view_name,
        "--otc", str(otc),
        "--column", "contactid",
        "--column", "createdon",
        "--order", "createdon desc",
        "--filter-active",
        "--no-publish",
    ])
    assert result.returncode == 0, (
        f"view create failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    sqid = json.loads(result.stdout)["data"].get("savedqueryid")
    assert sqid, f"savedqueryid missing from response: {result.stdout}"
    created_id.append(sqid)

    # export-spec reads the view back from the live fetchxml.
    r_read = cli(["--json", "metadata", "export-spec", "contact", "--with-views"])
    assert r_read.returncode == 0, r_read.stderr
    env = json.loads(r_read.stdout)
    assert env["ok"], env
    views = env["data"]["entities"][0].get("views", [])
    view = next((v for v in views if v.get("name") == view_name), None)
    assert view is not None, (
        f"view {view_name!r} not found in export; saw {[v.get('name') for v in views]}"
    )
    assert view.get("filter_active") is True, view
    assert view.get("order_desc") is True, view
    assert view.get("order_by") == "createdon", view


# ── view edit-columns / set-order ───────────────────────────────────────────


def _create_view(cli, backend, request, unique, *, name_prefix):
    """Create a public view on contact and register cleanup; return its id."""
    view_name = f"{name_prefix} {unique}"
    otc = _resolve_otc(backend, "contact")
    created_id: list[str] = []

    def _cleanup():
        if created_id:
            try:
                backend.delete(f"savedqueries({created_id[0]})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)
    result = cli([
        "--json", "view", "create", "contact",
        "--name", view_name, "--otc", str(otc),
        "--column", "contactid", "--column", "firstname",
        "--no-publish",
    ])
    assert result.returncode == 0, (
        f"view create failed:\n{result.stderr}\nstdout: {result.stdout}")
    sqid = json.loads(result.stdout)["data"]["savedqueryid"]
    assert sqid, "savedqueryid missing from create response"
    created_id.append(sqid)
    return sqid


def _cells(layoutxml: str) -> dict[str, str]:
    """{cell name: width} parsed from a view's layoutxml."""
    root = ElementTree.fromstring(layoutxml)
    return {(c.get("name") or ""): (c.get("width") or "") for c in root.iter("cell")}


@covers("view edit-columns")
@pytest.mark.slow
def test_view_edit_columns_live(backend, cli, request, unique):
    """Add + resize a column, publish, and verify the published layer landed."""
    from crm.utils.d365_backend import as_dict

    sqid = _create_view(cli, backend, request, unique, name_prefix="E2E EditCols")

    # --publish drives the T3 read-back (a GET returns the published layer).
    result = cli([
        "--json", "view", "edit-columns", "contact", sqid,
        "--add", "lastname:140", "--width", "firstname:160", "--publish",
    ])
    assert result.returncode == 0, (
        f"view edit-columns failed:\n{result.stderr}\nstdout: {result.stdout}")
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert env["data"]["updated"] is True, env
    assert "lastname" in env["data"]["columns"], env

    rb = as_dict(backend.get(
        f"savedqueries({sqid})", params={"$select": "layoutxml,fetchxml"}))
    cells = _cells(rb["layoutxml"])
    assert "lastname" in cells, f"lastname cell missing: {cells}"
    assert cells.get("firstname") == "160", f"firstname width not 160: {cells}"
    # Mismatch invariant: the new column is in the fetch too.
    assert 'name="lastname"' in rb["fetchxml"], rb["fetchxml"]


def _conditions(fetchxml: str) -> list[tuple[str, str, str]]:
    """[(attribute, operator, value)] for every <condition> in a view's fetchxml."""
    root = ElementTree.fromstring(fetchxml)
    return [(c.get("attribute") or "", c.get("operator") or "",
             c.get("value") or "")
            for c in root.iter("condition")]


@covers("view add-filter", "view remove-filter")
@pytest.mark.slow
def test_view_add_and_remove_filter_live(backend, cli, request, unique):
    """Add a filter condition, publish, verify it landed; then remove it."""
    from crm.utils.d365_backend import as_dict

    sqid = _create_view(cli, backend, request, unique, name_prefix="E2E Filter")

    # Add a condition (--publish drives the T3 read-back).
    result = cli([
        "--json", "view", "add-filter", "contact", sqid,
        "--condition", "lastname eq Contoso", "--publish",
    ])
    assert result.returncode == 0, (
        f"view add-filter failed:\n{result.stderr}\nstdout: {result.stdout}")
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert env["data"]["updated"] is True, env

    rb = as_dict(backend.get(
        f"savedqueries({sqid})", params={"$select": "fetchxml"}))
    assert ("lastname", "eq", "Contoso") in _conditions(rb["fetchxml"]), rb["fetchxml"]

    # Remove it again and verify it is gone from the published layer.
    result = cli([
        "--json", "view", "remove-filter", "contact", sqid,
        "--condition", "lastname eq", "--publish",
    ])
    assert result.returncode == 0, (
        f"view remove-filter failed:\n{result.stderr}\nstdout: {result.stdout}")
    env = json.loads(result.stdout)
    assert env["ok"], env

    rb = as_dict(backend.get(
        f"savedqueries({sqid})", params={"$select": "fetchxml"}))
    assert ("lastname", "eq", "Contoso") not in _conditions(rb["fetchxml"]), rb["fetchxml"]


@covers("view set-order")
@pytest.mark.slow
def test_view_set_order_live(backend, cli, request, unique):
    """Set a descending sort, publish, and verify the fetch <order> landed."""
    from crm.utils.d365_backend import as_dict

    sqid = _create_view(cli, backend, request, unique, name_prefix="E2E SetOrder")

    result = cli([
        "--json", "view", "set-order", "contact", sqid,
        "--order", "createdon desc", "--publish",
    ])
    assert result.returncode == 0, (
        f"view set-order failed:\n{result.stderr}\nstdout: {result.stdout}")
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert env["data"]["order"] == [
        {"attribute": "createdon", "descending": True}], env

    rb = as_dict(backend.get(
        f"savedqueries({sqid})", params={"$select": "fetchxml"}))
    root = ElementTree.fromstring(rb["fetchxml"])
    orders = [(o.get("attribute"), o.get("descending"))
              for o in root.iter("order")]
    assert ("createdon", "true") in orders, f"order did not land: {orders}"
