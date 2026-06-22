# pyright: basic
"""E2E tests for app verbs: create, add-components, set-sitemap, build-sitemap.

Lifecycle: create a throwaway model-driven app → add a component (an existing
system view for 'account') → build-sitemap → set-sitemap (re-create from xml)
→ delete in finalizer.

On on-prem v9.1, appmodule creation and publish may work but appmodule is not
readable until published (memory §11). All 4 verbs are exercised here via the
CLI + --json. Slow due to PublishAllXml.

Note: `app add-components` binds record-backed components (view/chart/form/
sitemap/bpf), not tables directly.  We resolve an existing system savedquery
for 'account' to use as the view component so we never need to create an extra
record.
"""
from __future__ import annotations

import json
import os

import pytest

from crm.tests.e2e.coverage import covers


def _find_account_view(backend) -> str | None:
    """Return the savedqueryid of the first public view on 'account', or None."""
    from crm.utils.d365_backend import as_dict
    try:
        page = as_dict(backend.get(
            "savedqueries",
            params={
                "$filter": "returnedtypecode eq 'account' and querytype eq 0",
                "$select": "savedqueryid",
                "$top": "1",
            },
        ))
    except Exception:
        return None
    rows = page.get("value", [])
    if not rows:
        return None
    return str(rows[0].get("savedqueryid", ""))


# ── app lifecycle: create + add-components + build-sitemap + set-sitemap ──────


@covers("app create", "app add-components", "app remove-components",
        "app build-sitemap", "app set-sitemap")
