"""A synthetic solution export for the ribbon diff tests (#1100)."""

from __future__ import annotations

import io
import zipfile

CUSTOM_ACTIONS = (
    "<CustomActions><CustomAction Id='new.Project.Do' Location='Mscrm.Form.new_project.MainTab'>"
    "<CommandUIDefinition/></CustomAction></CustomActions>"
)
EMPTY_DIFF = (
    "<RibbonDiffXml><CustomActions/><Templates><RibbonTemplates/></Templates>"
    "<CommandDefinitions/><RuleDefinitions><TabDisplayRules/><DisplayRules/>"
    "<EnableRules/></RuleDefinitions><LocLabels/></RibbonDiffXml>"
)
CUSTOM_DIFF = f"<RibbonDiffXml>{CUSTOM_ACTIONS}</RibbonDiffXml>"


def solution_zip(entity_diffs: dict[str, str], app: str = EMPTY_DIFF) -> bytes:
    """A solution zip whose customizations.xml holds *entity_diffs* (name -> RibbonDiffXml)."""
    entities = "".join(
        f"<Entity><Name LocalizedName='X'>{name}</Name>{diff}</Entity>"
        for name, diff in entity_diffs.items()
    )
    doc = f"<ImportExportXml><Entities>{entities}</Entities>{app}</ImportExportXml>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("customizations.xml", doc)
    return buf.getvalue()


def default_zip() -> bytes:
    """The application diff plus a custom entity's, and an empty skeleton for another."""
    return solution_zip({"New_Project": CUSTOM_DIFF, "account": EMPTY_DIFF}, app=CUSTOM_DIFF)
