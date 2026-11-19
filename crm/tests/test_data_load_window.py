"""CLI tests for `crm data load-window begin` and `end` against the fake backend."""

# pyright: basic
from __future__ import annotations

import json
from unittest.mock import patch
from urllib.parse import unquote

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import load_window as lw
from crm.utils.d365_backend import D365Error

_ORG = "0a0a0a0a-1111-2222-3333-444444444444"
_OTHER_ORG = "0b0b0b0b-1111-2222-3333-444444444444"
_STEP = "11111111-0000-0000-0000-000000000001"
_WF = "22222222-0000-0000-0000-000000000002"
_RULE = "33333333-0000-0000-0000-000000000003"

_OTC = {"account": 1, "contact": 2}
_STEP_ROW = {"sdkmessageprocessingstepid": _STEP, "name": "Contoso.Plugins: Create of account"}
_WF_ROW = {"workflowid": _WF, "name": "Notify owner", "primaryentity": "account"}
_RULE_ROW = {
    "duplicateruleid": _RULE,
    "name": "Accounts with the same name",
    "baseentityname": "account",
}


def _make(make_fake_backend, *, org=_ORG, current=None, step_rows=None, **kwargs):
    """A fake org with one custom step, one workflow and one published rule on account.

    ``current`` maps a component id to its live statecode for `end`'s reads.
    """
    current = current or {}
    steps = [dict(_STEP_ROW)] if step_rows is None else step_rows

    def _get(path):
        if path == "WhoAmI":
            return {"OrganizationId": org, "UserId": "u"}
        if path == "sdkmessageprocessingsteps":
            return {"value": steps}
        if path.startswith("EntityDefinitions(LogicalName="):
            return {"ObjectTypeCode": _OTC[path.split("'")[1]]}
        for gid in (_STEP, _WF, _RULE):
            if path.endswith(f"({gid})"):
                return {"statecode": current[gid]}
        return {"value": []}

    def _get_collection(path):
        if path == "workflows":
            return [dict(_WF_ROW)]
        if path == "duplicaterules":
            return [dict(_RULE_ROW)]
        if path == "sdkmessageprocessingsteps":  # set_step_state's id lookup
            return [{"sdkmessageprocessingstepid": _STEP, "stage": 40, "_sdkmessageid_value": "m"}]
        return None  # EntityDefinitions: the fake's standard definitions

    responses = {"get": _get, "get_collection": _get_collection}
    responses.update(kwargs.pop("responses", {}))
    backend = make_fake_backend(responses=responses, **kwargs)
    backend.poll_async_operation = lambda *_a, **_k: {"statecode": 3, "statuscode": 30}
    return backend


def _run(*args):
    return CliRunner().invoke(cli, ["--json", *args])


def _writes(backend):
    return [(v, p, k) for v, p, k in backend.calls if v in ("patch", "post", "put", "delete")]


