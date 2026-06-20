# pyright: basic
"""E2E tests for dashboard verbs: list, create, get, delete (systemform type=0).

A dashboard's FormXml is posted verbatim. The lifecycle test uses a minimal but
server-accepted dashboard layout — one empty tab/section, standard structure
only, placeholder GUIDs (no org identifiers) — verified live on the cloud org.
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers

# Minimal valid dashboard FormXml: one tab, one empty section. Placeholder GUIDs
# only — safe as a public fixture.
_FORMXML = (
    '<form><tabs>'
    '<tab showlabel="true" verticallayout="true" '
    'id="{11111111-1111-1111-1111-111111111111}" name="Tab1" '
    'locklevel="0" expanded="true">'
    '<labels><label description="Tab1" languagecode="1033" /></labels>'
    '<columns><column width="100%"><sections>'
    '<section showlabel="true" showbar="false" columns="11" '
    'id="{22222222-2222-2222-2222-222222222222}" name="Section1">'
    '<labels><label description="Section1" languagecode="1033" /></labels>'
    '<rows><row /></rows></section></sections></column></columns>'
    '</tab></tabs></form>'
)


def _write_formxml(tmp_path) -> str:
    f = tmp_path / "dash.xml"
    f.write_text(_FORMXML, encoding="utf-8")
    return str(f)


# ── dashboard list ──────────────────────────────────────────────────────────


@covers("dashboard list")
def test_dashboard_list(cli):
    """Stock orgs ship system dashboards; assert non-empty + shape."""
    result = cli(["--json", "dashboard", "list"])
    assert result.returncode == 0, (
        f"dashboard list failed:\n{result.stderr}\nstdout: {result.stdout}")
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list) and len(items) > 0, env
    first = items[0]
    assert "formid" in first, first
    assert "name" in first, first
    # list returns list columns only — formxml is fetched via `dashboard get`
    assert "formxml" not in first, first


# ── dashboard create / get / delete ───────────────────────────────────────────


@covers("dashboard create", "dashboard get", "dashboard delete")
@pytest.mark.slow
def test_dashboard_lifecycle(cli, tmp_path, unique):
    """Create an org dashboard from FormXml, read it back, then delete it."""
    name = f"E2E Dashboard {unique}"
    formxml = _write_formxml(tmp_path)

    result = cli([
        "--json", "dashboard", "create",
        "--name", name, "--formxml", formxml, "--no-publish"])
    assert result.returncode == 0, (
        f"dashboard create failed:\n{result.stderr}\nstdout: {result.stdout}")
    created = json.loads(result.stdout)
    assert created["ok"], created
    dashboard_id = created["data"]["formid"]
    assert dashboard_id, created

    try:
        got = cli(["--json", "dashboard", "get", dashboard_id])
        assert got.returncode == 0, got.stderr
        env = json.loads(got.stdout)
        assert env["ok"], env
        assert env["data"]["name"] == name
        assert "<form" in env["data"]["formxml"]
        # org-owned dashboards are not bound to a single table
        assert env["data"]["objecttypecode"] == "none", env
    finally:
        deleted = cli(["--json", "dashboard", "delete", dashboard_id])
        assert deleted.returncode == 0, deleted.stderr
        assert json.loads(deleted.stdout)["data"]["deleted"] is True
