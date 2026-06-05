# pyright: basic
"""Unit tests for crm.core.plugin (register_assembly) and the plugin command.

Identity derivation is Option A: filename stem + documented defaults
(version 1.0.0.0, culture neutral, publickeytoken null) with per-call overrides.
No .NET reflection. The pluginassembly column map (isolationmode 1=None/2=Sandbox,
sourcetype 0=Database) is verified against MS Learn's pluginassembly entity
reference.
"""
from __future__ import annotations

import base64

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


_PA_ID = "11111111-1111-1111-1111-111111111111"


def _posts(m):
    return [r for r in m.request_history if r.method == "POST"]


def _patches(m):
    return [r for r in m.request_history if r.method == "PATCH"]


def _write_dll(tmp_path, name="Contoso.Plugins.dll", data=b"MZ\x90\x00fake-assembly"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


class TestRegisterAssemblyCreate:
    def test_posts_base64_content_and_required_fields(self, backend, tmp_path):
        from crm.core import plugin
        raw = b"MZ\x90\x00fake-assembly"
        path = _write_dll(tmp_path, "Contoso.Plugins.dll", raw)
        pa_url = backend.url_for(f"pluginassemblies({_PA_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("pluginassemblies"), status_code=204,
                   headers={"OData-EntityId": pa_url})
            out = plugin.register_assembly(backend, path=path)
        assert out["created"] is True
        assert out["pluginassemblyid"] == _PA_ID
        body = _posts(m)[0].json()
        assert body["content"] == base64.b64encode(raw).decode("ascii")
        # identity derived from filename stem + documented defaults
        assert body["name"] == "Contoso.Plugins"
        assert body["version"] == "1.0.0.0"
        assert body["culture"] == "neutral"
        assert body["publickeytoken"] == "null"
        # isolation sandbox -> 2, sourcetype always Database (0)
        assert body["isolationmode"] == 2
        assert body["sourcetype"] == 0
        # echoed in the return dict
        assert out["name"] == "Contoso.Plugins"
        assert out["isolationmode"] == 2
        assert out["version"] == "1.0.0.0"

    def test_overrides_win_over_defaults(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        pa_url = backend.url_for(f"pluginassemblies({_PA_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("pluginassemblies"), status_code=204,
                   headers={"OData-EntityId": pa_url})
            plugin.register_assembly(
                backend, path=path, name="Custom.Name", version="2.3.4.5",
                culture="en-US", public_key_token="0123456789abcdef",
            )
        body = _posts(m)[0].json()
        assert body["name"] == "Custom.Name"
        assert body["version"] == "2.3.4.5"
        assert body["culture"] == "en-US"
        assert body["publickeytoken"] == "0123456789abcdef"

    def test_isolation_none_maps_to_one(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        pa_url = backend.url_for(f"pluginassemblies({_PA_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("pluginassemblies"), status_code=204,
                   headers={"OData-EntityId": pa_url})
            out = plugin.register_assembly(backend, path=path, isolation_mode="none")
        assert _posts(m)[0].json()["isolationmode"] == 1
        assert out["isolationmode"] == 1

    def test_unknown_isolation_mode_raises(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("pluginassemblies"), status_code=204)
            with pytest.raises(D365Error, match="isolation"):
                plugin.register_assembly(backend, path=path, isolation_mode="bogus")
        # validation happens before any HTTP call
        assert m.request_history == []

    def test_description_sent_when_given(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        pa_url = backend.url_for(f"pluginassemblies({_PA_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("pluginassemblies"), status_code=204,
                   headers={"OData-EntityId": pa_url})
            plugin.register_assembly(backend, path=path, description="My plugin")
        assert _posts(m)[0].json()["description"] == "My plugin"

    def test_description_omitted_when_absent(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        pa_url = backend.url_for(f"pluginassemblies({_PA_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("pluginassemblies"), status_code=204,
                   headers={"OData-EntityId": pa_url})
            plugin.register_assembly(backend, path=path)
        assert "description" not in _posts(m)[0].json()

    def test_solution_header_routed(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        pa_url = backend.url_for(f"pluginassemblies({_PA_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("pluginassemblies"), status_code=204,
                   headers={"OData-EntityId": pa_url})
            out = plugin.register_assembly(backend, path=path, solution="cwx_sol")
        assert _posts(m)[0].headers["MSCRM.SolutionUniqueName"] == "cwx_sol"
        assert out["solution"] == "cwx_sol"

    def test_does_not_post_plugintype_rows(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        pa_url = backend.url_for(f"pluginassemblies({_PA_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("pluginassemblies"), status_code=204,
                   headers={"OData-EntityId": pa_url})
            plugin.register_assembly(backend, path=path)
        # exactly one POST, to pluginassemblies — platform auto-creates types
        posts = _posts(m)
        assert len(posts) == 1
        assert "plugintypes" not in posts[0].url

    def test_missing_path_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error):
                plugin.register_assembly(
                    backend, path=str("/no/such/file/Contoso.Plugins.dll"))
        assert m.request_history == []

    def test_unparseable_id_sets_lookup_error(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("pluginassemblies"), status_code=204,
                   headers={"OData-EntityId": "https://x/pluginassemblies(bogus)"})
            out = plugin.register_assembly(backend, path=path)
        assert out["created"] is True
        assert out["pluginassemblyid"] is None
        assert "pluginassembly_lookup_error" in out

    def test_dry_run_returns_preview_no_post(self, profile, tmp_path):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        path = _write_dll(tmp_path)
        with requests_mock.Mocker() as m:
            out = plugin.register_assembly(dry, path=path)
        assert out["_dry_run"] is True
        assert not _posts(m)


class TestRegisterAssemblyUpdate:
    def test_update_resolves_id_and_patches_content_only(self, backend, tmp_path):
        from crm.core import plugin
        raw = b"MZ\x90\x00updated-assembly"
        path = _write_dll(tmp_path, "Contoso.Plugins.dll", raw)
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            m.patch(backend.url_for(f"pluginassemblies({_PA_ID})"), status_code=204)
            out = plugin.register_assembly(backend, path=path, update=True)
        assert out["updated"] is True
        assert out["pluginassemblyid"] == _PA_ID
        patch = _patches(m)[0]
        assert f"pluginassemblies({_PA_ID})" in patch.url
        body = patch.json()
        assert body["content"] == base64.b64encode(raw).decode("ascii")
        # content-only PATCH must not carry identity columns
        assert "name" not in body
        assert "isolationmode" not in body
        assert out["fields"] == ["content"]

    def test_update_resolves_by_name_override(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            m.patch(backend.url_for(f"pluginassemblies({_PA_ID})"), status_code=204)
            plugin.register_assembly(
                backend, path=path, name="Other.Name", update=True)
        # resolves by the explicit name, not the filename stem
        assert m.request_history[0].qs["$filter"] == ["name eq 'other.name'"]

    def test_update_resolves_by_filename_stem_when_name_omitted(
            self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path, "Contoso.Plugins.dll")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            m.patch(backend.url_for(f"pluginassemblies({_PA_ID})"), status_code=204)
            plugin.register_assembly(backend, path=path, update=True)
        # id resolution uses the filename stem as the assembly name
        assert m.request_history[0].qs["$filter"] == ["name eq 'contoso.plugins'"]

    def test_update_solution_header_routed(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            m.patch(backend.url_for(f"pluginassemblies({_PA_ID})"), status_code=204)
            out = plugin.register_assembly(
                backend, path=path, update=True, solution="Foo")
        # the PATCH carries MSCRM.SolutionUniqueName, mirroring webresource update
        assert _patches(m)[0].headers["MSCRM.SolutionUniqueName"] == "Foo"
        assert out["solution"] == "Foo"

    def test_update_name_not_found_raises(self, backend, tmp_path):
        from crm.core import plugin
        path = _write_dll(tmp_path)
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"), json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                plugin.register_assembly(backend, path=path, update=True)

    def test_update_dry_run_force_reads_id_no_patch(self, profile, tmp_path):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        path = _write_dll(tmp_path)
        with requests_mock.Mocker() as m:
            # _resolve_id_by_name force-reads even under dry-run
            m.get(dry.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            out = plugin.register_assembly(dry, path=path, update=True)
        assert out["_dry_run"] is True
        assert not _patches(m)


class TestListTypes:
    def test_no_filter_returns_rows(self, backend):
        from crm.core import plugin
        rows = [
            {"plugintypeid": "aaa", "typename": "Contoso.Plugins.Foo",
             "friendlyname": "Foo", "assemblyname": "Contoso.Plugins"},
            {"plugintypeid": "bbb", "typename": "Contoso.Plugins.Bar",
             "friendlyname": "Bar", "assemblyname": "Contoso.Plugins"},
        ]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("plugintypes"), json={"value": rows})
            out = plugin.list_types(backend)
        assert out["value"] == rows
        # selects the documented columns; no $filter without an assembly
        req = m.request_history[0]
        assert "plugintypeid,typename,friendlyname,assemblyname" in req.qs["$select"][0]
        assert "$filter" not in req.qs

    def test_assembly_resolves_id_then_filters_by_assembly_value(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            # 1) resolve assembly name -> pluginassemblyid
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            # 2) list plugintypes filtered by that assembly id
            m.get(backend.url_for("plugintypes"),
                  json={"value": [{"plugintypeid": "aaa",
                                   "typename": "Contoso.Plugins.Foo"}]})
            out = plugin.list_types(backend, assembly="Contoso.Plugins")
        assert len(out["value"]) == 1
        # the assembly was resolved by exact name
        resolve = next(r for r in m.request_history if "pluginassemblies" in r.url)
        assert resolve.qs["$filter"] == ["name eq 'contoso.plugins'"]
        # the type list filters on the resolved assembly lookup value
        listing = next(r for r in m.request_history if "plugintypes" in r.url)
        assert listing.qs["$filter"] == [f"_pluginassemblyid_value eq {_PA_ID}"]

    def test_assembly_not_found_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"), json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                plugin.list_types(backend, assembly="Nope")


_MSG_ID = "22222222-2222-2222-2222-222222222222"
_TYPE_ID = "33333333-3333-3333-3333-333333333333"
_FILTER_ID = "44444444-4444-4444-4444-444444444444"
_STEP_ID = "55555555-5555-5555-5555-555555555555"


def _mock_step_resolution(m, backend, *, message="Create",
                          plugin_type="Contoso.Plugins.PreCreateAccount",
                          entity="account", with_filter=True):
    """Mock the sdkmessages / plugintypes / sdkmessagefilters resolution GETs."""
    m.get(backend.url_for("sdkmessages"),
          json={"value": [{"sdkmessageid": _MSG_ID, "name": message}]})
    m.get(backend.url_for("plugintypes"),
          json={"value": [{"plugintypeid": _TYPE_ID, "typename": plugin_type}]})
    if with_filter:
        m.get(backend.url_for("sdkmessagefilters"),
              json={"value": [{"sdkmessagefilterid": _FILTER_ID}]})


class TestRegisterStep:
    def test_with_entity_binds_all_three(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            out = plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account")
        assert out["created"] is True
        assert out["sdkmessageprocessingstepid"] == _STEP_ID
        body = _posts(m)[0].json()
        assert body["SdkMessageId@odata.bind"] == f"/sdkmessages({_MSG_ID})"
        assert body["PluginTypeId@odata.bind"] == f"/plugintypes({_TYPE_ID})"
        assert body["SdkMessageFilterId@odata.bind"] == \
            f"/sdkmessagefilters({_FILTER_ID})"
        # default stage=postoperation(40), mode=sync(0), rank=1
        assert body["stage"] == 40
        assert body["mode"] == 0
        assert body["rank"] == 1
        # derived default name names type/message/entity
        assert body["name"] == \
            "Contoso.Plugins.PreCreateAccount: Create of account"
        assert out["stage"] == 40
        assert out["mode"] == 0
        assert out["message"] == "Create"
        assert out["entity"] == "account"
        assert out["plugintype"] == "Contoso.Plugins.PreCreateAccount"

    def test_without_entity_no_filter_bind_and_no_filter_get(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend, with_filter=False)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            out = plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount")
        body = _posts(m)[0].json()
        assert "SdkMessageFilterId@odata.bind" not in body
        # no GET to sdkmessagefilters when entity is None
        assert not any("sdkmessagefilters" in r.url for r in m.request_history)
        # derived default names "any entity"
        assert body["name"] == \
            "Contoso.Plugins.PreCreateAccount: Create of any entity"
        assert out["entity"] is None

    def test_explicit_name_wins(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account",
                name="My Custom Step")
        assert _posts(m)[0].json()["name"] == "My Custom Step"

    def test_filtering_attributes_passed_through(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            plugin.register_step(
                backend, message="Update",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account",
                filtering_attributes="a,b")
        assert _posts(m)[0].json()["filteringattributes"] == "a,b"

    def test_no_filtering_attributes_key_when_absent(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account")
        assert "filteringattributes" not in _posts(m)[0].json()

    def test_filtering_attributes_ignored_for_non_update_message(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account",
                filtering_attributes="name,telephone1")
        # filtering_attributes only applies to Update steps
        assert "filteringattributes" not in _posts(m)[0].json()

    def test_stage_and_mode_word_to_int(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            out = plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account",
                stage="preoperation", mode="sync", rank=7)
        body = _posts(m)[0].json()
        assert body["stage"] == 20
        assert body["mode"] == 0
        assert body["rank"] == 7
        assert out["stage"] == 20
        assert out["mode"] == 0

    def test_prevalidation_maps_to_ten(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account",
                stage="prevalidation")
        assert _posts(m)[0].json()["stage"] == 10

    def test_unknown_stage_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="stage"):
                plugin.register_step(
                    backend, message="Create",
                    plugin_type="Contoso.Plugins.PreCreateAccount",
                    stage="bogus")
        # validation happens before any HTTP call
        assert m.request_history == []

    def test_unknown_mode_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="mode"):
                plugin.register_step(
                    backend, message="Create",
                    plugin_type="Contoso.Plugins.PreCreateAccount",
                    mode="bogus")
        assert m.request_history == []

    def test_async_non_postoperation_raises_no_http(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="postoperation"):
                plugin.register_step(
                    backend, message="Create",
                    plugin_type="Contoso.Plugins.PreCreateAccount",
                    mode="async", stage="preoperation")
        # guard fires before any resolution/POST
        assert m.request_history == []

    def test_async_postoperation_allowed(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            out = plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account",
                mode="async", stage="postoperation")
        assert out["created"] is True
        body = _posts(m)[0].json()
        assert body["mode"] == 1
        assert body["stage"] == 40

    def test_message_not_found_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessages"), json={"value": []})
            with pytest.raises(D365Error, match="[Mm]essage"):
                plugin.register_step(
                    backend, message="Nope",
                    plugin_type="Contoso.Plugins.PreCreateAccount")
        assert not _posts(m)

    def test_plugintype_not_found_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessages"),
                  json={"value": [{"sdkmessageid": _MSG_ID, "name": "Create"}]})
            m.get(backend.url_for("plugintypes"), json={"value": []})
            with pytest.raises(D365Error, match="[Pp]lug-?in type"):
                plugin.register_step(
                    backend, message="Create",
                    plugin_type="Contoso.Plugins.Missing")
        assert not _posts(m)

    def test_entity_without_filter_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend, with_filter=False)
            m.get(backend.url_for("sdkmessagefilters"), json={"value": []})
            with pytest.raises(D365Error, match="account"):
                plugin.register_step(
                    backend, message="Create",
                    plugin_type="Contoso.Plugins.PreCreateAccount",
                    entity="account")
        assert not _posts(m)

    def test_filter_get_scoped_by_message_and_entity(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account")
        filt = next(r for r in m.request_history if "sdkmessagefilters" in r.url)
        assert filt.qs["$filter"] == [
            f"primaryobjecttypecode eq 'account' and _sdkmessageid_value eq {_MSG_ID}"
        ]

    def test_assembly_scopes_plugintype_lookup(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            # assembly name -> pluginassemblyid (via _resolve_id_by_name)
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            _mock_step_resolution(m, backend, with_filter=False)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount",
                assembly="Contoso.Plugins")
        types = next(r for r in m.request_history
                     if r.url.split("?")[0].endswith("plugintypes"))
        assert types.qs["$filter"] == [
            f"typename eq 'contoso.plugins.precreateaccount' "
            f"and _pluginassemblyid_value eq {_PA_ID}"
        ]

    def test_unparseable_id_sets_lookup_error(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": "https://x/sdkmessageprocessingsteps(bogus)"})
            out = plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account")
        assert out["created"] is True
        assert out["sdkmessageprocessingstepid"] is None
        assert "sdkmessageprocessingstep_lookup_error" in out

    def test_dry_run_returns_preview_no_post(self, profile):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, dry)
            out = plugin.register_step(
                dry, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account")
        assert out["_dry_run"] is True
        assert not _posts(m)


_DELETE_STEP_ID = "66666666-6666-6666-6666-666666666666"
_PTID_A = "77777777-7777-7777-7777-777777777777"
_PTID_B = "88888888-8888-8888-8888-888888888888"
_STEP_A = "99999999-9999-9999-9999-999999999999"
_STEP_B = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _deletes(m):
    return [r for r in m.request_history if r.method == "DELETE"]


class TestUnregisterStep:
    def test_resolves_by_name_then_deletes(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessageprocessingsteps"),
                  json={"value": [{"sdkmessageprocessingstepid": _DELETE_STEP_ID}]})
            m.delete(
                backend.url_for(f"sdkmessageprocessingsteps({_DELETE_STEP_ID})"),
                status_code=204)
            out = plugin.unregister_step(backend, "My Step")
        assert out["deleted"] is True
        assert out["sdkmessageprocessingstepid"] == _DELETE_STEP_ID
        # resolved by exact name
        resolve = next(r for r in m.request_history if r.method == "GET")
        assert resolve.qs["$filter"] == ["name eq 'my step'"]
        # the DELETE targets the resolved id
        dels = _deletes(m)
        assert len(dels) == 1
        assert f"sdkmessageprocessingsteps({_DELETE_STEP_ID})" in dels[0].url

    def test_guid_skips_resolve_get(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.delete(
                backend.url_for(f"sdkmessageprocessingsteps({_DELETE_STEP_ID})"),
                status_code=204)
            out = plugin.unregister_step(backend, _DELETE_STEP_ID)
        assert out["deleted"] is True
        assert out["sdkmessageprocessingstepid"] == _DELETE_STEP_ID
        # no resolution GET when a GUID is passed directly
        assert not any(r.method == "GET" for r in m.request_history)
        assert len(_deletes(m)) == 1

    def test_not_found_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessageprocessingsteps"), json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                plugin.unregister_step(backend, "Nope")
        assert not _deletes(m)

    def test_dry_run_force_reads_id_no_delete(self, profile):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            # name resolution force-reads even under dry-run
            m.get(dry.url_for("sdkmessageprocessingsteps"),
                  json={"value": [{"sdkmessageprocessingstepid": _DELETE_STEP_ID}]})
            out = plugin.unregister_step(dry, "My Step")
        assert out["_dry_run"] is True
        assert not _deletes(m)

    def test_brace_wrapped_guid_falls_through_to_name_resolution(self, backend):
        # A wrapped value is NOT a bare GUID, so it must resolve by name (and,
        # absent in the mock, raise) rather than build a malformed DELETE.
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessageprocessingsteps"), json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                plugin.unregister_step(backend, "{" + _DELETE_STEP_ID + "}")
        resolve = next(r for r in m.request_history if r.method == "GET")
        assert resolve.qs["$filter"] == [f"name eq '{{{_DELETE_STEP_ID}}}'"]
        assert not _deletes(m)

    def test_ambiguous_name_raises_no_delete(self, backend):
        # Step names are not unique; refuse to delete the first of several.
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessageprocessingsteps"),
                  json={"value": [{"sdkmessageprocessingstepid": _STEP_A},
                                  {"sdkmessageprocessingstepid": _STEP_B}]})
            with pytest.raises(D365Error, match="Multiple plug-in steps match"):
                plugin.unregister_step(backend, "Dupe")
        assert not _deletes(m)


class TestUnregisterAssembly:
    def test_deletes_dependent_steps_before_assembly(self, backend):
        from crm.core import plugin
        aid = _PA_ID
        with requests_mock.Mocker() as m:
            # assembly name -> id
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": aid}]})
            # plugintypes of the assembly
            m.get(backend.url_for("plugintypes"),
                  json={"value": [{"plugintypeid": _PTID_A},
                                  {"plugintypeid": _PTID_B}]})
            # steps per plugintype (requests_mock matches on the same url; we
            # discriminate via the _plugintypeid_value filter at assertion time)
            def steps_cb(request, context):
                context.status_code = 200
                filt = request.qs["$filter"][0]
                if _PTID_A in filt:
                    return {"value": [{"sdkmessageprocessingstepid": _STEP_A}]}
                if _PTID_B in filt:
                    return {"value": [{"sdkmessageprocessingstepid": _STEP_B}]}
                return {"value": []}
            m.get(backend.url_for("sdkmessageprocessingsteps"), json=steps_cb)
            m.delete(
                backend.url_for(f"sdkmessageprocessingsteps({_STEP_A})"),
                status_code=204)
            m.delete(
                backend.url_for(f"sdkmessageprocessingsteps({_STEP_B})"),
                status_code=204)
            m.delete(backend.url_for(f"pluginassemblies({aid})"), status_code=204)
            out = plugin.unregister_assembly(backend, "Contoso.Plugins")
        assert out["deleted"] is True
        assert out["pluginassemblyid"] == aid
        assert out["steps_deleted"] == 2
        assert set(out["deleted_step_ids"]) == {_STEP_A, _STEP_B}
        # SEQUENCE: every step DELETE must precede the assembly DELETE
        del_urls = [r.url for r in _deletes(m)]
        assembly_idx = next(
            i for i, u in enumerate(del_urls) if f"pluginassemblies({aid})" in u)
        step_idxs = [
            i for i, u in enumerate(del_urls)
            if "sdkmessageprocessingsteps(" in u]
        assert step_idxs, "expected step deletes"
        assert max(step_idxs) < assembly_idx

    def test_no_dependent_steps_just_deletes_assembly(self, backend):
        from crm.core import plugin
        aid = _PA_ID
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": aid}]})
            m.get(backend.url_for("plugintypes"), json={"value": []})
            m.delete(backend.url_for(f"pluginassemblies({aid})"), status_code=204)
            out = plugin.unregister_assembly(backend, "Contoso.Plugins")
        assert out["deleted"] is True
        assert out["steps_deleted"] == 0
        assert out["deleted_step_ids"] == []
        dels = _deletes(m)
        assert len(dels) == 1
        assert f"pluginassemblies({aid})" in dels[0].url

    def test_guid_skips_assembly_name_resolve(self, backend):
        from crm.core import plugin
        aid = _PA_ID
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("plugintypes"), json={"value": []})
            m.delete(backend.url_for(f"pluginassemblies({aid})"), status_code=204)
            out = plugin.unregister_assembly(backend, aid)
        assert out["pluginassemblyid"] == aid
        # no GET to pluginassemblies (name resolution) when a GUID is passed
        assert not any(
            r.url.split("?")[0].endswith("pluginassemblies")
            for r in m.request_history if r.method == "GET")

    def test_dry_run_issues_no_real_delete(self, profile):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        aid = _PA_ID
        with requests_mock.Mocker() as m:
            # resolution GETs force-read even under dry-run
            m.get(dry.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": aid}]})
            m.get(dry.url_for("plugintypes"),
                  json={"value": [{"plugintypeid": _PTID_A}]})
            m.get(dry.url_for("sdkmessageprocessingsteps"),
                  json={"value": [{"sdkmessageprocessingstepid": _STEP_A}]})
            out = plugin.unregister_assembly(dry, "Contoso.Plugins")
        assert not _deletes(m)
        assert out.get("_dry_run") is True
        assert out["pluginassemblyid"] == aid
        assert out["steps_deleted"] == 1
        assert out["deleted_step_ids"] == [_STEP_A]


class TestUnregisterCommands:
    def _runner(self):
        from click.testing import CliRunner
        return CliRunner()

    def test_unregister_assembly_yes_skips_prompt(self, monkeypatch):
        from crm.cli import cli
        called = {}
        monkeypatch.setattr(
            "crm.core.plugin.unregister_assembly",
            lambda backend, assembly: called.setdefault("a", assembly)
            or {"deleted": True, "pluginassemblyid": _PA_ID,
                "steps_deleted": 0, "deleted_step_ids": []})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(cli, [
            "--json", "plugin", "unregister-assembly", "Contoso.Plugins", "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert called["a"] == "Contoso.Plugins"

    def test_unregister_assembly_no_yes_non_tty_aborts(self, monkeypatch):
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(cli, [
            "--json", "plugin", "unregister-assembly", "Contoso.Plugins",
        ])
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output

    def test_unregister_step_yes_skips_prompt(self, monkeypatch):
        from crm.cli import cli
        called = {}
        monkeypatch.setattr(
            "crm.core.plugin.unregister_step",
            lambda backend, step: called.setdefault("s", step)
            or {"deleted": True, "sdkmessageprocessingstepid": _STEP_ID})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(cli, [
            "--json", "plugin", "unregister-step", "My Step", "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert called["s"] == "My Step"

    def test_unregister_step_no_yes_non_tty_aborts(self, monkeypatch):
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(cli, [
            "--json", "plugin", "unregister-step", "My Step",
        ])
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output

    def test_unregister_assembly_handles_d365_error(self, monkeypatch):
        import json
        from crm.cli import cli

        def boom(backend, assembly):
            raise D365Error("boom", status=400)

        monkeypatch.setattr("crm.core.plugin.unregister_assembly", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(cli, [
            "--json", "plugin", "unregister-assembly", "Contoso.Plugins", "--yes",
        ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "boom" in env["error"]

    def test_unregister_step_handles_d365_error(self, monkeypatch):
        import json
        from crm.cli import cli

        def boom(backend, step):
            raise D365Error("boom", status=400)

        monkeypatch.setattr("crm.core.plugin.unregister_step", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(cli, [
            "--json", "plugin", "unregister-step", "My Step", "--yes",
        ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "boom" in env["error"]


class TestPluginCommands:
    def test_register_assembly_command_wires_core(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_register(backend, **kw):
            captured.update(kw)
            return {"created": True, "pluginassemblyid": _PA_ID,
                    "name": "Contoso.Plugins"}

        monkeypatch.setattr("crm.core.plugin.register_assembly", fake_register)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("Contoso.Plugins.dll", "wb") as fh:
                fh.write(b"MZ\x90\x00fake")
            result = runner.invoke(cli, [
                "--json", "plugin", "register-assembly", "Contoso.Plugins.dll",
            ])
        assert result.exit_code == 0, result.output
        # command passes the path through; core reads the bytes
        assert captured["path"] == "Contoso.Plugins.dll"
        assert captured["isolation_mode"] == "sandbox"
        assert captured["update"] is False
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["pluginassemblyid"] == _PA_ID

    def test_register_assembly_command_passes_options(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.plugin.register_assembly",
            lambda backend, **kw: captured.update(kw)
            or {"created": True, "pluginassemblyid": _PA_ID, "name": kw.get("name")})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("a.dll", "wb") as fh:
                fh.write(b"MZ")
            result = runner.invoke(cli, [
                "--json", "plugin", "register-assembly", "a.dll",
                "--name", "Custom.Name", "--version", "2.0.0.0",
                "--culture", "en-US", "--public-key-token", "abc",
                "--isolation-mode", "none", "--description", "desc",
                "--update",
            ])
        assert result.exit_code == 0, result.output
        assert captured["name"] == "Custom.Name"
        assert captured["version"] == "2.0.0.0"
        assert captured["culture"] == "en-US"
        assert captured["public_key_token"] == "abc"
        assert captured["isolation_mode"] == "none"
        assert captured["description"] == "desc"
        assert captured["update"] is True

    def test_update_with_identity_flag_warns(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr(
            "crm.core.plugin.register_assembly",
            lambda backend, **kw: {"updated": True, "pluginassemblyid": _PA_ID,
                                   "name": "Contoso.Plugins"})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("a.dll", "wb") as fh:
                fh.write(b"MZ")
            result = runner.invoke(cli, [
                "--json", "plugin", "register-assembly", "a.dll",
                "--update", "--version", "2.0.0.0",
            ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        warnings = env["meta"]["warnings"]
        assert any("--version" in w and "content only" in w for w in warnings)

    def test_update_without_identity_flags_no_warning(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr(
            "crm.core.plugin.register_assembly",
            lambda backend, **kw: {"updated": True, "pluginassemblyid": _PA_ID,
                                   "name": "Contoso.Plugins"})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("a.dll", "wb") as fh:
                fh.write(b"MZ")
            result = runner.invoke(cli, [
                "--json", "plugin", "register-assembly", "a.dll", "--update",
            ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        # plain --update is the content-only happy path: no ignored-flags warning
        assert env.get("meta", {}).get("warnings") is None

    def test_register_assembly_command_handles_d365_error(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli

        def boom(backend, **kw):
            raise D365Error("boom", status=400)

        monkeypatch.setattr("crm.core.plugin.register_assembly", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("a.dll", "wb") as fh:
                fh.write(b"MZ")
            result = runner.invoke(cli, [
                "--json", "plugin", "register-assembly", "a.dll",
            ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "boom" in env["error"]

    def test_register_assembly_command_missing_file_errors(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-assembly", "does-not-exist.dll",
        ])
        # click.Path(exists=True) rejects the missing file
        assert result.exit_code != 0

    def test_list_types_command_wires_core(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_list(backend, **kw):
            captured.update(kw)
            return {"value": [
                {"plugintypeid": _PA_ID, "typename": "Contoso.Plugins.Foo",
                 "friendlyname": "Foo"}]}

        monkeypatch.setattr("crm.core.plugin.list_types", fake_list)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "list-types", "--assembly", "Contoso.Plugins",
        ])
        assert result.exit_code == 0, result.output
        assert captured["assembly"] == "Contoso.Plugins"
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["meta"]["count"] == 1
        assert env["data"][0]["typename"] == "Contoso.Plugins.Foo"

    def test_list_types_command_handles_d365_error(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli

        def boom(backend, **kw):
            raise D365Error("boom", status=400)

        monkeypatch.setattr("crm.core.plugin.list_types", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, ["--json", "plugin", "list-types"])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "boom" in env["error"]

    def test_register_step_command_wires_core(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_step(backend, **kw):
            captured.update(kw)
            return {"created": True, "sdkmessageprocessingstepid": _STEP_ID,
                    "name": "step", "stage": 40, "mode": 0,
                    "message": "Create", "entity": "account",
                    "plugintype": "Contoso.Plugins.PreCreateAccount"}

        monkeypatch.setattr("crm.core.plugin.register_step", fake_step)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-step",
            "--message", "Create",
            "--plugin-type", "Contoso.Plugins.PreCreateAccount",
            "--entity", "account",
            "--stage", "preoperation", "--mode", "async", "--rank", "5",
            "--filtering-attributes", "name,telephone1",
            "--name", "My Step", "--assembly", "Contoso.Plugins",
        ])
        assert result.exit_code == 0, result.output
        assert captured["message"] == "Create"
        assert captured["plugin_type"] == "Contoso.Plugins.PreCreateAccount"
        assert captured["entity"] == "account"
        assert captured["stage"] == "preoperation"
        assert captured["mode"] == "async"
        assert captured["rank"] == 5
        assert captured["filtering_attributes"] == "name,telephone1"
        assert captured["name"] == "My Step"
        assert captured["assembly"] == "Contoso.Plugins"
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["sdkmessageprocessingstepid"] == _STEP_ID

    def test_register_step_command_defaults(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        monkeypatch.setattr(
            "crm.core.plugin.register_step",
            lambda backend, **kw: captured.update(kw) or {
                "created": True, "sdkmessageprocessingstepid": _STEP_ID})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-step",
            "--message", "Create",
            "--plugin-type", "Contoso.Plugins.PreCreateAccount",
        ])
        assert result.exit_code == 0, result.output
        assert captured["entity"] is None
        assert captured["stage"] == "postoperation"
        assert captured["mode"] == "sync"
        assert captured["rank"] == 1
        assert captured["filtering_attributes"] is None
        assert captured["name"] is None
        assert captured["assembly"] is None

    def test_register_step_command_handles_d365_error(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli

        def boom(backend, **kw):
            raise D365Error("boom", status=400)

        monkeypatch.setattr("crm.core.plugin.register_step", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-step",
            "--message", "Create",
            "--plugin-type", "Contoso.Plugins.PreCreateAccount",
        ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "boom" in env["error"]
