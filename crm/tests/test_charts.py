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
    "name": "My Tickets",
    "primaryentitytypecode": "new_project",
    "datadescription": '<datadefinition><fetchcollection><fetch><entity name="new_project"/></fetch></fetchcollection></datadefinition>',
    "presentationdescription": "<Chart/>",
    "description": "personal",
}


def _user_charts_url(backend) -> str:
    return backend.url_for("userqueryvisualizations")


class TestListEntityCharts:
    def test_lists_system_charts(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_charts_url(backend), json={"value": [_CHART_ROW]})
            result = charts.list_entity_charts(backend, "new_project")
        assert len(result) == 1
        c = result[0]
        assert c["savedqueryvisualizationid"] == _CHART_ROW["savedqueryvisualizationid"]
        assert c["name"] == "Tickets by Priority"
        assert c["isdefault"] is False
        assert "primaryentitytypecode" in m.last_request.url

    def test_lists_user_charts_from_userset(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_user_charts_url(backend), json={"value": [_USER_CHART_ROW]})
            result = charts.list_entity_charts(backend, "new_project", user=True)
        assert len(result) == 1
        c = result[0]
        assert c["userqueryvisualizationid"] == _USER_CHART_ROW["userqueryvisualizationid"]
        # user charts carry no isdefault
        assert "isdefault" not in c
        assert "userqueryvisualizations" in m.last_request.url


class TestGetChart:
    def test_gets_system_chart_by_id(self, backend):
        from crm.core import charts
        cid = _CHART_ROW["savedqueryvisualizationid"]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"savedqueryvisualizations({cid})"), json=_CHART_ROW)
            out = charts.get_chart(backend, cid)
        assert out["savedqueryvisualizationid"] == cid
        assert out["name"] == "Tickets by Priority"
        assert "<Chart>" in out["presentationdescription"]
        assert out["isdefault"] is False

    def test_gets_user_chart_by_id(self, backend):
        from crm.core import charts
        cid = _USER_CHART_ROW["userqueryvisualizationid"]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"userqueryvisualizations({cid})"), json=_USER_CHART_ROW)
            out = charts.get_chart(backend, cid, user=True)
        assert out["userqueryvisualizationid"] == cid
        assert "isdefault" not in out

    def test_rejects_non_guid_id(self, backend):
        from crm.core import charts
        from crm.utils.d365_backend import D365Error
        import pytest
        with pytest.raises(D365Error):
            charts.get_chart(backend, "not-a-guid")


class TestDeleteChart:
    def test_deletes_system_chart(self, backend):
        from crm.core import charts
        cid = _CHART_ROW["savedqueryvisualizationid"]
        with requests_mock.Mocker() as m:
            m.delete(backend.url_for(f"savedqueryvisualizations({cid})"), status_code=204)
            out = charts.delete_chart(backend, cid)
        assert out == {"deleted": True, "savedqueryvisualizationid": cid}

    def test_deletes_user_chart(self, backend):
        from crm.core import charts
        cid = _USER_CHART_ROW["userqueryvisualizationid"]
        with requests_mock.Mocker() as m:
            m.delete(backend.url_for(f"userqueryvisualizations({cid})"), status_code=204)
            out = charts.delete_chart(backend, cid, user=True)
        assert out == {"deleted": True, "userqueryvisualizationid": cid}

    def test_delete_dry_run_previews(self, dry_backend):
        from crm.core import charts
        cid = _CHART_ROW["savedqueryvisualizationid"]
        out = charts.delete_chart(dry_backend, cid)
        assert out == {"_dry_run": True, "would_delete": True,
                       "savedqueryvisualizationid": cid}

    def test_rejects_non_guid_id(self, backend):
        from crm.core import charts
        from crm.utils.d365_backend import D365Error
        import pytest
        with pytest.raises(D365Error):
            charts.delete_chart(backend, "not-a-guid")


