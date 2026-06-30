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

from crm.utils.d365_backend import D365Backend, D365Error


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
        # exactly one POST, to pluginassemblies — register_assembly never POSTs
        # plugintype rows (a content-only upload does not create them; use
        # register_type)
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


_PT_ID = "66666666-6666-6666-6666-666666666666"


class TestRegisterType:
    def test_resolves_assembly_then_posts_plugintype(self, backend):
        from crm.core import plugin
        pt_url = backend.url_for(f"plugintypes({_PT_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            m.post(backend.url_for("plugintypes"), status_code=204,
                   headers={"OData-EntityId": pt_url})
            out = plugin.register_type(
                backend, assembly="Contoso.Plugins",
                type_name="Contoso.Plugins.PreCreateAccount")
        assert out["created"] is True
        assert out["plugintypeid"] == _PT_ID
        # assembly resolved by exact name
        resolve = next(r for r in m.request_history
                       if "pluginassemblies" in r.url)
        assert resolve.qs["$filter"] == ["name eq 'contoso.plugins'"]
        body = _posts(m)[0].json()
        assert body["typename"] == "Contoso.Plugins.PreCreateAccount"
        # friendlyname defaults to the type name
        assert body["friendlyname"] == "Contoso.Plugins.PreCreateAccount"
        # bound to the resolved assembly id (navprop = pluginassemblyid)
        assert body["pluginassemblyid@odata.bind"] == (
            f"/pluginassemblies({_PA_ID})")
        # read-only / server-derived identity is never sent
        assert "version" not in body
        assert "culture" not in body
        assert "publickeytoken" not in body

    def test_friendly_name_override(self, backend):
        from crm.core import plugin
        pt_url = backend.url_for(f"plugintypes({_PT_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            m.post(backend.url_for("plugintypes"), status_code=204,
                   headers={"OData-EntityId": pt_url})
            out = plugin.register_type(
                backend, assembly="Contoso.Plugins",
                type_name="Contoso.Plugins.PreCreateAccount",
                friendly_name="Pre-create account")
        body = _posts(m)[0].json()
        assert body["friendlyname"] == "Pre-create account"
        assert out["friendlyname"] == "Pre-create account"

    def test_solution_header_routed(self, backend):
        from crm.core import plugin
        pt_url = backend.url_for(f"plugintypes({_PT_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            m.post(backend.url_for("plugintypes"), status_code=204,
                   headers={"OData-EntityId": pt_url})
            out = plugin.register_type(
                backend, assembly="Contoso.Plugins",
                type_name="Contoso.Plugins.Foo", solution="Foo")
        assert _posts(m)[0].headers["MSCRM.SolutionUniqueName"] == "Foo"
        assert out["solution"] == "Foo"

    def test_unknown_assembly_raises_clean_d365error(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"), json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                plugin.register_type(
                    backend, assembly="Nope", type_name="X.Y")
        # raised before any POST
        assert not _posts(m)

    def test_unparseable_id_sets_lookup_error(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            m.post(backend.url_for("plugintypes"), status_code=204,
                   headers={"OData-EntityId": "https://x/plugintypes(bogus)"})
            out = plugin.register_type(
                backend, assembly="Contoso.Plugins", type_name="X.Y")
        assert out["created"] is True
        assert out["plugintypeid"] is None
        assert "plugintype_lookup_error" in out

    def test_dry_run_force_reads_assembly_no_post(self, profile):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            # assembly name->id resolution is a GET: runs live even under dry-run
            m.get(dry.url_for("pluginassemblies"),
                  json={"value": [{"pluginassemblyid": _PA_ID}]})
            out = plugin.register_type(
                dry, assembly="Contoso.Plugins", type_name="X.Y")
        assert out["_dry_run"] is True
        assert out["would_create"] is True
        assert not _posts(m)


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
        assert body["sdkmessageid@odata.bind"] == f"/sdkmessages({_MSG_ID})"
        assert body["plugintypeid@odata.bind"] == f"/plugintypes({_TYPE_ID})"
        assert body["sdkmessagefilterid@odata.bind"] == \
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
        assert "sdkmessagefilterid@odata.bind" not in body
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

    def test_configuration_passed_through(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            plugin.register_step(
                backend, message="Update",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account",
                configuration="someconfig")
        assert _posts(m)[0].json()["configuration"] == "someconfig"

    def test_asyncautodelete_passed_through(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            plugin.register_step(
                backend, message="Update",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account",
                mode="async", asyncautodelete=True)
        assert _posts(m)[0].json()["asyncautodelete"] is True

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

    @pytest.mark.parametrize("stage,stage_int", [
        ("prevalidation", 10),
        ("preoperation", 20),
        ("postoperation", 40),
    ])
    def test_stage_and_mode_word_to_int(self, backend, stage, stage_int):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            out = plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account",
                stage=stage, mode="sync", rank=7)
        body = _posts(m)[0].json()
        assert body["stage"] == stage_int
        assert body["mode"] == 0
        assert body["rank"] == 7
        assert out["stage"] == stage_int
        assert out["mode"] == 0

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

    def test_service_endpoint_binds_eventhandler_not_plugintype(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessages"),
                  json={"value": [{"sdkmessageid": _MSG_ID, "name": "Create"}]})
            m.get(backend.url_for("serviceendpoints"),
                  json={"value": [{"serviceendpointid": _SE_ID}]})
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            out = plugin.register_step(
                backend, message="Create", service_endpoint="Contoso Hook")
        body = _posts(m)[0].json()
        assert body["eventhandler_serviceendpoint@odata.bind"] == \
            f"/serviceendpoints({_SE_ID})"
        assert "plugintypeid@odata.bind" not in body
        # no plug-in type lookup happens
        assert not any("plugintypes" in r.url for r in m.request_history)
        # derived name uses the endpoint as the handler label
        assert body["name"] == "Contoso Hook: Create of any entity"
        assert out["service_endpoint"] == "Contoso Hook"
        assert out["plugintype"] is None

    def test_requires_exactly_one_handler_neither(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="exactly one"):
                plugin.register_step(backend, message="Create")
        assert m.request_history == []

    def test_requires_exactly_one_handler_both(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="exactly one"):
                plugin.register_step(
                    backend, message="Create",
                    plugin_type="Contoso.Plugins.PreCreateAccount",
                    service_endpoint="Contoso Hook")
        assert m.request_history == []

    def test_service_endpoint_not_found_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessages"),
                  json={"value": [{"sdkmessageid": _MSG_ID, "name": "Create"}]})
            m.get(backend.url_for("serviceendpoints"), json={"value": []})
            with pytest.raises(D365Error, match="[Ss]ervice endpoint"):
                plugin.register_step(
                    backend, message="Create", service_endpoint="Nope")
        assert not _posts(m)

    def test_service_endpoint_dry_run_reference(self, profile):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(dry.url_for("sdkmessages"),
                  json={"value": [{"sdkmessageid": _MSG_ID, "name": "Create"}]})
            m.get(dry.url_for("serviceendpoints"), json={"value": []})
            out = plugin.register_step(
                dry, message="Create", service_endpoint="Ghost Hook")
        assert out["_dry_run"] is True
        kinds = {r["kind"]: r["_exists"] for r in out["references"]}
        assert kinds["service_endpoint"] is False
        assert kinds["message"] is True
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

    def test_solution_header_routed(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            out = plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account",
                solution="cwx_sol")
        # the step row lands in the targeted solution via the create header
        assert _posts(m)[0].headers["MSCRM.SolutionUniqueName"] == "cwx_sol"
        assert out["solution"] == "cwx_sol"

    def test_no_solution_header_when_absent(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_step_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
                   headers={"OData-EntityId": step_url})
            out = plugin.register_step(
                backend, message="Create",
                plugin_type="Contoso.Plugins.PreCreateAccount", entity="account")
        assert "MSCRM.SolutionUniqueName" not in _posts(m)[0].headers
        assert out["solution"] is None


_SE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class TestRegisterWebhook:
    def test_posts_serviceendpoint_with_webhook_fields(self, backend):
        from crm.core import plugin
        se_url = backend.url_for(f"serviceendpoints({_SE_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("serviceendpoints"), status_code=204,
                   headers={"OData-EntityId": se_url})
            out = plugin.register_webhook(
                backend, name="Contoso Hook",
                url="https://example.com/hook", auth="webhookkey",
                auth_value="secret-code")
        assert out["created"] is True
        assert out["serviceendpointid"] == _SE_ID
        body = _posts(m)[0].json()
        assert body["name"] == "Contoso Hook"
        assert body["url"] == "https://example.com/hook"
        # contract 8=Webhook, connectionmode 1=Normal, messageformat 2=Json
        assert body["contract"] == 8
        assert body["connectionmode"] == 1
        assert body["messageformat"] == 2
        # webhookkey -> authtype 4
        assert body["authtype"] == 4
        assert body["authvalue"] == "secret-code"
        # echoed back (but never the secret authvalue)
        assert out["name"] == "Contoso Hook"
        assert out["contract"] == 8
        assert out["authtype"] == 4
        assert "authvalue" not in out

    def test_auth_scheme_maps_to_authtype(self, backend):
        from crm.core import plugin
        se_url = backend.url_for(f"serviceendpoints({_SE_ID})")
        for scheme, expected in [("webhookkey", 4), ("httpheader", 5),
                                 ("httpquerystring", 6)]:
            with requests_mock.Mocker() as m:
                m.post(backend.url_for("serviceendpoints"), status_code=204,
                       headers={"OData-EntityId": se_url})
                plugin.register_webhook(
                    backend, name="H", url="https://e/h", auth=scheme,
                    auth_value="v")
            assert _posts(m)[0].json()["authtype"] == expected

    def test_unknown_auth_raises_no_http(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="auth"):
                plugin.register_webhook(
                    backend, name="H", url="https://e/h", auth="bogus",
                    auth_value="v")
        assert m.request_history == []

    def test_solution_header_routed(self, backend):
        from crm.core import plugin
        se_url = backend.url_for(f"serviceendpoints({_SE_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("serviceendpoints"), status_code=204,
                   headers={"OData-EntityId": se_url})
            out = plugin.register_webhook(
                backend, name="H", url="https://e/h", auth="webhookkey",
                auth_value="v", solution="cwx_sol")
        assert _posts(m)[0].headers["MSCRM.SolutionUniqueName"] == "cwx_sol"
        assert out["solution"] == "cwx_sol"

    def test_dry_run_returns_preview_no_post(self, profile):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            out = plugin.register_webhook(
                dry, name="H", url="https://e/h", auth="webhookkey",
                auth_value="v")
        assert out["_dry_run"] is True
        assert not _posts(m)

    def test_unparseable_id_sets_lookup_error(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("serviceendpoints"), status_code=204,
                   headers={"OData-EntityId": "https://x/serviceendpoints(bogus)"})
            out = plugin.register_webhook(
                backend, name="H", url="https://e/h", auth="webhookkey",
                auth_value="v")
        assert out["created"] is True
        assert out["serviceendpointid"] is None
        assert "serviceendpoint_lookup_error" in out


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


_IMG_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _mock_image_resolution(m, backend, *, step_id=_STEP_ID, stage=20,
                           message="Update"):
    """Mock the step-info and sdkmessage-name resolution GETs for images."""
    m.get(backend.url_for("sdkmessageprocessingsteps"),
          json={"value": [{"sdkmessageprocessingstepid": step_id,
                           "stage": stage, "_sdkmessageid_value": _MSG_ID}]})
    m.get(backend.url_for("sdkmessages"),
          json={"value": [{"sdkmessageid": _MSG_ID, "name": message}]})


class TestRegisterImage:
    def test_pre_image_posts_required_fields_with_lowercase_bind(self, backend):
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            out = plugin.register_image(
                backend, step=_STEP_ID, image_type="pre", alias="preimg")
        assert out["created"] is True
        assert out["sdkmessageprocessingstepimageid"] == _IMG_ID
        body = _posts(m)[0].json()
        # nav-prop is the lowercase logical name (issue #159 lesson)
        assert body["sdkmessageprocessingstepid@odata.bind"] == \
            f"/sdkmessageprocessingsteps({_STEP_ID})"
        assert body["imagetype"] == 0
        assert body["entityalias"] == "preimg"
        # name defaults to the alias (PRT does the same)
        assert body["name"] == "preimg"
        # messagepropertyname derived from the step's message (Update -> Target)
        assert body["messagepropertyname"] == "Target"
        assert out["entityalias"] == "preimg"
        assert out["imagetype"] == 0
        assert out["messagepropertyname"] == "Target"
        assert out["sdkmessageprocessingstepid"] == _STEP_ID

    def test_step_resolved_by_exact_name(self, backend):
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            out = plugin.register_image(
                backend, step="My Step", image_type="pre", alias="preimg")
        assert out["created"] is True
        # resolved by exact name, selecting stage and message for derivation
        resolve = next(r for r in m.request_history
                       if r.url.split("?")[0].endswith(
                           "sdkmessageprocessingsteps"))
        assert resolve.qs["$filter"] == ["name eq 'my step'"]
        # the bind targets the resolved id
        assert _posts(m)[0].json()["sdkmessageprocessingstepid@odata.bind"] == \
            f"/sdkmessageprocessingsteps({_STEP_ID})"

    def test_post_image_on_postoperation_step(self, backend):
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend, stage=40)
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            out = plugin.register_image(
                backend, step=_STEP_ID, image_type="post", alias="postimg")
        assert out["created"] is True
        assert _posts(m)[0].json()["imagetype"] == 1
        assert out["imagetype"] == 1

    def test_both_image_on_postoperation_step(self, backend):
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend, stage=40)
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            out = plugin.register_image(
                backend, step=_STEP_ID, image_type="both", alias="bothimg")
        assert out["created"] is True
        assert _posts(m)[0].json()["imagetype"] == 2
        assert out["imagetype"] == 2

    def test_post_image_requires_postoperation_stage(self, backend):
        # MS Learn: post-images only exist for PostOperation-stage steps.
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend, stage=20)
            with pytest.raises(D365Error, match="[Pp]ost.*PostOperation"):
                plugin.register_image(
                    backend, step=_STEP_ID, image_type="post", alias="postimg")
        assert not _posts(m)

    def test_pre_image_on_create_step_raises(self, backend):
        # MS Learn: no pre-image on Create — the record does not exist yet.
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend, stage=40, message="Create")
            with pytest.raises(D365Error, match="[Pp]re-image.*Create"):
                plugin.register_image(
                    backend, step=_STEP_ID, image_type="pre", alias="preimg")
        assert not _posts(m)

    def test_post_image_on_create_step_keys_on_id(self, backend):
        # Create images use messagepropertyname "Id" (the created row's id), not
        # "Target" — the platform rejects "Target" on the Create message. Only a
        # post-image is valid on Create.
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend, stage=40, message="Create")
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            out = plugin.register_image(
                backend, step=_STEP_ID, image_type="post", alias="postimg")
        assert _posts(m)[0].json()["messagepropertyname"] == "Id"
        assert out["messagepropertyname"] == "Id"

    def test_post_image_on_delete_step_raises(self, backend):
        # MS Learn: no post-image on Delete — the record no longer exists.
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend, stage=40, message="Delete")
            with pytest.raises(D365Error, match="[Pp]ost-image.*Delete"):
                plugin.register_image(
                    backend, step=_STEP_ID, image_type="post", alias="postimg")
        assert not _posts(m)

    def test_unsupported_message_without_override_raises(self, backend):
        # Send is ambiguous (FaxId/EmailId/TemplateId) so it is absent from the
        # derivation table; without an explicit property the call must fail
        # clean, naming the escape hatch.
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend, stage=40, message="Send")
            with pytest.raises(D365Error, match="message_property_name"):
                plugin.register_image(
                    backend, step=_STEP_ID, image_type="pre", alias="preimg")
        assert not _posts(m)

    def test_message_property_override_wins(self, backend):
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend, stage=40, message="Send")
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            out = plugin.register_image(
                backend, step=_STEP_ID, image_type="pre", alias="preimg",
                message_property_name="EmailId")
        assert _posts(m)[0].json()["messagepropertyname"] == "EmailId"
        assert out["messagepropertyname"] == "EmailId"

    def test_attributes_filter_passed_through(self, backend):
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            out = plugin.register_image(
                backend, step=_STEP_ID, image_type="pre", alias="preimg",
                attributes="name,telephone1")
        assert _posts(m)[0].json()["attributes"] == "name,telephone1"
        assert out["attributes"] == "name,telephone1"

    def test_attributes_omitted_means_all_columns(self, backend):
        # Omitting the column = all columns (documented anti-pattern but valid).
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            plugin.register_image(
                backend, step=_STEP_ID, image_type="pre", alias="preimg")
        assert "attributes" not in _posts(m)[0].json()

    def test_explicit_name_wins_over_alias_default(self, backend):
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            out = plugin.register_image(
                backend, step=_STEP_ID, image_type="pre", alias="preimg",
                name="My Image")
        assert _posts(m)[0].json()["name"] == "My Image"
        assert out["name"] == "My Image"

    def test_unknown_image_type_raises_no_http(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="image type"):
                plugin.register_image(
                    backend, step=_STEP_ID, image_type="bogus", alias="a")
        # validation happens before any HTTP call
        assert m.request_history == []

    def test_step_not_found_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessageprocessingsteps"),
                  json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                plugin.register_image(
                    backend, step="Nope", image_type="pre", alias="preimg")
        assert not _posts(m)

    def test_ambiguous_step_name_raises_no_post(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessageprocessingsteps"),
                  json={"value": [
                      {"sdkmessageprocessingstepid": _STEP_A, "stage": 40,
                       "_sdkmessageid_value": _MSG_ID},
                      {"sdkmessageprocessingstepid": _STEP_B, "stage": 40,
                       "_sdkmessageid_value": _MSG_ID}]})
            with pytest.raises(D365Error, match="Multiple plug-in steps"):
                plugin.register_image(
                    backend, step="Dupe", image_type="pre", alias="preimg")
        assert not _posts(m)

    def test_unparseable_id_sets_lookup_error(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204,
                   headers={"OData-EntityId":
                            "https://x/sdkmessageprocessingstepimages(bogus)"})
            out = plugin.register_image(
                backend, step=_STEP_ID, image_type="pre", alias="preimg")
        assert out["created"] is True
        assert out["sdkmessageprocessingstepimageid"] is None
        assert "sdkmessageprocessingstepimage_lookup_error" in out

    def test_dry_run_force_reads_resolution_no_post(self, profile):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            # step + message resolution force-read even under dry-run so the
            # validity rules and messagepropertyname derivation still run
            _mock_image_resolution(m, dry)
            out = plugin.register_image(
                dry, step=_STEP_ID, image_type="pre", alias="preimg")
        assert out["_dry_run"] is True
        assert not _posts(m)
        gets = [r for r in m.request_history if r.method == "GET"]
        assert len(gets) == 2

    def test_solution_header_routed(self, backend):
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            out = plugin.register_image(
                backend, step=_STEP_ID, image_type="pre", alias="preimg",
                solution="cwx_sol")
        # the image row lands in the targeted solution via the create header
        assert _posts(m)[0].headers["MSCRM.SolutionUniqueName"] == "cwx_sol"
        assert out["solution"] == "cwx_sol"

    def test_no_solution_header_when_absent(self, backend):
        from crm.core import plugin
        img_url = backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend)
            m.post(backend.url_for("sdkmessageprocessingstepimages"),
                   status_code=204, headers={"OData-EntityId": img_url})
            out = plugin.register_image(
                backend, step=_STEP_ID, image_type="pre", alias="preimg")
        assert "MSCRM.SolutionUniqueName" not in _posts(m)[0].headers
        assert out["solution"] is None


