"""Plug-in step, type and assembly usage and `--in-use` on the plug-in listings (slice #1093)."""

# pyright: basic
from __future__ import annotations

import json
from xml.sax.saxutils import escape

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import session as session_mod
from crm.core import usage
from crm.core.legacy import LegacyClient
from crm.core.usage import Evidence, PluginType, Step, Usage
from crm.utils.d365_backend import ConnectionProfile, D365Error
from crm.utils.safe_xml import fromstring

AS_OF = "2026-11-05T00:00:00Z"


def _step(id, *, enabled=True, sync=False, autodelete=False, type_id="t1"):
    return Step(id, type_id, enabled, sync, autodelete)


_T1 = PluginType("t1", "a1", "Contoso.Plugins.One")


def _states(steps, types=(_T1,), ev=None, **kw):
    ev = ev or Evidence()
    by_step, by_type, by_assembly = usage.plugin_usage(steps, types, ev, clock=lambda: AS_OF, **kw)
    return by_step, by_type, by_assembly


# ── rules ────────────────────────────────────────────────────────────────


def test_a_disabled_step_is_unused_even_with_a_job_row():
    got, _, _ = _states([_step("s", enabled=False)], ev=Evidence(jobs=frozenset({"s"})))
    assert got["s"] == Usage("unused", "plugin_step_runs", None, AS_OF, "disabled")


def test_an_async_step_with_a_job_row_is_used():
    got, _, _ = _states([_step("s")], ev=Evidence(jobs=frozenset({"s"})))
    assert got["s"].state == "used" and got["s"].signal == "async_job"


def test_a_sync_step_with_no_evidence_is_unknown_and_names_sync():
    got, _, _ = _states([_step("s", sync=True)])
    assert got["s"].state == "unknown" and "synchronous" in (got["s"].reason or "")


def test_an_autodelete_async_step_without_a_row_is_unknown():
    got, _, _ = _states([_step("s", autodelete=True)])
    assert got["s"].state == "unknown" and "deleted" in (got["s"].reason or "")


def test_an_async_step_without_a_row_is_unknown_not_unused():
    got, _, _ = _states([_step("s")])
    assert got["s"].state == "unknown" and "purged" in (got["s"].reason or "")


def test_a_job_read_gap_is_the_reason_for_an_async_step():
    got, _, _ = _states([_step("s")], ev=Evidence(jobs_gap="async job read failed: boom"))
    assert (got["s"].reason or "").startswith("async job read failed")


def test_tracing_off_keeps_a_trace_only_step_unknown_and_says_why():
    got, _, _ = _states([_step("s", sync=True)], ev=Evidence(trace_gap="plug-in trace log is off"))
    assert got["s"].state == "unknown" and "trace log is off" in (got["s"].reason or "")


def test_a_trace_hit_on_the_step_makes_a_sync_step_used():
    ev = Evidence(traces=frozenset({"hit"}))
    got, _, _ = _states([_step("hit", sync=True), _step("miss", sync=True)], ev=ev)
    assert (got["hit"].state, got["hit"].signal) == ("used", "plugin_trace")
    assert got["miss"].state == "unknown"


def test_types_and_assemblies_roll_up():
    types = [
        PluginType("t1", "a1", "One"),  # a step with a job row
        PluginType("t2", "a1", "Two"),  # only a disabled step
        PluginType("t3", "a2", "Three"),  # an unproven sync step
        PluginType("t4", "a3", "Four"),  # no steps
        PluginType("t5", "a4", "Five", outside=usage.WORKFLOW),  # no steps, but a workflow activity
    ]
    steps = [
        _step("s1", type_id="t1"),
        _step("s2", type_id="t2", enabled=False),
        _step("s3", type_id="t3", sync=True),
    ]
    _, by_type, by_asm = _states(
        steps, types, Evidence(jobs=frozenset({"s1"})), assembly_ids=["a5"]
    )
    assert {k: v.state for k, v in by_type.items()} == {
        "t1": "used",
        "t2": "unused",
        "t3": "unknown",
        "t4": "unused",
        "t5": "unknown",
    }
    assert "workflow" in (by_type["t5"].reason or "")
    assert {k: v.state for k, v in by_asm.items()} == {
        "a1": "used",
        "a2": "unknown",
        "a3": "unused",
        "a4": "unknown",
        "a5": "unused",  # owns no type
    }


