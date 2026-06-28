"""Unit tests for crm.core.views."""
# pyright: basic
from __future__ import annotations

import re

import pytest
import requests_mock

from crm.core.views import build_fetchxml, build_layoutxml
from crm.utils.d365_backend import D365Backend, D365Error


_VIEW_ID = "55555555-5555-5555-5555-555555555555"


def _post_body(m):
    for r in m.request_history:
        if r.method == "POST":
            return r.json()
    raise AssertionError("no POST recorded")


class TestCreateView:
    def test_builds_layout_and_fetch_and_posts(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            # existence guard: no view with that name yet
            m.get(backend.url_for("savedqueries"), json={"value": []})
            view_url = backend.url_for(f"savedqueries({_VIEW_ID})")
            m.post(backend.url_for("savedqueries"), status_code=204,
                   headers={"OData-EntityId": view_url})
            m.get(view_url, json={"savedqueryid": _VIEW_ID, "name": "Active Tickets"})
            out = views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="Active Tickets",
                columns=[("cwx_name", 220), ("cwx_priority", 120)],
                order_by="cwx_name", filter_active=True,
            )
        assert out["created"] is True
        assert out["savedqueryid"] == _VIEW_ID
        body = _post_body(m)
        assert body["returnedtypecode"] == "cwx_ticket"
        assert body["querytype"] == 0
        # LayoutXml carries the OTC and the columns in order
        assert 'object="10042"' in body["layoutxml"]
        assert body["layoutxml"].index("cwx_name") < body["layoutxml"].index("cwx_priority")
        # FetchXml carries the active filter + order
        assert 'attribute="statecode"' in body["fetchxml"]
        assert 'value="0"' in body["fetchxml"]
        assert 'attribute="cwx_name"' in body["fetchxml"]

    def test_query_type_advanced_find_sets_querytype_1(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            view_url = backend.url_for(f"savedqueries({_VIEW_ID})")
            m.post(backend.url_for("savedqueries"), status_code=204,
                   headers={"OData-EntityId": view_url})
            m.get(view_url, json={"savedqueryid": _VIEW_ID, "name": "AF"})
            views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="AF", columns=[("cwx_name", 220)],
                query_type="advanced-find",
            )
        body = _post_body(m)
        assert body["querytype"] == 1
        assert "isquickfindquery" not in body

    def test_query_type_quick_find_sets_isquickfindquery(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            view_url = backend.url_for(f"savedqueries({_VIEW_ID})")
            m.post(backend.url_for("savedqueries"), status_code=204,
                   headers={"OData-EntityId": view_url})
            m.get(view_url, json={"savedqueryid": _VIEW_ID, "name": "QF"})
            views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="QF", columns=[("cwx_name", 220)],
                query_type="quick-find",
            )
        body = _post_body(m)
        assert body["querytype"] == 4
        assert body["isquickfindquery"] is True

    def test_description_set_in_body_when_provided(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            view_url = backend.url_for(f"savedqueries({_VIEW_ID})")
            m.post(backend.url_for("savedqueries"), status_code=204,
                   headers={"OData-EntityId": view_url})
            m.get(view_url, json={"savedqueryid": _VIEW_ID, "name": "D"})
            views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="D", columns=[("cwx_name", 220)],
                description="My view description",
            )
        assert _post_body(m)["description"] == "My view description"

    def test_default_query_type_preserves_public_body(self, backend):
        """Omitting both new flags reproduces today's public-view body exactly."""
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            view_url = backend.url_for(f"savedqueries({_VIEW_ID})")
            m.post(backend.url_for("savedqueries"), status_code=204,
                   headers={"OData-EntityId": view_url})
            m.get(view_url, json={"savedqueryid": _VIEW_ID, "name": "P"})
            views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="P", columns=[("cwx_name", 220)],
            )
        body = _post_body(m)
        assert body["querytype"] == 0
        assert "isquickfindquery" not in body
        assert "description" not in body

    def test_existence_guard_uses_resolved_querytype(self, backend):
        """The collision probe filters on the requested type, not always 0."""
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            view_url = backend.url_for(f"savedqueries({_VIEW_ID})")
            m.post(backend.url_for("savedqueries"), status_code=204,
                   headers={"OData-EntityId": view_url})
            m.get(view_url, json={"savedqueryid": _VIEW_ID, "name": "AF"})
            views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="AF", columns=[("cwx_name", 220)],
                query_type="advanced-find",
            )
        probe = m.request_history[0]
        assert "querytype eq 1" in probe.qs["$filter"][0]

    def test_unknown_query_type_raises(self, backend):
        from crm.core import views
        with pytest.raises(D365Error, match="unknown query_type"):
            views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="X", columns=[("cwx_name", 220)], query_type="bogus")

    def test_descending_order_emits_descending_true(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            view_url = backend.url_for(f"savedqueries({_VIEW_ID})")
            m.post(backend.url_for("savedqueries"), status_code=204,
                   headers={"OData-EntityId": view_url})
            m.get(view_url, json={"savedqueryid": _VIEW_ID, "name": "Recent"})
            views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="Recent", columns=[("cwx_name", 220)],
                order_by="createdon", order_desc=True,
            )
        body = _post_body(m)
        assert 'attribute="createdon" descending="true"' in body["fetchxml"]

    def test_ascending_order_emits_descending_false(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            view_url = backend.url_for(f"savedqueries({_VIEW_ID})")
            m.post(backend.url_for("savedqueries"), status_code=204,
                   headers={"OData-EntityId": view_url})
            m.get(view_url, json={"savedqueryid": _VIEW_ID, "name": "Oldest"})
            views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="Oldest", columns=[("cwx_name", 220)],
                order_by="createdon",
            )
        body = _post_body(m)
        assert 'attribute="createdon" descending="false"' in body["fetchxml"]

    def test_existing_view_skips(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"),
                  json={"value": [{"savedqueryid": _VIEW_ID, "name": "Active Tickets"}]})
            out = views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="Active Tickets", columns=[("cwx_name", 220)],
                if_exists="skip",
            )
        assert out["skipped"] is True
        assert not any(r.method == "POST" for r in m.request_history)

    def test_existing_view_errors_by_default(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"),
                  json={"value": [{"savedqueryid": _VIEW_ID, "name": "Active Tickets"}]})
            with pytest.raises(D365Error, match="already exists"):
                views.create_view(
                    backend, entity="cwx_ticket", object_type_code=10042,
                    name="Active Tickets", columns=[("cwx_name", 220)],
                )

    def test_requires_columns(self, backend):
        from crm.core import views
        with pytest.raises(D365Error, match="at least one column"):
            views.create_view(backend, entity="cwx_ticket", object_type_code=10042,
                              name="X", columns=[])

    def test_unparseable_id_sets_lookup_error(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            m.post(backend.url_for("savedqueries"), status_code=204,
                   headers={"OData-EntityId": "https://x/savedqueries(bogus)"})
            out = views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="X", columns=[("cwx_name", 100)],
            )
        assert out["created"] is True
        assert out["savedqueryid"] is None
        assert "view_lookup_error" in out

    def test_rejects_nonpositive_width(self, backend):
        from crm.core import views
        with pytest.raises(D365Error, match="width must be positive"):
            views.create_view(backend, entity="cwx_ticket", object_type_code=10042,
                              name="X", columns=[("cwx_name", 0)])

    def test_dry_run_probes_for_real_and_reports_would_skip(self, profile):
        from crm.core import views
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            # the existence probe must issue a real GET despite dry-run
            m.get(dry.url_for("savedqueries"),
                  json={"value": [{"savedqueryid": _VIEW_ID, "name": "Active Tickets"}]})
            out = views.create_view(
                dry, entity="cwx_ticket", object_type_code=10042,
                name="Active Tickets", columns=[("cwx_name", 220)], if_exists="skip",
            )
        assert out["_dry_run"] is True
        assert out["_exists"] is True
        assert out["would_skip"] is True