def _state(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _by_kind(components):
    return {c["kind"]: c for c in components}


# ── begin ────────────────────────────────────────────────────────────────────


def test_begin_turns_off_each_component_and_records_it(tmp_path, make_fake_backend, inject_backend):
    b = inject_backend(_make(make_fake_backend))
    state_file = tmp_path / "window.json"

    result = _run("data", "load-window", "begin", str(state_file), "--table", "account", "--yes")

    assert result.exit_code == 0, result.output
    writes = _writes(b)
    assert ("patch", f"sdkmessageprocessingsteps({_STEP})") in [(v, p) for v, p, _ in writes]
    assert ("patch", f"workflows({_WF})") in [(v, p) for v, p, _ in writes]
    assert ("post", "UnpublishDuplicateRule") in [(v, p) for v, p, _ in writes]
    step_body = next(
        k["json_body"] for v, p, k in writes if p == f"sdkmessageprocessingsteps({_STEP})"
    )
    assert step_body == {"statecode": 1, "statuscode": 2}
    wf_body = next(k["json_body"] for v, p, k in writes if p == f"workflows({_WF})")
    assert wf_body == {"statecode": 0, "statuscode": 1}

    state = _state(state_file)
    assert state["org"]["organization_id"] == _ORG
    assert state["org"]["url"] == b.profile.url
    assert state["tables"] == ["account"]
    kinds = _by_kind(state["components"])
    assert set(kinds) == {"plugin_step", "workflow", "duplicate_rule"}
    assert all(c["outcome"] == "turned_off" for c in state["components"])
    assert kinds["workflow"]["name"] == "Notify owner"
    assert kinds["plugin_step"]["prior_state"] == "enabled"

    data = json.loads(result.output)["data"]
    assert data["counts"]["turned_off"] == 3
    assert data["counts"]["failed"] == 0


def test_begin_writes_the_state_file_before_its_first_write(
    tmp_path, make_fake_backend, inject_backend
):
    state_file = tmp_path / "window.json"
    seen: list[dict] = []

    def _patch(path):
        seen.append(_state(state_file))

    b = inject_backend(_make(make_fake_backend, responses={"patch": _patch}))

    result = _run("data", "load-window", "begin", str(state_file), "--table", "account", "--yes")

    assert result.exit_code == 0, result.output
    first = seen[0]
    # The file names every component before the first change lands, so an
    # interrupted begin still leaves end something to restore from.
    assert len(first["components"]) == 3
    assert _by_kind(first["components"])["plugin_step"]["outcome"] == "attempted"
    assert b.count("patch") == 2


def test_begin_selects_only_custom_steps_classic_workflows_and_published_rules(
    tmp_path, make_fake_backend, inject_backend
):
    b = inject_backend(_make(make_fake_backend))

    result = _run(
        "data",
        "load-window",
        "begin",
        str(tmp_path / "w.json"),
        "--table",
        "account,contact",
        "--yes",
    )

    assert result.exit_code == 0, result.output
    fetch = next(
        k["params"]["fetchXml"]
        for v, p, k in b.calls
        if p == "sdkmessageprocessingsteps" and v == "get"
    )
    # Enabled steps on the named tables whose assembly is not Microsoft-signed.
    assert '<condition attribute="statecode" operator="eq" value="0"' in fetch
    assert '<condition attribute="publickeytoken" operator="ne" value="31bf3856ad364e35"' in fetch
    # primaryobjecttypecode filters on the object type code, not the logical name.
    assert "<value>1</value>" in fetch and "<value>2</value>" in fetch
    wf_filter = next(k["params"]["$filter"] for v, p, k in b.calls if p == "workflows")
    # Category 0 is the classic workflow: business rules (2) and BPFs (4) never match.
    assert (
        "category eq 0" in wf_filter and "type eq 1" in wf_filter and "statecode eq 1" in wf_filter
    )
    assert "primaryentity eq 'contact'" in wf_filter
    rule_filter = next(k["params"]["$filter"] for v, p, k in b.calls if p == "duplicaterules")
    assert "statecode eq 1" in rule_filter and "baseentityname eq 'account'" in rule_filter


def test_begin_records_a_failure_and_exits_1(tmp_path, make_fake_backend, inject_backend):
    def _patch(path):
        if path.startswith("workflows("):
            raise D365Error("workflow is locked", status=400)

    inject_backend(_make(make_fake_backend, responses={"patch": _patch}))
    state_file = tmp_path / "window.json"

    result = _run("data", "load-window", "begin", str(state_file), "--table", "account", "--yes")

    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert "Notify owner" in env["error"] and "workflow is locked" in env["error"]
    kinds = _by_kind(_state(state_file)["components"])
    assert kinds["workflow"]["outcome"] == "failed"
    assert "workflow is locked" in kinds["workflow"]["error"]
    assert kinds["plugin_step"]["outcome"] == "turned_off"


def test_begin_refuses_an_existing_state_file(tmp_path, make_fake_backend, inject_backend):
    b = inject_backend(_make(make_fake_backend))
    state_file = tmp_path / "window.json"
    state_file.write_text("{}", encoding="utf-8")

    result = _run("data", "load-window", "begin", str(state_file), "--table", "account", "--yes")

    assert result.exit_code == 1
    assert "already exists" in json.loads(result.output)["error"]
    assert state_file.read_text(encoding="utf-8") == "{}"
    assert _writes(b) == []


def test_begin_refuses_an_unknown_table(tmp_path, make_fake_backend, inject_backend):
    b = inject_backend(_make(make_fake_backend))
    state_file = tmp_path / "window.json"

    result = _run("data", "load-window", "begin", str(state_file), "--table", "acount", "--yes")

    assert result.exit_code == 1
    assert "acount" in json.loads(result.output)["error"]
    assert not state_file.exists()
    assert _writes(b) == []


def test_begin_dry_run_lists_and_writes_nothing(tmp_path, make_fake_backend, inject_backend):
    b = inject_backend(_make(make_fake_backend, dry_run=True))
    state_file = tmp_path / "window.json"

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "--dry-run",
            "data",
            "load-window",
            "begin",
            str(state_file),
            "--table",
            "account",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["_dry_run"] is True
    assert {c["kind"] for c in data["components"]} == {"plugin_step", "workflow", "duplicate_rule"}
    assert all(c["outcome"] == "would_turn_off" for c in data["components"])
    assert not state_file.exists()
    assert _writes(b) == []


def test_begin_is_refused_on_a_read_only_profile(tmp_path, make_fake_backend, inject_backend):
    b = _make(make_fake_backend)
    b.read_only = True
    inject_backend(b)
    state_file = tmp_path / "window.json"

    result = _run("data", "load-window", "begin", str(state_file), "--table", "account", "--yes")

    assert result.exit_code == 1
    assert "read-only" in json.loads(result.output)["error"]
    assert not state_file.exists()
    assert _writes(b) == []


def test_begin_without_yes_off_a_tty_is_refused(tmp_path, make_fake_backend, inject_backend):
    b = inject_backend(_make(make_fake_backend))

    result = _run("data", "load-window", "begin", str(tmp_path / "w.json"), "--table", "account")

    assert result.exit_code == 1
    assert "--yes" in json.loads(result.output)["error"]
    assert b.calls == []


# ── end ──────────────────────────────────────────────────────────────────────


def _write_state(path, outcomes, *, org=_ORG):
    components = [
        {"kind": "plugin_step", "id": _STEP, "name": _STEP_ROW["name"], "table": "account"},
        {"kind": "workflow", "id": _WF, "name": _WF_ROW["name"], "table": "account"},
        {"kind": "duplicate_rule", "id": _RULE, "name": _RULE_ROW["name"], "table": "account"},
    ]
    for c in components:
        c["outcome"] = outcomes[c["kind"]]
    path.write_text(
        json.dumps(
            {
                "format": "crm-load-window",
                "version": 1,
                "org": {"organization_id": org, "url": "https://crm.contoso.local/contoso"},
                "begun_at": "2026-11-19T10:00:00+00:00",
                "tables": ["account"],
                "components": components,
            }
        ),
        encoding="utf-8",
    )


_ALL_OFF = {"plugin_step": "turned_off", "workflow": "turned_off", "duplicate_rule": "turned_off"}
_ALL_INACTIVE = {_STEP: 1, _WF: 0, _RULE: 0}


def test_end_restores_what_begin_turned_off(tmp_path, make_fake_backend, inject_backend):
    b = inject_backend(_make(make_fake_backend, current=_ALL_INACTIVE))
    state_file = tmp_path / "window.json"
    _write_state(state_file, _ALL_OFF)

    result = _run("data", "load-window", "end", str(state_file), "--yes")

    assert result.exit_code == 0, result.output
    writes = [(v, unquote(p)) for v, p, _ in _writes(b)]
    assert ("patch", f"sdkmessageprocessingsteps({_STEP})") in writes
    assert ("patch", f"workflows({_WF})") in writes
    assert (
        "post",
        f"duplicaterules({_RULE})/Microsoft.Dynamics.CRM.PublishDuplicateRule",
    ) in writes
    step_body = next(
        k["json_body"] for v, p, k in _writes(b) if p == f"sdkmessageprocessingsteps({_STEP})"
    )
    assert step_body == {"statecode": 0, "statuscode": 1}
    data = json.loads(result.output)["data"]
    assert data["counts"] == {"restored": 3, "already_active": 0, "failed": 0, "not_turned_off": 0}


def test_end_leaves_alone_what_begin_did_not_turn_off(tmp_path, make_fake_backend, inject_backend):
    b = inject_backend(_make(make_fake_backend, current=_ALL_INACTIVE))
    state_file = tmp_path / "window.json"
    # The step was never reached, the workflow failed to turn off, and the rule's
    # write was attempted when begin stopped: only the rule is restored.
    _write_state(
        state_file, {"plugin_step": "pending", "workflow": "failed", "duplicate_rule": "attempted"}
    )

    result = _run("data", "load-window", "end", str(state_file), "--yes")

    assert result.exit_code == 0, result.output
    assert [p for _, p, _ in _writes(b)] == [
        f"duplicaterules({_RULE})/Microsoft.Dynamics.CRM.PublishDuplicateRule"
    ]
    counts = json.loads(result.output)["data"]["counts"]
    assert counts["restored"] == 1 and counts["not_turned_off"] == 2


def test_end_skips_components_already_active(tmp_path, make_fake_backend, inject_backend):
    b = inject_backend(_make(make_fake_backend, current={_STEP: 0, _WF: 1, _RULE: 1}))
    state_file = tmp_path / "window.json"
    _write_state(state_file, _ALL_OFF)

    result = _run("data", "load-window", "end", str(state_file), "--yes")

    assert result.exit_code == 0, result.output
    assert _writes(b) == []
    assert json.loads(result.output)["data"]["counts"]["already_active"] == 3


def test_end_names_each_failure_exits_1_and_a_rerun_finishes(
    tmp_path, make_fake_backend, inject_backend
):
    def _patch(path):
        if path.startswith("workflows("):
            raise D365Error("workflow is locked", status=400)

    inject_backend(_make(make_fake_backend, current=_ALL_INACTIVE, responses={"patch": _patch}))
    state_file = tmp_path / "window.json"
    _write_state(state_file, _ALL_OFF)

    result = _run("data", "load-window", "end", str(state_file), "--yes")

    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert "Notify owner" in env["error"] and "workflow is locked" in env["error"]
    assert env["data"]["counts"]["failed"] == 1

    # Re-run once the lock is gone: the two restored ones read active and are
    # skipped, the workflow is restored.
    b = inject_backend(_make(make_fake_backend, current={_STEP: 0, _WF: 0, _RULE: 1}))
    result = _run("data", "load-window", "end", str(state_file), "--yes")
    assert result.exit_code == 0, result.output
    assert [p for _, p, _ in _writes(b)] == [f"workflows({_WF})"]
    counts = json.loads(result.output)["data"]["counts"]
    assert counts["restored"] == 1 and counts["already_active"] == 2


def test_end_refuses_a_state_file_from_another_org(tmp_path, make_fake_backend, inject_backend):
    b = inject_backend(_make(make_fake_backend, org=_OTHER_ORG, current=_ALL_INACTIVE))
    state_file = tmp_path / "window.json"
    _write_state(state_file, _ALL_OFF)

    result = _run("data", "load-window", "end", str(state_file), "--yes")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert _ORG in error and _OTHER_ORG in error
    assert _writes(b) == []


def test_end_dry_run_lists_and_writes_nothing(tmp_path, make_fake_backend, inject_backend):
    b = inject_backend(_make(make_fake_backend, dry_run=True, current={_STEP: 1, _WF: 1, _RULE: 0}))
    state_file = tmp_path / "window.json"
    _write_state(state_file, _ALL_OFF)

    result = CliRunner().invoke(
        cli, ["--json", "--dry-run", "data", "load-window", "end", str(state_file)]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["_dry_run"] is True
    outcomes = {c["kind"]: c["outcome"] for c in data["components"]}
    assert outcomes == {
        "plugin_step": "would_restore",
        "workflow": "already_active",
        "duplicate_rule": "would_restore",
    }
    assert _writes(b) == []


def test_end_is_refused_on_a_read_only_profile(tmp_path, make_fake_backend, inject_backend):
    b = _make(make_fake_backend, current=_ALL_INACTIVE)
    b.read_only = True
    inject_backend(b)
    state_file = tmp_path / "window.json"
    _write_state(state_file, _ALL_OFF)

    result = _run("data", "load-window", "end", str(state_file), "--yes")

    assert result.exit_code == 1
    assert "read-only" in json.loads(result.output)["error"]
    assert _writes(b) == []


@pytest.mark.parametrize("content", ["not json", json.dumps({"format": "something-else"})])
def test_end_refuses_a_file_that_is_not_a_state_file(
    tmp_path, make_fake_backend, inject_backend, content
):
    b = inject_backend(_make(make_fake_backend, current=_ALL_INACTIVE))
    state_file = tmp_path / "window.json"
    state_file.write_text(content, encoding="utf-8")

    result = _run("data", "load-window", "end", str(state_file), "--yes")

    assert result.exit_code == 1
    assert "load-window state file" in json.loads(result.output)["error"]
    assert _writes(b) == []


def test_begin_refuses_a_table_whose_object_type_code_is_unreadable(
    tmp_path, make_fake_backend, inject_backend
):
    def _get(path):
        if path.startswith("EntityDefinitions(LogicalName="):
            return {}
        return {"OrganizationId": _ORG} if path == "WhoAmI" else {"value": []}

    b = inject_backend(_make(make_fake_backend, responses={"get": _get}))
    state_file = tmp_path / "window.json"

    result = _run("data", "load-window", "begin", str(state_file), "--table", "account", "--yes")

    assert result.exit_code == 1
    assert "account" in json.loads(result.output)["error"]
    assert not state_file.exists()
    assert _writes(b) == []


def test_begin_names_what_is_off_when_the_state_file_cannot_be_written(
    tmp_path, make_fake_backend, inject_backend
):
    real = lw.atomic_write_json
    calls = []

    def _write(path, data):
        calls.append(path)
        if len(calls) == 3:  # the record after the first component's write
            raise OSError("No space left on device")
        real(path, data)

    inject_backend(_make(make_fake_backend))
    state_file = tmp_path / "window.json"

    with patch.object(lw, "atomic_write_json", _write):
        result = _run(
            "data", "load-window", "begin", str(state_file), "--table", "account", "--yes"
        )

    assert result.exit_code == 1, result.output
    error = json.loads(result.output)["error"]
    assert "No space left on device" in error
    assert _STEP_ROW["name"] in error and str(state_file) in error


def test_end_refuses_when_no_org_id_is_recorded_or_connected(
    tmp_path, make_fake_backend, inject_backend
):
    b = inject_backend(_make(make_fake_backend, org="", current=_ALL_INACTIVE))
    state_file = tmp_path / "window.json"
    _write_state(state_file, _ALL_OFF, org="")

    result = _run("data", "load-window", "end", str(state_file), "--yes")

    assert result.exit_code == 1
    assert _writes(b) == []


@pytest.mark.parametrize(
    "state",
    [
        {"format": "crm-load-window", "org": {"organization_id": _ORG}, "components": "x"},
        {"format": "crm-load-window", "org": "x", "components": []},
        {
            "format": "crm-load-window",
            "org": {"organization_id": _ORG},
            "components": [{"kind": "plugin", "id": _STEP, "outcome": "turned_off"}],
        },
        {
            "format": "crm-load-window",
            "org": {"organization_id": _ORG},
            "components": [{"kind": "workflow", "outcome": "turned_off"}],
        },
        {
            "format": "crm-load-window",
            "org": {"organization_id": _ORG},
            "components": [{"kind": ["workflow"], "id": _WF, "outcome": "turned_off"}],
        },
        {
            "format": "crm-load-window",
            "org": {"organization_id": _ORG},
            "components": [{"kind": "workflow", "id": _WF, "outcome": {}}],
        },
    ],
)
def test_end_refuses_a_malformed_state_file(tmp_path, make_fake_backend, inject_backend, state):
    b = inject_backend(_make(make_fake_backend, current=_ALL_INACTIVE))
    state_file = tmp_path / "window.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")

    result = _run("data", "load-window", "end", str(state_file), "--yes")

    assert result.exit_code == 1
    assert "load-window state file" in json.loads(result.output)["error"]
    assert _writes(b) == []
