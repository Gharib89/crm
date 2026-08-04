"""Unit tests for crm.core.solution add/remove_solution_component (#71).

The AddSolutionComponent / RemoveSolutionComponent Web API action contract
(unbound actions; add takes ComponentId, remove takes a SolutionComponent
entity reference — see #181) and the `componenttype` global optionset integer
values are verified against the Dataverse Web API docs.
All HTTP is mocked via requests_mock; no live D365 server.
"""

# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution as sol_mod
from crm.utils.d365_backend import D365Error

_SOL_ID = "22222222-2222-2222-2222-222222222222"
_COMP_ID = "33333333-3333-3333-3333-333333333333"


def _posts(m):
    return [r for r in m.request_history if r.method == "POST"]


def _mock_solution(m, backend, *, managed: bool):
    """Mock the force-real solution_info GET with the given managed flag."""
    m.get(
        backend.url_for("solutions"),
        json={"value": [{"solutionid": _SOL_ID, "uniquename": "CRMWorx", "ismanaged": managed}]},
    )


class TestResolveComponentType:
    def test_int_passthrough(self):
        assert sol_mod.resolve_component_type(61) == 61

    def test_numeric_string_passthrough(self):
        assert sol_mod.resolve_component_type("61") == 61

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("entity", 1),
            ("attribute", 2),
            ("relationship", 3),  # canonical: base relationship (not 10)
            ("optionset", 9),
            ("entityrelationship", 10),  # canonical: entity relationship
            ("webresource", 61),
        ],
    )
    def test_canonical_names(self, name, expected):
        assert sol_mod.resolve_component_type(name) == expected

    @pytest.mark.parametrize(
        "variant", ["WebResource", "web resource", "web-resource", "WEB_RESOURCE", " webresource "]
    )
    def test_name_normalized(self, variant):
        assert sol_mod.resolve_component_type(variant) == 61

    def test_unknown_name_raises(self):
        with pytest.raises(D365Error, match="component type"):
            sol_mod.resolve_component_type("nonsense")


class TestComponentTypeName:
    """Reverse resolver: componenttype int → friendly name (#627)."""

    @pytest.mark.parametrize(
        "code,expected",
        [
            (1, "entity"),
            (20, "role"),
            (29, "workflow"),
            (152, "sla"),  # #627: Customer-Service family, previously unmapped
            (153, "slaitem"),
        ],
    )
    def test_known_codes(self, code, expected):
        assert sol_mod.component_type_name(code) == expected

    def test_unknown_code_falls_back_to_str(self):
        # An unmapped code must not crash — it renders as its integer's string form.
        assert sol_mod.component_type_name(99999) == "99999"