# ── v9 ───────────────────────────────────────────────────────────────────

_V9_TYPES = [
    {
        "plugintypeid": "t1",
        "typename": "One",
        "_pluginassemblyid_value": "a1",
        "isworkflowactivity": False,
    },
    {
        "plugintypeid": "t2",
        "typename": "Two",
        "_pluginassemblyid_value": "a1",
        "isworkflowactivity": False,
    },
    {
        "plugintypeid": "t3",
        "typename": "Three",
        "_pluginassemblyid_value": "a2",
        "isworkflowactivity": False,
    },
]
_V9_STEPS = [
    {
        "sdkmessageprocessingstepid": "s1",
        "_eventhandler_value": "t1",
        "statecode": 0,
        "mode": 1,
        "asyncautodelete": False,
        "sdkmessageid": {"name": "Update"},
    },
    {
        "sdkmessageprocessingstepid": "s2",
        "_eventhandler_value": "t2",
        "statecode": 1,
        "mode": 0,
        "asyncautodelete": False,
        "sdkmessageid": {"name": "Create"},
    },
    {
        "sdkmessageprocessingstepid": "s3",
        "_eventhandler_value": "t3",
        "statecode": 0,
        "mode": 0,
        "asyncautodelete": False,
        "sdkmessageid": {"name": "Create"},
    },
    {  # a webhook step: its handler is not a plug-in type
        "sdkmessageprocessingstepid": "s4",
        "_eventhandler_value": "endpoint",
        "statecode": 0,
        "mode": 1,
        "asyncautodelete": False,
    },
]


def _v9_org(
    *, tracing=0, jobs=("s1",), job_error=None, traces=(), setting_status=None, custom_apis=()
):
    def get(path):
        if path == "organizations":
            if setting_status:
                raise D365Error("no such column", status=setting_status)
            return {"value": [{"plugintracelogsetting": tracing}]}
        if path == "asyncoperations":
            if job_error:
                raise job_error
            return {"value": [{"step": j, "n": 1} for j in jobs]}
        if path == "plugintracelogs":
            return {"value": [{"step": t, "n": 1} for t in traces]}
        raise AssertionError(path)

    def get_collection(path):
        if path == "customapis":
            return [{"_plugintypeid_value": t} for t in custom_apis]
        return {"plugintypes": _V9_TYPES, "sdkmessageprocessingsteps": _V9_STEPS}[path]

    return {"get": get, "get_collection": get_collection}


def _types():
    return [
        PluginType(r["plugintypeid"], r["_pluginassemblyid_value"], r["typename"])
        for r in _V9_TYPES
    ]


def _v9_probe(make_fake_backend, **kw):
    backend = make_fake_backend(responses=_v9_org(**kw))
    return usage.probe_v9_plugin_types(backend, _types(), clock=lambda: AS_OF), backend


def test_v9_types_roll_up_from_one_bulk_read_of_steps_and_jobs(make_fake_backend):
    got, backend = _v9_probe(make_fake_backend)
    assert {k: v.state for k, v in got.items()} == {"t1": "used", "t2": "unused", "t3": "unknown"}
    paths = [p for v, p, _ in backend.calls if v == "get"]
    assert paths.count("asyncoperations") == 1  # grouped, never one read per step
    assert "plugintracelogs" not in paths  # tracing is off


def test_v9_job_aggregate_names_the_owning_step_and_operation_type(make_fake_backend):
    _, backend = _v9_probe(make_fake_backend)
    (xml,) = [k["params"]["fetchXml"] for v, p, k in backend.calls if p == "asyncoperations"]
    root = fromstring(xml)
    assert root.get("aggregate") == "true"
    group, cond = root.find(".//attribute[@groupby='true']"), root.find(".//condition")
    assert group is not None and cond is not None
    assert group.get("name") == "owningextensionid"
    assert cond.attrib == {
        "attribute": "operationtype",
        "operator": "eq",
        "value": "1",
    }