class TestSetStepState:
    def test_enables_step(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        img_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend)
            m.patch(step_url, status_code=204)
            out = plugin.set_step_state(backend, step="My Step", enable=True)
        assert out["enabled"] is True
        assert out["sdkmessageprocessingstepid"] == _STEP_ID
        assert [r for r in m.request_history if r.method == "PATCH"][0].json()["statecode"] == 0
        assert [r for r in m.request_history if r.method == "PATCH"][0].json()["statuscode"] == 1

    def test_disables_step(self, backend):
        from crm.core import plugin
        step_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        img_url = backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")
        with requests_mock.Mocker() as m:
            _mock_image_resolution(m, backend)
            m.patch(step_url, status_code=204)
            out = plugin.set_step_state(backend, step="My Step", enable=False)
        assert out["enabled"] is False
        assert out["sdkmessageprocessingstepid"] == _STEP_ID
        assert [r for r in m.request_history if r.method == "PATCH"][0].json()["statecode"] == 1
        assert [r for r in m.request_history if r.method == "PATCH"][0].json()["statuscode"] == 2


class TestUnregisterImage:
    def test_guid_deletes_directly(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.delete(
                backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})"),
                status_code=204)
            out = plugin.unregister_image(backend, _IMG_ID)
        assert out["deleted"] is True
        assert out["sdkmessageprocessingstepimageid"] == _IMG_ID
        # no resolution GET when a GUID is passed directly
        assert not any(r.method == "GET" for r in m.request_history)
        assert len(_deletes(m)) == 1

    def test_resolves_by_name_then_deletes(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessageprocessingstepimages"),
                  json={"value": [
                      {"sdkmessageprocessingstepimageid": _IMG_ID}]})
            m.delete(
                backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})"),
                status_code=204)
            out = plugin.unregister_image(backend, "My Image")
        assert out["deleted"] is True
        assert out["sdkmessageprocessingstepimageid"] == _IMG_ID
        resolve = next(r for r in m.request_history if r.method == "GET")
        assert resolve.qs["$filter"] == ["name eq 'my image'"]
        dels = _deletes(m)
        assert len(dels) == 1
        assert f"sdkmessageprocessingstepimages({_IMG_ID})" in dels[0].url

    def test_not_found_raises(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessageprocessingstepimages"),
                  json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                plugin.unregister_image(backend, "Nope")
        assert not _deletes(m)

    def test_ambiguous_name_raises_no_delete(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("sdkmessageprocessingstepimages"),
                  json={"value": [
                      {"sdkmessageprocessingstepimageid": _STEP_A},
                      {"sdkmessageprocessingstepimageid": _STEP_B}]})
            with pytest.raises(D365Error,
                               match="Multiple plug-in step images"):
                plugin.unregister_image(backend, "Dupe")
        assert not _deletes(m)

    def test_dry_run_force_reads_id_no_delete(self, profile):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(dry.url_for("sdkmessageprocessingstepimages"),
                  json={"value": [
                      {"sdkmessageprocessingstepimageid": _IMG_ID}]})
            out = plugin.unregister_image(dry, "My Image")
        assert out["_dry_run"] is True
        assert not _deletes(m)


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

    def test_unregister_image_yes_skips_prompt(self, monkeypatch):
        from crm.cli import cli
        called = {}
        monkeypatch.setattr(
            "crm.core.plugin.unregister_image",
            lambda backend, image: called.setdefault("i", image)
            or {"deleted": True, "sdkmessageprocessingstepimageid": _IMG_ID})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(cli, [
            "--json", "plugin", "unregister-image", _IMG_ID, "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert called["i"] == _IMG_ID

    def test_unregister_image_no_yes_non_tty_aborts(self, monkeypatch):
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(cli, [
            "--json", "plugin", "unregister-image", _IMG_ID,
        ])
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output

    def test_unregister_image_handles_d365_error(self, monkeypatch):
        import json
        from crm.cli import cli

        def boom(backend, image):
            raise D365Error("boom", status=400)

        monkeypatch.setattr("crm.core.plugin.unregister_image", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(cli, [
            "--json", "plugin", "unregister-image", _IMG_ID, "--yes",
        ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "boom" in env["error"]

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
                "--solution", "MySol",
            ])
        assert result.exit_code == 0, result.output
        # command passes the path through; core reads the bytes
        assert captured["path"] == "Contoso.Plugins.dll"
        assert captured["isolation_mode"] == "sandbox"
        assert captured["update"] is False
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["pluginassemblyid"] == _PA_ID

    def test_register_type_command_wires_core(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_register_type(backend, **kw):
            captured.update(kw)
            return {"created": True, "plugintypeid": _PT_ID,
                    "typename": kw["type_name"]}

        monkeypatch.setattr("crm.core.plugin.register_type", fake_register_type)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-type",
            "--assembly", "Contoso.Plugins",
            "--type", "Contoso.Plugins.PreCreateAccount",
            "--friendly-name", "Pre-create account",
            "--solution", "MySol",
        ])
        assert result.exit_code == 0, result.output
        assert captured["assembly"] == "Contoso.Plugins"
        assert captured["type_name"] == "Contoso.Plugins.PreCreateAccount"
        assert captured["friendly_name"] == "Pre-create account"
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["plugintypeid"] == _PT_ID

    def test_register_type_command_handles_d365_error(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli

        def boom(backend, **kw):
            raise D365Error("Plug-in assembly not found: Nope",
                            code="PluginAssemblyNotFound")

        monkeypatch.setattr("crm.core.plugin.register_type", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-type",
            "--assembly", "Nope", "--type", "X.Y", "--solution", "MySol",
        ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "not found" in env["error"]

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
                "--update", "--solution", "MySol",
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
                "--update", "--version", "2.0.0.0", "--solution", "MySol",
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
                "--solution", "cwx_sol",
            ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        # plain --update is the content-only happy path: no ignored-flags warning
        # (--solution given so the shared solution resolution stays silent too)
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
                "--solution", "MySol",
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
            "--solution", "MySol",
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
            "--solution", "MySol",
        ])
        assert result.exit_code == 0, result.output
        assert captured["entity"] is None
        assert captured["stage"] == "postoperation"
        assert captured["mode"] == "sync"
        assert captured["rank"] == 1
        assert captured["filtering_attributes"] is None
        assert captured["name"] is None
        assert captured["assembly"] is None
        assert captured["service_endpoint"] is None

    def test_register_step_command_threads_service_endpoint(self, monkeypatch):
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
            "--message", "Create", "--service-endpoint", "Contoso Hook",
            "--solution", "MySol",
        ])
        assert result.exit_code == 0, result.output
        assert captured["service_endpoint"] == "Contoso Hook"
        assert captured["plugin_type"] is None

    def test_register_webhook_command_wires_core(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_webhook(backend, **kw):
            captured.update(kw)
            return {"created": True, "serviceendpointid": _SE_ID,
                    "name": "Contoso Hook",
                    "url": "https://example.com/hook", "contract": 8,
                    "authtype": 4}

        monkeypatch.setattr("crm.core.plugin.register_webhook", fake_webhook)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-webhook",
            "--name", "Contoso Hook", "--url", "https://example.com/hook",
            "--auth", "webhookkey", "--auth-value", "secret-code",
            "--solution", "MySol",
        ])
        assert result.exit_code == 0, result.output
        assert captured["name"] == "Contoso Hook"
        assert captured["url"] == "https://example.com/hook"
        assert captured["auth"] == "webhookkey"
        assert captured["auth_value"] == "secret-code"
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["serviceendpointid"] == _SE_ID

    def test_register_webhook_command_rejects_bad_auth(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-webhook",
            "--name", "H", "--url", "https://e/h",
            "--auth", "bogus", "--auth-value", "v",
        ])
        assert result.exit_code != 0
        assert "bogus" in result.output

    def test_register_image_command_wires_core(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_image(backend, **kw):
            captured.update(kw)
            return {"created": True,
                    "sdkmessageprocessingstepimageid": _IMG_ID,
                    "name": "preimg", "entityalias": "preimg", "imagetype": 0,
                    "messagepropertyname": "Target",
                    "sdkmessageprocessingstepid": _STEP_ID,
                    "attributes": "name,telephone1"}

        monkeypatch.setattr("crm.core.plugin.register_image", fake_image)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-image",
            "--step", _STEP_ID, "--type", "pre", "--alias", "preimg",
            "--attributes", "name,telephone1",
            "--name", "My Image", "--message-property-name", "Target",
            "--solution", "MySol",
        ])
        assert result.exit_code == 0, result.output
        assert captured["step"] == _STEP_ID
        assert captured["image_type"] == "pre"
        assert captured["alias"] == "preimg"
        assert captured["attributes"] == "name,telephone1"
        assert captured["name"] == "My Image"
        assert captured["message_property_name"] == "Target"
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["sdkmessageprocessingstepimageid"] == _IMG_ID

    def test_register_image_command_defaults(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.plugin.register_image",
            lambda backend, **kw: captured.update(kw) or {
                "created": True, "sdkmessageprocessingstepimageid": _IMG_ID})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-image",
            "--step", _STEP_ID, "--type", "post", "--alias", "postimg",
            "--solution", "MySol",
        ])
        assert result.exit_code == 0, result.output
        assert captured["image_type"] == "post"
        assert captured["attributes"] is None
        assert captured["name"] is None
        assert captured["message_property_name"] is None

    def test_register_image_command_rejects_bad_type(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-image",
            "--step", _STEP_ID, "--type", "invalid_type", "--alias", "a",
        ])
        # click.Choice rejects unknown image types with a usage error (exit 2)
        assert result.exit_code == 2

    def test_register_image_command_handles_d365_error(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli

        def boom(backend, **kw):
            raise D365Error("boom", status=400)

        monkeypatch.setattr("crm.core.plugin.register_image", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-image",
            "--step", _STEP_ID, "--type", "pre", "--alias", "preimg",
            "--solution", "MySol",
        ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "boom" in env["error"]

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
            "--solution", "MySol",
        ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "boom" in env["error"]

    def test_register_verbs_expose_shared_solution_options(self):
        # All three register verbs must carry the shared _solution_option:
        # --solution flag plus the header named in its help text.
        from click.testing import CliRunner
        from crm.cli import cli
        runner = CliRunner()
        for verb in ("register-assembly", "register-step", "register-image"):
            result = runner.invoke(cli, ["plugin", verb, "--help"])
            assert result.exit_code == 0, result.output
            assert "--solution" in result.output
            # --require-solution is removed in #623 Pass A
            assert "--require-solution" not in result.output
            # single tokens from the shared help, robust to Click's line wrapping
            assert "MSCRM.SolutionUniqueName" in result.output

    def test_register_step_command_threads_solution(self, monkeypatch):
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
            "--solution", "cwx_sol",
        ])
        assert result.exit_code == 0, result.output
        # explicit --solution is resolved and threaded to the core function
        assert captured["solution"] == "cwx_sol"

    def test_register_image_command_threads_solution(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.plugin.register_image",
            lambda backend, **kw: captured.update(kw) or {
                "created": True, "sdkmessageprocessingstepimageid": _IMG_ID})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-image",
            "--step", _STEP_ID, "--type", "post", "--alias", "postimg",
            "--solution", "cwx_sol",
        ])
        assert result.exit_code == 0, result.output
        assert captured["solution"] == "cwx_sol"

    def test_register_step_no_solution_exits_2(
            self, monkeypatch, tmp_path):
        # No --solution must fail with exit 2 (UsageError) before any core call.
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
        called = {"n": 0}
        monkeypatch.setattr(
            "crm.core.plugin.register_step",
            lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "plugin", "register-step",
            "--message", "Create",
            "--plugin-type", "Contoso.Plugins.PreCreateAccount",
        ])
        assert result.exit_code == 2, result.output
        assert called["n"] == 0


