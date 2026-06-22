# pyright: basic
"""E2E tests for theme verbs: list, get, create, update, publish.

Themes are an ordinary `themes` entity (org-wide branding). `theme publish`
(PublishTheme) promotes a theme to the active org-wide theme; the CLI ships no
inverse verb, so the publish test captures the active theme first and
re-publishes it at the end to restore the org (it runs on the pollution-tolerant
sandbox org).

There is no `theme delete` verb, so the lifecycle and publish tests tear the
created theme down through the backend directly (the standard e2e cleanup
pattern).
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


@covers("theme list")
def test_theme_list(cli):
    """Stock orgs ship at least one theme; assert non-empty + summary shape."""
    result = cli(["--json", "theme", "list"])
    assert result.returncode == 0, (
        f"theme list failed:\n{result.stderr}\nstdout: {result.stdout}")
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list) and len(items) > 0, env
    first = items[0]
    assert "themeid" in first, first
    assert "name" in first, first


@covers("theme create", "theme get", "theme update")
@pytest.mark.slow
def test_theme_lifecycle(cli, backend, unique):
    """Create a theme, read it back, update a color, then delete it (via backend)."""
    name = f"E2E Theme {unique}"
    result = cli([
        "--json", "theme", "create",
        "--name", name,
        "--set", "maincolor=#0066cc",
        "--set", "navbarbackgroundcolor=#002050",
    ])
    assert result.returncode == 0, (
        f"theme create failed:\n{result.stderr}\nstdout: {result.stdout}")
    created = json.loads(result.stdout)
    assert created["ok"], created
    theme_id = created["data"]["themeid"]
    assert theme_id, created

    try:
        got = cli(["--json", "theme", "get", theme_id])
        assert got.returncode == 0, got.stderr
        env = json.loads(got.stdout)
        assert env["ok"], env
        assert env["data"]["name"] == name
        assert env["data"]["maincolor"] == "#0066cc"

        updated = cli([
            "--json", "theme", "update", theme_id, "--set", "maincolor=#ff0000",
        ])
        assert updated.returncode == 0, updated.stderr
        assert json.loads(updated.stdout)["data"]["updated"] is True

        reread = cli(["--json", "theme", "get", theme_id])
        assert json.loads(reread.stdout)["data"]["maincolor"] == "#ff0000"
    finally:
        # No `theme delete` verb — tear down through the backend (best effort).
        try:
            backend.delete(f"themes({theme_id})")
        except Exception:
            pass


def _active_theme_id(backend) -> str:
    """The org's current theme — the one row with isdefaulttheme=true, per the
    documented `themes?$filter=isdefaulttheme eq true` query. Asserts exactly one
    so a publish that fails to flip the flag is caught rather than masked."""
    rows = backend.get_collection(
        "themes", params={"$select": "themeid,isdefaulttheme"})
    active = [r["themeid"] for r in rows if r.get("isdefaulttheme")]
    assert len(active) == 1, f"expected exactly one active theme, got {active}"
    return active[0]


@covers("theme publish")
@pytest.mark.slow
def test_theme_publish_sets_active_then_restores(cli, backend, unique):
    """Publish a throwaway theme, assert it becomes the active org theme, then
    re-publish the captured original so the shared org ends where it started.

    Publishing flips isdefaulttheme onto the published theme and off the prior
    one; restoring the captured original keeps reruns repeatable and never
    strands the org on the junk theme. The restore is asserted in the body (it
    exercises `theme publish` a second time) and re-run best-effort in `finally`
    so a mid-test failure still hands the org back on its starting theme.
    """
    original_id = _active_theme_id(backend)

    created = cli([
        "--json", "theme", "create",
        "--name", f"E2E Publish {unique}",
        "--set", "maincolor=#0066cc",
    ])
    assert created.returncode == 0, (
        f"theme create failed:\n{created.stderr}\nstdout: {created.stdout}")
    theme_id = json.loads(created.stdout)["data"]["themeid"]
    assert theme_id and theme_id != original_id, (theme_id, original_id)

    try:
        published = cli(["--json", "theme", "publish", theme_id])
        assert published.returncode == 0, (
            f"theme publish failed:\n{published.stderr}\nstdout: {published.stdout}")
        assert json.loads(published.stdout)["ok"]
        assert _active_theme_id(backend) == theme_id  # throwaway is now active

        # Restore the captured original — a second `theme publish`, asserted.
        restored = cli(["--json", "theme", "publish", original_id])
        assert restored.returncode == 0, (
            f"restore publish failed:\n{restored.stderr}\nstdout: {restored.stdout}")
        assert _active_theme_id(backend) == original_id
    finally:
        # Safety net: guarantee the org is back on its original theme (re-publish
        # is idempotent if the body already did) before removing the throwaway —
        # the active theme cannot be the one we delete. Both best-effort so a
        # finalizer never masks the test result.
        cli(["--json", "theme", "publish", original_id], check=False)
        try:
            backend.delete(f"themes({theme_id})")
        except Exception:
            pass