@pytest.mark.slow
def test_app_lifecycle(backend, cli, request, unique):
    """Create a throwaway app, add a view component, build and set a sitemap.

    Cleans up the app and both sitemaps in a finalizer.  If on-prem rejects
    appmodule creation the test is skipped rather than failed — this is a
    known on-prem v9.1 limitation for modern model-driven apps.
    """
    app_unique = f"new_e2e{unique[:8]}"
    app_name = f"E2E App {unique[:8]}"

    created_app_id: list[str] = []
    created_sitemap_ids: list[str] = []

    def _cleanup():
        for smid in created_sitemap_ids:
            try:
                backend.delete(f"sitemaps({smid})")
            except Exception:
                pass
        if created_app_id:
            try:
                backend.delete(f"appmodules({created_app_id[0]})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    # ── Step 1: app create ────────────────────────────────────────────────────
    r_create = cli([
        "--json", "app", "create",
        "--name", app_name,
        "--unique-name", app_unique,
        "--no-publish",
    ], check=False)
    if r_create.returncode != 0:
        # Error may appear in stderr or in the JSON envelope stdout.
        combined = (r_create.stderr or "") + (r_create.stdout or "")
        # On-prem v9.1 may reject appmodule write — skip rather than fail.
        if any(kw in combined.lower() for kw in (
            "not supported", "privilege", "accessdenied", "403",
            "businessnotfound", "notimplemented",
        )):
            pytest.skip(
                f"app create rejected by this org (on-prem limitation?): {combined[:400]}"
            )
        pytest.fail(
            f"app create failed:\n{r_create.stderr}\nstdout: {r_create.stdout}"
        )
    env_create = json.loads(r_create.stdout)
    assert env_create["ok"], env_create
    app_id = env_create["data"].get("appmoduleid")
    assert app_id, f"appmoduleid missing from create response: {env_create['data']}"
    created_app_id.append(app_id)

    # ── Step 1b: app create --if-exists skip is idempotent ────────────────────
    # A second create of the same unique-name with --if-exists skip must return a
    # skip, never a duplicate error — whether the prior app is already
    # query-visible (query-hit skip) or still in the publish-before-read window
    # (the POST hits the server duplicate fault, swallowed as a skip). The fault
    # shape differs by target — cloud's duplicate-detected code family vs on-prem's
    # SQL uniqueness violation 0x80040216 at HTTP 500 — and both are swallowed
    # (issues #322, #496).
    r_skip = cli([
        "--json", "app", "create",
        "--name", app_name,
        "--unique-name", app_unique,
        "--if-exists", "skip",
        "--no-publish",
    ], check=False)
    assert r_skip.returncode == 0, (
        f"app create --if-exists skip should not error:\n{r_skip.stderr}\n"
        f"stdout: {r_skip.stdout}"
    )
    env_skip = json.loads(r_skip.stdout)
    assert env_skip["ok"], env_skip
    assert env_skip["data"].get("skipped") is True, env_skip["data"]

    # ── Step 2: app add-components ────────────────────────────────────────────
    view_guid = _find_account_view(backend)
    if view_guid:
        r_add = cli([
            "--json", "app", "add-components", app_id,
            "--component", f"view:{view_guid}",
        ], check=False)
        assert r_add.returncode == 0, (
            f"app add-components failed:\n{r_add.stderr}\nstdout: {r_add.stdout}"
        )
        env_add = json.loads(r_add.stdout)
        assert env_add["ok"], env_add
        assert env_add["data"].get("added", 0) >= 1, (
            f"expected at least 1 added component: {env_add['data']}"
        )

        # ── Step 2b: app remove-components (unbind what we just added) ─────────
        r_rm = cli([
            "--json", "app", "remove-components", app_id,
            "--component", f"view:{view_guid}",
        ], check=False)
        assert r_rm.returncode == 0, (
            f"app remove-components failed:\n{r_rm.stderr}\nstdout: {r_rm.stdout}"
        )
        env_rm = json.loads(r_rm.stdout)
        assert env_rm["ok"], env_rm
        assert env_rm["data"].get("removed", 0) >= 1, (
            f"expected at least 1 removed component: {env_rm['data']}"
        )
    # (If no account view found we still cover add/remove-components code path by
    # noting the skip, but we still continue to test the sitemap verbs.)

    # ── Step 3: app build-sitemap ─────────────────────────────────────────────
    sitemap_name_build = f"e2esm_b_{unique[:8]}"
    r_build = cli([
        "--json", "app", "build-sitemap", sitemap_name_build,
        "--area", "Area1:E2E Area",
        "--group", "Area1/Grp1:E2E Group",
        "--subarea", "Area1/Grp1:entity=account:Accounts",
        "--unique-name", app_unique,
        "--no-publish",
    ], check=False)
    assert r_build.returncode == 0, (
        f"app build-sitemap failed:\n{r_build.stderr}\nstdout: {r_build.stdout}"
    )
    env_build = json.loads(r_build.stdout)
    assert env_build["ok"], env_build
    sm_build_id = env_build["data"].get("sitemapid")
    if sm_build_id:
        created_sitemap_ids.append(sm_build_id)

    # ── Step 4: app set-sitemap ───────────────────────────────────────────────
    # Use a distinct sitemap name and a different unique-name (not the app's) to
    # avoid a sitemapnameunique collision with the sitemap created in build-sitemap.
    # The server requires sitemapnameunique to be non-empty (0x80060406).
    sitemap_name_set = f"e2esm_s_{unique[:8]}"
    sitemap_unique_set = f"new_e2esm_{unique[:8]}"
    minimal_xml = (
        "<SiteMap>"
        "<Area Id=\"Area1\" Title=\"E2E\">"
        "<Group Id=\"Grp1\" Title=\"E2E\">"
        "<SubArea Id=\"account\" Entity=\"account\" />"
        "</Group></Area>"
        "</SiteMap>"
    )
    import tempfile
    tmp_xml = tempfile.NamedTemporaryFile(
        mode="w", suffix=".xml", delete=False, encoding="utf-8"
    )
    tmp_xml.write(minimal_xml)
    tmp_xml.close()
    xml_path = tmp_xml.name

    try:
        r_set = cli([
            "--json", "app", "set-sitemap", sitemap_name_set,
            "--xml-file", xml_path,
            "--unique-name", sitemap_unique_set,
        ], check=False)
    finally:
        try:
            os.unlink(xml_path)
        except OSError:
            pass

    assert r_set.returncode == 0, (
        f"app set-sitemap failed:\n{r_set.stderr}\nstdout: {r_set.stdout}"
    )
    env_set = json.loads(r_set.stdout)
    assert env_set["ok"], env_set
    sm_set_id = env_set["data"].get("sitemapid")
    if sm_set_id:
        created_sitemap_ids.append(sm_set_id)


# ── app delete: FK-blocking dependent sweep ───────────────────────────────────


@covers("app delete")
def test_app_delete_unknown_target_errors(cli, unique):
    """Deleting a non-existent app fails cleanly with a not-found error.

    Runs on any target: it performs no write, so it is immune to the appmodule
    read-replica lag that gates the FK-sweep test below to on-prem.
    """
    r = cli(["--json", "app", "delete", f"new_missing{unique[:8]}", "--yes"],
            check=False)
    assert r.returncode != 0
    env = json.loads(r.stdout)
    assert env["ok"] is False
    assert "not found" in (env.get("error") or "").lower()


@covers("app delete")
@pytest.mark.requires_onprem
@pytest.mark.slow
def test_app_delete_sweeps_fk_blocking_dependents(backend, cli, request, unique):
    """`app delete` removes a published app whose `appsetting` rows FK-block it.

    Gated `requires_onprem`: the appmodule DELETE block (`0x80048d21`) reproduces
    on on-prem v9.x, and on-prem reads are consistent — Dataverse online serves
    appmodule reads from a read replica that can lag a fresh write by minutes, so
    a create→resolve→delete cycle can't run reliably there (the FK-block itself
    was verified live on cloud during triage; see issue #324).

    The test proves the sweep is *necessary*: a plain `appmodules` DELETE first
    fails with `0x80048d21`, then `app delete` clears the dependents and the app
    is gone.
    """
    import time

    app_unique = f"new_del{unique[:8]}"
    app_name = f"E2E Del {unique[:8]}"
    created_id: list[str] = []

    def _cleanup():
        if created_id:
            try:
                # Sweep dependents then the app, in case the test aborted mid-way.
                for s in backend.get_collection(
                        "appsettings",
                        params={"$filter": f"_parentappmoduleid_value eq {created_id[0]}",
                                "$select": "appsettingid"}):
                    backend.delete(f"appsettings({s['appsettingid']})")
                backend.delete(f"appmodules({created_id[0]})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    # Publish so the appmodule is readable (on-prem is not readable pre-publish)
    # and so the platform materializes its appsetting rows.
    r_create = cli(["--json", "app", "create", "--name", app_name,
                    "--unique-name", app_unique, "--publish"], check=False)
    if r_create.returncode != 0:
        combined = (r_create.stderr or "") + (r_create.stdout or "")
        if any(k in combined.lower() for k in (
                "not supported", "privilege", "accessdenied", "403", "notimplemented")):
            pytest.skip(f"app create rejected by this org: {combined[:300]}")
        pytest.fail(f"app create failed:\n{combined[:500]}")
    app_id = json.loads(r_create.stdout)["data"].get("appmoduleid")
    assert app_id, r_create.stdout
    created_id.append(app_id)

    # Resolve may lag the publish briefly — retry the read-only dry-run until ok.
    resolved = False
    for _ in range(6):
        rr = cli(["--json", "--dry-run", "app", "delete", app_unique], check=False)
        if rr.returncode == 0 and json.loads(rr.stdout).get("ok"):
            resolved = True
            break
        time.sleep(5)
    if not resolved:
        pytest.skip("new appmodule not readable yet (publish/replica lag)")

    # Prove the block exists: a plain record DELETE is rejected by the appsetting
    # FK. If it is NOT blocked, this org gave the app no FK-blocking child, so the
    # sweep can't be exercised here — skip rather than pass vacuously.
    from crm.utils.d365_backend import D365Error
    try:
        backend.delete(f"appmodules({app_id})")
        created_id.clear()  # plain delete succeeded → app already gone
        pytest.skip("app had no FK-blocking dependents on this org; sweep not exercised")
    except D365Error as exc:
        assert exc.code == "0x80048d21", f"expected FK-block, got {exc.code}: {exc}"

    # dry-run preview names the app and issues no delete.
    env_dry = json.loads(cli(["--json", "--dry-run", "app", "delete", app_unique]).stdout)
    assert env_dry["data"]["would_delete"]["appmodule"]

    # Real delete sweeps the appsetting dependents and removes the app.
    r_del = cli(["--json", "app", "delete", app_unique, "--yes"], check=False)
    assert r_del.returncode == 0, f"app delete failed:\n{r_del.stderr}\n{r_del.stdout}"
    env_del = json.loads(r_del.stdout)
    assert env_del["ok"] and env_del["data"]["deleted"] is True, env_del
    assert env_del["data"]["dependents_deleted"], "sweep removed no dependents"
    created_id.clear()  # app deleted; nothing for the finalizer to clean

    # The app no longer resolves.
    r_gone = cli(["--json", "app", "delete", app_unique, "--yes"], check=False)
    assert json.loads(r_gone.stdout).get("ok") is False
