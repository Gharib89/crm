"""A form's activation state (#1213): the v9 reader records `active`, the CRM 2011 reader
never does, and `inventory diff` reports a two-sided difference and skips a one-sided value.
"""

# pyright: basic
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from crm.core import inventory
from crm.core.inventory_diff import diff_inventories
from crm.tests import test_legacy_inventory as legacy
from crm.tests.test_legacy_inventory import reads  # noqa: F401
from crm.tests.test_webapi_inventory import FakeBackend, _extract, _manifest, exports  # noqa: F401

pytestmark = pytest.mark.usefixtures("exports", "reads")

_FIXTURES = Path(__file__).parent / "fixtures" / "inventory"


def _forms(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["fields"]["name"]: r["fields"] for r in data["records"] if r["kind"] == "form"}


def test_a_v9_extract_records_whether_each_form_is_active(tmp_path) -> None:
    out = tmp_path / "inv"
    backend = FakeBackend()
    assert _extract(backend, out).complete
    got = _forms(_manifest(out))
    assert got["Account"]["active"] is True
    assert got["Project"]["active"] is False
    assert 'name="formactivationstate"' in backend.fetch_xml["systemforms"]


def test_a_crm2011_extract_succeeds_and_records_no_activation(tmp_path) -> None:
    out = tmp_path / "inv"
    assert legacy._extract(legacy.FakeClient(), out).complete
    got = _forms(json.loads((out / "manifest.json").read_text(encoding="utf-8")))
    assert got and all("active" not in f for f in got.values())


def _fixture(**active: bool) -> dict[str, Any]:
    data = json.loads((_FIXTURES / "v9" / "manifest.json").read_text(encoding="utf-8"))
    for r in data["records"]:
        if r["kind"] == "form" and r["key"] in active:
            r["fields"]["active"] = active[r["key"]]
    return data


def _form_verdicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, tuple[str, list]]:
    diff = diff_inventories(inventory.parse_manifest(a), inventory.parse_manifest(b))
    return {
        e.key: (e.verdict, [(f.name, f.a, f.b) for f in e.fields])
        for e in diff.entries
        if e.kind == "form"
    }


def _first_form(data: dict[str, Any]) -> str:
    return next(r["key"] for r in data["records"] if r["kind"] == "form")


def test_diff_names_activation_when_only_it_differs() -> None:
    key = _first_form(_fixture())
    got = _form_verdicts(_fixture(**{key: True}), _fixture(**{key: False}))
    assert got[key] == ("changed", [("fields.active", True, False)])


def test_a_form_that_agrees_is_unchanged() -> None:
    key = _first_form(_fixture())
    assert _form_verdicts(_fixture(**{key: True}), _fixture(**{key: True}))[key] == (
        "unchanged",
        [],
    )


def test_a_one_sided_activation_is_not_a_difference_either_way() -> None:
    plain = _fixture()
    key = _first_form(plain)
    read = _fixture(**{key: False})
    assert _form_verdicts(plain, read)[key] == ("unchanged", [])
    assert _form_verdicts(read, plain)[key] == ("unchanged", [])
