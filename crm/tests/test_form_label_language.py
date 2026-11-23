"""Form and dashboard labels are written in the caller's UI language (#1361).

D365 serves and accepts formxml projected to the caller's ``uilanguageid`` and
drops any ``<label>`` in another language on PATCH (#940), so a label the CLI
writes in a fixed 1033 is lost for a non-1033 caller.
"""

# pyright: basic
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
import requests_mock

from crm.core import connection, dashboard, forms

_FORM_ID = "aaaaaaaa-0000-0000-0000-000000000001"


def _layout(tab_labels: str) -> str:
    return (
        "<form><tabs>"
        f'<tab name="general" id="{{aaaa1111-0000-0000-0000-000000000001}}">{tab_labels}'
        '<columns><column width="100%"><sections>'
        '<section name="summary" id="{bbbb2222-0000-0000-0000-000000000002}">'
        '<labels><label description="Summary" languagecode="1025" /></labels>'
        "<rows></rows></section></sections></column></columns></tab>"
        "</tabs></form>"
    )


_ONLY_1025 = '<labels><label description="General" languagecode="1025" /></labels>'
_BOTH = (
    '<labels><label description="General" languagecode="1033" />'
    '<label description="Aam" languagecode="1025" /></labels>'
)


def _form_row(formxml: str) -> dict:
    return {
        "formid": _FORM_ID,
        "name": "Information",
        "objecttypecode": "new_project",
        "type": 2,
        "formxml": formxml,
        "isdefault": True,
    }


def _caller(monkeypatch: pytest.MonkeyPatch, language: int | None) -> None:
    monkeypatch.setattr(connection, "caller_ui_language_id", lambda _b: language)


def _labels(formxml: str, tag: str, name: str) -> dict[str, str]:
    element = next(e for e in ET.fromstring(formxml).iter(tag) if e.get("name") == name)
    labels = element.find("labels")
    assert labels is not None
    return {lab.get("languagecode") or "": lab.get("description") or "" for lab in labels}


def _run(backend, formxml: str, call) -> tuple[dict, str]:
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("systemforms"), json={"value": [_form_row(formxml)]})
        m.get(
            backend.url_for(
                "EntityDefinitions(LogicalName='new_project')/Attributes(LogicalName='new_owner')"
            ),
            json={
                "AttributeType": "Lookup",
                "DisplayName": {"UserLocalizedLabel": {"Label": "Owner"}},
            },
        )
        m.patch(backend.url_for(f"systemforms({_FORM_ID})"), status_code=204)
        out = call()
        patched = next(r for r in m.request_history if r.method == "PATCH").json()["formxml"]
    return out, patched


class TestFormWritesInCallerLanguage:
    @pytest.mark.parametrize(
        ("call", "tag", "name"),
        [
            (
                lambda b: forms.add_form_tab(b, "new_project", "extra", label="Extra"),
                "tab",
                "extra",
            ),
            (
                lambda b: forms.add_form_section(
                    b, "new_project", "more", label="More", tab="general"
                ),
                "section",
                "more",
            ),
        ],
        ids=["add-tab", "add-section"],
    )
    def test_added_labels_carry_the_caller_language(self, backend, monkeypatch, call, tag, name):
        _caller(monkeypatch, 1025)
        out, patched = _run(backend, _layout(_ONLY_1025), lambda: call(backend))
        assert _labels(patched, tag, name) == {"1025": name.capitalize()}
        assert forms.label_languages_in_formxml(patched) == {1025}
        assert "_warnings" not in out

    def test_added_field_label_carries_the_caller_language(self, backend, monkeypatch):
        _caller(monkeypatch, 1025)
        out, patched = _run(
            backend,
            _layout(_ONLY_1025),
            lambda: forms.add_form_field(backend, "new_project", "new_owner"),
        )
        assert forms.label_languages_in_formxml(patched) == {1025}
        assert "_warnings" not in out

    def test_unknown_caller_language_writes_1033(self, backend, monkeypatch):
        _caller(monkeypatch, None)
        _, patched = _run(
            backend,
            _layout(_ONLY_1025),
            lambda: forms.add_form_tab(backend, "new_project", "extra", label="Extra"),
        )
        assert _labels(patched, "tab", "extra") == {"1033": "Extra"}

    def test_rename_of_a_1025_only_tab_sets_that_label(self, backend, monkeypatch):
        _caller(monkeypatch, 1025)
        _, patched = _run(
            backend,
            _layout(_ONLY_1025),
            lambda: forms.rename_form_tab(backend, "new_project", "general", label="Renamed"),
        )
        assert _labels(patched, "tab", "general") == {"1025": "Renamed"}

    def test_rename_of_a_bilingual_tab_changes_only_the_caller_label(self, backend, monkeypatch):
        _caller(monkeypatch, 1025)
        _, patched = _run(
            backend,
            _layout(_BOTH),
            lambda: forms.rename_form_tab(backend, "new_project", "general", label="Renamed"),
        )
        assert _labels(patched, "tab", "general") == {"1033": "General", "1025": "Renamed"}

    def test_rename_section_as_1025_caller(self, backend, monkeypatch):
        _caller(monkeypatch, 1025)
        _, patched = _run(
            backend,
            _layout(_ONLY_1025),
            lambda: forms.rename_form_section(
                backend, "new_project", "summary", label="Renamed", tab="general"
            ),
        )
        assert _labels(patched, "section", "summary") == {"1025": "Renamed"}


