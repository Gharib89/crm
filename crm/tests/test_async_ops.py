"""Unit tests for Spec C asyncoperations browse helpers."""
# pyright: basic

from __future__ import annotations

from typing import Any

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend
from crm.core import async_ops


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=False)


class TestList:
    def test_list_no_filter(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}asyncoperations",
                  json={"value": [{"asyncoperationid": "a", "messagename": "ImportSolution"}]})
            rows = async_ops.list_async_operations(backend)
            assert len(rows) == 1
            qs = m.last_request.qs
            assert qs.get("$top") == ["50"]
            assert qs.get("$orderby") == ["createdon desc"]
            assert "$filter" not in qs

    def test_list_with_state_filter(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}asyncoperations",
                  json={"value": []})
            async_ops.list_async_operations(backend, state=3)
            qs = m.last_request.qs
            assert qs.get("$filter") == ["statecode eq 3"]

    def test_list_with_combined_filters(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}asyncoperations",
                  json={"value": []})
            async_ops.list_async_operations(
                backend,
                state=0,
                message_name="ImportSolution",
                owner_id="11111111-2222-3333-4444-555555555555",
            )
            qs = m.last_request.qs
            f = qs.get("$filter", [""])[0]
            fl = f.lower()
            assert "statecode eq 0" in fl
            assert "messagename eq 'importsolution'" in fl
            assert "_ownerid_value eq 11111111-2222-3333-4444-555555555555" in fl
            assert " and " in fl


class TestGet:
    def test_get_returns_row(self, backend, profile):
        gid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}asyncoperations({gid})",
                  json={"asyncoperationid": gid, "statecode": 3, "statuscode": 30})
            row = async_ops.get_async_operation(backend, gid)
            assert row["asyncoperationid"] == gid  # pyright: ignore[reportTypedDictNotRequiredAccess]
            assert row["statecode"] == 3  # pyright: ignore[reportTypedDictNotRequiredAccess]


class TestCancel:
    def test_cancel_issues_patch(self, backend, profile):
        gid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            m.patch(f"{profile.api_base}asyncoperations({gid})", status_code=204)
            async_ops.cancel_async_operation(backend, gid)
            req = m.last_request
            assert req.method == "PATCH"
            body = req.json()
            assert body == {"statecode": 3, "statuscode": 32}


class TestOwnerValidation:
    def test_list_rejects_invalid_owner_id(self, backend):
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error, match="owner_id"):
            async_ops.list_async_operations(backend, owner_id="not-a-guid")

    def test_list_all_rejects_invalid_owner_id(self, backend):
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error, match="owner_id"):
            async_ops.list_all_async_operations(backend, owner_id="not-a-guid")


class TestListAll:
    def test_follows_next_link_until_exhausted(self, backend, profile):
        next_url = f"{profile.api_base}asyncoperations?$skiptoken=cookie"
        with requests_mock.Mocker() as m:
            m.get(
                f"{profile.api_base}asyncoperations",
                json={"value": [{"asyncoperationid": "1"}], "@odata.nextLink": next_url},
            )
            m.get(
                next_url,
                json={"value": [{"asyncoperationid": "2"}]},
            )
            rows = async_ops.list_all_async_operations(backend, page_size=1, max_pages=10)
            assert [r["asyncoperationid"] for r in rows] == ["1", "2"]  # pyright: ignore[reportTypedDictNotRequiredAccess]

    def test_max_pages_caps_pagination(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(
                f"{profile.api_base}asyncoperations",
                json={
                    "value": [{"asyncoperationid": "1"}],
                    "@odata.nextLink": f"{profile.api_base}asyncoperations?$skiptoken=a",
                },
            )
            m.get(
                f"{profile.api_base}asyncoperations?$skiptoken=a",
                json={
                    "value": [{"asyncoperationid": "2"}],
                    "@odata.nextLink": f"{profile.api_base}asyncoperations?$skiptoken=b",
                },
            )
            rows = async_ops.list_all_async_operations(backend, page_size=1, max_pages=2)
            assert [r["asyncoperationid"] for r in rows] == ["1", "2"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
            # 3rd page (skiptoken=b) is not fetched.
            assert m.call_count == 2


class TestAsyncCLI:
    def test_async_list_help(self):
        from click.testing import CliRunner
        from crm import cli as crm_cli
        runner = CliRunner()
        result = runner.invoke(crm_cli.cli, ["async", "list", "--help"])
        assert result.exit_code == 0
        assert "--state" in result.output
        assert "--message" in result.output

    def test_async_list_state_resolves_named_value(self, monkeypatch, profile):
        from click.testing import CliRunner
        from crm import cli as crm_cli

        captured: dict[str, Any] = {}

        def fake_list(backend, **kw):
            captured.update(kw)
            return []

        monkeypatch.setattr("crm.core.async_ops.list_async_operations", fake_list)
        monkeypatch.setattr("crm.cli.CLIContext.backend",
                            lambda self: object())  # dummy backend; fake_list ignores it
        runner = CliRunner()
        result = runner.invoke(crm_cli.cli, ["async", "list", "--state", "ready"])
        assert result.exit_code == 0, result.output
        assert captured["state"] == 0

    def test_solution_job_status_alias(self, monkeypatch):
        from click.testing import CliRunner
        from crm import cli as crm_cli

        called: dict[str, Any] = {}

        def fake_get(backend, async_id):
            called["async_id"] = async_id
            return {"asyncoperationid": async_id, "statecode": 3, "statuscode": 30}

        monkeypatch.setattr("crm.core.async_ops.get_async_operation", fake_get)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        runner = CliRunner()
        result = runner.invoke(crm_cli.cli, [
            "solution", "job-status", "11111111-1111-1111-1111-111111111111",
        ])
        assert result.exit_code == 0, result.output
        assert called["async_id"] == "11111111-1111-1111-1111-111111111111"
