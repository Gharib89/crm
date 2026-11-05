"""`crm inventory extract --with-usage`: a `usage` object on the rows that have a signal (#1095).

Runs over the synthetic CRM 2011 org of test_legacy_inventory.py. The source's `probe_usage`
seam is stubbed, so these tests drive the stage (what it reads, how it attaches, how a failure
and a resume go); the probes themselves are covered by test_usage*.py.
"""

# pyright: basic, reportArgumentType=false
from __future__ import annotations

import copy
import json

import pytest

from crm.core import inventory, inventory_diff, inventory_extract
from crm.core.inventory import InventoryError
from crm.core.usage import Usage
from crm.tests.test_legacy_inventory import (
    FakeClient,
    _id,
    _manifest,
    reads,  # noqa: F401  (a fixture, used through pytestmark)
)
from crm.utils.d365_backend import D365Error

pytestmark = pytest.mark.usefixtures("reads")

AS_OF = "2026-11-05T00:00:00Z"
USED = Usage("used", "record_presence", 7, AS_OF)
UNUSED = Usage("unused", "record_presence", 0, AS_OF)
USAGE_KINDS = {"entity", "plugin_assembly", "plugin_type", "step", "process"}


def _answers() -> dict:
    return {
        "entity": {"entity": {"account": USED, "new_project": UNUSED}},
        "plugins": {
            "plugin_assembly": {_id(20): USED, _id(21): UNUSED},
            "plugin_type": {_id(30): USED},
            "step": {_id(40): Usage("unknown", "plugin_step_runs", None, AS_OF, "synchronous")},
        },
        "process": {"process": {_id(50): UNUSED}},
    }


class UsageSource(inventory_extract.LegacySource):
    """A CRM 2011 source whose usage probes answer from *answers* (an exception is raised)."""

    def __init__(self, client, answers: dict, calls: list | None = None):
        super().__init__(client)
        self.answers = answers
        self.calls = [] if calls is None else calls
        self.seen: dict = {}

    def probe_usage(self, group, *, names, time_cap, progress):
        self.calls.append(group)
        self.seen[group] = {"names": set(names), "time_cap": time_cap}
        answer = self.answers[group]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _extract(source, out, **kwargs):
    return inventory_extract.extract(
        source,
        out,
        cli_version="9.9.9",
        include_system=True,
        now=lambda: "2026-11-04T10:00:00Z",
        **kwargs,
    )


def _usage_of(data: dict, kind: str) -> dict:
    return {r["key"]: r.get("usage") for r in data["records"] if r["kind"] == kind}


def test_a_default_extract_has_no_usage_and_no_scope_flag(tmp_path):
    source = UsageSource(FakeClient(), _answers())
    _extract(source, tmp_path / "inv")
    data = _manifest(tmp_path / "inv")
    assert source.calls == []
    assert all("usage" not in r for r in data["records"])
    assert "usage_read" not in data["scope"]


def test_with_usage_adds_usage_only_to_the_kinds_that_have_a_signal(tmp_path):
    source = UsageSource(FakeClient(), _answers())
    summary = _extract(source, tmp_path / "inv", with_usage=True)
    data = _manifest(tmp_path / "inv")
    assert summary.complete and data["scope"]["usage_read"] is True
    assert {r["kind"] for r in data["records"] if "usage" in r} == USAGE_KINDS
    assert _usage_of(data, "entity")["account"]["state"] == "used"
    assert _usage_of(data, "entity")["new_project"] == UNUSED.to_dict()
    assert {u["state"] for u in _usage_of(data, "plugin_assembly").values()} == {"used", "unused"}
    assert _usage_of(data, "step").popitem()[1]["reason"] == "synchronous"


def test_a_row_the_probe_did_not_answer_for_is_unknown_never_unused(tmp_path):
    answers = _answers()
    answers["entity"] = {"entity": {"account": USED}}
    _extract(UsageSource(FakeClient(), answers), tmp_path / "inv", with_usage=True)
    got = _usage_of(_manifest(tmp_path / "inv"), "entity")["new_project"]
    assert got["state"] == "unknown" and got["reason"]


