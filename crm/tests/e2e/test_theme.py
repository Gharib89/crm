# pyright: basic
"""E2E tests for theme verbs: list, get, create, update.

Themes are an ordinary `themes` entity (org-wide branding). `theme publish`
(PublishTheme) is intentionally not exercised here — it promotes a theme to the
active org-wide theme and the CLI ships no inverse verb to restore the prior
one, so it would leave the shared test org on a throwaway theme (see the
`theme publish` E2E_SKIP entry; its happy path is covered by the wire-level unit
tests in crm/tests/test_themes.py).

There is no `theme delete` verb, so the lifecycle test tears the created theme
down through the backend directly (the standard e2e cleanup pattern).
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
