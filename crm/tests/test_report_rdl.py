"""Offline tests for the RDL data-provider classifier (`report.classify_rdl`)."""

# pyright: basic
from __future__ import annotations

from pathlib import Path

import pytest

from crm.core.report import classify_rdl

_FIXTURES = Path(__file__).parent / "fixtures" / "report"
_NS08 = "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
_NS05 = "http://schemas.microsoft.com/sqlserver/reporting/2005/01/reportdefinition"


def _rdl(*sources: str, ns: str = _NS08, extra: str = "") -> str:
    return f'<Report xmlns="{ns}"><DataSources>{"".join(sources)}</DataSources>{extra}</Report>'


def _provider(value: str) -> str:
    return (
        f'<DataSource Name="d"><ConnectionProperties><DataProvider>{value}</DataProvider>'
        "</ConnectionProperties></DataSource>"
    )


_REF = '<DataSource Name="s"><DataSourceReference>/Shared/CRM</DataSourceReference></DataSource>'


@pytest.mark.parametrize(
    ("fixture", "provider"),
    [
        ("fetch.rdl", "fetch"),
        ("sql_simple.rdl", "sql"),
        ("sql_complex.rdl", "sql"),
        ("shared_reference.rdl", "unknown"),
    ],
)
def test_fixtures(fixture, provider):
    assert classify_rdl((_FIXTURES / fixture).read_text("utf-8")).provider == provider


def test_shared_reference_reason_names_the_reference():
    result = classify_rdl((_FIXTURES / "shared_reference.rdl").read_text("utf-8"))
    assert result.reason is not None
    assert "/Contoso_MSCRM/SharedCRM" in result.reason


def test_fetch_and_sql_carry_no_reason():
    assert classify_rdl(_rdl(_provider("MSCRMFETCH"))).reason is None
    assert classify_rdl(_rdl(_provider("SQL"))).reason is None


def test_sql_beats_fetch_and_shared_reference():
    assert classify_rdl(_rdl(_provider("MSCRMFETCH"), _provider("SQL"))).provider == "sql"
    assert classify_rdl(_rdl(_REF, _provider("SQL"))).provider == "sql"


def test_unresolved_source_beats_fetch():
    result = classify_rdl(_rdl(_provider("MSCRMFETCH"), _REF))
    assert result.provider == "unknown"


@pytest.mark.parametrize("ns", [_NS05, _NS08])
def test_both_namespaces(ns):
    assert classify_rdl(_rdl(_provider("MSCRMFETCH"), ns=ns)).provider == "fetch"
    assert classify_rdl(_rdl(_provider("SQL"), ns=ns)).provider == "sql"


def test_provider_value_trimmed_and_case_insensitive():
    assert classify_rdl(_rdl(_provider("  mscrmFetch \n"))).provider == "fetch"
    assert classify_rdl(_rdl(_provider(" sql "))).provider == "sql"


def test_unrecognised_provider_reason_names_it():
    result = classify_rdl(_rdl(_provider("OLEDB")))
    assert result.provider == "unknown"
    assert "OLEDB" in (result.reason or "")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "<Report",
        "<NotAReport/>",
        f'<Report xmlns="{_NS08}"/>',
        f'<Report xmlns="{_NS08}"><DataSources/></Report>',
        '<!DOCTYPE r [<!ENTITY x "y">]><Report>&x;</Report>',
    ],
)
def test_bad_input_is_unknown_with_reason(text):
    result = classify_rdl(text)
    assert result.provider == "unknown"
    assert result.reason


def test_provider_text_in_comment_or_command_text_is_ignored():
    rdl = _rdl(
        _provider("MSCRMFETCH"),
        extra="<!-- <DataProvider>SQL</DataProvider> -->"
        "<DataSets><DataSet><Query><CommandText>&lt;DataProvider&gt;SQL&lt;/DataProvider&gt;"
        "</CommandText></Query></DataSet></DataSets>",
    )
    assert classify_rdl(rdl).provider == "fetch"


def test_leading_bom_is_tolerated():
    assert classify_rdl("﻿" + _rdl(_provider("MSCRMFETCH"))).provider == "fetch"
