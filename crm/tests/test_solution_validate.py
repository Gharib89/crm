# pyright: basic
"""Tests for offline solution validation (#141)."""
from __future__ import annotations

import re
import zipfile

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution_validate as sv
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


# ── fixtures ────────────────────────────────────────────────────────────────

def _make_pkg(path, solution_xml, customizations_xml, content_types=True):
    """Write a minimal solution zip (solution.xml + customizations.xml [+ Content_Types])."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("solution.xml", solution_xml)
        zf.writestr("customizations.xml", customizations_xml)
        if content_types:
            zf.writestr("[Content_Types].xml", "<Types/>")


def _sol(roots=""):
    return (
        '<?xml version="1.0"?>\n'
        "<ImportExportXml><SolutionManifest><UniqueName>cwx_test</UniqueName>"
        f"<Managed>0</Managed><RootComponents>{roots}</RootComponents>"
        "</SolutionManifest></ImportExportXml>"
    )


def _cust(optionsets="", dashboards="", webresources="", entities="", forms=""):
    return (
        '<?xml version="1.0"?>\n'
        f"<ImportExportXml><Entities>{entities}</Entities>"
        f"<optionsets>{optionsets}</optionsets>"
        f"<InteractionCentricDashboards>{dashboards}</InteractionCentricDashboards>"
        f"<WebResources>{webresources}</WebResources>{forms}</ImportExportXml>"
    )


@pytest.fixture
def backend() -> D365Backend:
    profile = ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso", domain="C",
        username="u", api_version="v9.2", verify_ssl=False,
    )
    return D365Backend(profile, password="pw", dry_run=False)


# ── Task 1: package-level checks ──────────────────────────────────────────────

class TestPackageChecks:
    def test_good_empty_package_is_valid(self, tmp_path):
        p = tmp_path / "good.zip"
        _make_pkg(p, _sol(), _cust())
        report = sv.validate_solution(p)
        assert report["valid"] is True
        assert report["findings"] == []
        assert "package" in report["checks_run"]

    def test_missing_customizations_member_is_fatal(self, tmp_path):
        p = tmp_path / "nocust.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("solution.xml", _sol())
            zf.writestr("[Content_Types].xml", "<Types/>")
        report = sv.validate_solution(p)
        assert report["valid"] is False
        assert any(f["check"] == "package" and "customizations.xml" in f["message"]
                   for f in report["findings"])
        assert report["checks_run"] == ["package"]

    def test_not_a_zip_is_fatal_finding(self, tmp_path):
        p = tmp_path / "junk.zip"
        p.write_bytes(b"not a zip")
        report = sv.validate_solution(p)
        assert report["valid"] is False
        assert any(f["check"] == "package" for f in report["findings"])

    def test_unparseable_xml_is_fatal(self, tmp_path):
        p = tmp_path / "bad.zip"
        _make_pkg(p, _sol(), "<ImportExportXml><unclosed>")
        report = sv.validate_solution(p)
        assert report["valid"] is False
        assert any("well-formed" in f["message"] for f in report["findings"])

    def test_finding_envelope_shape(self, tmp_path):
        p = tmp_path / "nocust.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("solution.xml", _sol())
        report = sv.validate_solution(p)
        f = report["findings"][0]
        assert set(f.keys()) == {"severity", "check", "message", "component", "location"}
