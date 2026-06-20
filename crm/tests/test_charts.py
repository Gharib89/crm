"""Unit tests for crm.core.charts."""
# pyright: basic
from __future__ import annotations

import requests_mock


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


_USER_CHART_ROW = {
    "userqueryvisualizationid": "aaaa1111-2222-3333-4444-555566667777",
    "name": "My Chart",
    "primaryentitytypecode": "contact",
    "datadescription": '<datadefinition><fetchcollection><fetch><entity name="contact"></entity></fetch></fetchcollection></datadefinition>',
    "presentationdescription": '<Chart><Series><Series ChartType="Bar" /></Series></Chart>',
    "description": "A user chart",
}

_SYSTEM_ID = "11112222-3333-4444-5555-666677778888"
_SYSTEM_ID_URL_SUFFIX = f"savedqueryvisualizations({_SYSTEM_ID})"
_USER_ID = "aaaa1111-2222-3333-4444-555566667777"
_USER_ID_URL_SUFFIX = f"userqueryvisualizations({_USER_ID})"


class TestListEntityCharts:
    def test_lists_system_charts(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueryvisualizations"), json={"value": [_CHART_ROW]})
            result = charts.list_entity_charts(backend, "new_project")
        assert len(result) == 1
        assert result[0]["savedqueryvisualizationid"] == _CHART_ROW["savedqueryvisualizationid"]
        assert result[0]["isdefault"] is False

    def test_lists_user_charts(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("userqueryvisualizations"), json={"value": [_USER_CHART_ROW]})
            result = charts.list_entity_charts(backend, "contact", user=True)
        assert len(result) == 1
        assert result[0]["userqueryvisualizationid"] == _USER_CHART_ROW["userqueryvisualizationid"]
        assert "isdefault" not in result[0]

    def test_system_charts_filters_by_entity(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueryvisualizations"), json={"value": []})
            charts.list_entity_charts(backend, "new_project")
        assert "primaryentitytypecode" in m.last_request.url

    def test_user_charts_hits_correct_entity_set(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("userqueryvisualizations"), json={"value": []})
            charts.list_entity_charts(backend, "contact", user=True)
        assert "userqueryvisualizations" in m.last_request.url


class TestGetChart:
    def test_gets_system_chart_by_id(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"savedqueryvisualizations({_SYSTEM_ID})"),
                  json=_CHART_ROW)
            result = charts.get_chart(backend, _SYSTEM_ID)
        assert result["savedqueryvisualizationid"] == _SYSTEM_ID
        assert result["isdefault"] is False

    def test_gets_user_chart_by_id(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"userqueryvisualizations({_USER_ID})"),
                  json=_USER_CHART_ROW)
            result = charts.get_chart(backend, _USER_ID, user=True)
        assert result["userqueryvisualizationid"] == _USER_ID
        assert "isdefault" not in result

    def test_get_uses_correct_entity_set_for_user(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"userqueryvisualizations({_USER_ID})"),
                  json=_USER_CHART_ROW)
            charts.get_chart(backend, _USER_ID, user=True)
        assert "userqueryvisualizations" in m.last_request.url


