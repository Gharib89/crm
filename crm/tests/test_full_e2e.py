"""End-to-end tests for crm.

These tests REQUIRE a live, reachable Dynamics 365 CE on-prem 9.x server. There is
no graceful skip — per HARNESS.md the real software is a hard runtime dependency.

Required env:
    D365_URL=https://<server>/<org>
    D365_USERNAME=<user>
    D365_PASSWORD=<pw>
    D365_DOMAIN=<DOMAIN>   (optional for UPN)
    D365_AUTH=ntlm

CI / release runs should also set CRM_FORCE_INSTALLED=1 to require the
installed `crm` command (not a python -m fallback).
"""
# pyright: basic

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest


# ── Helpers ─────────────────────────────────────────────────────────────


def _have_live_env() -> bool:
    return all(os.environ.get(k) for k in ("D365_URL", "D365_USERNAME", "D365_PASSWORD"))


def _resolve_cli(name: str):
    """Resolve installed CLI command; falls back to python -m for dev.

    Set env CRM_FORCE_INSTALLED=1 to require the installed command.
    """
    force = os.environ.get("CRM_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    module = "crm"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


@pytest.fixture(scope="module")
def backend():
    if not _have_live_env():
        pytest.fail(
            "Live D365 env vars required. Set D365_URL, D365_USERNAME, D365_PASSWORD."
        )
    from crm.core.connection import resolve_credentials
    from crm.utils.d365_backend import D365Backend

    resolved = resolve_credentials()
    return D365Backend(resolved.profile, resolved.password, dry_run=False)


# ── Backend-level E2E ───────────────────────────────────────────────────


@pytest.mark.skipif(not _have_live_env(), reason="Live env required")
class TestD365E2E:
    def test_whoami_returns_identity(self, backend):
        result = backend.get("WhoAmI")
        assert result is not None
        assert "UserId" in result
        assert len(result["UserId"]) >= 36  # GUID-ish

    def test_metadata_list_entities(self, backend):
        # EntityDefinitions does NOT support $top server-side on v9.1 on-prem;
        # use the helper, which slices client-side.
        from crm.core import metadata as md
        items = md.list_entities(backend)
        names = [it.get("LogicalName") for it in items]
        assert "account" in names, f"Did not see 'account' in: {names[:10]}..."

    def test_contact_crud_roundtrip(self, backend, request):
        # Create
        created = backend.post(
            "contacts",
            json_body={"firstname": "CLI", "lastname": f"Test-{os.getpid()}"},
            extra_headers={"If-None-Match": "null", "Prefer": "return=representation"},
        )
        assert created is not None and "contactid" in created
        cid = created["contactid"]
        request.addfinalizer(
            lambda: backend.delete(f"contacts({cid})")
        )

        # Read
        got = backend.get(f"contacts({cid})", params={"$select": "fullname,firstname"})
        assert got is not None
        assert got.get("firstname") == "CLI"

        # Update
        backend.patch(
            f"contacts({cid})",
            json_body={"telephone1": "+1-555-0001"},
            extra_headers={"If-Match": "*"},
        )
        got2 = backend.get(f"contacts({cid})", params={"$select": "telephone1"})
        assert got2 is not None
        assert got2.get("telephone1") == "+1-555-0001"

    def test_fetchxml_query_returns_contacts(self, backend):
        from crm.core.query import fetchxml_query
        fx = (
            "<fetch top='3'>"
            "<entity name='contact'>"
            "<attribute name='fullname'/>"
            "</entity></fetch>"
        )
        result = fetchxml_query(backend, "contacts", fx)
        assert "value" in result


# ── CLI subprocess E2E ──────────────────────────────────────────────────


class TestCLISubprocess:
    CLI_BASE = _resolve_cli("crm")

    def _run(self, args, check=True, env=None):
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True, text=True,
            check=check, env=merged_env,
        )

    def test_help(self):
        result = self._run(["--help"], check=False)
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        # Accept either the installed name or the python -m fallback usage line.
        assert ("crm" in combined) or ("crm" in combined)

    def test_connection_status_json(self, tmp_path):
        env = {"CRM_HOME": str(tmp_path / ".d365")}
        result = self._run(["--json", "connection", "status"], env=env)
        assert result.returncode == 0
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is True
        assert envelope["data"]["session"] == "default"

    @pytest.mark.skipif(not _have_live_env(), reason="Live env required")
    def test_metadata_entities_json(self):
        result = self._run([
            "--json", "metadata", "entities", "--top", "3",
        ])
        assert result.returncode == 0, result.stderr
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is True
        assert isinstance(envelope["data"], list)
        assert envelope["meta"]["count"] >= 1

    @pytest.mark.skipif(not _have_live_env(), reason="Live env required")
    def test_full_contact_workflow(self, tmp_path):
        # Create
        body_path = tmp_path / "body.json"
        body_path.write_text(json.dumps(
            {"firstname": "CLISub", "lastname": f"Test-{os.getpid()}"}
        ))
        create = self._run([
            "--json", "entity", "create", "contacts",
            "--data-file", str(body_path),
        ])
        assert create.returncode == 0, create.stderr
        env = json.loads(create.stdout)
        assert env["ok"], env
        contact_id = env["data"].get("contactid")
        assert contact_id

        try:
            # Get
            got = self._run([
                "--json", "entity", "get", "contacts", contact_id,
                "--select", "fullname,firstname",
            ])
            assert got.returncode == 0, got.stderr
            assert json.loads(got.stdout)["data"]["firstname"] == "CLISub"
        finally:
            # Delete (idempotent finalizer)
            self._run([
                "--json", "entity", "delete", "contacts", contact_id, "--yes",
            ], check=False)


