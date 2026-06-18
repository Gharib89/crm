"""Unit tests for crm.core.views."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.core.views import _build_fetchxml, _build_layoutxml
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
        assert any(r.method == "GET" for r in m.request_history)
        assert not any(r.method == "POST" for r in m.request_history)


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
        layoutxml = _build_layoutxml("cwx_ticket", 10042, cols)
        fetchxml = _build_fetchxml("cwx_ticket", cols, "cwx_name", False)
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

    def test_view_descending_order_parsed(self, backend):
        """order attribute is extracted correctly regardless of descending flag."""
        from crm.core.views import read_entity_views
        cols = [("cwx_subject", 300)]
        layoutxml = _build_layoutxml("cwx_task", 10043, cols)
        # manually build fetchxml with descending="true"
        fetchxml = (
            '<fetch version="1.0" output-format="xml-platform" mapping="logical">'
            '<entity name="cwx_task">'
            '<attribute name="cwx_taskid" />'
            '<attribute name="cwx_subject" />'
            '<order attribute="cwx_createdon" descending="true" />'
            '</entity></fetch>'
        )
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("savedqueries"),
                json={"value": [{
                    "savedqueryid": _READ_VIEW_ID,
                    "name": "Recent Tasks",
                    "layoutxml": layoutxml,
                    "fetchxml": fetchxml,
                    "isdefault": False,
                }]},
            )
            views = read_entity_views(backend, "cwx_task")
        assert views[0]["order_by"] == "cwx_createdon"

    def test_view_with_no_order_element(self, backend):
        from crm.core.views import read_entity_views
        cols = [("cwx_name", 200)]
        layoutxml = _build_layoutxml("cwx_ticket", 10042, cols)
        fetchxml = _build_fetchxml("cwx_ticket", cols, None, False)
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

    def test_view_includes_savedqueryid_and_querytype(self, backend):
        """`view list` needs the id + querytype, so the reader must surface them."""
        from crm.core.views import read_entity_views
        cols = [("cwx_name", 200)]
        layoutxml = _build_layoutxml("cwx_ticket", 10042, cols)
        fetchxml = _build_fetchxml("cwx_ticket", cols, None, False)
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
        must omit the width key (inline literal needed: _build_layoutxml always
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
        fetchxml = _build_fetchxml("cwx_ticket", [("cwx_name", 1)], None, False)
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