# --------------------------------------------------------------------------- #
# update_step
# --------------------------------------------------------------------------- #

_UPDATE_STEP_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class TestUpdateStep:
    def test_patches_single_field_and_returns_fields_list(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"sdkmessageprocessingsteps({_UPDATE_STEP_ID})"),
                    status_code=204)
            out = plugin.update_step(backend, step_id=_UPDATE_STEP_ID, rank=5)
        assert out["updated"] is True
        assert out["sdkmessageprocessingstepid"] == _UPDATE_STEP_ID
        assert out["fields"] == ["rank"]
        patch = _patches(m)[0]
        assert patch.json() == {"rank": 5}

    def test_patches_multiple_fields_body_correct(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"sdkmessageprocessingsteps({_UPDATE_STEP_ID})"),
                    status_code=204)
            out = plugin.update_step(
                backend, step_id=_UPDATE_STEP_ID,
                stage="preoperation", mode="sync",
                filtering_attributes="name,email", configuration="<cfg/>")
        body = _patches(m)[0].json()
        assert body["stage"] == 20        # _STAGE["preoperation"]
        assert body["mode"] == 0          # _MODE["sync"]
        assert body["filteringattributes"] == "name,email"
        assert body["configuration"] == "<cfg/>"
        assert sorted(out["fields"]) == ["configuration", "filteringattributes", "mode", "stage"]

    def test_unknown_stage_raises_before_patch(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="Unknown stage"):
                plugin.update_step(backend, step_id=_UPDATE_STEP_ID, stage="bogus")
        assert m.request_history == []

    def test_unknown_mode_raises_before_patch(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="Unknown mode"):
                plugin.update_step(backend, step_id=_UPDATE_STEP_ID, mode="bogus")
        assert m.request_history == []

    def test_solution_header_sent(self, backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"sdkmessageprocessingsteps({_UPDATE_STEP_ID})"),
                    status_code=204)
            plugin.update_step(backend, step_id=_UPDATE_STEP_ID, rank=1, solution="cwx_sol")
        assert _patches(m)[0].headers["MSCRM.SolutionUniqueName"] == "cwx_sol"

    def test_dry_run_short_circuits(self, profile):
        from crm.core import plugin
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            out = plugin.update_step(dry, step_id=_UPDATE_STEP_ID, rank=3)
        assert out.get("_dry_run") is True
        assert not _patches(m)
