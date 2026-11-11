"""`read_ribbon_diffs` streams a solution zip's ribbon diffs (#1100)."""

# pyright: basic

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree.ElementTree import ParseError

import pytest

from crm.core.inventory_ribbon import read_ribbon_diff
from crm.core.ribbon_diff import read_ribbon_diffs, rebuild_ribbon_diffs
from crm.tests.ribbon_zip import CUSTOM_ACTIONS, EMPTY_DIFF

_CUSTOM = CUSTOM_ACTIONS
_EMPTY = EMPTY_DIFF


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
    assert b"new.Project.Do" in diffs["new_project"]
    assert b"new.Project.Do" in diffs[None]


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


# Rows as the #1198 probe read them from `ribbondiffs` and `ribboncommands` on Dataverse online.
_ACTION = (
    "<CustomAction Id='prbz.p1198dflt.dflt' Location='Mscrm.HomepageGrid.prbz_p1198dflt.MainTab'>"
    "<CommandUIDefinition><Button Id='prbz.b' Command='prbz.cmd'/></CommandUIDefinition>"
    "</CustomAction>"
)
_COMMAND = (
    "<CommandDefinition Id='prbz.cmd'><EnableRules/><DisplayRules/><Actions>"
    "<JavaScriptFunction FunctionName='go' Library='$webresource:prbz_/probe1198.js'/>"
    "</Actions></CommandDefinition>"
)


def test_a_diff_rebuilt_from_table_rows_is_one_per_entity_and_convert_reads_it():
    diffs = rebuild_ribbon_diffs(
        [
            {"entity": "PRBZ_P1198Dflt", "diffid": "a", "rdx": _ACTION},
            {"entity": "account", "diffid": "b", "rdx": "<CustomAction Id='acc.x'/>"},
            {"entity": None, "diffid": "c", "rdx": "<CustomAction Id='app.x'/>"},
        ],
        [{"entity": "PRBZ_P1198Dflt", "command": "prbz.cmd", "commanddefinition": _COMMAND}],
    )
    assert sorted(diffs, key=str) == [None, "account", "prbz_p1198dflt"]
    text = diffs["prbz_p1198dflt"]
    assert text.startswith(b"<RibbonDiffXml>") and _ACTION.encode() in text
    parsed = read_ribbon_diff(text)
    assert parsed is not None
    (action,) = parsed.actions
    assert action.command == "prbz.cmd" and action.key == ("prbz_/probe1198.js", "go")


def test_a_command_with_no_action_row_still_makes_a_diff():
    diffs = rebuild_ribbon_diffs(
        [], [{"entity": "account", "command": "prbz.cmd", "commanddefinition": _COMMAND}]
    )
    assert list(diffs) == ["account"] and b"<CommandDefinitions>" in diffs["account"]


def test_rows_with_no_content_make_no_diff():
    assert rebuild_ribbon_diffs([{"entity": "account", "diffid": "a", "rdx": None}], []) == {}


def test_a_row_that_is_not_xml_raises():
    with pytest.raises(ParseError):
        rebuild_ribbon_diffs([{"entity": "account", "diffid": "a", "rdx": "<CustomAction"}], [])