def test_the_entity_probe_reads_only_the_kept_entities_and_takes_the_time_cap(tmp_path):
    source = UsageSource(FakeClient(), _answers())
    _extract(source, tmp_path / "inv", with_usage=True, usage_time_cap=9.0)
    assert source.seen["entity"]["names"] == {"account", "new_project", "new_project_account"}
    assert source.seen["plugins"]["time_cap"] == 9.0


def test_a_failed_probe_is_unknown_with_a_gap_and_the_run_goes_on(tmp_path):
    answers = _answers()
    answers["plugins"] = D365Error("the async job read was refused")
    out = tmp_path / "inv"
    summary = _extract(UsageSource(FakeClient(), answers), out, with_usage=True)
    data = _manifest(out)
    assert not summary.complete and any("usage" in e for e in summary.errors)
    assert (out / ".checkpoint").exists()
    steps = _usage_of(data, "step")
    assert {u["state"] for u in steps.values()} == {"unknown"}
    assert (
        "usage probe failed: the async job read was refused" in next(iter(steps.values()))["reason"]
    )
    assert _usage_of(data, "entity")["account"]["state"] == "used"  # the other groups ran
    assert any("usage" in (g["description"] or "") for g in data["gaps"])


def test_a_connection_wide_failure_stops_the_run(tmp_path):
    answers = _answers()
    answers["entity"] = D365Error("unauthorized", status=401)
    with pytest.raises(D365Error):
        _extract(UsageSource(FakeClient(), answers), tmp_path / "inv", with_usage=True)


def test_a_resume_skips_the_finished_groups_and_retries_the_failed_one(tmp_path):
    out = tmp_path / "inv"
    answers = _answers()
    answers["plugins"] = D365Error("boom")
    first = UsageSource(FakeClient(), answers)
    _extract(first, out, with_usage=True)
    assert first.calls == ["entity", "plugins", "process"]
    second = UsageSource(FakeClient(), _answers())
    summary = _extract(second, out, with_usage=True)
    assert second.calls == ["plugins"]
    assert summary.complete and summary.resumed
    assert {u["state"] for u in _usage_of(_manifest(out), "step").values()} == {"unknown"}
    assert _usage_of(_manifest(out), "entity")["account"]["state"] == "used"


# ── the manifest ─────────────────────────────────────────────────────────


def _usage_manifest(tmp_path, name="inv", answers=None):
    out = tmp_path / name
    _extract(UsageSource(FakeClient(), answers or _answers()), out, with_usage=True)
    return out


def test_a_manifest_without_usage_loads_with_no_usage_and_not_read(tmp_path):
    out = tmp_path / "plain"
    _extract(UsageSource(FakeClient(), _answers()), out)
    inv = inventory.read_inventory(out)
    assert inv.scope.usage_read is False
    assert all(r.usage is None for r in inv.records)  # not read, which is not `unused`


def test_the_reader_round_trips_usage(tmp_path):
    inv = inventory.read_inventory(_usage_manifest(tmp_path))
    assert inv.scope.usage_read is True
    by_key = {(r.kind, r.key): r.usage for r in inv.records}
    assert by_key[("entity", "new_project")] == UNUSED.to_dict()


@pytest.mark.parametrize(
    "bad",
    [
        {"state": "dead", "signal": "s", "value": None, "as_of": AS_OF, "reason": None},
        {"state": "used", "signal": "s", "value": "7", "as_of": AS_OF, "reason": None},
        {"state": "used", "signal": "s", "value": None, "reason": None},
        "used",
    ],
)
def test_the_reader_refuses_a_malformed_usage(tmp_path, bad):
    out = _usage_manifest(tmp_path)
    data = json.loads((out / "manifest.json").read_text())
    next(r for r in data["records"] if r["kind"] == "entity")["usage"] = bad
    with pytest.raises(InventoryError, match="usage"):
        inventory.parse_manifest(data)


def test_the_schema_accepts_usage_and_the_flag(tmp_path):
    _manifest(_usage_manifest(tmp_path))  # validates against the v2 JSON schema


# ── diff ─────────────────────────────────────────────────────────────────


def _diff(a, b):
    return inventory_diff.diff_inventories(*inventory_diff.read_pair(a, b)).to_dict()


