"""Unit tests for crm.core.charts."""
# pyright: basic
from __future__ import annotations

import re

import pytest
import requests_mock

from crm.utils.d365_backend import D365Error



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


# --- Chart editors --------------------------------------------------------------

_EDIT_DATA = (
    '<datadefinition><fetchcollection>'
    '<fetch mapping="logical" aggregate="true"><entity name="new_project">'
    '<attribute name="new_priority" groupby="true" alias="groupby_column" />'
    '<attribute name="new_projectid" aggregate="count" alias="aggregate_column" />'
    '</entity></fetch></fetchcollection>'
    '<categorycollection><category alias="groupby_column">'
    '<measurecollection><measure alias="aggregate_column" /></measurecollection>'
    '</category></categorycollection></datadefinition>'
)
_EDIT_PRES = (
    '<Chart><Series><Series ChartType="Column" /></Series>'
    '<ChartAreas><ChartArea /></ChartAreas></Chart>'
)
_EDIT_CHART = {
    "savedqueryvisualizationid": "11112222-3333-4444-5555-666677778888",
    "name": "Projects by Priority",
    "primaryentitytypecode": "new_project",
    "datadescription": _EDIT_DATA,
    "presentationdescription": _EDIT_PRES,
    "description": None,
    "isdefault": False,
}
_EDIT_ID = _EDIT_CHART["savedqueryvisualizationid"]


def _edit_url(backend) -> str:
    return backend.url_for(f"savedqueryvisualizations({_EDIT_ID})")


class TestPureXmlHelpers:
    def test_set_chart_type_rewrites_every_inner_series(self):
        from crm.core.charts import _set_chart_type
        out = _set_chart_type(_EDIT_PRES, "Bar")
        assert 'ChartType="Bar"' in out
        assert 'ChartType="Column"' not in out

    def test_set_chart_type_raises_without_series(self):
        from crm.core.charts import _set_chart_type
        with pytest.raises(D365Error):
            _set_chart_type("<Chart><Series /></Chart>", "Bar")

    def test_replace_fetch_swaps_inner_fetch_keeps_categories(self):
        from crm.core.charts import _replace_fetch
        new_fetch = (
            '<fetch mapping="logical" aggregate="true"><entity name="new_project">'
            '<attribute name="new_stage" groupby="true" alias="groupby_column" />'
            '<attribute name="new_projectid" aggregate="count" alias="aggregate_column" />'
            '</entity></fetch>')
        out = _replace_fetch(_EDIT_DATA, new_fetch)
        assert 'name="new_stage"' in out
        assert 'name="new_priority"' not in out
        # categorycollection survives the swap
        assert '<category alias="groupby_column">' in out

    def test_replace_fetch_rejects_non_fetch_root(self):
        from crm.core.charts import _replace_fetch
        with pytest.raises(D365Error):
            _replace_fetch(_EDIT_DATA, "<entity name='x'/>")

    def test_append_series_adds_attribute_measure_and_series(self):
        from crm.core.charts import _append_series
        data, pres = _append_series(
            _EDIT_DATA, _EDIT_PRES, column="new_budget", aggregate="sum", alias="series2")
        assert 'name="new_budget" aggregate="sum" alias="series2"' in data
        assert '<measure alias="series2" />' in data
        # One measurecollection per series — the server couples inner <Series>
        # count to measurecollection count, not <measure> count.
        assert data.count("<measurecollection>") == 2
        assert pres.count("<Series ChartType") == 2  # cloned styling

    def test_append_series_rejects_duplicate_alias(self):
        from crm.core.charts import _append_series
        with pytest.raises(D365Error):
            _append_series(_EDIT_DATA, _EDIT_PRES,
                           column="new_budget", aggregate="sum", alias="aggregate_column")

    def test_drop_series_removes_attribute_measure_and_positional_series(self):
        from crm.core.charts import _append_series, _drop_series
        data2, pres2 = _append_series(
            _EDIT_DATA, _EDIT_PRES, column="new_budget", aggregate="sum", alias="series2")
        data3, pres3 = _drop_series(data2, pres2, alias="series2")
        assert "series2" not in data3
        assert pres3.count("<Series ChartType") == 1

    def test_drop_series_refuses_last_series(self):
        from crm.core.charts import _drop_series
        with pytest.raises(D365Error):
            _drop_series(_EDIT_DATA, _EDIT_PRES, alias="aggregate_column")

    def test_drop_series_unknown_alias_raises(self):
        from crm.core.charts import _drop_series
        with pytest.raises(D365Error):
            _drop_series(_EDIT_DATA, _EDIT_PRES, alias="nope")

    def test_set_groupby_sets_name_and_dategrouping(self):
        from crm.core.charts import _set_groupby
        out = _set_groupby(_EDIT_DATA, column="createdon", dategrouping="month")
        assert 'name="createdon" groupby="true"' in out
        assert 'dategrouping="month"' in out

    def test_set_groupby_clears_stale_dategrouping(self):
        from crm.core.charts import _set_groupby
        dated = _EDIT_DATA.replace(
            '<attribute name="new_priority" groupby="true" alias="groupby_column" />',
            '<attribute name="createdon" groupby="true" dategrouping="month" '
            'alias="groupby_column" />')
        out = _set_groupby(dated, column="new_priority", dategrouping=None)
        assert 'name="new_priority"' in out
        assert "dategrouping" not in out


