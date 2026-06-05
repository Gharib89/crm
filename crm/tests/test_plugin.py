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
