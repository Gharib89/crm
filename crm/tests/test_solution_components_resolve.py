# pyright: basic
"""Tests for `solution components --resolve` (#913).

Behavior-label map (pure), objectid → name resolution (batched by-id GETs + a
bulk attribute pull), and the CLI enrichment wiring. GUIDs are generic
placeholders (no real org names).
"""

from __future__ import annotations

import json

import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution as sol_mod

_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_D = "dddddddd-dddd-dddd-dddd-dddddddddddd"

_BATCH_HDR = {"Content-Type": "multipart/mixed; boundary=batchresp"}


def _batch_response(parts: list[tuple[int, dict]]) -> bytes:
    """Build a multipart/mixed $batch body, one application/http part per (status, body)."""
    chunks = [
        "Content-Type: application/http\r\n"
        "Content-Transfer-Encoding: binary\r\n"
        "\r\n"
        f"HTTP/1.1 {status} {'OK' if 200 <= status < 300 else 'Not Found'}\r\n"
        "Content-Type: application/json\r\n"
        "\r\n" + json.dumps(body)
        for status, body in parts
    ]
    text = "--batchresp\r\n" + "\r\n--batchresp\r\n".join(chunks) + "\r\n--batchresp--\r\n"
    return text.encode("utf-8")


class TestRootBehaviorName:
    def test_known_labels(self):
        assert sol_mod.root_behavior_name(0) == "whole-entity (all subcomponents)"
        assert sol_mod.root_behavior_name(1) == "shell (no subcomponents)"
        assert sol_mod.root_behavior_name(2) == "shell + metadata"

    def test_none_stays_none(self):
        # Non-root components carry no behavior; the label must not fabricate one.
        assert sol_mod.root_behavior_name(None) is None

    def test_unknown_falls_back_to_raw_int(self):
        assert sol_mod.root_behavior_name(99) == "99"


class TestResolveComponentNames:
    def test_directly_resolvable_types_batched_in_one_request(self, backend):
        items = [
            {"componenttype": 1, "objectid": _A, "rootcomponentbehavior": 0},
            {"componenttype": 60, "objectid": _B, "rootcomponentbehavior": 0},
            {"componenttype": 26, "objectid": _C, "rootcomponentbehavior": None},
            {"componenttype": 29, "objectid": _D, "rootcomponentbehavior": None},
        ]
        with requests_mock.Mocker() as m:
            post = m.post(
                backend.url_for("$batch"),
                content=_batch_response(
                    [
                        (200, {"LogicalName": "account"}),
                        (200, {"name": "Account Main Form", "objecttypecode": "account"}),
                        (200, {"name": "Active Accounts", "returnedtypecode": "account"}),
                        (200, {"name": "Auto-assign WF", "primaryentity": "account"}),
                    ]
                ),
                headers=_BATCH_HDR,
                status_code=200,
            )
            resolved = sol_mod.resolve_component_names(backend, items)

        # One $batch POST for all four — not one GET per objectid.
        assert post.call_count == 1
        assert resolved[(1, _A)] == {"name": "account"}
        assert resolved[(60, _B)] == {"name": "Account Main Form", "entity": "account"}
        assert resolved[(26, _C)] == {"name": "Active Accounts", "entity": "account"}
        assert resolved[(29, _D)] == {"name": "Auto-assign WF", "entity": "account"}

    def test_unresolvable_objectid_falls_back_gracefully(self, backend):
        items = [{"componenttype": 1, "objectid": _A, "rootcomponentbehavior": 0}]
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("$batch"),
                content=_batch_response([(404, {"error": {"message": "Does Not Exist"}})]),
                headers=_BATCH_HDR,
                status_code=200,
            )
            resolved = sol_mod.resolve_component_names(backend, items)
        # No crash; the errored id is simply absent (caller keeps the raw GUID).
        assert (1, _A) not in resolved

    def test_unknown_component_type_is_skipped(self, backend):
        # A type with no resolve spec issues no request at all.
        items = [{"componenttype": 44, "objectid": _A, "rootcomponentbehavior": 0}]
        with requests_mock.Mocker() as m:
            post = m.post(backend.url_for("$batch"), status_code=200)
            resolved = sol_mod.resolve_component_names(backend, items)
        assert post.call_count == 0
        assert resolved == {}

    def test_attributes_resolved_via_bulk_metadata_pull(self, backend):
        items = [{"componenttype": 2, "objectid": _A, "rootcomponentbehavior": None}]
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions"),
                json={
                    "value": [
                        {
                            "LogicalName": "account",
                            "Attributes": [{"LogicalName": "new_score", "MetadataId": _A}],
                        }
                    ]
                },
            )
            resolved = sol_mod.resolve_component_names(backend, items)
        assert resolved[(2, _A)] == {"name": "new_score", "entity": "account"}

    def test_attribute_bulk_failure_falls_back_gracefully(self, backend):
        # A failed metadata pull must not abort --resolve — the attribute just
        # stays unresolved. 500 fails fast (backend retries only 502/503/504).
        items = [{"componenttype": 2, "objectid": _A, "rootcomponentbehavior": None}]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("EntityDefinitions"), status_code=500, json={"error": {}})
            resolved = sol_mod.resolve_component_names(backend, items)
        assert (2, _A) not in resolved


# ── CLI wiring ───────────────────────────────────────────────────────────────

_ITEMS = [
    {"componenttype": 1, "objectid": _A, "rootcomponentbehavior": 0},
    {"componenttype": 60, "objectid": _B, "rootcomponentbehavior": 1},
]
_RESOLVED = {
    (1, _A): {"name": "account"},
    (60, _B): {"name": "Account Main Form", "entity": "account"},
}


class TestComponentsResolveCli:
    def _invoke(self, *args):
        return CliRunner().invoke(cli, ["--json", "solution", "components", "Contoso", *args])

    def _patch(self, monkeypatch, *, expect_resolve=True):
        monkeypatch.setattr(
            "crm.core.solution.solution_components", lambda backend, name: list(_ITEMS)
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        if expect_resolve:
            monkeypatch.setattr(
                "crm.core.solution.resolve_component_names",
                lambda backend, items: dict(_RESOLVED),
            )
        else:
            # Prove --resolve absent never triggers resolution.
            def _boom(backend, items):
                raise AssertionError("resolve_component_names called without --resolve")

            monkeypatch.setattr("crm.core.solution.resolve_component_names", _boom)

    def test_resolve_json_enriches_each_row(self, monkeypatch):
        self._patch(monkeypatch)
        result = self._invoke("--resolve")
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)["data"]
        assert rows[0]["componenttypename"] == "entity"
        assert rows[0]["rootcomponentbehaviorname"] == "whole-entity (all subcomponents)"
        assert rows[0]["name"] == "account"
        assert "entity" not in rows[0]  # entity component: no separate parent
        assert rows[1]["name"] == "Account Main Form"
        assert rows[1]["entity"] == "account"
        assert rows[1]["rootcomponentbehaviorname"] == "shell (no subcomponents)"

    def test_without_resolve_output_has_no_resolve_keys(self, monkeypatch):
        self._patch(monkeypatch, expect_resolve=False)
        result = self._invoke()
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)["data"]
        # Existing contract: componenttypename only; no name/behavior-label keys.
        assert set(rows[0]) == {
            "componenttype",
            "objectid",
            "rootcomponentbehavior",
            "componenttypename",
        }

    def test_resolve_with_save_is_usage_error(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, expect_resolve=False)
        result = self._invoke("--resolve", "--save", str(tmp_path / "out.json"))
        assert result.exit_code == 2  # Click UsageError