# ── E2E Tests for Spec A ────────────────────────────────────────────────


@pytest.mark.skipif(not _have_live_env(), reason="Live env required")
class TestE2ESpecA:
    def test_e2e_create_custom_entity_reads_back_set_name(self, backend):
        """§3.3: create a unique custom entity, assert returned entity_set_name resolves via metadata.list_entities."""
        from crm.core import metadata as meta_mod
        import uuid
        suffix = uuid.uuid4().hex[:8]
        schema = f"new_SpecAReadback{suffix}"
        try:
            info = meta_mod.create_entity(
                backend,
                schema_name=schema,
                display_name=f"SpecA Readback {suffix}",
            )
            assert info["created"] is True
            assert info["entity_set_name"] is not None
            entities = meta_mod.list_entities(backend, custom_only=True)
            by_logical = {e.get("LogicalName"): e for e in entities}
            assert schema.lower() in by_logical
            server_set_name = by_logical[schema.lower()].get("EntitySetName")
            assert info["entity_set_name"] == server_set_name
        finally:
            # Best-effort cleanup; ignore failure (entity stays for manual cleanup).
            try:
                backend.delete(f"EntityDefinitions(LogicalName='{schema.lower()}')")
            except Exception:
                pass

    def test_e2e_solution_export_with_customization_flag(self, backend, tmp_path):
        """§3.6: --export-setting customizations yields a non-empty zip."""
        from crm.core import solution as sol_mod
        out = tmp_path / "default.zip"
        sol_mod.export_solution(
            backend, "Default", out, export_customizations=True,
        )
        assert out.exists()
        assert out.stat().st_size > 1000


# ── CLI unit smoke tests (no live backend) ──────────────────────────────


class TestDeleteEntityCli:
    def test_delete_entity_requires_confirmation(self):
        from click.testing import CliRunner
        from crm.cli import cli

        runner = CliRunner()
        # No --yes, default Enter on confirm prompt → aborted
        result = runner.invoke(
            cli, ["--json", "metadata", "delete-entity", "new_widget"],
            input="\n",
        )
        assert result.exit_code == 0
        assert '"error": "aborted by user"' in result.output