def test_v9_a_refused_job_aggregate_leaves_async_steps_unknown(make_fake_backend):
    got, _ = _v9_probe(
        make_fake_backend, job_error=D365Error("AggregateQueryRecordLimit", status=400)
    )
    assert got["t1"].state == "unknown" and "async job read failed" in (got["t1"].reason or "")


def test_v9_a_step_less_custom_api_type_is_unknown_not_unused(make_fake_backend):
    backend = make_fake_backend(responses=_v9_org(custom_apis=["t6", "t3"]))
    types = [*_types(), PluginType("t6", "a3", "Six")]
    got = usage.probe_v9_plugin_types(backend, types, clock=lambda: AS_OF)
    assert got["t6"].state == "unknown" and "custom API" in (got["t6"].reason or "")
    assert got["t2"].state == "unused"  # a type no API names still proves out


def test_v9_an_org_without_custom_apis_is_no_signal_not_an_error(make_fake_backend):
    responses = _v9_org()
    base = responses["get_collection"]

    def get_collection(path):
        if path == "customapis":
            raise D365Error("no such entity", status=404)
        return base(path)

    backend = make_fake_backend(responses={**responses, "get_collection": get_collection})
    got = usage.probe_v9_plugin_types(backend, _types(), clock=lambda: AS_OF)
    assert got["t1"].state == "used"


def test_v9_a_missing_tracing_setting_is_no_signal_not_an_error(make_fake_backend):
    got, _ = _v9_probe(make_fake_backend, setting_status=400)
    assert got["t3"].state == "unknown"


def test_v9_tracing_on_reads_the_trace_log_and_a_hit_marks_a_sync_step_used(make_fake_backend):
    got, backend = _v9_probe(make_fake_backend, tracing=1, traces=["s3"])
    assert got["t3"].state == "used"
    assert any(p == "plugintracelogs" for _, p, _ in backend.calls)


# ── commands: v9 plugin list-types ───────────────────────────────────────


@pytest.fixture
def v9(inject_backend, make_fake_backend, monkeypatch):
    monkeypatch.setenv("CRM_HOME", "/nonexistent-crm-home")
    return inject_backend(make_fake_backend(responses=_v9_org()))


def _list_types(*flags):
    result = CliRunner().invoke(cli, ["--json", "plugin", "list-types", *flags])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["data"]


def test_list_types_without_the_flag_has_no_usage_and_no_probe(v9):
    rows = _list_types()
    assert len(rows) == 3 and all("usage" not in r for r in rows)
    assert not any(p == "asyncoperations" for _, p, _ in v9.calls)


def test_list_types_in_use_drops_unused_and_keeps_the_row_shape(v9):
    rows = _list_types("--in-use")
    assert {r["plugintypeid"]: r["usage"]["state"] for r in rows} == {"t1": "used", "t3": "unknown"}
    assert not {"_pluginassemblyid_value", "isworkflowactivity"} & set(rows[0])


def test_list_types_exclude_unknown_keeps_proven_used_only(v9):
    assert [r["plugintypeid"] for r in _list_types("--in-use", "--exclude-unknown")] == ["t1"]


def test_list_types_exclude_unknown_alone_is_a_usage_error(v9):
    result = CliRunner().invoke(cli, ["plugin", "list-types", "--exclude-unknown"])
    assert result.exit_code == 2 and "--in-use" in result.output


# ── CRM 2011 ─────────────────────────────────────────────────────────────

_URL = "https://crm.contoso.local/contoso"
_ENDPOINT = _URL + "/XRMServices/2011/Organization.svc/web"
_CRM = "http://schemas.microsoft.com/xrm/2011/Contracts"
_XML = {"Content-Type": "text/xml; charset=utf-8"}