# --------------------------------------------------------------------------- #
# edit-columns / set-order editors
# --------------------------------------------------------------------------- #

_ENTITY = "cwx_ticket"
_PK = "cwx_ticketid"
_LAYOUT = (
    '<grid name="resultset" object="10042" jump="cwx_name" select="1" '
    'icon="1" preview="1"><row name="result" id="cwx_ticketid">'
    '<cell name="cwx_name" width="200" />'
    '<cell name="cwx_status" width="120" /></row></grid>'
)
_FETCH = (
    '<fetch version="1.0" mapping="logical"><entity name="cwx_ticket">'
    '<attribute name="cwx_ticketid" />'
    '<attribute name="cwx_name" />'
    '<attribute name="cwx_status" />'
    '<order attribute="cwx_name" descending="false" /></entity></fetch>'
)


def _view_row(*, layout=_LAYOUT, fetch=_FETCH, iscustomizable=True, querytype=0):
    return {
        "savedqueryid": _VIEW_ID, "name": "Active Tickets",
        "returnedtypecode": _ENTITY, "querytype": querytype,
        "layoutxml": layout, "fetchxml": fetch, "layoutjson": "{}",
        "iscustomizable": {"Value": iscustomizable, "CanBeChanged": True},
    }


def _mock_resolve(m, backend, row):
    m.get(backend.url_for("savedqueries"), json={"value": [row]})


def _mock_attr_ok(m):
    m.get(re.compile(r"/Attributes\("), json={"LogicalName": "x"})


def _mock_patch(m, backend):
    m.patch(backend.url_for(f"savedqueries({_VIEW_ID})"), status_code=204)


def _patch_body(m):
    for r in m.request_history:
        if r.method == "PATCH":
            return r.json()
    raise AssertionError("no PATCH recorded")


