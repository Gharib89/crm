"""`read_ribbon_diffs` streams a solution zip's ribbon diffs (#1100)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree.ElementTree import ParseError

import pytest

from crm.core.ribbon_diff import read_ribbon_diffs

_CUSTOM = (
    "<CustomActions><CustomAction Id='new.A' Location='Mscrm.Form.new_a.MainTab'>"
    "<CommandUIDefinition/></CustomAction></CustomActions>"
)
_EMPTY = (
    "<RibbonDiffXml><CustomActions/><Templates><RibbonTemplates/></Templates>"
    "<CommandDefinitions/><RuleDefinitions><TabDisplayRules/><DisplayRules/>"
    "<EnableRules/></RuleDefinitions><LocLabels/></RibbonDiffXml>"
)


def _entity(name: str, diff: str) -> str:
    return (
        f"<Entity><Name LocalizedName='X' OriginalName='X'>{name}</Name>"
        f"<EntityInfo><entity><attributes><attribute><Name>ignored</Name></attribute>"
        f"</attributes></entity></EntityInfo>{diff}</Entity>"
    )


def _zip(tmp_path: Path, custom: str | None) -> Path:
    path = tmp_path / "solution.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("solution.xml", "<ImportExportXml/>")
        if custom is not None:
            z.writestr("customizations.xml", custom)
    return path


def _doc(entities: str, app: str = _EMPTY) -> str:
    return (
        f"<ImportExportXml><Entities>{entities}</Entities><Workflows><Workflow/></Workflows>"
        f"{app}</ImportExportXml>"
    )


def test_keeps_the_application_and_each_entity_diff_that_holds_content(tmp_path):
    custom = f"<RibbonDiffXml>{_CUSTOM}</RibbonDiffXml>"
    doc = _doc(
        _entity("New_Project", custom)
        + _entity("account", _EMPTY)
        + _entity("contact", "<RibbonDiffXml><RibbonNotSupported/></RibbonDiffXml>"),
        app=custom,
    )
    diffs = read_ribbon_diffs(_zip(tmp_path, doc))
    assert set(diffs) == {None, "new_project"}
    assert diffs["new_project"].startswith(b"<RibbonDiffXml>")
    assert b"new.A" in diffs["new_project"]
    assert b"new.A" in diffs[None]


def test_template_and_rule_content_count_one_level_down(tmp_path):
    rules = (
        "<RibbonDiffXml><RuleDefinitions><TabDisplayRules/><DisplayRules>"
        "<DisplayRule Id='r'/></DisplayRules></RuleDefinitions></RibbonDiffXml>"
    )
    templates = (
        "<RibbonDiffXml><Templates><RibbonTemplates Id='t'><GroupTemplate/>"
        "</RibbonTemplates></Templates></RibbonDiffXml>"
    )
    diffs = read_ribbon_diffs(_zip(tmp_path, _doc(_entity("a", rules) + _entity("b", templates))))
    assert set(diffs) == {"a", "b"}


def test_a_solution_with_no_ribbon_content_yields_nothing(tmp_path):
    assert read_ribbon_diffs(_zip(tmp_path, _doc(_entity("a", _EMPTY)))) == {}


def test_a_zip_without_customizations_raises(tmp_path):
    with pytest.raises(KeyError):
        read_ribbon_diffs(_zip(tmp_path, None))


def test_an_entity_declaration_is_rejected(tmp_path):
    bomb = '<!DOCTYPE x [<!ENTITY e "boom">]><ImportExportXml>&e;</ImportExportXml>'
    with pytest.raises(ParseError):
        read_ribbon_diffs(_zip(tmp_path, bomb))
