# pyright: basic
"""E2E: `solution export-spec` projects a model-driven app + sitemap (#797).

Creates a model-driven app whose sitemap exposes the throwaway entity (the ADR
0024 apps create path), projects the whole solution with `solution export-spec`,
then DELETES the app + sitemap so the org lacks them, and re-applies the exported
spec — asserting the app and its Entity-backed navigation are re-seeded from the
spec alone (the ADR 0019 seedable round-trip for the non-entity-rooted kind).
Runs on both the on-prem (NTLM) and Dataverse-online targets.

Two environmental skips (never a false failure): an org that rejects appmodule
writes (on-prem v9.1) skips at setup; and Dataverse imposes a multi-minute
read-visibility lag on a freshly-created appmodule (the row is invisible to Web
API reads long after its solution-component membership is — the create path
documents this "publish-before-read" window and reads success off the apply
envelope, never a query). Projection needs the app *readable*, so the test polls
for it and skips if the window has not cleared within the budget; on on-prem the
app is retrievable right after publish, so the full round-trip runs there.
"""
from __future__ import annotations

import json
import time

import pytest
import yaml

from crm.utils.d365_backend import D365Error

# Bounded wait for the freshly-created appmodule to become readable (Dataverse's
# read-visibility lag; on-prem clears immediately after publish).
_APP_VISIBLE_TIMEOUT_S = 150
_APP_VISIBLE_POLL_S = 6


def _wait_app_readable(backend, app_id: str) -> bool:
    """Poll GET appmodules(id) until it resolves; False if the window never clears."""
    deadline = time.time() + _APP_VISIBLE_TIMEOUT_S
    while time.time() < deadline:
        try:
            backend.get(f"appmodules({app_id})", params={"$select": "appmoduleid"})
            return True
        except D365Error:
            time.sleep(_APP_VISIBLE_POLL_S)
    return False

from crm.tests.e2e.coverage import covers


@covers("solution export-spec")
def test_export_spec_apps_round_trip(
    cli, backend, ephemeral_solution, ephemeral_entity, unique, tmp_path, request
):
    app_unique = f"new_appx{unique[:6]}"
    app_name = f"E2E ExportApp {unique[:6]}"

    created_app_ids: list[str] = []
    created_sitemap_ids: list[str] = []

    def _cleanup():
        for smid in created_sitemap_ids:
            try:
                backend.delete(f"sitemaps({smid})")
            except Exception:  # noqa: BLE001 — best-effort cleanup, never mask the test
                pass
        for aid in created_app_ids:
            try:
                backend.delete(f"appmodules({aid})")
            except Exception:  # noqa: BLE001
                pass

    request.addfinalizer(_cleanup)

    # Setup: create the app + a sitemap exposing the ephemeral entity, in the
    # ephemeral solution (so export-spec's member walk finds the appmodule).
    setup = {
        "solution": {"unique_name": ephemeral_solution},
        "apps": [{
            "name": app_name,
            "unique_name": app_unique,
            "sitemap": {"areas": [{
                "id": "xp_area", "title": "Export Area",
                "groups": [{
                    "id": "xp_group", "title": "Export Group",
                    "subareas": [{"entity": ephemeral_entity, "title": "Export Rows"}],
                }],
            }]},
        }],
    }
    setup_path = tmp_path / "setup.json"
    setup_path.write_text(json.dumps(setup), encoding="utf-8")

    r_setup = cli(["--json", "apply", "-f", str(setup_path)], check=False)
    combined = (r_setup.stderr or "") + (r_setup.stdout or "")
    # An org that rejects appmodule writes (on-prem v9.1) or is transiently
    # rate-limited (service-protection 429 exhausting the retry budget) is an
    # environmental skip, not a projection failure.
    if r_setup.returncode != 0 and any(kw in combined.lower() for kw in (
        "not supported", "privilege", "accessdenied", "403",
        "businessnotfound", "notimplemented", "429", "too many requests",
        "timed out", "service protection",
    )):
        pytest.skip(f"appmodule write unavailable on this org "
                    f"(on-prem limitation / throttled): {combined[:400]}")
    assert r_setup.returncode == 0, f"setup apply failed: {combined[:800]}"
    setup_data = json.loads(r_setup.stdout)["data"]
    assert not setup_data.get("failed"), f"setup apply failed: {setup_data}"
    app_applied = [e for e in setup_data["applied"] if e["kind"] == "app"]
    assert app_applied and app_applied[0].get("appmoduleid"), setup_data
    created_app_ids.append(app_applied[0]["appmoduleid"])
    if app_applied[0].get("sitemapid"):
        created_sitemap_ids.append(app_applied[0]["sitemapid"])

    # Projection reads the appmodule record; on Dataverse that read lags creation
    # by minutes. Wait for it, else skip — the export code is proven against
    # established apps; this window is a create-then-read-in-one-run artifact.
    if not _wait_app_readable(backend, app_applied[0]["appmoduleid"]):
        pytest.skip("freshly-created appmodule not yet readable (Dataverse "
                    "publish-before-read visibility lag); export needs it readable.")

    # 1) Project the solution: the app lands under a top-level apps: block with its
    #    Entity-backed sitemap (the seedable slice).
    out = tmp_path / "spec.yaml"
    r_exp = cli(["--json", "solution", "export-spec", ephemeral_solution, "-o", str(out)])
    assert r_exp.returncode == 0, r_exp.stderr
    assert json.loads(r_exp.stdout)["data"]["apps"] == 1

    written = yaml.safe_load(out.read_text(encoding="utf-8"))
    apps = written.get("apps") or []
    block = next((a for a in apps if a["unique_name"] == app_unique), None)
    assert block is not None, f"app not projected: {written.get('apps')}"
    subs = {
        s["entity"]
        for area in block["sitemap"]["areas"]
        for g in area["groups"] for s in g["subareas"]
    }
    assert ephemeral_entity in subs, f"entity subarea not projected: {block}"

    # 2) Delete the app + sitemap so the org LACKS them — the "org that does not
    #    already have it" side of the seed round-trip.
    del_ids = list(created_app_ids)
    for aid in del_ids:
        backend.delete(f"appmodules({aid})")
    created_app_ids.clear()
    for smid in list(created_sitemap_ids):
        try:
            backend.delete(f"sitemaps({smid})")
        except Exception:  # noqa: BLE001 — sitemap may cascade with the app
            pass
    created_sitemap_ids.clear()

    # 3) Re-apply the exported spec: the app + navigation are re-seeded from the
    #    spec alone (ADR 0019). apply create-path builds them afresh.
    r_apply = cli(["--json", "apply", "-f", str(out)])
    assert r_apply.returncode == 0, r_apply.stderr
    data = json.loads(r_apply.stdout)["data"]
    assert not data.get("failed"), f"re-apply reported failures: {data}"
    reseeded = [e for e in data["applied"] if e["kind"] == "app" and e["name"] == app_unique]
    assert reseeded, f"app not re-seeded: {data}"
    entry = reseeded[0]
    assert entry.get("appmoduleid") and entry.get("sitemapid"), entry
    created_app_ids.append(entry["appmoduleid"])
    created_sitemap_ids.append(entry["sitemapid"])