class TestEditViewColumns:
    def test_add_adds_cell_and_attribute_and_clears_layoutjson(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            out = views.edit_view_columns(
                backend, entity=_ENTITY, view="Active Tickets",
                add=[("cwx_priority", 150)])
        body = _patch_body(m)
        assert '<cell name="cwx_priority" width="150"' in body["layoutxml"]
        assert '<attribute name="cwx_priority"' in body["fetchxml"]
        # layoutjson cleared so the server rebuilds it from the new layoutxml.
        assert body["layoutjson"] == ""
        assert out["action"] == "edit-columns"
        assert "cwx_priority" in out["columns"]

    def test_add_inserts_attribute_before_order(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.edit_view_columns(
                backend, entity=_ENTITY, view="Active Tickets",
                add=[("cwx_priority", 100)])
        fetch = _patch_body(m)["fetchxml"]
        # The new <attribute> must precede <order> — FetchXML ignores an
        # attribute placed after order/filter siblings.
        assert fetch.index('name="cwx_priority"') < fetch.index("<order")

    def test_add_validates_column_exists(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            m.get(re.compile(r"/Attributes\("), status_code=404)
            with pytest.raises(D365Error, match="does not exist"):
                views.edit_view_columns(
                    backend, entity=_ENTITY, view="Active Tickets",
                    add=[("bogus", 100)])
        assert not any(r.method == "PATCH" for r in m.request_history)

    def test_add_rejects_duplicate_column(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            with pytest.raises(D365Error, match="already on the view"):
                views.edit_view_columns(
                    backend, entity=_ENTITY, view="Active Tickets",
                    add=[("cwx_name", 100)])

    def test_add_rejects_nonpositive_width(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            with pytest.raises(D365Error, match="width must be positive"):
                views.edit_view_columns(
                    backend, entity=_ENTITY, view="Active Tickets",
                    add=[("cwx_priority", 0)])

    def test_remove_drops_cell_and_attribute(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_patch(m, backend)
            views.edit_view_columns(
                backend, entity=_ENTITY, view="Active Tickets",
                remove=["cwx_status"])
        body = _patch_body(m)
        assert "cwx_status" not in body["layoutxml"]
        assert "cwx_status" not in body["fetchxml"]

    def test_remove_refuses_primary_key(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            with pytest.raises(D365Error, match="primary-key"):
                views.edit_view_columns(
                    backend, entity=_ENTITY, view="Active Tickets",
                    remove=[_PK])

    def test_remove_missing_column_errors(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            with pytest.raises(D365Error, match="not on the view"):
                views.edit_view_columns(
                    backend, entity=_ENTITY, view="Active Tickets",
                    remove=["cwx_missing"])

    def test_width_resizes_without_touching_fetch(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_patch(m, backend)
            views.edit_view_columns(
                backend, entity=_ENTITY, view="Active Tickets",
                width=[("cwx_name", 300)])
        body = _patch_body(m)
        assert '<cell name="cwx_name" width="300"' in body["layoutxml"]
        # width-only change leaves the fetch untouched (not in the PATCH).
        assert "fetchxml" not in body
        assert body["layoutjson"] == ""

    def test_reorder_permutes_columns(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_patch(m, backend)
            views.edit_view_columns(
                backend, entity=_ENTITY, view="Active Tickets",
                reorder=["cwx_status", "cwx_name"])
        layout = _patch_body(m)["layoutxml"]
        assert (layout.index('<cell name="cwx_status"')
                < layout.index('<cell name="cwx_name"'))

    def test_reorder_must_be_a_permutation(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            with pytest.raises(D365Error, match="exactly the current columns"):
                views.edit_view_columns(
                    backend, entity=_ENTITY, view="Active Tickets",
                    reorder=["cwx_name"])

    def test_reorder_exclusive_of_other_ops(self, backend):
        from crm.core import views
        with pytest.raises(D365Error, match="cannot be combined"):
            views.edit_view_columns(
                backend, entity=_ENTITY, view="Active Tickets",
                reorder=["cwx_name", "cwx_status"], add=[("cwx_p", 100)])

    def test_no_op_errors(self, backend):
        from crm.core import views
        with pytest.raises(D365Error, match="nothing to do"):
            views.edit_view_columns(
                backend, entity=_ENTITY, view="Active Tickets")

    def test_iscustomizable_false_refuses(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(iscustomizable=False))
            with pytest.raises(D365Error, match="not customizable"):
                views.edit_view_columns(
                    backend, entity=_ENTITY, view="Active Tickets",
                    width=[("cwx_name", 300)])
        assert not any(r.method == "PATCH" for r in m.request_history)

    def test_layoutxml_less_querytype_refused(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(querytype=8192))
            with pytest.raises(D365Error, match="no editable grid layout"):
                views.edit_view_columns(
                    backend, entity=_ENTITY, view="Active Tickets",
                    width=[("cwx_name", 300)])

    def test_resolve_ambiguous_errors(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"),
                  json={"value": [_view_row(), _view_row()]})
            with pytest.raises(D365Error, match="resolve by savedqueryid"):
                views.edit_view_columns(
                    backend, entity=_ENTITY, view="Active Tickets",
                    width=[("cwx_name", 300)])

    def test_resolve_not_found_errors(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            with pytest.raises(D365Error, match="No public view named"):
                views.edit_view_columns(
                    backend, entity=_ENTITY, view="Active Tickets",
                    width=[("cwx_name", 300)])

    def test_resolve_by_guid_uses_direct_get(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"savedqueries({_VIEW_ID})"),
                  json=_view_row())
            m.patch(backend.url_for(f"savedqueries({_VIEW_ID})"),
                    status_code=204)
            views.edit_view_columns(
                backend, entity=_ENTITY, view=_VIEW_ID,
                width=[("cwx_name", 300)])
        # No collection probe — the GUID resolves directly.
        assert not any(
            r.method == "GET" and r.path.endswith("/savedqueries")
            for r in m.request_history)

    def test_dry_run_issues_no_patch(self, dry_backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, dry_backend, _view_row())
            _mock_attr_ok(m)
            out = views.edit_view_columns(
                dry_backend, entity=_ENTITY, view="Active Tickets",
                add=[("cwx_priority", 150)])
        assert out["would_update"] is True
        assert not any(r.method == "PATCH" for r in m.request_history)

    def test_publish_reads_back_and_verifies(self, backend):
        from crm.core import views

        def _echo_patched(request, context):
            # Read-back returns the published layer; echo the last PATCH body.
            for r in m.request_history:
                if r.method == "PATCH":
                    return r.json()
            raise AssertionError("read-back before any PATCH")

        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            m.post(re.compile(r"PublishAllXml"), status_code=204)
            m.get(backend.url_for(f"savedqueries({_VIEW_ID})"),
                  json=_echo_patched)
            out = views.edit_view_columns(
                backend, entity=_ENTITY, view="Active Tickets",
                add=[("cwx_priority", 150)], publish=True)
        assert out["published"] is True
        assert out["updated"] is True


_FETCH_WITH_FILTER = (
    '<fetch version="1.0" mapping="logical"><entity name="cwx_ticket">'
    '<attribute name="cwx_ticketid" />'
    '<attribute name="cwx_name" />'
    '<order attribute="cwx_name" descending="false" />'
    '<filter type="and"><condition attribute="statecode" '
    'operator="eq" value="0" /></filter></entity></fetch>'
)
# A fetch whose only filter/condition lives under a <link-entity> — the editor
# must never touch it.
_FETCH_LINKED = (
    '<fetch version="1.0" mapping="logical"><entity name="cwx_ticket">'
    '<attribute name="cwx_ticketid" />'
    '<link-entity name="account" from="accountid" to="cwx_account">'
    '<filter type="and"><condition attribute="name" operator="eq" '
    'value="Contoso" /></filter></link-entity></entity></fetch>'
)


class TestAddViewFilter:
    def test_add_eq_condition_creates_filter(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            out = views.add_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_status", "eq", ["1"])])
        fetch = _patch_body(m)["fetchxml"]
        assert '<condition attribute="cwx_status" operator="eq" value="1"' in fetch
        assert '<filter type="and">' in fetch
        assert out["action"] == "add-filter"
        assert out["conditions"] == [
            {"attribute": "cwx_status", "operator": "eq", "values": ["1"]}]

    def test_add_appends_to_existing_and_filter(self, backend):
        """A new and-condition joins the existing and-filter, not a second one."""
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=_FETCH_WITH_FILTER))
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.add_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_priority", "eq", ["2"])])
        fetch = _patch_body(m)["fetchxml"]
        # existing condition preserved, new one in the same single filter
        assert 'attribute="statecode"' in fetch
        assert 'attribute="cwx_priority"' in fetch
        assert fetch.count("<filter") == 1

    def test_add_or_type_creates_separate_filter(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=_FETCH_WITH_FILTER))
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.add_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_priority", "eq", ["2"])], filter_type="or")
        fetch = _patch_body(m)["fetchxml"]
        assert '<filter type="and">' in fetch
        assert '<filter type="or">' in fetch

    def test_add_in_operator_emits_child_value_elements(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.add_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_status", "in", ["1", "2", "3"])])
        fetch = _patch_body(m)["fetchxml"]
        assert '<condition attribute="cwx_status" operator="in">' in fetch
        assert "<value>1</value><value>2</value><value>3</value>" in fetch

    def test_add_null_operator_emits_no_value(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.add_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_closed", "null", [])])
        fetch = _patch_body(m)["fetchxml"]
        assert '<condition attribute="cwx_closed" operator="null" />' in fetch

    def test_add_between_requires_two_values(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            with pytest.raises(D365Error, match="exactly two values"):
                views.add_view_filter(
                    backend, entity=_ENTITY, view="Active Tickets",
                    conditions=[("cwx_amount", "between", ["5"])])

    def test_add_null_rejects_value(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            with pytest.raises(D365Error, match="takes no value"):
                views.add_view_filter(
                    backend, entity=_ENTITY, view="Active Tickets",
                    conditions=[("cwx_closed", "null", ["1"])])

    def test_add_cardinality_checked_before_metadata_lookup(self, backend):
        """A cardinality error surfaces without a metadata GET (fail fast). No
        /Attributes mock is registered, so a stray lookup would raise a different
        error than the cardinality D365Error asserted here."""
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            with pytest.raises(D365Error, match="exactly two values"):
                views.add_view_filter(
                    backend, entity=_ENTITY, view="Active Tickets",
                    conditions=[("cwx_amount", "between", ["5"])])
        assert not any("/Attributes" in (r.url or "") for r in m.request_history)

    def test_add_unknown_operator_errors(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            with pytest.raises(D365Error, match="unknown FetchXML operator"):
                views.add_view_filter(
                    backend, entity=_ENTITY, view="Active Tickets",
                    conditions=[("cwx_status", "equals", ["1"])])
        assert not any(r.method == "PATCH" for r in m.request_history)

    def test_add_validates_attribute_exists(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            m.get(re.compile(r"/Attributes\("), status_code=404)
            with pytest.raises(D365Error, match="does not exist"):
                views.add_view_filter(
                    backend, entity=_ENTITY, view="Active Tickets",
                    conditions=[("bogus", "eq", ["1"])])
        assert not any(r.method == "PATCH" for r in m.request_history)

    def test_add_single_value_joins_spaces(self, backend):
        """A single-value operator keeps a space-bearing value intact."""
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.add_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_name", "eq", ["Contoso", "Ltd"])])
        fetch = _patch_body(m)["fetchxml"]
        assert 'value="Contoso Ltd"' in fetch

    def test_add_filter_placed_before_link_entity(self, backend):
        from crm.core import views
        fetch_linked = (
            '<fetch version="1.0" mapping="logical"><entity name="cwx_ticket">'
            '<attribute name="cwx_ticketid" />'
            '<link-entity name="account" from="accountid" to="cwx_account">'
            '<attribute name="name" /></link-entity></entity></fetch>'
        )
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=fetch_linked))
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.add_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_status", "eq", ["1"])])
        fetch = _patch_body(m)["fetchxml"]
        assert fetch.index("<filter") < fetch.index("<link-entity")

    def test_add_empty_conditions_errors(self, backend):
        from crm.core import views
        with pytest.raises(D365Error, match="nothing to do"):
            views.add_view_filter(
                backend, entity=_ENTITY, view="Active Tickets", conditions=[])

    def test_add_dry_run_issues_no_patch(self, dry_backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, dry_backend, _view_row())
            _mock_attr_ok(m)
            out = views.add_view_filter(
                dry_backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_status", "eq", ["1"])])
        assert out["would_update"] is True
        assert not any(r.method == "PATCH" for r in m.request_history)

    def test_add_publish_reads_back_and_verifies(self, backend):
        from crm.core import views

        def _echo_patched(request, context):
            for r in m.request_history:
                if r.method == "PATCH":
                    return r.json()
            raise AssertionError("read-back before any PATCH")

        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            m.post(re.compile(r"PublishAllXml"), status_code=204)
            m.get(backend.url_for(f"savedqueries({_VIEW_ID})"), json=_echo_patched)
            out = views.add_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_status", "eq", ["1"])], publish=True)
        assert out["published"] is True
        assert out["updated"] is True


class TestRemoveViewFilter:
    def test_remove_drops_matching_condition(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=_FETCH_WITH_FILTER))
            _mock_patch(m, backend)
            out = views.remove_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("statecode", "eq", [])])
        fetch = _patch_body(m)["fetchxml"]
        assert 'attribute="statecode"' not in fetch
        assert out["action"] == "remove-filter"

    def test_remove_prunes_emptied_filter(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=_FETCH_WITH_FILTER))
            _mock_patch(m, backend)
            views.remove_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("statecode", "eq", [])])
        fetch = _patch_body(m)["fetchxml"]
        assert "<filter" not in fetch

    def test_remove_preserves_sibling_condition(self, backend):
        from crm.core import views
        two_cond = (
            '<fetch version="1.0" mapping="logical"><entity name="cwx_ticket">'
            '<attribute name="cwx_ticketid" />'
            '<filter type="and">'
            '<condition attribute="statecode" operator="eq" value="0" />'
            '<condition attribute="cwx_priority" operator="eq" value="2" />'
            '</filter></entity></fetch>'
        )
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=two_cond))
            _mock_patch(m, backend)
            views.remove_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("statecode", "eq", [])])
        fetch = _patch_body(m)["fetchxml"]
        assert 'attribute="statecode"' not in fetch
        assert 'attribute="cwx_priority"' in fetch
        assert "<filter" in fetch  # filter kept — still has a sibling

    def test_remove_no_match_errors(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=_FETCH_WITH_FILTER))
            with pytest.raises(D365Error, match="no condition matches"):
                views.remove_view_filter(
                    backend, entity=_ENTITY, view="Active Tickets",
                    conditions=[("cwx_priority", "eq", [])])
        assert not any(r.method == "PATCH" for r in m.request_history)

    def test_remove_ambiguous_errors(self, backend):
        from crm.core import views
        dup = (
            '<fetch version="1.0" mapping="logical"><entity name="cwx_ticket">'
            '<attribute name="cwx_ticketid" />'
            '<filter type="and">'
            '<condition attribute="cwx_priority" operator="eq" value="1" />'
            '<condition attribute="cwx_priority" operator="eq" value="2" />'
            '</filter></entity></fetch>'
        )
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=dup))
            with pytest.raises(D365Error, match="disambiguate"):
                views.remove_view_filter(
                    backend, entity=_ENTITY, view="Active Tickets",
                    conditions=[("cwx_priority", "eq", [])])

    def test_remove_value_disambiguates(self, backend):
        from crm.core import views
        dup = (
            '<fetch version="1.0" mapping="logical"><entity name="cwx_ticket">'
            '<attribute name="cwx_ticketid" />'
            '<filter type="and">'
            '<condition attribute="cwx_priority" operator="eq" value="1" />'
            '<condition attribute="cwx_priority" operator="eq" value="2" />'
            '</filter></entity></fetch>'
        )
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=dup))
            _mock_patch(m, backend)
            views.remove_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_priority", "eq", ["1"])])
        fetch = _patch_body(m)["fetchxml"]
        assert 'value="1"' not in fetch
        assert 'value="2"' in fetch

    def test_remove_never_touches_link_entity_filter(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=_FETCH_LINKED))
            with pytest.raises(D365Error, match="no condition matches"):
                views.remove_view_filter(
                    backend, entity=_ENTITY, view="Active Tickets",
                    conditions=[("name", "eq", ["Contoso"])])
        assert not any(r.method == "PATCH" for r in m.request_history)

    def test_remove_empty_conditions_errors(self, backend):
        from crm.core import views
        with pytest.raises(D365Error, match="nothing to do"):
            views.remove_view_filter(
                backend, entity=_ENTITY, view="Active Tickets", conditions=[])

    def test_remove_two_conditions_from_same_filter(self, backend):
        """Removing several conditions from one filter drops all and prunes the
        now-empty filter exactly once (no double-remove crash)."""
        from crm.core import views
        two_cond = (
            '<fetch version="1.0" mapping="logical"><entity name="cwx_ticket">'
            '<attribute name="cwx_ticketid" />'
            '<filter type="and">'
            '<condition attribute="statecode" operator="eq" value="0" />'
            '<condition attribute="cwx_priority" operator="eq" value="2" />'
            '</filter></entity></fetch>'
        )
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=two_cond))
            _mock_patch(m, backend)
            views.remove_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("statecode", "eq", []), ("cwx_priority", "eq", [])])
        fetch = _patch_body(m)["fetchxml"]
        assert "statecode" not in fetch
        assert "cwx_priority" not in fetch
        assert "<filter" not in fetch  # emptied filter pruned once, no crash

    def test_remove_cascades_prune_to_emptied_parent_filter(self, backend):
        """Removing the last condition in a nested filter prunes the inner filter
        AND its now-empty parent, matching the documented pruning behavior."""
        from crm.core import views
        nested = (
            '<fetch version="1.0" mapping="logical"><entity name="cwx_ticket">'
            '<attribute name="cwx_ticketid" />'
            '<filter type="and"><filter type="or">'
            '<condition attribute="cwx_priority" operator="eq" value="1" />'
            '</filter></filter></entity></fetch>'
        )
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=nested))
            _mock_patch(m, backend)
            views.remove_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_priority", "eq", [])])
        fetch = _patch_body(m)["fetchxml"]
        assert "<filter" not in fetch  # both inner and emptied parent pruned

    def test_remove_duplicate_spec_errors_cleanly(self, backend):
        """The same condition listed twice yields a clean NotFound on the second
        pass, not an ElementTree ValueError."""
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=_FETCH_WITH_FILTER))
            _mock_patch(m, backend)
            with pytest.raises(D365Error, match="no condition matches"):
                views.remove_view_filter(
                    backend, entity=_ENTITY, view="Active Tickets",
                    conditions=[("statecode", "eq", []), ("statecode", "eq", [])])

    def test_remove_does_not_validate_attribute_existence(self, backend):
        """A filter on a since-deleted column can still be removed (no metadata
        existence check on remove)."""
        from crm.core import views
        deleted_attr = (
            '<fetch version="1.0" mapping="logical"><entity name="cwx_ticket">'
            '<attribute name="cwx_ticketid" />'
            '<filter type="and"><condition attribute="cwx_gone" '
            'operator="eq" value="1" /></filter></entity></fetch>'
        )
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=deleted_attr))
            # no /Attributes mock — a metadata lookup would 500 (connection error)
            _mock_patch(m, backend)
            views.remove_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("cwx_gone", "eq", [])])
        fetch = _patch_body(m)["fetchxml"]
        assert "cwx_gone" not in fetch

    def test_remove_publish_reads_back_and_verifies(self, backend):
        """publish=True triggers read-back; _verify runs against the returned XML."""
        from crm.core import views

        def _echo_patched(request, context):
            for r in m.request_history:
                if r.method == "PATCH":
                    return r.json()
            raise AssertionError("read-back before any PATCH")

        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=_FETCH_WITH_FILTER))
            _mock_patch(m, backend)
            m.post(re.compile(r"PublishAllXml"), status_code=204)
            m.get(backend.url_for(f"savedqueries({_VIEW_ID})"), json=_echo_patched)
            out = views.remove_view_filter(
                backend, entity=_ENTITY, view="Active Tickets",
                conditions=[("statecode", "eq", [])], publish=True)
        assert out["published"] is True
        assert out["updated"] is True

    def test_remove_publish_readback_verifies_failure(self, backend):
        """_verify raises if the read-back still shows the removed condition."""
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=_FETCH_WITH_FILTER))
            _mock_patch(m, backend)
            m.post(re.compile(r"PublishAllXml"), status_code=204)
            # Read-back returns the ORIGINAL fetchxml (condition was not removed)
            m.get(backend.url_for(f"savedqueries({_VIEW_ID})"),
                  json={"fetchxml": _FETCH_WITH_FILTER})
            with pytest.raises(D365Error, match="read-back"):
                views.remove_view_filter(
                    backend, entity=_ENTITY, view="Active Tickets",
                    conditions=[("statecode", "eq", [])], publish=True)


