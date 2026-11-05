"""Workflow and process usage and `--in-use` on `workflow list` and `legacy components` (#1094)."""

# pyright: basic, reportArgumentType=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import usage
from crm.core.legacy import LegacyClient
from crm.core.usage import Process, ProcessEvidence
from crm.tests.test_usage_plugins import (
    _CRM,
    _ENDPOINT,
    _URL,
    _XML,
    _by_state,
    _rm,
    crm_home,  # noqa: F401  (a fixture)
)
from crm.tests.test_usage_plugins import (
    _legacy_org as _plugin_org,
)
from crm.utils.d365_backend import ConnectionProfile, D365Error
from crm.utils.safe_xml import fromstring

AS_OF = "2026-11-05T00:00:00Z"
WORKFLOW, DIALOG, RULE, ACTION, BPF, FLOW = 0, 1, 2, 3, 4, 5


def _p(id, *, category=WORKFLOW, draft=False, sync=False, autodelete=False):
    return Process(id, category, draft, sync, autodelete)


def _state(p, ev=None):
    return usage.process_usage([p], ev or ProcessEvidence(), clock=lambda: AS_OF)[p.id]


# ── rules ────────────────────────────────────────────────────────────────


def test_a_draft_is_unused_even_with_a_job_row():
    got = _state(_p("w", draft=True), ProcessEvidence(jobs=frozenset({"w"})))
    assert got == usage.Usage("unused", "process_runs", None, AS_OF, "not activated")


def test_an_activated_process_with_a_job_row_is_used():
    got = _state(_p("w"), ProcessEvidence(jobs=frozenset({"w"})))
    assert (got.state, got.signal) == ("used", "async_job")


def test_a_process_session_row_makes_a_process_used():
    got = _state(_p("w", category=DIALOG), ProcessEvidence(sessions=frozenset({"w"})))
    assert (got.state, got.signal) == ("used", "process_session")


@pytest.mark.parametrize(
    ("process", "why"),
    [
        (_p("w", sync=True), "real-time"),
        (_p("w", category=RULE), "business rule"),
        (_p("w", category=ACTION), "action"),
        (_p("w", category=BPF), "business process flow"),
        (_p("w", category=FLOW), "cloud flow"),
        (_p("w", autodelete=True), "deleted"),
        (_p("w"), "purged"),
    ],
)
def test_an_activated_process_with_no_row_is_unknown_and_names_why(process, why):
    got = _state(process)
    assert got.state == "unknown" and why in (got.reason or "")


def test_a_read_gap_is_named_in_the_reason_and_never_makes_a_row_unused():
    got = _state(_p("w"), ProcessEvidence(gap="async job read failed: boom"))
    assert got.state == "unknown" and "async job read failed" in (got.reason or "")


# ── v9 ───────────────────────────────────────────────────────────────────

_DEFS = [
    {"workflowid": "d-draft", "name": "Draft", "category": 0, "statecode": 0, "mode": 0},
    {"workflowid": "d-used", "name": "Used", "category": 0, "statecode": 1, "mode": 0},
    {"workflowid": "d-rule", "name": "Rule", "category": 2, "statecode": 1, "mode": 1},
    {"workflowid": "d-sync", "name": "Sync", "category": 0, "statecode": 1, "mode": 1},
    {
        "workflowid": "d-auto",
        "name": "Auto",
        "category": 0,
        "statecode": 1,
        "asyncautodelete": True,
    },
    {"workflowid": "d-unset", "name": "No state", "category": 0},
]
_ACTS = [{"workflowid": "a-used", "_parentworkflowid_value": "d-used"}]


class _Org:
    """A v9 org that tells definitions from activations by the `type` filter."""

    def __init__(self, jobs=("a-used",), sessions=(), job_error=None):
        self.calls, self._jobs, self._sessions, self._job_error = [], jobs, sessions, job_error

    def get_collection(self, path, params=None):
        self.calls.append((path, params))
        assert path == "workflows"
        return _ACTS if "type eq 2" in params["$filter"] else _DEFS

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "asyncoperations":
            if self._job_error:
                raise self._job_error
            return {"value": [{"step": j, "n": 1} for j in self._jobs]}
        assert path == "processsessions"
        return {"value": [{"step": s, "n": 1} for s in self._sessions]}


def test_v9_a_job_names_the_activation_and_the_definition_it_copies_reads_used():
    got = usage.probe_v9_processes(_Org(), clock=lambda: AS_OF)
    assert {k: v.state for k, v in got.items()} == {
        "d-draft": "unused",
        "d-used": "used",
        "d-rule": "unknown",
        "d-sync": "unknown",
        "d-auto": "unknown",
        "d-unset": "unknown",  # an unread state is never proof of a draft
    }