class TestCreateChart:
    _NEW_ID_URL = "savedqueryvisualizations(99998888-7777-6666-5555-444433332222)"
    _NEW_ID = "99998888-7777-6666-5555-444433332222"

    def test_creates_system_chart_from_xml(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.post(_charts_url(backend), status_code=204,
                   headers={"OData-EntityId": backend.url_for(self._NEW_ID_URL)})
            out = charts.create_chart(
                backend, entity="new_project", name="By Priority",
                data_description="<datadefinition/>",
                presentation_description="<Chart/>",
            )
        body = m.last_request.json()
        assert body["primaryentitytypecode"] == "new_project"
        assert body["name"] == "By Priority"
        assert body["datadescription"] == "<datadefinition/>"
        assert body["presentationdescription"] == "<Chart/>"
        assert out["created"] is True
        assert out["savedqueryvisualizationid"] == self._NEW_ID

    def test_creates_user_chart_in_userset(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.post(_user_charts_url(backend), status_code=204,
                   headers={"OData-EntityId": backend.url_for(
                       f"userqueryvisualizations({self._NEW_ID})")})
            out = charts.create_chart(
                backend, entity="new_project", name="Mine",
                data_description="<datadefinition/>",
                presentation_description="<Chart/>", user=True,
            )
        assert out["created"] is True
        assert out["userqueryvisualizationid"] == self._NEW_ID
        assert "userqueryvisualizations" in m.last_request.url

    def test_web_resource_mode_resolves_name_and_binds(self, backend):
        from crm.core import charts
        wr_id = "dddddddd-0000-0000-0000-000000000001"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={
                "value": [{"webresourceid": wr_id, "name": "new_chartscript"}]})
            m.post(_charts_url(backend), status_code=204,
                   headers={"OData-EntityId": backend.url_for(self._NEW_ID_URL)})
            out = charts.create_chart(
                backend, entity="new_project", name="Script Chart",
                web_resource="new_chartscript",
            )
        body = m.last_request.json()
        assert body["webresourceid@odata.bind"] == f"/webresourceset({wr_id})"
        assert "datadescription" not in body
        assert out["created"] is True

    def test_web_resource_accepts_guid_without_lookup(self, backend):
        from crm.core import charts
        wr_id = "dddddddd-0000-0000-0000-000000000002"
        with requests_mock.Mocker() as m:
            m.post(_charts_url(backend), status_code=204,
                   headers={"OData-EntityId": backend.url_for(self._NEW_ID_URL)})
            out = charts.create_chart(
                backend, entity="new_project", name="Script Chart",
                web_resource=wr_id,
            )
        body = m.last_request.json()
        assert body["webresourceid@odata.bind"] == f"/webresourceset({wr_id})"
        assert out["created"] is True

    def test_web_resource_not_found_raises(self, backend):
        from crm.core import charts
        from crm.utils.d365_backend import D365Error
        import pytest
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": []})
            with pytest.raises(D365Error):
                charts.create_chart(
                    backend, entity="new_project", name="X",
                    web_resource="missing_wr",
                )

    def test_adds_solution_header(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.post(_charts_url(backend), status_code=204,
                   headers={"OData-EntityId": backend.url_for(self._NEW_ID_URL)})
            charts.create_chart(
                backend, entity="new_project", name="X",
                data_description="<a/>", presentation_description="<b/>",
                solution="MySol",
            )
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_create_dry_run_previews_resolved_body(self, dry_backend):
        from crm.core import charts
        out = charts.create_chart(
            dry_backend, entity="new_project", name="By Priority",
            data_description="<datadefinition/>", presentation_description="<Chart/>",
        )
        assert out["_dry_run"] is True
        assert out["would_create"]["entity_set"] == "savedqueryvisualizations"
        assert out["would_create"]["body"]["name"] == "By Priority"

    def test_publish_runs_publishallxml(self, backend, monkeypatch):
        from crm.core import charts
        called = {}
        monkeypatch.setattr("crm.core.solution.publish_all",
                            lambda b: called.setdefault("published", True))
        with requests_mock.Mocker() as m:
            m.post(_charts_url(backend), status_code=204,
                   headers={"OData-EntityId": backend.url_for(self._NEW_ID_URL)})
            out = charts.create_chart(
                backend, entity="new_project", name="X",
                data_description="<a/>", presentation_description="<b/>",
                publish=True,
            )
        assert called.get("published") is True
        assert out["published"] is True