class TestSetViewOrder:
    def test_order_replaces_sort(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.set_view_order(
                backend, entity=_ENTITY, view="Active Tickets",
                order=[("createdon", True)])
        fetch = _patch_body(m)["fetchxml"]
        assert '<order attribute="createdon" descending="true"' in fetch
        # the previous cwx_name order is replaced.
        assert 'attribute="cwx_name" descending' not in fetch

    def test_add_order_appends(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.set_view_order(
                backend, entity=_ENTITY, view="Active Tickets",
                add_order=[("createdon", False)])
        fetch = _patch_body(m)["fetchxml"]
        assert 'attribute="cwx_name"' in fetch
        assert 'attribute="createdon"' in fetch

    def test_clear_order_removes_all(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_patch(m, backend)
            views.set_view_order(
                backend, entity=_ENTITY, view="Active Tickets",
                clear_order=True)
        fetch = _patch_body(m)["fetchxml"]
        assert "<order" not in fetch

    def test_validates_order_attribute(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            m.get(re.compile(r"/Attributes\("), status_code=404)
            with pytest.raises(D365Error, match="does not exist"):
                views.set_view_order(
                    backend, entity=_ENTITY, view="Active Tickets",
                    order=[("bogus", False)])

    def test_protects_filter_sibling(self, backend):
        from crm.core import views
        fetch_with_filter = (
            '<fetch version="1.0" mapping="logical">'
            '<entity name="cwx_ticket">'
            '<attribute name="cwx_ticketid" />'
            '<attribute name="cwx_name" />'
            '<order attribute="cwx_name" descending="false" />'
            '<filter type="and"><condition attribute="statecode" '
            'operator="eq" value="0" /></filter></entity></fetch>'
        )
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row(fetch=fetch_with_filter))
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.set_view_order(
                backend, entity=_ENTITY, view="Active Tickets",
                order=[("createdon", True)])
        fetch = _patch_body(m)["fetchxml"]
        assert '<condition attribute="statecode"' in fetch

    def test_order_placed_after_attributes(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            _mock_resolve(m, backend, _view_row())
            _mock_attr_ok(m)
            _mock_patch(m, backend)
            views.set_view_order(
                backend, entity=_ENTITY, view="Active Tickets",
                order=[("createdon", True)])
        fetch = _patch_body(m)["fetchxml"]
        assert fetch.index("<attribute") < fetch.index("<order")

    def test_no_op_errors(self, backend):
        from crm.core import views
        with pytest.raises(D365Error, match="nothing to do"):
            views.set_view_order(
                backend, entity=_ENTITY, view="Active Tickets")


class TestViewEditCommandUsage:
    """Invalid flag combinations are usage errors (exit 2) at the command layer,
    raised before any backend call."""

    def _invoke(self, args):
        from click.testing import CliRunner
        from crm.cli import cli
        return CliRunner().invoke(cli, args)

    def test_edit_columns_no_flags_is_usage_error(self):
        result = self._invoke(["view", "edit-columns", _ENTITY, "View"])
        assert result.exit_code == 2
        assert "nothing to do" in result.output

    def test_edit_columns_reorder_with_add_is_usage_error(self):
        result = self._invoke([
            "view", "edit-columns", _ENTITY, "View",
            "--reorder", "a,b", "--add", "c"])
        assert result.exit_code == 2
        assert "cannot be combined" in result.output

    def test_set_order_no_flags_is_usage_error(self):
        result = self._invoke(["view", "set-order", _ENTITY, "View"])
        assert result.exit_code == 2
        assert "nothing to do" in result.output

    def test_add_filter_malformed_condition_is_usage_error(self):
        result = self._invoke(
            ["view", "add-filter", _ENTITY, "View", "--condition", "loneword"])
        assert result.exit_code == 2
        combined = result.output + result.stderr
        assert "operator" in combined


class TestViewCommand:
    def test_view_create_command_wires_core(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_create_view(backend, **kw):
            captured.update(kw)
            return {"created": True, "savedqueryid": _VIEW_ID, "name": kw["name"]}

        monkeypatch.setattr("crm.core.views.create_view", fake_create_view)
        # Avoid a real backend/publish:
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.core.solution.publish_all", lambda b: {"ok": True})

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "view", "create", "cwx_ticket",
            "--name", "Active Tickets", "--otc", "10042",
            "--column", "cwx_name:220", "--column", "cwx_priority:120",
            "--order", "cwx_name", "--filter-active", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["entity"] == "cwx_ticket"
        assert captured["object_type_code"] == 10042
        assert captured["columns"] == [("cwx_name", 220), ("cwx_priority", 120)]
        assert captured["order_by"] == "cwx_name"
        assert captured["filter_active"] is True

    def test_view_create_wires_query_type_and_description(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.views.create_view",
            lambda backend, **kw: captured.update(kw) or {"created": True, "name": kw["name"]})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "view", "create", "cwx_ticket", "--name", "X", "--otc", "1",
            "--column", "cwx_name:220", "--query-type", "quick-find",
            "--description", "Quick find columns", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["query_type"] == "quick-find"
        assert captured["description"] == "Quick find columns"

    def test_view_create_query_type_defaults_public(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.views.create_view",
            lambda backend, **kw: captured.update(kw) or {"created": True, "name": kw["name"]})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "view", "create", "cwx_ticket", "--name", "X", "--otc", "1",
            "--column", "cwx_name:220", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["query_type"] == "public"
        assert captured["description"] is None

    def test_view_create_rejects_unknown_query_type(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "view", "create", "cwx_ticket", "--name", "X", "--otc", "1",
            "--column", "cwx_name:220", "--query-type", "bogus", "--no-publish",
        ])
        assert result.exit_code == 2, result.output

    def test_view_create_parses_descending_order(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.views.create_view",
            lambda backend, **kw: captured.update(kw) or {"created": True, "name": kw["name"]})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "view", "create", "cwx_ticket", "--name", "X", "--otc", "1",
            "--column", "cwx_name:220", "--order", "createdon desc", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["order_by"] == "createdon"
        assert captured["order_desc"] is True

    def test_view_create_ascending_order_default(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.views.create_view",
            lambda backend, **kw: captured.update(kw) or {"created": True, "name": kw["name"]})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "view", "create", "cwx_ticket", "--name", "X", "--otc", "1",
            "--column", "cwx_name:220", "--order", "createdon", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["order_by"] == "createdon"
        assert captured["order_desc"] is False

    def test_view_create_invalid_direction_exits_2(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "view", "create", "cwx_ticket", "--name", "X", "--otc", "1",
            "--column", "cwx_name:220", "--order", "createdon banana", "--no-publish",
        ])
        assert result.exit_code == 2, result.output
        combined = result.output + result.stderr
        assert "asc" in combined and "desc" in combined

    def test_view_add_filter_command_wires_core(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.views.add_view_filter",
            lambda backend, **kw: captured.update(kw) or {"action": "add-filter"})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "view", "add-filter", "cwx_ticket", "Active Tickets",
            "--condition", "cwx_status eq 1", "--condition", "cwx_amount between 5 9",
            "--type", "or", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["conditions"] == [
            ("cwx_status", "eq", ["1"]), ("cwx_amount", "between", ["5", "9"])]
        assert captured["filter_type"] == "or"

    def test_view_remove_filter_command_wires_core(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.views.remove_view_filter",
            lambda backend, **kw: captured.update(kw) or {"action": "remove-filter"})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "view", "remove-filter", "cwx_ticket", "Active Tickets",
            "--condition", "statecode eq", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["conditions"] == [("statecode", "eq", [])]

    def test_view_create_strips_column_whitespace(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.views.create_view",
            lambda backend, **kw: captured.update(kw) or {"created": True, "name": kw["name"]})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "view", "create", "cwx_ticket", "--name", "X", "--otc", "1",
            "--column", " cwx_name : 220 ", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["columns"] == [("cwx_name", 220)]


# ---------------------------------------------------------------------------
# crm view list
# ---------------------------------------------------------------------------

# Saved-query rows used across `view list` tests. layoutxml/fetchxml are absent
# on purpose — `view list` projects them away, mirroring how `form list` drops
# formxml; the reader tolerates their absence (columns → []).
_VIEW_ROW_A = {
    "savedqueryid": "aaaaaaaa-0000-0000-0000-000000000001",
    "name": "Active Tickets",
    "isdefault": True,
    "querytype": 0,
}
_VIEW_ROW_B = {
    "savedqueryid": "bbbbbbbb-0000-0000-0000-000000000002",
    "name": "All Tickets",
    "isdefault": False,
    "querytype": 0,
}


def _views_url(backend):
    return backend.url_for("savedqueries")


class TestViewList:
    def test_list_renders_view_names(self, backend, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with requests_mock.Mocker() as m:
            m.get(_views_url(backend), json={"value": [_VIEW_ROW_A, _VIEW_ROW_B]})
            result = CliRunner().invoke(cli, ["--json", "view", "list", "cwx_ticket"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        names = [v["name"] for v in data["data"]]
        assert "Active Tickets" in names
        assert "All Tickets" in names

    def test_list_projects_to_list_fields_only(self, backend, monkeypatch):
        """JSON rows carry exactly name/savedqueryid/isdefault/querytype — no
        columns/order_by (mirrors `form list` dropping formxml)."""
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with requests_mock.Mocker() as m:
            m.get(_views_url(backend), json={"value": [_VIEW_ROW_A]})
            result = CliRunner().invoke(cli, ["--json", "view", "list", "cwx_ticket"])
        assert result.exit_code == 0, result.output
        row = json.loads(result.output)["data"][0]
        assert row == {
            "name": "Active Tickets",
            "savedqueryid": "aaaaaaaa-0000-0000-0000-000000000001",
            "isdefault": True,
            "querytype": 0,
        }

    def test_list_renders_table_in_human_mode(self, backend, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with requests_mock.Mocker() as m:
            m.get(_views_url(backend), json={"value": [_VIEW_ROW_A]})
            result = CliRunner().invoke(cli, ["view", "list", "cwx_ticket"])
        assert result.exit_code == 0, result.output
        assert "Active Tickets" in result.output

    def test_list_filters_to_queried_entity(self, backend, monkeypatch):
        """The GET request URL must include the entity logical name in the filter."""
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with requests_mock.Mocker() as m:
            m.get(_views_url(backend), json={"value": []})
            CliRunner().invoke(cli, ["view", "list", "cwx_ticket"])
        assert "cwx_ticket" in m.last_request.url

    def test_list_empty_exits_ok(self, backend, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with requests_mock.Mocker() as m:
            m.get(_views_url(backend), json={"value": []})
            result = CliRunner().invoke(cli, ["--json", "view", "list", "cwx_ticket"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"] == []


# ---------------------------------------------------------------------------
# Tests for read_entity_views
# ---------------------------------------------------------------------------

_READ_VIEW_ID = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"


class TestReadEntityViews:
    def test_view_with_columns_and_order_by(self, backend):
        from crm.core.views import read_entity_views
        cols = [("cwx_name", 220), ("cwx_priority", 120)]
        layoutxml = build_layoutxml("cwx_ticket", 10042, cols)
        fetchxml = build_fetchxml("cwx_ticket", cols, "cwx_name", False)
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("savedqueries"),
                json={"value": [{
                    "savedqueryid": _READ_VIEW_ID,
                    "name": "Active Tickets",
                    "layoutxml": layoutxml,
                    "fetchxml": fetchxml,
                    "isdefault": True,
                }]},
            )
            views = read_entity_views(backend, "cwx_ticket")
        assert len(views) == 1
        v = views[0]
        assert v["name"] == "Active Tickets"
        assert v["is_default"] is True
        assert v["columns"] == [
            {"name": "cwx_name", "width": 220},
            {"name": "cwx_priority", "width": 120},
        ]
        assert v["order_by"] == "cwx_name"

    def test_view_with_no_order_element(self, backend):
        from crm.core.views import read_entity_views
        cols = [("cwx_name", 200)]
        layoutxml = build_layoutxml("cwx_ticket", 10042, cols)
        fetchxml = build_fetchxml("cwx_ticket", cols, None, False)
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("savedqueries"),
                json={"value": [{
                    "savedqueryid": _READ_VIEW_ID,
                    "name": "All Tickets",
                    "layoutxml": layoutxml,
                    "fetchxml": fetchxml,
                    "isdefault": False,
                }]},
            )
            views = read_entity_views(backend, "cwx_ticket")
        assert len(views) == 1
        assert "order_by" not in views[0]

    def test_filter_active_and_order_desc_emitted_when_set(self, backend):
        """A view whose fetchxml carries an active-state filter and a descending
        sort emits `filter_active`/`order_desc` (the apply view adapter keys), so
        export-spec → apply preserves them."""
        from crm.core.views import read_entity_views
        cols = [("cwx_name", 200)]
        layoutxml = build_layoutxml("cwx_ticket", 10042, cols)
        fetchxml = build_fetchxml("cwx_ticket", cols, "cwx_name", True, True)
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("savedqueries"),
                json={"value": [{
                    "savedqueryid": _READ_VIEW_ID,
                    "name": "Active Tickets",
                    "layoutxml": layoutxml,
                    "fetchxml": fetchxml,
                    "isdefault": False,
                }]},
            )
            views = read_entity_views(backend, "cwx_ticket")
        v = views[0]
        assert v["filter_active"] is True
        assert v["order_desc"] is True
        assert v["order_by"] == "cwx_name"

    def test_filter_active_and_order_desc_omitted_when_default(self, backend):
        """No active-state filter and an ascending (or absent) sort → neither key
        is emitted (defaults omitted, no spec bloat)."""
        from crm.core.views import read_entity_views
        cols = [("cwx_name", 200)]
        layoutxml = build_layoutxml("cwx_ticket", 10042, cols)
        fetchxml = build_fetchxml("cwx_ticket", cols, "cwx_name", False, False)
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("savedqueries"),
                json={"value": [{
                    "name": "All Tickets",
                    "layoutxml": layoutxml,
                    "fetchxml": fetchxml,
                    "isdefault": False,
                }]},
            )
            views = read_entity_views(backend, "cwx_ticket")
        assert "filter_active" not in views[0]
        assert "order_desc" not in views[0]

    def test_link_entity_order_and_filter_do_not_leak_into_main_view(self, backend):
        """order_by/order_desc/filter_active are read from the ROOT entity only —
        a sort or active-state filter that lives inside a <link-entity> must not
        be mis-attributed to the main view (which would export a key that changes
        the view's semantics on re-apply)."""
        from crm.core.views import read_entity_views
        # Root entity: plain ascending sort on cwx_name, no active filter.
        # Link-entity: a descending order AND a statecode=0 filter — neither
        # should surface on the main view.
        fetchxml = (
            '<fetch><entity name="cwx_ticket">'
            '<attribute name="cwx_ticketid" />'
            '<order attribute="cwx_name" descending="false" />'
            '<link-entity name="account" from="accountid" to="cwx_accountid">'
            '<order attribute="name" descending="true" />'
            '<filter type="and"><condition attribute="statecode" operator="eq" value="0" /></filter>'
            '</link-entity>'
            '</entity></fetch>'
        )
        layoutxml = build_layoutxml("cwx_ticket", 10042, [("cwx_name", 200)])
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("savedqueries"),
                json={"value": [{
                    "name": "Joined View",
                    "layoutxml": layoutxml,
                    "fetchxml": fetchxml,
                    "isdefault": False,
                }]},
            )
            views = read_entity_views(backend, "cwx_ticket")
        v = views[0]
        assert v["order_by"] == "cwx_name"      # root sort, not the link-entity's
        assert "order_desc" not in v            # root sort is ascending
        assert "filter_active" not in v         # statecode filter was the link-entity's

    def test_view_includes_savedqueryid_and_querytype(self, backend):
        """`view list` needs the id + querytype, so the reader must surface them."""
        from crm.core.views import read_entity_views
        cols = [("cwx_name", 200)]
        layoutxml = build_layoutxml("cwx_ticket", 10042, cols)
        fetchxml = build_fetchxml("cwx_ticket", cols, None, False)
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("savedqueries"),
                json={"value": [{
                    "savedqueryid": _READ_VIEW_ID,
                    "name": "All Tickets",
                    "layoutxml": layoutxml,
                    "fetchxml": fetchxml,
                    "isdefault": False,
                    "querytype": 0,
                }]},
            )
            views = read_entity_views(backend, "cwx_ticket")
        assert views[0]["savedqueryid"] == _READ_VIEW_ID
        assert views[0]["querytype"] == 0

    def test_no_public_views_returns_empty(self, backend):
        from crm.core.views import read_entity_views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            views = read_entity_views(backend, "cwx_ticket")
        assert views == []

    def test_single_quote_escaped_in_filter(self, backend):
        """Single quotes in entity name are doubled in the OData $filter literal."""
        from crm.core.views import read_entity_views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"), json={"value": []})
            read_entity_views(backend, "o'brien_entity")
        qs = m.request_history[0].qs
        # requests encodes the query string; decode and check the raw filter value
        filter_val = qs["$filter"][0]
        assert "o''brien_entity" in filter_val

    def test_non_numeric_width_omitted_no_crash(self, backend):
        """A cell with a non-numeric or empty width attribute must not crash and
        must omit the width key (inline literal needed: build_layoutxml always
        emits integer widths so it cannot produce this edge-case input)."""
        from crm.core.views import read_entity_views
        # Two cells: one with width="auto" (non-numeric), one with width="" (empty).
        layoutxml = (
            '<grid name="resultset" object="10042" jump="cwx_name" '
            'select="1" icon="1" preview="1">'
            '<row name="result" id="cwx_ticketid">'
            '<cell name="cwx_name" width="auto" />'
            '<cell name="cwx_priority" width="" />'
            '</row></grid>'
        )
        fetchxml = build_fetchxml("cwx_ticket", [("cwx_name", 1)], None, False)
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("savedqueries"),
                json={"value": [{
                    "name": "Edge Case View",
                    "layoutxml": layoutxml,
                    "fetchxml": fetchxml,
                    "isdefault": False,
                }]},
            )
            views = read_entity_views(backend, "cwx_ticket")
        assert len(views) == 1
        cols = views[0]["columns"]
        assert len(cols) == 2
        # width must be absent from both columns (unparseable → omitted)
        assert "width" not in cols[0]
        assert "width" not in cols[1]
        assert cols[0]["name"] == "cwx_name"
        assert cols[1]["name"] == "cwx_priority"



class TestParseHelpers:
    """The reconcile-path inverses of build_layoutxml / build_fetchxml."""

    def test_layout_columns_round_trip(self):
        from crm.core.views import parse_layout_columns
        xml = build_layoutxml("cwx_ticket", 10042,
                               [("cwx_name", 220), ("cwx_priority", 120)])
        assert parse_layout_columns(xml) == [
            {"name": "cwx_name", "width": 220},
            {"name": "cwx_priority", "width": 120},
        ]

    def test_layout_columns_empty_or_unparseable(self):
        from crm.core.views import parse_layout_columns
        assert parse_layout_columns("") == []
        assert parse_layout_columns("<not-xml") == []

    def test_fetch_order_filter_round_trip(self):
        from crm.core.views import parse_fetch_order_filter
        xml = build_fetchxml("cwx_ticket", [("cwx_name", 100)],
                              order_by="cwx_name", filter_active=True, order_desc=True)
        assert parse_fetch_order_filter(xml) == ("cwx_name", True, True)

    def test_fetch_order_filter_none_when_absent(self):
        from crm.core.views import parse_fetch_order_filter
        xml = build_fetchxml("cwx_ticket", [("cwx_name", 100)],
                              order_by=None, filter_active=False)
        assert parse_fetch_order_filter(xml) == (None, False, False)
        assert parse_fetch_order_filter("") == (None, False, False)


class TestUpdateView:
    def test_dry_run_issues_no_patch(self, dry_backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.patch(dry_backend.url_for(f"savedqueries({_VIEW_ID})"), status_code=204)
            out = views.update_view(dry_backend, savedqueryid=_VIEW_ID,
                                    changes={"description": "x"})
        assert out["_dry_run"] is True and out["would_update"] is True
        assert [r for r in m.request_history if r.method == "PATCH"] == []

    def test_patches_changed_fields(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"savedqueries({_VIEW_ID})"), status_code=204)
            out = views.update_view(backend, savedqueryid=_VIEW_ID,
                                    changes={"isdefault": True, "description": "x"})
        assert out["updated"] is True
        patches = [r for r in m.request_history if r.method == "PATCH"]
        assert len(patches) == 1
        assert patches[0].json() == {"isdefault": True, "description": "x"}

    def test_empty_changes_raises(self, backend):
        from crm.core import views
        with pytest.raises(D365Error):
            views.update_view(backend, savedqueryid=_VIEW_ID, changes={})