def test_v9_reads_are_grouped_never_one_per_definition():
    org = _Org()
    usage.probe_v9_processes(org)
    assert [p for p, _ in org.calls].count("asyncoperations") == 1
    assert [p for p, _ in org.calls].count("processsessions") == 1
    (fetch,) = [p["fetchXml"] for path, p in org.calls if path == "asyncoperations"]
    root = fromstring(fetch)
    assert root.find(".//attribute[@groupby='true']").get("name") == "workflowactivationid"
    assert root.find(".//condition").attrib["value"] == "10"


def test_v9_a_session_row_makes_its_process_used():
    got = usage.probe_v9_processes(_Org(jobs=(), sessions=("d-rule",)))
    assert got["d-rule"].state == "used" and got["d-rule"].signal == "process_session"


def test_v9_a_refused_job_aggregate_leaves_definitions_unknown_and_says_why():
    org = _Org(job_error=D365Error("AggregateQueryRecordLimit", status=400))
    got = usage.probe_v9_processes(org)
    assert got["d-used"].state == "unknown"
    assert "async job read failed" in (got["d-used"].reason or "")
    assert got["d-draft"].state == "unused"


def _list(*flags):
    result = CliRunner().invoke(cli, ["--json", "workflow", "list", *flags])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["data"]


@pytest.fixture
def v9(inject_backend, monkeypatch):
    monkeypatch.setenv("CRM_HOME", "/nonexistent-crm-home")
    return inject_backend(_Org())


def test_list_without_the_flag_has_no_usage_and_no_probe(v9):
    rows = _list()
    assert len(rows) == 6 and all("usage" not in r for r in rows)
    assert not any(p in ("asyncoperations", "processsessions") for p, _ in v9.calls)


def test_list_in_use_drops_only_the_unused_rows(v9):
    rows = _list("--in-use")
    assert {r["workflowid"]: r["usage"]["state"] for r in rows} == {
        "d-used": "used",
        "d-rule": "unknown",
        "d-sync": "unknown",
        "d-auto": "unknown",
        "d-unset": "unknown",
    }


def test_list_exclude_unknown_keeps_proven_used_only(v9):
    assert [r["workflowid"] for r in _list("--in-use", "--exclude-unknown")] == ["d-used"]


def test_list_exclude_unknown_alone_is_a_usage_error(v9):
    result = CliRunner().invoke(cli, ["workflow", "list", "--exclude-unknown"])
    assert result.exit_code == 2 and "--in-use" in result.output


def test_list_a_failed_probe_keeps_every_row_labelled_unknown(inject_backend, monkeypatch):
    class Failing(_Org):
        def get_collection(self, path, params=None):
            if "type eq 2" in params["$filter"]:
                raise D365Error("boom", status=500)
            return _DEFS

    monkeypatch.setenv("CRM_HOME", "/nonexistent-crm-home")
    inject_backend(Failing())
    rows = _list("--in-use")
    assert len(rows) == 6 and {r["usage"]["state"] for r in rows} == {"unknown"}
    assert "usage probe failed" in rows[0]["usage"]["reason"]


# ── CRM 2011 ─────────────────────────────────────────────────────────────

_L_DEFS = [
    {"id": "d-draft", "name": "Draft", "category": 0, "type": 1, "statecode": 0},
    {"id": "d-job", "name": "Job", "category": 0, "type": 1, "statecode": 1},
    {"id": "d-session", "name": "Session", "category": 1, "type": 1, "statecode": 1},
    {"id": "d-quiet", "name": "Quiet", "category": 0, "type": 1, "statecode": 1},
    {"id": "d-unset", "name": "No state", "category": 0, "type": 1},
]
_L_ACTS = [{"id": "a-job", "type": 2, "parentworkflowid": ("d-job", "workflow")}]


def _legacy_org(jobs_fault=False, sessions=(), jobs_more=False):
    seen = {"jobs": 0, "queries": []}

    def respond(request, context):
        context.headers.update(_XML)
        query = fromstring(fromstring(request.body).findtext(f".//{{{_CRM}}}Query") or "")
        entity = query[0].get("name")
        text = json.dumps(query[0].attrib) + "".join(
            c.get("value", "") for c in query.iter("condition")
        )
        seen["queries"].append((entity, text))
        if entity == "asyncoperation":
            seen["jobs"] += 1
            if jobs_fault:
                context.status_code = 500
                return "<x/>"
            rows = [{"id": "j1", "workflowactivationid": ("a-job", "workflow")}]
            return _rm(rows, more=jobs_more)
        if entity == "processsession":
            return _rm([{"id": "ps", "processid": (s, "workflow")} for s in sessions])
        if entity == "workflow":
            kind = [c.get("value") for c in query.iter("condition")]
            return _rm(_L_ACTS if "2" in kind else _L_DEFS)
        return _rm([])

    return respond, seen