def _value(v):
    if isinstance(v, bool):
        return f'<b:value i:type="c:boolean" xmlns:c="http://www.w3.org/2001/XMLSchema">{str(v).lower()}</b:value>'
    if isinstance(v, int):
        return f'<b:value i:type="a:OptionSetValue"><a:Value>{v}</a:Value></b:value>'
    if isinstance(v, tuple):
        return (
            f'<b:value i:type="a:EntityReference"><a:Id>{v[0]}</a:Id>'
            f"<a:LogicalName>{v[1]}</a:LogicalName></b:value>"
        )
    return f'<b:value i:type="c:string" xmlns:c="http://www.w3.org/2001/XMLSchema">{escape(v)}</b:value>'


def _rm(records, *, more=False):
    entities = "".join(
        '<a:Entity><a:Attributes xmlns:b="http://schemas.datacontract.org/2004/07/System.Collections.Generic">'
        + "".join(
            f"<a:KeyValuePairOfstringanyType><b:key>{k}</b:key>{_value(v)}</a:KeyValuePairOfstringanyType>"
            for k, v in r.items()
            if k != "id"
        )
        + f"</a:Attributes><a:Id>{r['id']}</a:Id></a:Entity>"
        for r in records
    )
    cookie = "<a:PagingCookie>c</a:PagingCookie>" if more else '<a:PagingCookie i:nil="true"/>'
    return (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        '<RetrieveMultipleResponse xmlns="http://schemas.microsoft.com/xrm/2011/Contracts/Services">'
        '<RetrieveMultipleResult xmlns:a="http://schemas.microsoft.com/xrm/2011/Contracts"'
        ' xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
        f"<a:Entities>{entities}</a:Entities><a:EntityName>x</a:EntityName>"
        f"<a:MoreRecords>{str(more).lower()}</a:MoreRecords>{cookie}"
        "</RetrieveMultipleResult></RetrieveMultipleResponse></s:Body></s:Envelope>"
    )


_LEGACY = {
    "pluginassembly": [
        {"id": "a1", "name": "One"},
        {"id": "a2", "name": "Two"},
        {"id": "a3", "name": "Bare"},
    ],
    "plugintype": [
        {
            "id": "t1",
            "typename": "One",
            "pluginassemblyid": ("a1", "pluginassembly"),
            "isworkflowactivity": False,
        },
        {
            "id": "t2",
            "typename": "Two",
            "pluginassemblyid": ("a2", "pluginassembly"),
            "isworkflowactivity": False,
        },
    ],
    "sdkmessageprocessingstep": [
        {
            "id": "s1",
            "name": "n1",
            "plugintypeid": ("t1", "plugintype"),
            "statecode": 0,
            "mode": 1,
            "asyncautodelete": False,
        },
        {
            "id": "s2",
            "name": "n2",
            "plugintypeid": ("t2", "plugintype"),
            "statecode": 1,
            "mode": 0,
            "asyncautodelete": False,
        },
        {
            "id": "s3",
            "name": "n3",
            "plugintypeid": ("t2", "plugintype"),
            "statecode": 0,
            "mode": 0,
            "asyncautodelete": False,
        },
    ],
}


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    import crm.core.keyring_store as ks

    monkeypatch.setattr(ks, "is_available", lambda: False)
    session_mod.save_profile(
        ConnectionProfile(
            name="legacy", url=_URL, domain="CONTOSO", username="alice", platform="crm2011"
        )
    )
    session_mod.save_profile_secret_plaintext("legacy", "pw")


