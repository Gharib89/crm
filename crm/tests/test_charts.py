"""Unit tests for crm.core.charts."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


_CHART_ROW = {
    "savedqueryvisualizationid": "11112222-3333-4444-5555-666677778888",
    "name": "Tickets by Priority",
    "primaryentitytypecode": "new_project",
    "datadescription": '<datadefinition><fetchcollection><fetch aggregate="true"><entity name="new_project"><attribute alias="aggregate_column" name="new_projectid" aggregate="count" /></entity></fetch></fetchcollection></datadefinition>',
    "presentationdescription": '<Chart><Series><Series ChartType="Column" /></Series></Chart>',
    "description": "By priority",
    "isdefault": False,
}


def _charts_url(backend) -> str:
    return backend.url_for("savedqueryvisualizations")


class TestReadEntityCharts:
    def test_reads_chart_row(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_charts_url(backend), json={"value": [_CHART_ROW]})
            result = charts.read_entity_charts(backend, "new_project")
        assert len(result) == 1
        c = result[0]
        assert c["savedqueryvisualizationid"] == _CHART_ROW["savedqueryvisualizationid"]
        assert c["name"] == "Tickets by Priority"
        assert c["primaryentitytypecode"] == "new_project"
        assert '<entity name="new_project">' in c["datadescription"]
        assert "<Chart>" in c["presentationdescription"]
        assert c["description"] == "By priority"
        assert c["isdefault"] is False

    def test_filters_by_primaryentitytypecode_in_request(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_charts_url(backend), json={"value": []})
            charts.read_entity_charts(backend, "new_project")
        assert "primaryentitytypecode" in m.last_request.url
        assert "new_project" in m.last_request.url

    def test_escapes_single_quote_in_entity_name(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_charts_url(backend), json={"value": []})
            charts.read_entity_charts(backend, "it's_table")
        assert "it%27%27s_table" in m.last_request.url


class TestRetargetChartxml:
    def test_rewrites_whole_word_entity_refs(self):
        from crm.core.charts import retarget_chartxml
        xml = '<fetch><entity name="new_project"></entity></fetch>'
        out = retarget_chartxml(xml, src_entity="new_project", dst_entity="cwx_ticketclone")
        assert '<entity name="cwx_ticketclone">' in out

    def test_protects_attribute_logical_names(self):
        from crm.core.charts import retarget_chartxml
        xml = ('<entity name="new_projectid"></entity>'
               '<attribute name="new_project_code" />')
        out = retarget_chartxml(xml, src_entity="new_project", dst_entity="cwx_ticketclone")
        assert 'name="new_projectid"' in out
        assert 'name="new_project_code"' in out
        assert "cwx_ticketclone" not in out

    def test_noop_on_empty_string(self):
        from crm.core.charts import retarget_chartxml
        out = retarget_chartxml("", src_entity="new_project", dst_entity="cwx_ticketclone")
        assert out == ""


class TestCloneChartToEntity:
    def test_posts_retargeted_chart(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("savedqueryvisualizations"), status_code=204, headers={
                "OData-EntityId":
                    backend.url_for("savedqueryvisualizations(99998888-7777-6666-5555-444433332222)"),
            })
            out = charts.clone_chart_to_entity(backend, _CHART_ROW, "cwx_ticketclone")
        body = m.last_request.json()
        assert body["primaryentitytypecode"] == "cwx_ticketclone"
        assert '<entity name="cwx_ticketclone">' in body["datadescription"]
        assert body["name"] == "Tickets by Priority"
        assert out["created"] is True
        assert out["savedqueryvisualizationid"] == "99998888-7777-6666-5555-444433332222"
        assert out["primaryentitytypecode"] == "cwx_ticketclone"

    def test_adds_solution_header_when_given(self, backend):
        from crm.core import charts
        chart = {
            "savedqueryvisualizationid": "old", "name": "C",
            "primaryentitytypecode": "new_project",
            "datadescription": '<fetch><entity name="new_project"></entity></fetch>',
            "presentationdescription": "<Chart/>", "description": None, "isdefault": False,
        }
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("savedqueryvisualizations"), status_code=204, headers={
                "OData-EntityId":
                    backend.url_for("savedqueryvisualizations(99998888-7777-6666-5555-444433332222)"),
            })
            charts.clone_chart_to_entity(backend, chart, "cwx_ticketclone", solution="MySol")
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_presentationdescription_without_entity_ref_roundtrips(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("savedqueryvisualizations"), status_code=204, headers={
                "OData-EntityId":
                    backend.url_for("savedqueryvisualizations(99998888-7777-6666-5555-444433332222)"),
            })
            charts.clone_chart_to_entity(backend, _CHART_ROW, "cwx_ticketclone")
        body = m.last_request.json()
        assert body["presentationdescription"] == _CHART_ROW["presentationdescription"]
