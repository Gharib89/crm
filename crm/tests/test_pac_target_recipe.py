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


class RecordingPac:
    def __init__(self) -> None:
        self.writes = 0

    def push(self, outcome: str) -> None:
        if outcome == "match":
            self.writes += 1


@pytest.mark.parametrize(
    ("label", "pac", "org_fn", "on_path", "outcome", "writes"),
    [
        ("match", PAC_WHO, ORG_FN, True, "match", 1),
        ("org mismatch", {**PAC_WHO, "OrgId": "other"}, ORG_FN, True, "mismatch", 0),
        ("env mismatch", {**PAC_WHO, "EnvironmentId": "other"}, ORG_FN, True, "mismatch", 0),
        ("no pac profile", None, ORG_FN, True, "unverifiable", 0),
        ("pac not on PATH", None, ORG_FN, False, "pac-unavailable", 0),
        ("env not comparable", PAC_WHO, {"OrganizationId": ORG}, True, "match", 1),
    ],
)
def test_pac_write_only_on_confirmed_match(label, pac, org_fn, on_path, outcome, writes):
    got = decide(WHOAMI, org_fn, pac, on_path)
    pac_tool = RecordingPac()
    pac_tool.push(got)
    assert (got, pac_tool.writes) == (outcome, writes), label


def test_recipe_uses_measured_fields_and_outcomes():
    text = SETUP_MD.read_text(encoding="utf-8")
    section = text[text.index("## Verify the PAC target") :]
    for key in (*PAC_WHO, *WHOAMI, *ORG_FN):
        assert f"`{key}`" in section, f"recipe omits measured field {key}"
    for word in ("Confirmed match", "Confirmed mismatch", "Unverifiable", "`pac` unavailable"):
        assert word in section, f"recipe omits outcome {word}"
    assert "zero writes" in section
    assert "pac org who --json" in section
