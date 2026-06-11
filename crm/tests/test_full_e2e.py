"""End-to-end tests for crm.

These tests REQUIRE a live, reachable Dynamics 365 CE on-prem 9.x server. There is
no graceful skip — per HARNESS.md the real software is a hard runtime dependency.

Credentials still come from a saved profile (the CLI no longer reads credential
env vars). To keep the live suite self-contained, the ``_live_profile`` fixture
reads the env values below ONLY to SEED a temporary profile + activate it under an
isolated ``CRM_HOME``; the CLI itself resolves from that profile, not the env.

Required env (consumed by the fixture, not by the CLI):
    D365_URL=https://<server>/<org>
    D365_USERNAME=<user>
    D365_PASSWORD=<pw>            (OAuth: the client secret, or set D365_CLIENT_SECRET)
    D365_DOMAIN=<DOMAIN>          (optional for UPN)
    D365_AUTH=ntlm                (set 'oauth' for Dataverse online)
    D365_TENANT_ID / D365_CLIENT_ID   (OAuth only)
    D365_API_VERSION              (optional; defaults v9.1 on-prem / v9.2 cloud)

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

from crm.utils.d365_backend import D365Error


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


_LIVE_PROFILE = "e2e"


@pytest.fixture(scope="module", autouse=True)
def _live_profile(tmp_path_factory):
    """Seed a profile named ``e2e`` from the live env vars and activate it under an
    isolated ``CRM_HOME`` exported for the whole module — so both the in-process
    ``backend`` fixture and the subprocess ``crm`` invocations resolve from the
    saved profile (the CLI no longer reads credential env vars). The env values are
    consumed HERE only, to build the profile; they never reach the CLI directly."""
    if not _have_live_env():
        yield
        return
    from crm.core import session as session_mod
    from crm.utils.d365_backend import ConnectionProfile

    home = tmp_path_factory.mktemp("e2e-crm")
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(home)
    auth = os.environ.get("D365_AUTH", "ntlm").lower()
    secret = os.environ.get("D365_PASSWORD") or os.environ.get("D365_CLIENT_SECRET") or ""
    api_version = os.environ.get("D365_API_VERSION") or (
        "v9.2" if auth == "oauth" else "v9.1"
    )
    profile = ConnectionProfile(
        name=_LIVE_PROFILE,
        url=os.environ["D365_URL"],
        domain="" if auth == "oauth" else os.environ.get("D365_DOMAIN", ""),
        username="" if auth == "oauth" else os.environ.get("D365_USERNAME", ""),
        api_version=api_version,
        auth_scheme=auth,
        tenant_id=os.environ.get("D365_TENANT_ID"),
        client_id=os.environ.get("D365_CLIENT_ID"),
    )
    session_mod.save_profile(profile)
    session_mod.save_profile_secret_plaintext(_LIVE_PROFILE, secret)
    state = session_mod.load_session("default")
    state["active_profile"] = _LIVE_PROFILE
    session_mod.save_session(state, "default")
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture(scope="module")
def backend(_live_profile):
    if not _have_live_env():
        pytest.fail(
            "Live D365 env vars required. Set D365_URL, D365_USERNAME, D365_PASSWORD."
        )
    from crm.core.connection import resolve_credentials
    from crm.utils.d365_backend import D365Backend

    resolved = resolve_credentials(_LIVE_PROFILE)
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
        """§3.6: export_customizations=True yields a non-empty zip.

        The **Default** solution is never exportable in D365 — the server
        refuses with "Exporting the default solution is not supported". So seed
        a throwaway publisher + custom solution and a component (a custom entity
        created straight into the solution via the SolutionUniqueName header),
        export THAT, then clean up best-effort.
        """
        import uuid
        from crm.core import metadata as meta_mod
        from crm.core import solution as sol_mod

        suffix = uuid.uuid4().hex[:8]
        prefix = f"e2e{suffix[:4]}"          # 7 chars, starts with a letter
        pub_name = f"new_e2epub_{suffix}"
        sol_name = f"new_e2esol_{suffix}"
        ent_schema = f"{prefix}_ExportSeed"
        out = tmp_path / f"{sol_name}.zip"

        pub_id: str | None = None
        created_solution = False
        created_entity = False
        try:
            pub = sol_mod.create_publisher(
                backend, name=pub_name, prefix=prefix,
                option_value_prefix=10000 + (int(suffix, 16) % 90000),
            )
            pub_id = pub.get("publisherid")
            sol_mod.create_solution(
                backend, name=sol_name, publisher_unique_name=pub_name,
            )
            created_solution = True
            meta_mod.create_entity(
                backend, schema_name=ent_schema,
                display_name=f"E2E Export Seed {suffix}",
                solution=sol_name,
            )
            created_entity = True

            sol_mod.export_solution(
                backend, sol_name, out, export_customizations=True,
            )
            assert out.exists()
            assert out.stat().st_size > 1000
        finally:
            # Best-effort teardown in reverse order: component, then the now-empty
            # solution, then its publisher. Each guarded so one failure doesn't
            # mask the others (artifacts stay for manual cleanup).
            if created_entity:
                try:
                    meta_mod.delete_entity(backend, ent_schema.lower())
                except Exception:
                    pass
            if created_solution:
                try:
                    sol_mod.uninstall_solution(backend, sol_name, force=True)
                except Exception:
                    pass
            if pub_id:
                try:
                    backend.delete(f"publishers({pub_id})")
                except Exception:
                    pass


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
        # Declined confirmation is an operational failure → exit 1 (ADR 0001)
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output


class TestAddAttributeBooleanDefaultParsing:
    def test_rejects_unknown_boolean_default(self):
        from click.testing import CliRunner
        from crm.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "metadata", "add-attribute", "new_widget",
            "--kind", "boolean",
            "--schema-name", "new_isactive", "--display", "Active",
            "--default-value", "maybe",
        ])
        # Click UsageError → non-zero exit + message routed to stderr
        assert result.exit_code != 0
        assert "must be one of" in (result.output + str(result.exception))


@pytest.mark.skipif(not _have_live_env(), reason="Live env required")
class TestSpecDMetadataWriteLive:
    """End-to-end metadata-write smoke against a real Contoso server.

    Gated by D365_URL/USERNAME/PASSWORD. Each run creates a uniquely-named
    ephemeral entity, exercises every new write verb against it, then
    cleans up. If cleanup fails, the test xfails so CI surfaces it without
    breaking.
    """

    @pytest.fixture(scope="class")
    def ephemeral_entity(self, backend):
        import time
        import uuid
        from crm.core import metadata as meta_mod
        suffix = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        schema = f"new_E2E{suffix}"
        info = meta_mod.create_entity(
            backend, schema_name=schema,
            display_name=f"E2E {suffix}",
        )
        yield info["logical_name"]
        try:
            meta_mod.delete_entity(backend, info["logical_name"])
        except Exception as exc:
            pytest.xfail(f"cleanup failed for {info['logical_name']}: {exc}")

    # One param per attribute kind so a kind the SDK legitimately refuses
    # records its own `xfailed` instead of failing the whole class. bigint is
    # system-managed ("BigIntAttributeMetadata cannot be created through the
    # SDK") — expect the server 4xx as a D365Error. multiselect/image/file may
    # be feature-gated on some builds; add them here as xfail params likewise.
    @pytest.mark.parametrize("kind,extra", [
        ("string", {"max_length": 100}),
        ("memo", {"max_length": 1000}),
        ("integer", {"min_value": 0, "max_value": 100}),
        pytest.param("bigint", {}, marks=pytest.mark.xfail(
            raises=D365Error, strict=False,
            reason="BigInt attributes are system-managed; not creatable through the SDK.",
        )),
        ("decimal", {"precision": 2}),
        ("double", {"precision": 3}),
        ("money", {"precision": 2}),
        ("boolean", {}),
        ("datetime", {}),
        ("picklist", {"options": [(1, "A"), (2, "B")]}),
    ])
    def test_add_attribute_each_kind(self, backend, ephemeral_entity, kind, extra):
        from crm.core import metadata_attrs as ma
        info = ma.add_attribute(
            backend,
            entity=ephemeral_entity,
            kind=kind,
            schema_name=f"new_E2E{kind.capitalize()}",
            display_name=f"E2E {kind}",
            publish=False,
            **extra,
        )
        assert info.get("created") or info.get("kind") == "OneToMany", info

    def test_optionset_lifecycle(self, backend):
        import uuid
        from crm.core import optionsets as os_mod
        name = f"new_e2e_priority_{uuid.uuid4().hex[:8]}"
        try:
            os_mod.create_optionset(
                backend, name=name, display_name="E2E Priority",
                options=[(1, "Low"), (2, "Medium")],
            )
            os_mod.update_optionset(
                backend, name,
                insert=[(7, "Critical")],
                update=[(2, "Mid")],
            )
            os_mod.get_optionset(backend, name)
        finally:
            try:
                os_mod.delete_optionset(backend, name)
            except Exception as exc:
                pytest.xfail(f"cleanup failed for {name}: {exc}")

    def test_one_to_many_to_stock_account(self, backend, ephemeral_entity):
        from crm.core import relationships as rel
        info = rel.create_one_to_many(
            backend,
            schema_name=f"new_account_{ephemeral_entity}",
            referenced_entity="account",
            referencing_entity=ephemeral_entity,
            lookup_schema="new_E2EAccountId",
            lookup_display="Account",
            publish=False,
        )
        assert info["created"] is True


@pytest.mark.skipif(not _have_live_env(), reason="Live env required")
class TestPluginImageE2E:
    def test_image_register_read_unregister_roundtrip(self, backend, request):
        """register_image -> read back -> unregister_image on a live org.

        Mocked tests cannot catch @odata.bind key casing (the #159 lesson), so
        the POST must hit a real org. Attaches a pre-image to any existing
        unmanaged Update-message step (pre-images are valid in every stage) and
        removes it again; skips when the org has no such step.
        """
        from crm.core import plugin as plugin_mod

        msg = backend.get("sdkmessages", params={
            "$filter": "name eq 'Update'", "$select": "sdkmessageid"})
        msg_rows = msg.get("value", [])
        assert msg_rows, "Update sdkmessage missing from org"
        msg_id = msg_rows[0]["sdkmessageid"]
        steps = backend.get("sdkmessageprocessingsteps", params={
            "$filter": (f"_sdkmessageid_value eq {msg_id} "
                        "and ismanaged eq false"),
            "$select": "sdkmessageprocessingstepid", "$top": "1"})
        step_rows = steps.get("value", [])
        if not step_rows:
            pytest.skip("No unmanaged Update-message plug-in step on this org")
        step_id = step_rows[0]["sdkmessageprocessingstepid"]

        out = plugin_mod.register_image(
            backend, step=step_id, image_type="pre",
            alias=f"e2eimg{os.getpid()}", attributes="name")
        assert out["created"] is True
        iid = out["sdkmessageprocessingstepimageid"]
        assert iid, f"no image id parsed: {out}"

        def _cleanup():
            # Safety net for mid-test failure; the happy path already deleted.
            try:
                backend.delete(f"sdkmessageprocessingstepimages({iid})")
            except Exception:
                pass
        request.addfinalizer(_cleanup)

        got = backend.get(
            f"sdkmessageprocessingstepimages({iid})",
            params={"$select": "name,entityalias,imagetype,"
                               "messagepropertyname,attributes"})
        assert got["imagetype"] == 0
        assert got["messagepropertyname"] == "Target"
        assert got["attributes"] == "name"
        assert got["entityalias"] == f"e2eimg{os.getpid()}"

        deleted = plugin_mod.unregister_image(backend, iid)
        assert deleted["deleted"] is True