class TestConvergeInCallerLanguage:
    _BLOCK = {"tabs": [{"name": "general", "label": "Renamed", "sections": []}]}

    def test_relabel_writes_the_caller_label_and_reapply_is_clean(self, backend, monkeypatch):
        _caller(monkeypatch, 1025)
        with requests_mock.Mocker():
            once, changes = forms.converge_declared_form(
                backend, "new_project", _form_row(_layout(_ONLY_1025)), self._BLOCK
            )
            twice, again = forms.converge_declared_form(
                backend, "new_project", _form_row(once), self._BLOCK
            )
        assert changes[0]["diff"]["label"] == {"old": "General", "new": "Renamed"}
        assert _labels(once, "tab", "general") == {"1025": "Renamed"}
        assert again == []
        assert twice == once

    def test_drift_reads_the_caller_label_not_1033(self, backend, monkeypatch):
        _caller(monkeypatch, 1025)
        block = {"tabs": [{"name": "general", "label": "General", "sections": []}]}
        with requests_mock.Mocker():
            new_xml, changes = forms.converge_declared_form(
                backend, "new_project", _form_row(_layout(_BOTH)), block
            )
        assert changes[0]["diff"]["label"] == {"old": "Aam", "new": "General"}
        assert _labels(new_xml, "tab", "general") == {"1033": "General", "1025": "General"}


class TestDashboardTileInCallerLanguage:
    _DASH_ID = "11112222-3333-4444-5555-666677778888"
    _FORMXML = (
        "<form><tabs>"
        '<tab name="tab0" id="{aaaaaaaa-0000-0000-0000-000000000001}">'
        '<columns><column width="100%"><sections>'
        '<section name="sec0" id="{aaaaaaaa-0000-0000-0000-000000000002}">'
        "<rows><row /></rows></section>"
        "</sections></column></columns></tab>"
        "</tabs></form>"
    )

    @pytest.mark.parametrize(("language", "expected"), [(1025, {1025}), (None, {1033})])
    def test_iframe_tile_labels(self, backend, monkeypatch, language, expected):
        _caller(monkeypatch, language)
        url = backend.url_for(f"systemforms({self._DASH_ID})")
        with requests_mock.Mocker() as m:
            m.get(
                url,
                json={"formid": self._DASH_ID, "name": "D", "type": 0, "formxml": self._FORMXML},
            )
            m.patch(url, status_code=204)
            dashboard.add_iframe_to_dashboard(backend, self._DASH_ID, url="https://example.com/x")
            patched = next(r for r in m.request_history if r.method == "PATCH").json()["formxml"]
        assert forms.label_languages_in_formxml(patched) == expected