class TestAliasCoupling:
    def _root(self, xml):
        from crm.core.xml_edit import parse_xml
        return parse_xml(xml)

    def test_valid_chart_passes(self):
        from crm.core.charts import _validate_alias_coupling
        _validate_alias_coupling(self._root(_EDIT_DATA), self._root(_EDIT_PRES))

    def test_measure_without_matching_aggregate_alias_raises(self):
        from crm.core.charts import _validate_alias_coupling
        bad = _EDIT_DATA.replace(
            '<measure alias="aggregate_column" />', '<measure alias="orphan" />')
        with pytest.raises(D365Error):
            _validate_alias_coupling(self._root(bad))

    def test_series_measure_count_mismatch_raises(self):
        from crm.core.charts import _validate_alias_coupling
        two_series = _EDIT_PRES.replace(
            '<Series ChartType="Column" />',
            '<Series ChartType="Column" /><Series ChartType="Column" />')
        with pytest.raises(D365Error):
            _validate_alias_coupling(self._root(_EDIT_DATA), self._root(two_series))


class TestUpdateChart:
    def test_partial_update_reads_other_column_and_patches_name(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=_EDIT_CHART)
            m.get(re.compile("EntityDefinitions"), json={"AttributeType": "String"})
            m.patch(_edit_url(backend), status_code=204)
            out = charts.update_chart(
                backend, _EDIT_ID, name="Renamed",
                data_description=_EDIT_DATA, publish=False)
        body = m.last_request.json()
        assert body["name"] == "Renamed"
        assert body["datadescription"] == _EDIT_DATA
        # presentationdescription untouched on a data-only update
        assert "presentationdescription" not in body
        assert out["updated"] is True

    def test_type_only_update_rewrites_presentation(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=_EDIT_CHART)
            m.get(re.compile("EntityDefinitions"), json={"AttributeType": "String"})
            m.patch(_edit_url(backend), status_code=204)
            charts.update_chart(backend, _EDIT_ID, chart_type="Pie", publish=False)
        body = m.last_request.json()
        assert 'ChartType="Pie"' in body["presentationdescription"]
        assert "datadescription" not in body

    def test_nothing_to_update_raises(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=_EDIT_CHART)
            with pytest.raises(D365Error):
                charts.update_chart(backend, _EDIT_ID, publish=False)

    def test_rehoming_fetch_entity_rejected(self, backend):
        from crm.core import charts
        rehomed = _EDIT_DATA.replace('name="new_project"', 'name="account"')
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=_EDIT_CHART)
            with pytest.raises(D365Error):
                charts.update_chart(
                    backend, _EDIT_ID, data_description=rehomed, publish=False)


class TestAddRemoveSeriesOrchestration:
    def test_add_series_patches_both_columns(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=_EDIT_CHART)
            m.get(re.compile("EntityDefinitions"), json={"AttributeType": "Money"})
            m.patch(_edit_url(backend), status_code=204)
            out = charts.add_chart_series(
                backend, _EDIT_ID, column="new_budget", aggregate="sum",
                alias="series2", publish=False)
        body = m.last_request.json()
        assert "datadescription" in body and "presentationdescription" in body
        assert 'alias="series2"' in body["datadescription"]
        assert out["action"] == "add-series"

    def test_add_series_missing_column_raises(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=_EDIT_CHART)
            m.get(re.compile("EntityDefinitions"), status_code=404,
                  json={"error": {"message": "Not found"}})
            with pytest.raises(D365Error):
                charts.add_chart_series(
                    backend, _EDIT_ID, column="ghost", aggregate="sum",
                    alias="series2", publish=False)

    def test_remove_series_requires_two_series_first(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=_EDIT_CHART)
            with pytest.raises(D365Error):
                charts.remove_chart_series(
                    backend, _EDIT_ID, alias="aggregate_column", publish=False)


