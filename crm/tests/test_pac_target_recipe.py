# crm/tests/test_pac_target_recipe.py
# pyright: basic
"""#1128: the PAC target check is a skill recipe, not a CLI feature, so this
guards two things offline (ADR 0030: no live org): the recipe names exactly the
fields the measured PAC 2.14.1 and `crm` JSON carry, and the decision table it
states performs zero PAC writes for a mismatched or unknown target.

`decide` below is the recipe's table as code; the shapes are the measured ones
from the issue's probe (Ubuntu and Windows identical), GUIDs replaced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SETUP_MD = Path(__file__).resolve().parent.parent / "skills" / "reference" / "setup.md"

ORG = "00000000-0000-0000-0000-0000000000a1"
ENV = "00000000-0000-0000-0000-0000000000e1"
USER = "00000000-0000-0000-0000-0000000000b1"

WHOAMI = {"OrganizationId": ORG, "UserId": USER, "url": "https://x.crm.dynamics.com/api/data/v9.2"}
ORG_FN = {"OrganizationId": ORG, "EnvironmentId": ENV}
PAC_WHO = {
    "OrgId": ORG,
    "EnvironmentId": ENV,
    "OrgUrl": "https://x.crm.dynamics.com/",
    "UserId": USER,
}


def decide(whoami: dict, org_fn: dict, pac: dict | None, pac_on_path: bool = True) -> str:
    if not pac_on_path:
        return "pac-unavailable"
    if pac is None or "OrganizationId" not in whoami:
        return "unverifiable"
    if pac.get("OrgId") != whoami["OrganizationId"]:
        return "mismatch"
    env = org_fn.get("EnvironmentId")
    if env is not None and pac.get("EnvironmentId") != env:
        return "mismatch"
    return "match"


@pytest.mark.parametrize(
    ("label", "pac", "org_fn", "on_path", "outcome"),
    [
        ("match", PAC_WHO, ORG_FN, True, "match"),
        ("org mismatch", {**PAC_WHO, "OrgId": "other"}, ORG_FN, True, "mismatch"),
        ("env mismatch", {**PAC_WHO, "EnvironmentId": "other"}, ORG_FN, True, "mismatch"),
        ("no pac profile", None, ORG_FN, True, "unverifiable"),
        ("pac not on PATH", None, ORG_FN, False, "pac-unavailable"),
        ("env not comparable", PAC_WHO, {"OrganizationId": ORG}, True, "match"),
    ],
)
def test_decision_table(label, pac, org_fn, on_path, outcome):
    got = decide(WHOAMI, org_fn, pac, on_path)
    assert got == outcome, label


def _recipe_section() -> str:
    text = SETUP_MD.read_text(encoding="utf-8")
    start = text.index("## PAC target check")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_recipe_uses_measured_fields():
    section = _recipe_section()
    for key in (*PAC_WHO, *WHOAMI, *ORG_FN):
        assert f"`{key}`" in section, f"recipe omits measured field {key}"
    assert "pac org who --json" in section


def test_recipe_outcome_rows_state_the_write_rule():
    rows = {
        cells[0]: (cells[1], cells[2])
        for line in _recipe_section().splitlines()
        if line.startswith("|")
        for cells in [[c.strip() for c in line.strip("|").split("|")]]
        if len(cells) == 3
    }
    assert rows["Confirmed match"][1].startswith("Proceed with the `pac` write")
    assert "the function result has none" in rows["Confirmed match"][0]
    assert rows["Confirmed mismatch"][1].startswith("No `pac` write")
    assert rows["Unverifiable"][1].startswith("No `pac` write")
    assert "Skip the `pac` steps" in rows["`pac` unavailable"][1]
    assert "zero writes" in _recipe_section()