def _verdicts(diff: dict, kind: str) -> dict:
    return {e["key"]: e for e in diff["entries"] if e["kind"] == kind}


def test_diff_ignores_value_and_as_of_drift(tmp_path):
    a = _usage_manifest(tmp_path, "a")
    drift = copy.deepcopy(_answers())
    drift["entity"]["entity"]["account"] = Usage(
        "used", "record_presence", 99, "2026-12-01T00:00:00Z"
    )
    b = _usage_manifest(tmp_path, "b", drift)
    assert _verdicts(_diff(a, b), "entity")["account"]["verdict"] == "unchanged"


def test_diff_reports_a_usage_state_change(tmp_path):
    a = _usage_manifest(tmp_path, "a")
    changed = copy.deepcopy(_answers())
    changed["entity"]["entity"]["new_project"] = USED
    b = _usage_manifest(tmp_path, "b", changed)
    entry = _verdicts(_diff(a, b), "entity")["new_project"]
    assert entry["verdict"] == "changed"
    assert entry["fields"] == [{"name": "usage.state", "a": "unused", "b": "used"}]


def test_diff_of_a_plain_and_a_usage_manifest_compares_no_usage_and_says_so(tmp_path):
    plain, other = tmp_path / "plain", tmp_path / "other"
    _extract(UsageSource(FakeClient(), _answers()), plain)
    _extract(UsageSource(FakeClient(), _answers()), other)
    baseline = _diff(plain, other)
    diff = _diff(plain, _usage_manifest(tmp_path))
    assert [e["verdict"] for e in diff["entries"]] == [e["verdict"] for e in baseline["entries"]]
    assert diff["header"]["usage_compared"] is False
    assert diff["header"]["same_scope"] is True


def test_diff_of_two_usage_manifests_says_usage_was_compared(tmp_path):
    diff = _diff(_usage_manifest(tmp_path, "a"), _usage_manifest(tmp_path, "b"))
    assert diff["header"]["usage_compared"] is True


# ── the group probes behind the sources ──────────────────────────────────


def test_the_v9_plugins_group_answers_assemblies_types_and_steps(make_fake_backend):
    from crm.core import usage
    from crm.tests.test_usage_plugins import _v9_org

    org = _v9_org()
    inner = org["get_collection"]
    org["get_collection"] = lambda path: (
        [{"pluginassemblyid": a} for a in ("a1", "a2", "a3")]
        if path == "pluginassemblies"
        else inner(path)
    )
    got = usage.probe_v9_group(make_fake_backend(responses=org), "plugins", ())
    assert set(got) == {"plugin_assembly", "plugin_type", "step"}
    assert got["step"]["s1"].state == "used" and got["plugin_assembly"]["a3"].state == "unused"
    assert got["plugin_type"]["t1"].state == "used"


def test_the_legacy_plugins_group_has_no_trace_log_so_a_sync_step_is_unknown():
    import requests_mock

    from crm.core import usage
    from crm.core.legacy import LegacyClient
    from crm.tests.test_usage_plugins import _ENDPOINT, _URL, _legacy_org
    from crm.utils.d365_backend import ConnectionProfile

    profile = ConnectionProfile(
        name="legacy", url=_URL, domain="CONTOSO", username="alice", platform="crm2011"
    )
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, text=_legacy_org()[0])
        got = usage.probe_legacy_group(LegacyClient(profile, "pw"), "plugins", ())
    assert got["step"]["s3"].state == "unknown"
    assert "trace log" in (got["step"]["s3"].reason or "")
    assert got["plugin_assembly"]["a3"].state == "unused"


# ── the command ──────────────────────────────────────────────────────────