def _legacy_org(jobs=(("s1",),), jobs_fault=False, probe_fault=None):
    """An answer per entity; `jobs` is a list of async job pages (step ids per page)."""
    pages = iter(jobs)
    seen = {"jobs": 0}

    def respond(request, context):
        context.headers.update(_XML)
        root = fromstring(request.body)
        entity = fromstring(root.findtext(f".//{{{_CRM}}}Query") or "")[0].get("name")
        if entity == "asyncoperation":
            seen["jobs"] += 1
            if jobs_fault:
                context.status_code = 500
                return "<x/>"
            page = next(pages)
            more = seen["jobs"] < len(jobs)
            return _rm(
                [
                    {"id": f"j{i}", "owningextensionid": (s, "sdkmessageprocessingstep")}
                    for i, s in enumerate(page)
                ],
                more=more,
            )
        if entity == probe_fault:  # only the probe reads it: the listing already passed
            context.status_code = 500
            return "<x/>"
        return _rm(_LEGACY[entity or ""])

    return respond, seen


def _components(*args, org=None):
    respond, _ = org or _legacy_org()
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, text=respond)
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "legacy", "legacy", "components", *args]
        )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["data"]


def _by_state(rows):
    return {r["id"]: r["usage"]["state"] for r in rows}


def test_legacy_in_use_labels_steps_types_and_assemblies(crm_home):
    data = _components("--in-use")
    assert _by_state(data["plugin-steps"]) == {"s1": "used", "s3": "unknown"}  # s2 is disabled
    assert _by_state(data["plugin-types"]) == {"t1": "used", "t2": "unknown"}
    assert _by_state(data["plugin-assemblies"]) == {
        "a1": "used",
        "a2": "unknown",
    }  # a3 owns no type
    assert "synchronous" in data["plugin-steps"][1]["usage"]["reason"]


def test_legacy_without_the_flag_has_no_usage_and_no_job_read(crm_home):
    respond, seen = _legacy_org()
    data = _components("plugin-steps", org=(respond, seen))
    assert all("usage" not in r for r in data["plugin-steps"]) and seen["jobs"] == 0


def test_legacy_exclude_unknown_keeps_proven_used_only(crm_home):
    data = _components("plugin-steps", "--in-use", "--exclude-unknown")
    assert [r["id"] for r in data["plugin-steps"]] == ["s1"]


def test_legacy_in_use_rejects_a_kind_that_is_not_a_plug_in(crm_home):
    result = CliRunner().invoke(
        cli, ["--profile", "legacy", "legacy", "components", "forms", "--in-use"]
    )
    assert result.exit_code == 2 and "forms" in result.output


def test_legacy_a_failed_job_read_is_no_signal_and_steps_stay_unknown(crm_home):
    data = _components("plugin-steps", "--in-use", org=_legacy_org(jobs_fault=True))
    assert _by_state(data["plugin-steps"]) == {"s1": "unknown", "s3": "unknown"}
    assert "async job read failed" in data["plugin-steps"][0]["usage"]["reason"]


def test_legacy_a_job_read_past_its_time_cap_stops_and_names_the_cap():
    ticks = iter([0.0, 999.0])  # the deadline is set at 0, the first page ends past it
    respond, seen = _legacy_org(jobs=(("s1",), ("s3",)))
    profile = ConnectionProfile(
        name="legacy", url=_URL, domain="CONTOSO", username="alice", platform="crm2011"
    )
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, text=respond)
        ev = usage.read_legacy_plugin_evidence(
            LegacyClient(profile, "pw"), time_cap=5, monotonic=lambda: next(ticks)
        )
    assert ev.jobs == {"s1"} and seen["jobs"] == 1  # page 2 (s3) was never requested
    assert "5 s time cap" in (ev.jobs_gap or "")


def test_legacy_a_failed_probe_read_keeps_the_rows_read_and_labels_them_unknown(crm_home):
    data = _components("plugin-steps", "--in-use", org=_legacy_org(probe_fault="plugintype"))
    assert _by_state(data["plugin-steps"]) == {"s1": "unknown", "s2": "unknown", "s3": "unknown"}
    assert "usage probe failed" in data["plugin-steps"][0]["usage"]["reason"]


def test_legacy_has_no_trace_entity_and_that_is_recorded_as_no_signal(crm_home):
    data = _components("plugin-steps", "--in-use")
    assert "no plug-in trace log" in data["plugin-steps"][1]["usage"]["reason"]