class TestDeleteChart:
    def test_deletes_system_chart(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.delete(backend.url_for(f"savedqueryvisualizations({_SYSTEM_ID})"),
                     status_code=204)
            result = charts.delete_chart(backend, _SYSTEM_ID)
        assert result["deleted"] is True
        assert result["savedqueryvisualizationid"] == _SYSTEM_ID

    def test_deletes_user_chart(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.delete(backend.url_for(f"userqueryvisualizations({_USER_ID})"),
                     status_code=204)
            result = charts.delete_chart(backend, _USER_ID, user=True)
        assert result["deleted"] is True
        assert result["userqueryvisualizationid"] == _USER_ID

    def test_dry_run_returns_would_delete(self, dry_backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.delete(dry_backend.url_for(f"savedqueryvisualizations({_SYSTEM_ID})"),
                     status_code=204)
            result = charts.delete_chart(dry_backend, _SYSTEM_ID)
        assert result.get("_dry_run") is True
        assert result.get("would_delete") is True
        assert result.get("savedqueryvisualizationid") == _SYSTEM_ID


class TestCreateChart:
    def test_creates_system_chart_with_xml(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("savedqueryvisualizations"), status_code=204, headers={
                "OData-EntityId": backend.url_for(f"savedqueryvisualizations({_SYSTEM_ID})"),
            })
            result = charts.create_chart(
                backend,
                entity="contact",
                name="My Chart",
                data_description="<datadefinition/>",
                presentation_description="<Chart/>",
            )
        body = m.last_request.json()
        assert body["name"] == "My Chart"
        assert body["primaryentitytypecode"] == "contact"
        assert body["datadescription"] == "<datadefinition/>"
        assert body["presentationdescription"] == "<Chart/>"
        assert result["created"] is True
        assert result["savedqueryvisualizationid"] == _SYSTEM_ID

    def test_creates_user_chart_with_xml(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("userqueryvisualizations"), status_code=204, headers={
                "OData-EntityId": backend.url_for(f"userqueryvisualizations({_USER_ID})"),
            })
            result = charts.create_chart(
                backend,
                entity="contact",
                name="My User Chart",
                data_description="<datadefinition/>",
                presentation_description="<Chart/>",
                user=True,
            )
        assert result["created"] is True
        assert result["userqueryvisualizationid"] == _USER_ID
        assert "savedqueryvisualizationid" not in result

    def test_creates_chart_with_web_resource(self, backend):
        from crm.core import charts
        wr_id = "cccccccc-dddd-eeee-ffff-000011112222"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": wr_id}]})
            m.post(backend.url_for("savedqueryvisualizations"), status_code=204, headers={
                "OData-EntityId": backend.url_for(f"savedqueryvisualizations({_SYSTEM_ID})"),
            })
            result = charts.create_chart(
                backend,
                entity="contact",
                name="WR Chart",
                web_resource="cwx_/scripts/chart.js",
            )
        body = m.last_request.json()
        assert "webresourceid@odata.bind" in body
        assert wr_id in body["webresourceid@odata.bind"]
        assert "datadescription" not in body
        assert result["created"] is True

    def test_web_resource_not_found_raises(self, backend):
        from crm.core import charts
        from crm.utils.d365_backend import D365Error
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": []})
            try:
                charts.create_chart(
                    backend,
                    entity="contact",
                    name="WR Chart",
                    web_resource="nonexistent_wr",
                )
                assert False, "Expected D365Error"
            except D365Error:
                pass

    def test_create_chart_solution_header(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("savedqueryvisualizations"), status_code=204, headers={
                "OData-EntityId": backend.url_for(f"savedqueryvisualizations({_SYSTEM_ID})"),
            })
            charts.create_chart(
                backend,
                entity="contact",
                name="My Chart",
                data_description="<datadefinition/>",
                presentation_description="<Chart/>",
                solution="MySolution",
            )
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySolution"

    def test_create_chart_includes_description(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("savedqueryvisualizations"), status_code=204, headers={
                "OData-EntityId": backend.url_for(f"savedqueryvisualizations({_SYSTEM_ID})"),
            })
            charts.create_chart(
                backend,
                entity="contact",
                name="My Chart",
                data_description="<datadefinition/>",
                presentation_description="<Chart/>",
                description="A test chart",
            )
        body = m.last_request.json()
        assert body["description"] == "A test chart"

    def test_create_chart_dry_run(self, dry_backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.post(dry_backend.url_for("savedqueryvisualizations"), status_code=204, headers={
                "OData-EntityId": dry_backend.url_for(f"savedqueryvisualizations({_SYSTEM_ID})"),
            })
            result = charts.create_chart(
                dry_backend,
                entity="contact",
                name="My Chart",
                data_description="<datadefinition/>",
                presentation_description="<Chart/>",
            )
        assert result.get("_dry_run") is True
        assert result.get("would_create") is True
        assert result.get("name") == "My Chart"
        assert result.get("primaryentitytypecode") == "contact"
        assert not m.called