class TestAddSolutionComponent:
    def test_posts_expected_body(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            m.post(backend.url_for("AddSolutionComponent"), status_code=204)
            out = sol_mod.add_solution_component(
                backend, solution="CRMWorx", component_id=_COMP_ID, component_type=1
            )
        assert out["added"] is True
        assert out["solution"] == "CRMWorx"
        body = _posts(m)[0].json()
        assert body["ComponentId"] == _COMP_ID
        assert body["ComponentType"] == 1
        assert body["SolutionUniqueName"] == "CRMWorx"
        assert body["AddRequiredComponents"] is True  # default on
        assert body["DoNotIncludeSubcomponents"] is False  # default include

    def test_flags_flip_body(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            m.post(backend.url_for("AddSolutionComponent"), status_code=204)
            sol_mod.add_solution_component(
                backend,
                solution="CRMWorx",
                component_id=_COMP_ID,
                component_type=1,  # entity — the one type --no-subcomponents is legal on
                add_required_components=False,
                do_not_include_subcomponents=True,
            )
        body = _posts(m)[0].json()
        assert body["AddRequiredComponents"] is False
        assert body["DoNotIncludeSubcomponents"] is True

    def test_no_subcomponents_rejected_on_non_entity(self, backend):
        # DoNotIncludeSubcomponents is accepted by the platform only on Entity
        # (type 1) roots; requesting it for a non-entity component is rejected
        # client-side, before any HTTP (#941).
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            m.post(backend.url_for("AddSolutionComponent"), status_code=204)
            with pytest.raises(D365Error, match="entity"):
                sol_mod.add_solution_component(
                    backend,
                    solution="CRMWorx",
                    component_id=_COMP_ID,
                    component_type=61,
                    do_not_include_subcomponents=True,
                )
            assert m.request_history == []

    def test_refuses_managed_no_post(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=True)
            with pytest.raises(D365Error, match="managed"):
                sol_mod.add_solution_component(
                    backend, solution="CRMWorx", component_id=_COMP_ID, component_type=1
                )
            assert _posts(m) == []

    def test_dry_run_previews_no_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, dry_backend, managed=False)
            out = sol_mod.add_solution_component(
                dry_backend, solution="CRMWorx", component_id=_COMP_ID, component_type=1
            )
        assert out["_dry_run"] is True
        assert "added" not in out
        assert _posts(m) == []


class TestRemoveSolutionComponent:
    def test_posts_expected_body(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            m.post(backend.url_for("RemoveSolutionComponent"), status_code=204)
            out = sol_mod.remove_solution_component(
                backend, solution="CRMWorx", component_id=_COMP_ID, component_type=61
            )
        assert out["removed"] is True
        assert out["solution"] == "CRMWorx"
        body = _posts(m)[0].json()
        # RemoveSolutionComponent takes a SolutionComponent entity reference —
        # the component objectid goes in as solutioncomponentid (live-verified
        # contract, #181); there is no ComponentId parameter on this action.
        assert body["SolutionComponent"] == {
            "solutioncomponentid": _COMP_ID,
            "@odata.type": "Microsoft.Dynamics.CRM.solutioncomponent",
        }
        assert body["ComponentType"] == 61
        assert body["SolutionUniqueName"] == "CRMWorx"
        assert "ComponentId" not in body

    def test_refuses_managed_no_post(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=True)
            with pytest.raises(D365Error, match="managed"):
                sol_mod.remove_solution_component(
                    backend, solution="CRMWorx", component_id=_COMP_ID, component_type=61
                )
            assert _posts(m) == []

    def test_dry_run_previews_no_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, dry_backend, managed=False)
            out = sol_mod.remove_solution_component(
                dry_backend, solution="CRMWorx", component_id=_COMP_ID, component_type=61
            )
        assert out["_dry_run"] is True
        assert "removed" not in out
        assert _posts(m) == []


# ── command wiring + exit codes ──────────────────────────────────────────────


_GUID = "33333333-3333-3333-3333-333333333333"


class TestComponentCommands:
    def test_add_component_resolves_name_and_wires_core(self, monkeypatch):
        captured = {}

        def fake(backend, **kw):
            captured.update(kw)
            return {"added": True, "solution": kw["solution"]}

        monkeypatch.setattr("crm.core.solution.add_solution_component", fake)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "webresource",
                "--id",
                _GUID,
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured["solution"] == "CRMWorx"
        assert captured["component_id"] == _GUID
        assert captured["component_type"] == 61  # resolved name -> int
        assert captured["add_required_components"] is True
        assert captured["do_not_include_subcomponents"] is False

    def test_add_component_int_type_and_flags(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "crm.core.solution.add_solution_component",
            lambda backend, **kw: captured.update(kw) or {"added": True},
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        # --type 1 (entity, given as an int) is the one type for which
        # --no-subcomponents is legal (#941), so it exercises both flag flips.
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "1",
                "--id",
                _GUID,
                "--no-add-required",
                "--no-subcomponents",
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured["component_type"] == 1
        assert captured["add_required_components"] is False
        assert captured["do_not_include_subcomponents"] is True

    def test_add_component_no_subcomponents_non_entity_singular_errors(self, monkeypatch):
        # Singular --type <non-entity> --no-subcomponents fails client-side (no
        # core mock → the real guard runs), before any request (#941).
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "webresource",
                "--id",
                _GUID,
                "--no-subcomponents",
            ],
        )
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "entity" in payload["error"].lower()

    def test_add_component_no_subcomponents_non_entity_validates_before_backend(self, monkeypatch):
        # Interactive singular non-entity --no-subcomponents (cascade path active,
        # no --json/--yes) must be rejected before _cascade_gate touches the
        # backend — validate untrusted flag input before ctx.backend() (#941).
        calls = {"backend": 0}

        def _backend(self):
            calls["backend"] += 1
            return object()

        monkeypatch.setattr("crm.cli.CLIContext.backend", _backend)
        monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
        result = CliRunner().invoke(
            cli,
            [
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "webresource",
                "--id",
                _GUID,
                "--no-subcomponents",
            ],
        )
        assert result.exit_code != 0, result.output
        assert "entity" in result.output.lower()
        assert calls["backend"] == 0, "backend was called before client-side validation"

    def test_add_component_no_subcomponents_non_entity_multi_id_errors(self, monkeypatch):
        # Repeated --id (a batch) sharing a non-entity --type with --no-subcomponents
        # is rejected by the batch core guard, before the $batch is issued (#941).
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "61",
                "--id",
                _GUID,
                "--id",
                _COMP_ID_2,
                "--no-subcomponents",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "entity" in result.output.lower()

    def test_add_component_help_documents_entity_only_no_subcomponents(self):
        # The --no-subcomponents help text names the entity-only restriction (#941);
        # "entity" appears in the add-component help only because of that note.
        result = CliRunner().invoke(cli, ["solution", "add-component", "--help"])
        assert result.exit_code == 0
        assert "entity" in result.output.lower()

    def test_add_component_entity_emits_required_components_note(self, monkeypatch):
        monkeypatch.setattr(
            "crm.core.solution.add_solution_component", lambda backend, **kw: {"added": True}
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "entity",
                "--id",
                _GUID,
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "required components" in payload["meta"]["note"]

    @pytest.mark.parametrize(
        "argv_extra",
        [
            ["--type", "entity", "--no-add-required"],  # entity but required-add off
            ["--type", "webresource"],  # non-entity type
        ],
    )
    def test_add_component_no_note_when_not_entity_with_required(self, monkeypatch, argv_extra):
        monkeypatch.setattr(
            "crm.core.solution.add_solution_component", lambda backend, **kw: {"added": True}
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--id",
                _GUID,
                *argv_extra,
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "note" not in payload.get("meta", {})

    def test_add_component_unknown_type_exit_1(self, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "nonsense",
                "--id",
                _GUID,
            ],
        )
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["ok"] is False

    def test_remove_component_no_yes_non_tty_aborts(self, monkeypatch):
        called = {"core": False}
        monkeypatch.setattr(
            "crm.core.solution.remove_solution_component",
            lambda backend, **kw: called.update(core=True) or {"removed": True},
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "remove-component",
                "--solution",
                "CRMWorx",
                "--type",
                "61",
                "--id",
                _GUID,
            ],
            input="\n",
        )
        assert result.exit_code == 1, result.output
        assert "Pass --yes to continue" in result.output
        assert called["core"] is False  # gated before the core call

    def test_remove_component_yes_wires_core(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "crm.core.solution.remove_solution_component",
            lambda backend, **kw: captured.update(kw) or {"removed": True},
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "remove-component",
                "--solution",
                "CRMWorx",
                "--type",
                "webresource",
                "--id",
                _GUID,
                "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured["component_type"] == 61
        assert captured["component_id"] == _GUID


# ── batch add/remove (issue #914) ────────────────────────────────────────────

_COMP_ID_2 = "44444444-4444-4444-4444-444444444444"
_BATCH_HDR = {"Content-Type": "multipart/mixed; boundary=batchresp"}


def _batch_posts(m):
    return [r for r in m.request_history if r.method == "POST" and r.url.endswith("$batch")]


def _changeset_response(parts, *, boundary="batchresp", cs="csresp") -> bytes:
    """Build a transactional `$batch` response: one changeset wrapping `parts`.

    `parts` is a list of ``(content_id, status, error_msg)``. A 2xx status is a
    bare ``204 No Content``; anything else carries an OData error body. Omitting an
    op's content_id from `parts` (as a rolled-back changeset does — the server
    returns only the offending subrequest) leaves that op to backfill.
    """
    inner = []
    for cid, st, err in parts:
        if 200 <= st < 300:
            inner.append(
                "Content-Type: application/http\r\n"
                "Content-Transfer-Encoding: binary\r\n"
                f"Content-ID: {cid}\r\n"
                "\r\n"
                f"HTTP/1.1 {st} No Content\r\n"
                "\r\n"
            )
        else:
            inner.append(
                "Content-Type: application/http\r\n"
                "Content-Transfer-Encoding: binary\r\n"
                f"Content-ID: {cid}\r\n"
                "\r\n"
                f"HTTP/1.1 {st} Error\r\n"
                "Content-Type: application/json\r\n"
                "\r\n" + json.dumps({"error": {"code": "0x0", "message": err or "failed"}})
            )
    cs_body = f"--{cs}\r\n" + f"\r\n--{cs}\r\n".join(inner) + f"\r\n--{cs}--\r\n"
    changeset_part = f"Content-Type: multipart/mixed; boundary={cs}\r\n\r\n" + cs_body
    text = f"--{boundary}\r\n" + changeset_part + f"\r\n--{boundary}--\r\n"
    return text.encode("utf-8")


class TestAddSolutionComponentsBatch:
    def test_posts_one_transactional_batch(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            m.post(
                backend.url_for("$batch"),
                content=_changeset_response([(1, 204, None), (2, 204, None)]),
                headers=_BATCH_HDR,
                status_code=200,
            )
            out = sol_mod.add_solution_components(
                backend,
                solution="CRMWorx",
                components=[
                    {"component_id": _COMP_ID, "component_type": 1},
                    {"component_id": _COMP_ID_2, "component_type": 61},
                ],
            )
        # One $batch POST, wrapped in a changeset (transactional).
        posts = _batch_posts(m)
        assert len(posts) == 1
        assert "multipart/mixed; boundary=changeset" in posts[0].text
        assert posts[0].text.count("AddSolutionComponent") == 2
        assert out["solution"] == "CRMWorx"
        assert out["count"] == 2
        assert out["succeeded"] == 2
        assert out["failed"] == 0
        assert out["rolled_back"] is False
        assert [r["id"] for r in out["added"]] == [_COMP_ID, _COMP_ID_2]
        assert all(r["ok"] for r in out["added"])
        assert out["added"][0]["type"] == 1
        assert out["added"][1]["type"] == 61

    def test_rollback_on_partial_failure(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            # Only op #2 comes back (400); op #1 is rolled back → backfilled.
            m.post(
                backend.url_for("$batch"),
                content=_changeset_response([(2, 400, "duplicate component")]),
                headers=_BATCH_HDR,
                status_code=200,
            )
            out = sol_mod.add_solution_components(
                backend,
                solution="CRMWorx",
                components=[
                    {"component_id": _COMP_ID, "component_type": 1},
                    {"component_id": _COMP_ID_2, "component_type": 61},
                ],
            )
        assert out["failed"] >= 1
        assert out["succeeded"] < out["count"]
        assert out["rolled_back"] is True
        # The offending row carries the server error message.
        bad = out["added"][1]
        assert bad["ok"] is False
        assert bad["status"] == 400
        assert "duplicate component" in (bad["error"] or "")

    def test_refuses_managed_no_batch(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=True)
            with pytest.raises(D365Error, match="managed"):
                sol_mod.add_solution_components(
                    backend,
                    solution="CRMWorx",
                    components=[{"component_id": _COMP_ID, "component_type": 1}],
                )
            assert _batch_posts(m) == []

    def test_dry_run_previews_no_batch(self, dry_backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, dry_backend, managed=False)
            out = sol_mod.add_solution_components(
                dry_backend,
                solution="CRMWorx",
                components=[
                    {"component_id": _COMP_ID, "component_type": 1},
                    {"component_id": _COMP_ID_2, "component_type": 61},
                ],
            )
        assert out["_dry_run"] is True
        assert "added" not in out
        assert out["would_add"] == [
            {"type": 1, "id": _COMP_ID},
            {"type": 61, "id": _COMP_ID_2},
        ]
        assert _batch_posts(m) == []

    def test_per_row_flags_flip_body(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            m.post(
                backend.url_for("$batch"),
                content=_changeset_response([(1, 204, None), (2, 204, None)]),
                headers=_BATCH_HDR,
                status_code=200,
            )
            sol_mod.add_solution_components(
                backend,
                solution="CRMWorx",
                components=[
                    {
                        "component_id": _COMP_ID,
                        "component_type": 1,
                        "add_required_components": False,
                        "do_not_include_subcomponents": True,
                    },
                    {"component_id": _COMP_ID_2, "component_type": 61},
                ],
            )
        text = _batch_posts(m)[0].text
        assert '"AddRequiredComponents": false' in text
        assert '"DoNotIncludeSubcomponents": true' in text
        # The second row keeps the defaults.
        assert '"AddRequiredComponents": true' in text

    def test_no_subcomponents_rejected_on_non_entity_row(self, backend):
        # A DoNotIncludeSubcomponents:true on a non-entity row is rejected before
        # the transactional $batch is issued — the platform would otherwise 500
        # and roll the whole batch back. The error names every offending row (#941).
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            with pytest.raises(D365Error, match="type 61"):
                sol_mod.add_solution_components(
                    backend,
                    solution="CRMWorx",
                    components=[
                        {
                            "component_id": _COMP_ID,
                            "component_type": 1,  # entity — legal
                            "do_not_include_subcomponents": True,
                        },
                        {
                            "component_id": _COMP_ID_2,
                            "component_type": 61,  # webresource — illegal
                            "do_not_include_subcomponents": True,
                        },
                    ],
                )
            assert m.request_history == []


class TestRemoveSolutionComponentsBatch:
    def test_posts_one_transactional_batch(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            m.post(
                backend.url_for("$batch"),
                content=_changeset_response([(1, 204, None), (2, 204, None)]),
                headers=_BATCH_HDR,
                status_code=200,
            )
            out = sol_mod.remove_solution_components(
                backend,
                solution="CRMWorx",
                components=[
                    {"component_id": _COMP_ID, "component_type": 61},
                    {"component_id": _COMP_ID_2, "component_type": 1},
                ],
            )
        posts = _batch_posts(m)
        assert len(posts) == 1
        assert posts[0].text.count("RemoveSolutionComponent") == 2
        # RemoveSolutionComponent uses the SolutionComponent entity reference.
        assert "solutioncomponentid" in posts[0].text
        assert out["count"] == 2
        assert out["failed"] == 0
        assert [r["id"] for r in out["removed"]] == [_COMP_ID, _COMP_ID_2]

    def test_dry_run_previews_no_batch(self, dry_backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, dry_backend, managed=False)
            out = sol_mod.remove_solution_components(
                dry_backend,
                solution="CRMWorx",
                components=[{"component_id": _COMP_ID, "component_type": 61}],
            )
        assert out["_dry_run"] is True
        assert "removed" not in out
        assert out["would_remove"] == [{"type": 61, "id": _COMP_ID}]
        assert _batch_posts(m) == []


class TestParseComponentsFile:
    def test_valid_add_file(self, tmp_path):
        p = tmp_path / "comps.json"
        p.write_text(
            json.dumps(
                [
                    {"type": "entity", "id": _COMP_ID, "no_add_required": True},
                    {"type": 61, "id": _COMP_ID_2},
                ]
            ),
            encoding="utf-8",
        )
        rows = sol_mod.parse_components_file(p, for_add=True)
        assert rows[0] == {
            "component_id": _COMP_ID,
            "component_type": 1,
            "add_required_components": False,
            "do_not_include_subcomponents": False,
        }
        assert rows[1]["component_type"] == 61
        assert rows[1]["add_required_components"] is True

    def test_valid_remove_file(self, tmp_path):
        p = tmp_path / "comps.json"
        p.write_text(json.dumps([{"type": "webresource", "id": _COMP_ID}]), encoding="utf-8")
        rows = sol_mod.parse_components_file(p, for_add=False)
        assert rows == [{"component_id": _COMP_ID, "component_type": 61}]

    def test_cli_flag_defaults_apply_to_rows_without_override(self, tmp_path):
        # The command-level --no-add-required/--no-subcomponents are the batch-wide
        # default; a row that carries no override inherits them (#914 spec).
        p = tmp_path / "comps.json"
        p.write_text(
            json.dumps(
                [
                    {"type": "entity", "id": _COMP_ID},  # no override → inherits defaults
                    {"type": 61, "id": _COMP_ID_2, "no_add_required": False},  # overrides
                ]
            ),
            encoding="utf-8",
        )
        rows = sol_mod.parse_components_file(
            p, for_add=True, default_no_add_required=True, default_no_subcomponents=True
        )
        # Row 0 is an entity → inherits the no_subcomponents default as-is.
        assert rows[0]["add_required_components"] is False
        assert rows[0]["do_not_include_subcomponents"] is True
        # Row 1's explicit key wins over the default; the inherited
        # no_subcomponents default is filtered off because it is a non-entity row
        # (DoNotIncludeSubcomponents is entity-only, #941).
        assert rows[1]["add_required_components"] is True
        assert rows[1]["do_not_include_subcomponents"] is False

    def test_batch_default_no_subcomponents_filters_to_entity_rows(self, tmp_path):
        # The batch-wide --no-subcomponents default applies DoNotIncludeSubcomponents
        # only to entity (type 1) rows; every non-entity row gets False, because the
        # platform 500s if it is sent for a non-entity root (#941).
        p = tmp_path / "comps.json"
        p.write_text(
            json.dumps(
                [
                    {"type": "entity", "id": _COMP_ID},
                    {"type": 61, "id": _COMP_ID_2},  # webresource
                    {"type": 29, "id": _GUID},  # workflow (BPF) — non-entity
                ]
            ),
            encoding="utf-8",
        )
        rows = sol_mod.parse_components_file(p, for_add=True, default_no_subcomponents=True)
        assert rows[0]["do_not_include_subcomponents"] is True  # entity
        assert rows[1]["do_not_include_subcomponents"] is False  # non-entity, filtered
        assert rows[2]["do_not_include_subcomponents"] is False  # non-entity, filtered

    def test_explicit_no_subcomponents_on_non_entity_row_flows_through(self, tmp_path):
        # An explicit per-row no_subcomponents:true on a non-entity is NOT silently
        # dropped at parse time (that would hide the platform restriction); it keeps
        # True here and the add core rejects it client-side (#941).
        p = tmp_path / "comps.json"
        p.write_text(
            json.dumps([{"type": 61, "id": _COMP_ID, "no_subcomponents": True}]),
            encoding="utf-8",
        )
        rows = sol_mod.parse_components_file(p, for_add=True)
        assert rows[0]["do_not_include_subcomponents"] is True

    def test_unknown_key_rejected(self, tmp_path):
        # The issue example carried a `behavior` key; the core has no
        # RootComponentBehavior parameter, so it is rejected, not silently dropped.
        p = tmp_path / "comps.json"
        p.write_text(
            json.dumps([{"type": "entity", "id": _COMP_ID, "behavior": 1}]), encoding="utf-8"
        )
        with pytest.raises(D365Error, match="behavior"):
            sol_mod.parse_components_file(p, for_add=True)

    def test_flag_keys_rejected_on_remove(self, tmp_path):
        p = tmp_path / "comps.json"
        p.write_text(
            json.dumps([{"type": "entity", "id": _COMP_ID, "no_add_required": True}]),
            encoding="utf-8",
        )
        with pytest.raises(D365Error, match="no_add_required"):
            sol_mod.parse_components_file(p, for_add=False)

    def test_missing_id_rejected(self, tmp_path):
        p = tmp_path / "comps.json"
        p.write_text(json.dumps([{"type": "entity"}]), encoding="utf-8")
        with pytest.raises(D365Error):
            sol_mod.parse_components_file(p, for_add=True)

    def test_non_list_root_rejected(self, tmp_path):
        p = tmp_path / "comps.json"
        p.write_text(json.dumps({"type": "entity", "id": _COMP_ID}), encoding="utf-8")
        with pytest.raises(D365Error, match="list"):
            sol_mod.parse_components_file(p, for_add=True)

    def test_empty_list_rejected(self, tmp_path):
        p = tmp_path / "comps.json"
        p.write_text(json.dumps([]), encoding="utf-8")
        with pytest.raises(D365Error):
            sol_mod.parse_components_file(p, for_add=True)


class TestBatchComponentCommands:
    def test_add_multiple_ids_batches(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "crm.core.solution.add_solution_components",
            lambda backend, **kw: (
                captured.update(kw)
                or {"solution": kw["solution"], "added": [], "count": 2, "failed": 0}
            ),
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "webresource",
                "--id",
                _GUID,
                "--id",
                _COMP_ID_2,
            ],
        )
        assert result.exit_code == 0, result.output
        comps = captured["components"]
        assert [c["component_id"] for c in comps] == [_GUID, _COMP_ID_2]
        assert all(c["component_type"] == 61 for c in comps)

    def test_add_components_file_batches(self, monkeypatch, tmp_path):
        p = tmp_path / "comps.json"
        p.write_text(
            json.dumps([{"type": "entity", "id": _GUID}, {"type": 61, "id": _COMP_ID_2}]),
            encoding="utf-8",
        )
        captured = {}
        monkeypatch.setattr(
            "crm.core.solution.add_solution_components",
            lambda backend, **kw: (
                captured.update(kw)
                or {"solution": kw["solution"], "added": [], "count": 2, "failed": 0}
            ),
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--components-file",
                str(p),
            ],
        )
        assert result.exit_code == 0, result.output
        comps = captured["components"]
        assert [c["component_type"] for c in comps] == [1, 61]

    def test_components_file_inherits_cli_flags(self, monkeypatch, tmp_path):
        # `add-component --components-file f --no-add-required` must apply the flag
        # to file rows that carry no per-row override (#914 spec).
        p = tmp_path / "comps.json"
        p.write_text(json.dumps([{"type": "entity", "id": _GUID}]), encoding="utf-8")
        captured = {}
        monkeypatch.setattr(
            "crm.core.solution.add_solution_components",
            lambda backend, **kw: (
                captured.update(kw)
                or {"solution": kw["solution"], "added": [], "count": 1, "failed": 0}
            ),
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--components-file",
                str(p),
                "--no-add-required",
                "--no-subcomponents",
            ],
        )
        assert result.exit_code == 0, result.output
        row = captured["components"][0]
        assert row["add_required_components"] is False
        assert row["do_not_include_subcomponents"] is True

    def test_add_no_id_no_file_usage_error(self, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            ["--json", "solution", "add-component", "--solution", "CRMWorx", "--type", "entity"],
        )
        assert result.exit_code == 2, result.output

    def test_add_type_without_id_usage_error(self, monkeypatch, tmp_path):
        # --type is meaningless alongside --components-file (rows carry their own).
        p = tmp_path / "comps.json"
        p.write_text(json.dumps([{"type": "entity", "id": _GUID}]), encoding="utf-8")
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "entity",
                "--components-file",
                str(p),
            ],
        )
        assert result.exit_code == 2, result.output

    def test_batch_partial_failure_exit_1(self, monkeypatch):
        monkeypatch.setattr(
            "crm.core.solution.add_solution_components",
            lambda backend, **kw: {
                "solution": "CRMWorx",
                "added": [{"type": 1, "id": _GUID, "ok": False, "status": 400, "error": "boom"}],
                "count": 2,
                "succeeded": 1,
                "failed": 1,
                "rolled_back": True,
            },
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "entity",
                "--id",
                _GUID,
                "--id",
                _COMP_ID_2,
            ],
        )
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "rolled back" in payload["error"]

    def test_remove_multiple_ids_batches(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "crm.core.solution.remove_solution_components",
            lambda backend, **kw: (
                captured.update(kw)
                or {"solution": kw["solution"], "removed": [], "count": 2, "failed": 0}
            ),
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "remove-component",
                "--solution",
                "CRMWorx",
                "--type",
                "webresource",
                "--id",
                _GUID,
                "--id",
                _COMP_ID_2,
                "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        assert len(captured["components"]) == 2

    def test_remove_merges_file_and_id_rows(self, monkeypatch, tmp_path):
        # --components-file AND --id together must merge, not drop --id (mirrors add).
        p = tmp_path / "comps.json"
        p.write_text(json.dumps([{"type": "entity", "id": _GUID}]), encoding="utf-8")
        captured = {}
        monkeypatch.setattr(
            "crm.core.solution.remove_solution_components",
            lambda backend, **kw: (
                captured.update(kw)
                or {"solution": kw["solution"], "removed": [], "count": 2, "failed": 0}
            ),
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "remove-component",
                "--solution",
                "CRMWorx",
                "--components-file",
                str(p),
                "--type",
                "webresource",
                "--id",
                _COMP_ID_2,
                "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        comps = captured["components"]
        assert [c["component_type"] for c in comps] == [1, 61]
        assert [c["component_id"] for c in comps] == [_GUID, _COMP_ID_2]

    def test_single_id_still_uses_singular_core(self, monkeypatch):
        # The single-`--id` path must not route through the batch core.
        singular = {"hit": False}
        batch = {"hit": False}
        monkeypatch.setattr(
            "crm.core.solution.add_solution_component",
            lambda backend, **kw: singular.update(hit=True) or {"added": True},
        )
        monkeypatch.setattr(
            "crm.core.solution.add_solution_components",
            lambda backend, **kw: batch.update(hit=True) or {},
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "add-component",
                "--solution",
                "CRMWorx",
                "--type",
                "webresource",
                "--id",
                _GUID,
            ],
        )
        assert result.exit_code == 0, result.output
        assert singular["hit"] is True
        assert batch["hit"] is False
