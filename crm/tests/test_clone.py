"""Unit tests for crm.core.clone."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.core.clone import retarget_spec


def _spec():
    return {
        "entities": [{
            "schema_name": "new_Project",
            "display_name": "Project",
            "display_collection_name": "Projects",
            "ownership": "UserOwned",
            "primary_attr": {"schema_name": "new_Name", "label": "Name"},
            "attributes": [
                {"kind": "string", "schema_name": "new_Code", "display_name": "Code",
                 "max_length": 100},
                {"kind": "lookup", "schema_name": "new_AccountId", "display_name": "Account",
                 "target_entity": "account"},
            ],
            "views": [{"name": "Active Projects",
                       "columns": [{"name": "new_name", "width": 200}]}],
        }],
        "optionsets": [{"name": "new_status", "display_name": "Status", "options": []}],
    }


class TestRetargetSpec:
    def test_renames_entity_schema_and_display(self):
        spec = _spec()
        retarget_spec(spec, new_schema="cwx_TicketClone", display="Ticket Clone")
        ent = spec["entities"][0]
        assert ent["schema_name"] == "cwx_TicketClone"
        assert ent["display_name"] == "Ticket Clone"
        assert "display_collection_name" not in ent

    def test_default_display_appends_clone(self):
        spec = _spec()
        retarget_spec(spec, new_schema="cwx_TicketClone")
        assert spec["entities"][0]["display_name"] == "Project (Clone)"

    def test_attributes_optionsets_views_untouched(self):
        spec = _spec()
        retarget_spec(spec, new_schema="cwx_TicketClone")
        ent = spec["entities"][0]
        assert ent["attributes"][0]["schema_name"] == "new_Code"
        assert ent["attributes"][1]["kind"] == "lookup"
        assert ent["attributes"][1]["target_entity"] == "account"
        assert ent["attributes"][1]["schema_name"] == "new_AccountId"
        assert ent["primary_attr"]["schema_name"] == "new_Name"
        assert ent["views"][0]["columns"][0]["name"] == "new_name"
        assert spec["optionsets"][0]["name"] == "new_status"

    def test_does_not_invent_a_relationships_key(self):
        spec = _spec()
        retarget_spec(spec, new_schema="cwx_TicketClone")
        assert "relationships" not in spec["entities"][0]
