"""Unit tests for crm.core.optionsets."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import D365Backend, D365Error


_OS_ID = "44444444-4444-4444-4444-444444444444"


class TestListOptionsets:
    def test_list_all(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions"),
                json={"value": [
                    {"Name": "new_priority", "IsCustomOptionSet": True, "IsGlobal": True},
                    {"Name": "statecode", "IsCustomOptionSet": False, "IsGlobal": True},
                ]},
            )
            rows = os_mod.list_optionsets(backend)
        assert len(rows) == 2

    def test_list_custom_only_filters(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions"),
                json={"value": [
                    {"Name": "new_priority", "IsCustomOptionSet": True},
                    {"Name": "statecode", "IsCustomOptionSet": False},
                ]},
            )
            rows = os_mod.list_optionsets(backend, custom_only=True)
        assert len(rows) == 1
        assert rows[0]["Name"] == "new_priority"

    def test_list_top_slice(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions"),
                json={"value": [
                    {"Name": f"opt_{i}"} for i in range(5)
                ]},
            )
            rows = os_mod.list_optionsets(backend, top=2)
        assert len(rows) == 2


class TestGetOptionset:
    def test_get_returns_options(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={"Name": "new_priority", "Options": [
                    {"Value": 1, "Label": {"LocalizedLabels": [{"Label": "Low"}]}}
                ]},
            )
            info = os_mod.get_optionset(backend, "new_priority")
        assert info["Name"] == "new_priority"
        assert info["Options"][0]["Value"] == 1

    def test_get_sends_plain_request_without_expand(self, backend):
        # $expand=Options is rejected with HTTP 400 on every target: the
        # entity set is typed as OptionSetMetadataBase (no Options property)
        # and Options is a complex-type collection, not a navigation
        # property. A plain GET serializes the full derived type, Options
        # included (issue #179).
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={"Name": "new_priority", "Options": []},
            )
            os_mod.get_optionset(backend, "new_priority")
        assert m.last_request.qs == {}


class TestCreateOptionset:
    def test_create_with_options(self, backend):
        from crm.core import optionsets as os_mod
        url = backend.url_for(f"GlobalOptionSetDefinitions({_OS_ID})")
        with requests_mock.Mocker() as m:
            # --if-exists probe: option set not present (404), so create proceeds.
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.post(
                backend.url_for("GlobalOptionSetDefinitions"),
                status_code=204,
                headers={"OData-EntityId": url},
            )
            m.get(
                url,
                json={"Name": "new_priority", "IsCustomOptionSet": True},
            )
            info = os_mod.create_optionset(
                backend,
                name="new_priority",
                display_name="Priority",
                options=[(1, "Low"), (2, "Medium"), (3, "High")],
                solution="DevSolution",
            )
        assert info["created"] is True
        assert info["name"] == "new_priority"
        post_req = next(r for r in m.request_history if r.method == "POST")
        body = post_req.json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.OptionSetMetadata"
        assert body["Name"] == "new_priority"
        assert body["IsGlobal"] is True
        assert body["Options"][0]["Value"] == 1
        assert post_req.headers["MSCRM.SolutionUniqueName"] == "DevSolution"

    def test_create_rejects_duplicate_values(self, backend):
        from crm.core import optionsets as os_mod
        with pytest.raises(D365Error, match="Duplicate"):
            os_mod.create_optionset(
                backend, name="new_dupe", display_name="Dupe",
                options=[(1, "A"), (1, "B")],
            )


class TestUpdateOptionset:
    def test_insert_only(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("InsertOptionValue"),
                   status_code=204, json={})
            info = os_mod.update_optionset(
                backend, "new_priority",
                insert=[(7, "Critical")],
            )
        assert info["completed_steps"] == ["insert:7"]
        body = m.request_history[0].json()
        assert body["OptionSetName"] == "new_priority"
        assert body["Value"] == 7

    def test_full_dispatch_order(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("InsertOptionValue"), status_code=204, json={})
            m.post(backend.url_for("UpdateOptionValue"), status_code=204, json={})
            m.post(backend.url_for("DeleteOptionValue"), status_code=204, json={})
            m.post(backend.url_for("OrderOption"), status_code=204, json={})
            info = os_mod.update_optionset(
                backend, "new_priority",
                insert=[(None, "Auto")],
                update=[(2, "New Medium")],
                delete=[3],
                reorder=[1, 2, 7],
            )
        # Verify order: InsertOptionValue, UpdateOptionValue, DeleteOptionValue, OrderOption
        history_paths = [r.url.split("/")[-1] for r in m.request_history]
        assert "InsertOptionValue" in history_paths[0]
        assert "UpdateOptionValue" in history_paths[1]
        assert "DeleteOptionValue" in history_paths[2]
        assert "OrderOption" in history_paths[3]
        assert info["completed_steps"]

    def test_partial_failure_returns_envelope(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("InsertOptionValue"), status_code=204, json={})
            m.post(backend.url_for("UpdateOptionValue"),
                   status_code=400,
                   json={"error": {"message": "value 99 not found"}})
            with pytest.raises(D365Error, match="value 99 not found") as exc_info:
                os_mod.update_optionset(
                    backend, "new_priority",
                    insert=[(7, "OK")],
                    update=[(99, "Bad")],
                )
            assert "value 99 not found" in str(exc_info.value)
            assert exc_info.value.completed_steps == ["insert:7"]
            assert exc_info.value.stage == "update"

    def test_empty_request_rejected(self, backend):
        from crm.core import optionsets as os_mod
        with pytest.raises(D365Error, match="nothing to update"):
            os_mod.update_optionset(backend, "new_priority")

    # --- dry-run diff tests ---

    def test_dryrun_fires_get_for_diff(self, profile):
        """GET must fire under dry-run; no POST must be issued."""
        from crm.core import optionsets as os_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={
                    "Name": "new_priority",
                    "Options": [
                        {"Value": 1, "Label": {"LocalizedLabels": [{"Label": "Low"}]}},
                        {"Value": 2, "Label": {"LocalizedLabels": [{"Label": "Medium"}]}},
                    ],
                },
            )
            result = os_mod.update_optionset(
                dry_backend, "new_priority",
                insert=[(7, "Critical")],
            )
        # GET for diff must have fired
        get_reqs = [r for r in m.request_history if r.method == "GET"]
        assert len(get_reqs) == 1
        assert "GlobalOptionSetDefinitions" in get_reqs[0].url
        # No POSTs at all
        post_reqs = [r for r in m.request_history if r.method == "POST"]
        assert post_reqs == []
        # Returns dry-run envelope, not false updated:True
        assert result.get("_dry_run") is True
        assert not result.get("updated")

    def test_dryrun_diff_classification(self, profile):
        """Diff must classify insert / update / delete / reorder correctly."""
        from crm.core import optionsets as os_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={
                    "Name": "new_priority",
                    "Options": [
                        {"Value": 1, "Label": {"LocalizedLabels": [{"Label": "Low"}]}},
                        {"Value": 2, "Label": {"LocalizedLabels": [{"Label": "Medium"}]}},
                        {"Value": 3, "Label": {"LocalizedLabels": [{"Label": "High"}]}},
                    ],
                },
            )
            result = os_mod.update_optionset(
                dry_backend, "new_priority",
                insert=[(7, "Critical")],
                update=[(2, "Mid")],
                delete=[3],
                reorder=[1, 2, 7],
            )
        diff = result["diff"]
        # inserts
        assert diff["inserts"] == [{"value": 7, "label": "Critical"}]
        # updates with old_label
        assert diff["updates"] == [{"value": 2, "old_label": "Medium", "new_label": "Mid"}]
        # deletes with old_label
        assert diff["deletes"] == [{"value": 3, "old_label": "High"}]
        # reorder
        assert diff["reorder"]["old"] == [1, 2, 3]
        assert diff["reorder"]["new"] == [1, 2, 7]

    def test_dryrun_return_shape(self, profile):
        """Dry-run returns _dry_run:True, name, diff, actions; no updated key."""
        from crm.core import optionsets as os_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={"Name": "new_priority", "Options": []},
            )
            result = os_mod.update_optionset(
                dry_backend, "new_priority",
                insert=[(None, "Auto")],
            )
        assert result["_dry_run"] is True
        assert result["name"] == "new_priority"
        assert "diff" in result
        assert "actions" in result
        assert "updated" not in result

    def test_dryrun_actions_in_dispatch_order(self, profile):
        """actions list captures bodies in insert→update→delete→reorder order."""
        from crm.core import optionsets as os_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={
                    "Name": "new_priority",
                    "Options": [
                        {"Value": 2, "Label": {"LocalizedLabels": [{"Label": "Medium"}]}},
                    ],
                },
            )
            result = os_mod.update_optionset(
                dry_backend, "new_priority",
                insert=[(7, "Critical")],
                update=[(2, "Mid")],
                delete=[2],
                reorder=[7, 2],
            )
        actions = result["actions"]
        assert len(actions) == 4  # insert, update, delete, reorder
        # First action is insert
        assert actions[0]["OptionSetName"] == "new_priority"
        assert actions[0]["Value"] == 7
        # Last action is reorder
        assert actions[3]["Values"] == [7, 2]

    def test_dryrun_old_label_none_for_missing_value(self, profile):
        """old_label is None when the value doesn't exist in current options."""
        from crm.core import optionsets as os_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={"Name": "new_priority", "Options": []},
            )
            result = os_mod.update_optionset(
                dry_backend, "new_priority",
                update=[(99, "Ghost")],
            )
        assert result["diff"]["updates"] == [
            {"value": 99, "old_label": None, "new_label": "Ghost"}
        ]

    def test_dryrun_no_reorder_key_when_absent(self, profile):
        """reorder key is absent from diff when no reorder is requested."""
        from crm.core import optionsets as os_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={"Name": "new_priority", "Options": []},
            )
            result = os_mod.update_optionset(
                dry_backend, "new_priority",
                insert=[(1, "One")],
            )
        assert "reorder" not in result["diff"]

    def test_dryrun_empty_insert_label_still_raises(self, profile):
        """Validation must still fire under dry-run; empty label raises D365Error."""
        from crm.core import optionsets as os_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        with pytest.raises(D365Error, match="insert label must not be empty"):
            os_mod.update_optionset(
                dry_backend, "new_priority",
                insert=[(1, "")],
            )

    def test_dryrun_empty_update_label_still_raises(self, profile):
        """Empty update label raises before the dry-run branch, no GET/POST fired."""
        from crm.core import optionsets as os_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="update label must not be empty"):
                os_mod.update_optionset(
                    dry_backend, "new_priority",
                    update=[(1, "")],
                )
            assert m.request_history == []


