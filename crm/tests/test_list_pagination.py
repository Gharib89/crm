"""Regression: collection list paths follow `@odata.nextLink` to exhaustion (#682).

`workflow list`, `solution components`, and `solution list` each issued a single
bare GET and returned only the server's first page, silently dropping every row
past it on large orgs. All three now route through `D365Backend.get_collection`,
which follows `@odata.nextLink`. Each test mocks a two-page response and asserts
the page-2 row is present in the result.
"""

# pyright: basic
from __future__ import annotations

import requests_mock

from crm.core import solution as sol_mod
from crm.core import workflow as wf_mod

_SOL_ID = "22222222-2222-2222-2222-222222222222"


def test_list_workflows_follows_nextlink(backend):
    url = backend.url_for("workflows")
    next_link = url + "?$skiptoken=page2"
    with requests_mock.Mocker() as m:
        m.get(
            url,
            json={
                "value": [{"workflowid": "w1", "name": "Page one", "type": 1}],
                "@odata.nextLink": next_link,
            },
        )
        m.get(
            next_link,
            json={
                "value": [{"workflowid": "w2", "name": "Page two", "type": 1}],
            },
        )
        items = wf_mod.list_workflows(backend)
    assert [r["workflowid"] for r in items] == ["w1", "w2"]


def test_solution_components_follows_nextlink(backend):
    comp_url = backend.url_for("solutioncomponents")
    next_link = comp_url + "?$skiptoken=page2"
    with requests_mock.Mocker() as m:
        m.get(
            backend.url_for("solutions"),
            json={"value": [{"solutionid": _SOL_ID, "uniquename": "CRMWorx"}]},
        )
        m.get(
            comp_url,
            json={
                "value": [{"objectid": "c1", "componenttype": 1}],
                "@odata.nextLink": next_link,
            },
        )
        m.get(next_link, json={"value": [{"objectid": "c2", "componenttype": 1}]})
        items = sol_mod.solution_components(backend, "CRMWorx")
    assert [r["objectid"] for r in items] == ["c1", "c2"]


def test_list_solutions_follows_nextlink(backend):
    url = backend.url_for("solutions")
    next_link = url + "?$skiptoken=page2"
    with requests_mock.Mocker() as m:
        m.get(
            url,
            json={
                "value": [{"uniquename": "s1"}],
                "@odata.nextLink": next_link,
            },
        )
        m.get(next_link, json={"value": [{"uniquename": "s2"}]})
        items = sol_mod.list_solutions(backend)
    assert [r["uniquename"] for r in items] == ["s1", "s2"]