class TestChartEditPublishGating:
    def test_user_chart_never_publishes(self, backend, monkeypatch):
        from crm.core import charts
        called = {}
        monkeypatch.setattr("crm.core.solution.publish_all",
                            lambda b: called.setdefault("p", True))
        user_chart = dict(_EDIT_CHART)
        user_chart["userqueryvisualizationid"] = _EDIT_ID
        del user_chart["isdefault"]
        url = backend.url_for(f"userqueryvisualizations({_EDIT_ID})")
        with requests_mock.Mocker() as m:
            m.get(url, json=user_chart)
            m.get(re.compile("EntityDefinitions"), json={"AttributeType": "Picklist"})
            m.patch(url, status_code=204)
            out = charts.set_chart_groupby(
                backend, _EDIT_ID, column="new_priority", user=True, publish=True)
        # publish forced off for user charts → no PublishAllXml, no read-back
        assert "p" not in called
        assert out.get("published") is not True

    def test_system_chart_publishes_and_reads_back(self, backend, monkeypatch):
        from crm.core import charts
        called = {}
        monkeypatch.setattr("crm.core.solution.publish_all",
                            lambda b: called.setdefault("p", True))
        with requests_mock.Mocker() as m:
            # get_chart (original), then read-back (mutated) — same path, sequential
            mutated = dict(_EDIT_CHART)
            mutated["datadescription"] = _set_groupby_helper()
            m.get(_edit_url(backend), [{"json": _EDIT_CHART}, {"json": mutated}])
            m.get(re.compile("EntityDefinitions"), json={"AttributeType": "DateTime"})
            m.patch(_edit_url(backend), status_code=204)
            out = charts.set_chart_groupby(
                backend, _EDIT_ID, column="createdon", dategrouping="month", publish=True)
        assert called.get("p") is True
        assert out["published"] is True


def _set_groupby_helper():
    from crm.core.charts import _set_groupby
    return _set_groupby(_EDIT_DATA, column="createdon", dategrouping="month")


# A comparison chart: two categories, one series.
_COMPARISON_DATA = (
    '<datadefinition><fetchcollection>'
    '<fetch mapping="logical" aggregate="true"><entity name="new_project">'
    '<attribute name="new_priority" groupby="true" alias="groupby_column" />'
    '<attribute name="new_stage" groupby="true" alias="groupby_column2" />'
    '<attribute name="new_projectid" aggregate="count" alias="aggregate_column" />'
    '</entity></fetch></fetchcollection>'
    '<categorycollection>'
    '<category alias="groupby_column"><measurecollection>'
    '<measure alias="aggregate_column" /></measurecollection></category>'
    '<category alias="groupby_column2"><measurecollection>'
    '<measure alias="aggregate_column" /></measurecollection></category>'
    '</categorycollection></datadefinition>'
)


class TestComparisonChartGuards:
    def _chart(self):
        c = dict(_EDIT_CHART)
        c["datadescription"] = _COMPARISON_DATA
        return c

    def test_add_series_rejected_on_comparison_chart(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=self._chart())
            with pytest.raises(D365Error, match="comparison"):
                charts.add_chart_series(
                    backend, _EDIT_ID, column="new_budget", aggregate="sum",
                    alias="series2", publish=False)

    def test_remove_series_rejected_on_comparison_chart(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=self._chart())
            with pytest.raises(D365Error, match="comparison"):
                charts.remove_chart_series(
                    backend, _EDIT_ID, alias="aggregate_column", publish=False)


class TestDategroupingValidation:
    def test_dategrouping_rejected_for_non_date_column(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=_EDIT_CHART)
            m.get(re.compile("EntityDefinitions"), json={"AttributeType": "Picklist"})
            with pytest.raises(D365Error, match="date column"):
                charts.set_chart_groupby(
                    backend, _EDIT_ID, column="new_priority",
                    dategrouping="month", publish=False)

    def test_dategrouping_accepted_for_date_column(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=_EDIT_CHART)
            m.get(re.compile("EntityDefinitions"), json={"AttributeType": "DateTime"})
            m.patch(_edit_url(backend), status_code=204)
            out = charts.set_chart_groupby(
                backend, _EDIT_ID, column="createdon", dategrouping="month", publish=False)
        assert out["updated"] is True


class TestLinkEntityValidation:
    _DATA_WITH_LINK = (
        '<datadefinition><fetchcollection>'
        '<fetch mapping="logical" aggregate="true"><entity name="new_project">'
        '<attribute name="new_priority" groupby="true" alias="groupby_column" />'
        '<attribute name="new_projectid" aggregate="count" alias="aggregate_column" />'
        '<link-entity name="account" from="accountid" to="new_accountid">'
        '<attribute name="ghostcol" />'
        '</link-entity>'
        '</entity></fetch></fetchcollection>'
        '<categorycollection><category alias="groupby_column">'
        '<measurecollection><measure alias="aggregate_column" /></measurecollection>'
        '</category></categorycollection></datadefinition>'
    )

    def test_bad_link_entity_attribute_rejected(self, backend):
        from crm.core import charts
        with requests_mock.Mocker() as m:
            m.get(_edit_url(backend), json=_EDIT_CHART)
            # primary-entity attributes resolve; the link target's column 404s
            m.get(re.compile(r"EntityDefinitions\(LogicalName='new_project'\)"),
                  json={"AttributeType": "String"})
            m.get(re.compile(r"EntityDefinitions\(LogicalName='account'\)"),
                  status_code=404, json={"error": {"message": "Not found"}})
            with pytest.raises(D365Error, match="ghostcol"):
                charts.update_chart(
                    backend, _EDIT_ID, data_description=self._DATA_WITH_LINK, publish=False)