def _components(*args, org=None):
    respond, _ = org or _legacy_org(sessions=("d-session",))
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, text=respond)
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "legacy", "legacy", "components", *args]
        )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["data"]


def test_legacy_processes_in_use_labels_each_definition(crm_home):  # noqa: F811
    data = _components("processes", "--in-use")
    assert _by_state(data["processes"]) == {
        "d-job": "used",  # its job row names the activation a-job
        "d-session": "used",  # a process session row names it directly
        "d-quiet": "unknown",
        "d-unset": "unknown",  # an unset state is never proof of a draft
    }  # d-draft is unused and dropped


def test_legacy_exclude_unknown_keeps_proven_used_only(crm_home):  # noqa: F811
    data = _components("processes", "--in-use", "--exclude-unknown")
    assert [r["id"] for r in data["processes"]] == ["d-job", "d-session"]


def test_legacy_without_the_flag_has_no_usage_and_no_job_read(crm_home):  # noqa: F811
    org = _legacy_org()
    data = _components("processes", org=org)
    assert all("usage" not in r for r in data["processes"]) and org[1]["jobs"] == 0


def test_legacy_a_failed_job_read_keeps_activated_rows_unknown(crm_home):  # noqa: F811
    data = _components("processes", "--in-use", org=_legacy_org(jobs_fault=True))
    states = _by_state(data["processes"])
    assert states["d-job"] == "unknown" and "d-draft" not in states
    reason = next(r for r in data["processes"] if r["id"] == "d-job")["usage"]["reason"]
    assert "async job read failed" in reason


def test_legacy_the_job_query_carries_no_not_null_filter(crm_home):  # noqa: F811
    org = _legacy_org()
    _components("processes", "--in-use", org=org)
    jobs = [q for e, q in org[1]["queries"] if e == "asyncoperation"]
    assert jobs and all("not-null" not in q for q in jobs)


def test_legacy_a_job_read_past_its_time_cap_stops_and_names_the_cap():
    ticks = iter([0.0, 999.0, 999.0])  # each read's deadline passes by its first page
    org = _legacy_org(jobs_more=True)
    profile = ConnectionProfile(
        name="legacy", url=_URL, domain="CONTOSO", username="alice", platform="crm2011"
    )

    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, text=org[0])
        got = usage.probe_legacy_processes(
            LegacyClient(profile, "pw"), time_cap=5, monotonic=lambda: next(ticks)
        )
    assert got["d-quiet"].state == "unknown" and "5 s time cap" in (got["d-quiet"].reason or "")


def test_legacy_a_process_row_born_after_the_probe_is_unknown_not_a_crash(monkeypatch):
    monkeypatch.setattr(usage, "probe_legacy_processes", lambda client: {})
    got = usage.annotate_legacy_components(
        None, {"processes": [{"id": "new"}]}, exclude_unknown=False
    )
    assert got["processes"][0]["usage"]["state"] == "unknown"


def test_legacy_a_failed_process_probe_keeps_the_plugin_usage(crm_home):  # noqa: F811
    # the process definitions read fails; the plug-in probe and its rows are unaffected
    respond, _ = _plugin_org()

    def faulty(request, context):
        query = fromstring(request.body).findtext(f".//{{{_CRM}}}Query") or ""
        if 'name="workflow"' in query and 'name="asyncautodelete"' in query:
            context.status_code = 500
            context.headers.update(_XML)
            return "<x/>"
        if 'name="workflow"' in query:  # the kind listing: two activated processes
            context.headers.update(_XML)
            return _rm(_L_DEFS[1:3])
        return respond(request, context)

    data = _components("--in-use", org=(faulty, None))
    assert {r["usage"]["state"] for r in data["processes"]} == {"unknown"}
    assert "usage probe failed" in data["processes"][0]["usage"]["reason"]
    assert any(r["usage"]["state"] == "used" for r in data["plugin-steps"])


def test_v9_a_truncated_group_read_is_named_in_the_reason():
    class Paged(_Org):
        def get(self, path, params=None):
            got = super().get(path, params)
            if path == "asyncoperations":
                got["@Microsoft.Dynamics.CRM.morerecords"] = True
            return got

    got = usage.probe_v9_processes(Paged(jobs=()))
    assert got["d-used"].state == "unknown"
    assert "async job read truncated" in (got["d-used"].reason or "")