def _run(monkeypatch, tmp_path, *flags):
    from click.testing import CliRunner

    from crm.cli import cli
    from crm.core import session as session_mod
    from crm.utils.d365_backend import ConnectionProfile

    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    import crm.core.keyring_store as ks

    monkeypatch.setattr(ks, "is_available", lambda: False)
    session_mod.save_profile(
        ConnectionProfile(
            name="legacy",
            url="https://crm.contoso.local/contoso",
            domain="CONTOSO",
            username="alice",
            platform="crm2011",
        )
    )
    session_mod.save_profile_secret_plaintext("legacy", "pw")
    monkeypatch.setattr("crm.cli.CLIContext.legacy_client", lambda self: FakeClient())
    seen: dict = {}

    def probe(self, group, *, names, time_cap, progress):
        seen[group] = time_cap
        return _answers()[group]

    monkeypatch.setattr(inventory_extract.LegacySource, "probe_usage", probe)
    out = tmp_path / "inv"
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "legacy", "inventory", "extract", "--out", str(out), *flags],
    )
    return result, out, seen


def test_with_usage_on_the_command_records_usage_and_takes_the_time_cap(monkeypatch, tmp_path):
    result, out, seen = _run(
        monkeypatch, tmp_path, "--with-usage", "--usage-time-cap", "30", "--include-system"
    )
    assert result.exit_code == 0, result.output
    assert seen["plugins"] == 30.0
    assert inventory.read_inventory(out).scope.usage_read is True


def test_the_command_reads_no_usage_by_default(monkeypatch, tmp_path):
    result, out, seen = _run(monkeypatch, tmp_path)
    assert result.exit_code == 0, result.output
    assert seen == {} and inventory.read_inventory(out).scope.usage_read is False


def test_a_time_cap_without_with_usage_is_a_usage_error(monkeypatch, tmp_path):
    result, _, _ = _run(monkeypatch, tmp_path, "--usage-time-cap", "30")
    assert result.exit_code == 2 and "--with-usage" in result.output


def test_diff_command_warns_when_only_one_side_read_usage(tmp_path):
    from click.testing import CliRunner

    from crm.cli import cli

    plain = tmp_path / "plain"
    _extract(UsageSource(FakeClient(), _answers()), plain)
    usage = _usage_manifest(tmp_path)
    mixed = CliRunner().invoke(cli, ["inventory", "diff", str(plain), str(usage)])
    assert mixed.exit_code == 0, mixed.output
    assert "usage is not compared" in mixed.output
    same = CliRunner().invoke(cli, ["inventory", "diff", str(usage), str(usage)])
    assert "usage is not compared" not in same.output


def test_diff_command_json_carries_the_mixed_usage_warning(tmp_path):
    from click.testing import CliRunner

    from crm.cli import cli

    plain = tmp_path / "plain"
    _extract(UsageSource(FakeClient(), _answers()), plain)
    usage = _usage_manifest(tmp_path)
    mixed = json.loads(
        CliRunner().invoke(cli, ["--json", "inventory", "diff", str(plain), str(usage)]).output
    )
    assert "usage is not compared" in mixed["meta"]["warning"]
    same = json.loads(
        CliRunner().invoke(cli, ["--json", "inventory", "diff", str(usage), str(usage)]).output
    )
    assert "warning" not in same.get("meta", {})


def test_a_resume_probes_again_when_the_scope_gained_entities(tmp_path):
    out = tmp_path / "inv"
    answers = _answers()
    answers["plugins"] = D365Error("boom")
    first = UsageSource(FakeClient(), answers)
    _extract(first, out, with_usage=True)
    cp_unit = out / ".checkpoint"
    assert cp_unit.exists()
    # The saved entity answer covered fewer names than the resumed scope holds.
    (f,) = cp_unit.rglob("usage/entity.json")
    saved = json.loads(f.read_text(encoding="utf-8"))
    saved["names"] = ["account"]
    f.write_text(json.dumps(saved), encoding="utf-8")
    second = UsageSource(FakeClient(), _answers())
    _extract(second, out, with_usage=True)
    assert "entity" in second.calls
    assert _usage_of(_manifest(out), "entity")["new_project"]["state"] != "unknown"


def test_same_scope_ignores_the_order_of_the_solutions(tmp_path):
    from dataclasses import replace

    inv = inventory.read_inventory(_usage_manifest(tmp_path))
    one = replace(inv, scope=replace(inv.scope, mode="solutions", solutions=("a", "b")))
    two = replace(inv, scope=replace(inv.scope, mode="solutions", solutions=("b", "a")))
    assert inventory_diff._same_scope(one, two)