_OS_META_ID = "cccc0001-0000-0000-0000-000000000000"


class TestDeleteOptionset:
    def test_refuses_non_custom(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='statecode')"),
                json={"Name": "statecode", "IsCustomOptionSet": False, "IsManaged": True},
            )
            with pytest.raises(D365Error, match="not a custom"):
                os_mod.delete_optionset(backend, "statecode")

    def test_refuses_managed(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='vendor_set')"),
                json={"Name": "vendor_set", "IsCustomOptionSet": True, "IsManaged": True},
            )
            with pytest.raises(D365Error, match="managed"):
                os_mod.delete_optionset(backend, "vendor_set")

    def test_happy_path(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={"Name": "new_priority", "IsCustomOptionSet": True, "IsManaged": False},
            )
            m.delete(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                status_code=204,
            )
            info = os_mod.delete_optionset(backend, "new_priority")
        assert info["deleted"] is True
        assert info["name"] == "new_priority"

    def test_check_dependencies_off_by_default_no_extra_get(self, backend):
        """Without check_dependencies, no dependency GETs fire."""
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={"Name": "new_priority", "IsCustomOptionSet": True, "IsManaged": False},
            )
            m.delete(backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"), status_code=204)
            info = os_mod.delete_optionset(backend, "new_priority")
        assert "can_delete" not in info
        assert "blockers" not in info
        dep_reqs = [r for r in m.request_history if "RetrieveDependencies" in r.url]
        assert dep_reqs == []

    def test_check_dependencies_with_blockers(self, backend):
        """check_dependencies=True fires resolve GET + function GET; blockers in result."""
        from crm.core import optionsets as os_mod
        dep_url = backend.url_for(
            f"RetrieveDependenciesForDelete(ObjectId={_OS_META_ID},ComponentType=9)"
        )
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={
                    "Name": "new_priority", "IsCustomOptionSet": True, "IsManaged": False,
                    "MetadataId": _OS_META_ID,
                },
            )
            m.get(dep_url, json={"value": [
                {
                    "dependentcomponenttype": 2,
                    "dependentcomponentobjectid": "dddd0001-0000-0000-0000-000000000000",
                    "dependentcomponentparentid": "eeee0001-0000-0000-0000-000000000000",
                    "requiredcomponenttype": 9,
                    "dependencytype": 1,
                },
            ]})
            m.delete(backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"), status_code=204)
            info = os_mod.delete_optionset(backend, "new_priority", check_dependencies=True)
        assert info["deleted"] is True
        assert info["can_delete"] is False
        assert len(info["blockers"]) == 1
        assert info["blockers"][0]["dependent_type"] == "Attribute"
        dep_reqs = [r for r in m.request_history if "RetrieveDependencies" in r.url]
        assert len(dep_reqs) == 1

    def test_check_dependencies_no_blockers(self, backend):
        """Empty dependency list → can_delete True."""
        from crm.core import optionsets as os_mod
        dep_url = backend.url_for(
            f"RetrieveDependenciesForDelete(ObjectId={_OS_META_ID},ComponentType=9)"
        )
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={
                    "Name": "new_priority", "IsCustomOptionSet": True, "IsManaged": False,
                    "MetadataId": _OS_META_ID,
                },
            )
            m.get(dep_url, json={"value": []})
            m.delete(backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"), status_code=204)
            info = os_mod.delete_optionset(backend, "new_priority", check_dependencies=True)
        assert info["can_delete"] is True
        assert info["blockers"] == []


class TestDeleteOptionsetDryRun:
    """Dry-run delete_optionset returns _dry_run preview, not {deleted: True}."""

    def test_dryrun_returns_preview_not_deleted(self, profile):
        from crm.core import optionsets as os_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={
                    "Name": "new_priority",
                    "IsCustomOptionSet": True,
                    "IsManaged": False,
                    "MetadataId": _OS_META_ID,
                },
            )
            info = os_mod.delete_optionset(dry_backend, "new_priority")
        assert info.get("_dry_run") is True
        assert info.get("would_delete") is True
        assert "deleted" not in info
        assert info["name"] == "new_priority"
        delete_reqs = [r for r in m.request_history if r.method == "DELETE"]
        assert delete_reqs == []

    def test_dryrun_with_check_dependencies_merges_blockers(self, profile):
        from crm.core import optionsets as os_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        dep_url = dry_backend.url_for(
            f"RetrieveDependenciesForDelete(ObjectId={_OS_META_ID},ComponentType=9)"
        )
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={
                    "Name": "new_priority", "IsCustomOptionSet": True, "IsManaged": False,
                    "MetadataId": _OS_META_ID,
                },
            )
            m.get(dep_url, json={"value": []})
            info = os_mod.delete_optionset(dry_backend, "new_priority", check_dependencies=True)
        assert info.get("_dry_run") is True
        assert info.get("would_delete") is True
        assert "deleted" not in info
        assert info["can_delete"] is True
        assert info["blockers"] == []
        delete_reqs = [r for r in m.request_history if r.method == "DELETE"]
        assert delete_reqs == []
