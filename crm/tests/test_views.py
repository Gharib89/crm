"""Unit tests for crm.core.views."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


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
