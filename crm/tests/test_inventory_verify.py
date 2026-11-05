"""`crm inventory verify`: judge recorded verification results against work packages."""

# pyright: basic

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory_verify
from crm.core.inventory_packages import (
    PackagesResult,
    Requirement,
    Unknown,
    WorkPackage,
    read_packages,
    write_packages,
)

SCHEMA = (
    Path(inventory_verify.__file__).parent.parent
    / "schemas"
    / "verification-results-v1.schema.json"
)
# Synthetic packages: id -> requirement ids. Each name says the verdict its results earn.
PACKAGES = {
    "pkg-00000000000a": ("req-00000000000000a1", "req-00000000000000a2"),  # complete
    "pkg-00000000000b": ("req-00000000000000b1", "req-00000000000000b2"),  # failed
    "pkg-00000000000c": ("req-00000000000000c1",),  # blocked
    "pkg-00000000000d": ("req-00000000000000d1", "req-00000000000000d2"),  # not_tested: missing
    "pkg-00000000000e": ("req-00000000000000e1",),  # not_tested: pass without evidence
    "pkg-00000000000f": (),  # not_tested: no requirement is known
}
ENV = {"profile": "test-org", "org": "https://internalcrm.contoso.local/Org"}


def _write_packages(out: Path) -> Path:
    packages = tuple(
        WorkPackage(
            id=pid,
            members=(f"form:member-{pid}",),
            edges=(),
            source=(Unknown("synthetic"),),
            consumers=(Unknown("synthetic"),),
            findings=(Unknown("synthetic"),),
            expected_behavior=(Unknown("synthetic"),),
            verification=tuple(
                Requirement(rid, "form_script", f"form:member-{pid}", {"libraries": []})
                for rid in reqs
            )
            or (Unknown("no member kind states a verification requirement"),),
            scenarios=(),
        )
        for pid, reqs in PACKAGES.items()
    )
    inventory = {"platform": "dataverse", "version": "9.2", "org_url": ENV["org"]}
    write_packages(PackagesResult(inventory, packages, (), (), (), {}), out)
    return out


def _evidence(folder: Path, name: str, body: str = "ok\n") -> dict:
    path = folder / "evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return {"path": f"evidence/{name}", "sha256": hashlib.sha256(body.encode()).hexdigest()}


def _result(package: str, requirement: str, outcome: str, evidence=(), **extra) -> dict:
    return {
        "package": package,
        "requirement": requirement,
        "outcome": outcome,
        "evidence": list(evidence),
        "environment": ENV,
        **extra,
    }


@pytest.fixture
def packages(tmp_path: Path) -> Path:
    return _write_packages(tmp_path / "packages")


def _write_results(folder: Path, packages: Path, results: list[dict], **top) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "packages_sha256": read_packages(packages).sha256,
        "results": results,
        **top,
    }
    path = folder / "results.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _every_verdict(folder: Path, packages: Path) -> Path:
    ev = _evidence(folder, "a1.txt")
    return _write_results(
        folder,
        packages,
        [
            _result("pkg-00000000000a", "req-00000000000000a1", "pass", [ev]),
            _result("pkg-00000000000a", "req-00000000000000a2", "pass", [_evidence(folder, "a2")]),
            _result("pkg-00000000000b", "req-00000000000000b1", "pass", [ev]),
            _result(
                "pkg-00000000000b",
                "req-00000000000000b2",
                "fail",
                [ev],
                expected="Saving locks the budget",
                actual="The budget stayed editable",
            ),
            _result(
                "pkg-00000000000c",
                "req-00000000000000c1",
                "blocked",
                errors=["the test org did not answer"],
            ),
            _result("pkg-00000000000d", "req-00000000000000d1", "pass", [ev]),
            _result("pkg-00000000000e", "req-00000000000000e1", "pass", []),
        ],
    )


def _invoke(*args: str):
    return CliRunner().invoke(cli, ["--json", "inventory", "verify", *args])


def test_each_package_gets_its_verdict(packages, tmp_path):
    results = _every_verdict(tmp_path / "run", packages)

    run = _invoke(str(packages), "--results", str(results))

    assert run.exit_code == 0, run.output
    data = json.loads(run.output)["data"]
    assert {p["id"]: p["verdict"] for p in data["packages"]} == {
        "pkg-00000000000a": "complete",
        "pkg-00000000000b": "failed",
        "pkg-00000000000c": "blocked",
        "pkg-00000000000d": "not_tested",
        "pkg-00000000000e": "not_tested",
        "pkg-00000000000f": "not_tested",
    }
    assert data["counts"] == {"failed": 1, "blocked": 1, "not_tested": 3, "complete": 1}


def test_requirement_statuses_say_why_a_package_is_not_tested(packages, tmp_path):
    results = _every_verdict(tmp_path / "run", packages)
    out = tmp_path / "report.json"

    run = _invoke(str(packages), "--results", str(results), "--out", str(out))

    assert run.exit_code == 0, run.output
    report = {p["id"]: p for p in json.loads(out.read_text(encoding="utf-8"))["packages"]}
    statuses = {
        r["id"]: (r["status"], r["reason"]) for p in report.values() for r in p["requirements"]
    }
    assert statuses["req-00000000000000b2"] == ("fail", None)
    assert statuses["req-00000000000000c1"] == ("blocked", None)
    assert statuses["req-00000000000000d2"] == ("not_tested", "no_result")
    assert statuses["req-00000000000000e1"] == ("not_tested", "no_evidence")
    assert report["pkg-00000000000f"]["requirements"] == []


def test_a_pass_whose_evidence_file_changed_is_not_tested(packages, tmp_path):
    folder = tmp_path / "run"
    ev = _evidence(folder, "a.txt")
    results = _write_results(
        folder,
        packages,
        [
            _result("pkg-00000000000a", "req-00000000000000a1", "pass", [ev]),
            _result("pkg-00000000000a", "req-00000000000000a2", "pass", [ev]),
        ],
    )
    (folder / ev["path"]).write_text("edited after the run\n", encoding="utf-8")

    data = json.loads(_invoke(str(packages), "--results", str(results)).output)["data"]

    assert data["packages"][0] == {"id": "pkg-00000000000a", "verdict": "not_tested"}


def test_negative_controls_hold_or_escape(packages, tmp_path):
    folder = tmp_path / "run"
    ev = _evidence(folder, "a.txt")
    passing = [
        _result("pkg-00000000000a", "req-00000000000000a1", "pass", [ev]),
        _result("pkg-00000000000a", "req-00000000000000a2", "pass", [ev]),
    ]

    def run(control_outcome: str) -> dict:
        control = _result(
            "pkg-00000000000a", "req-00000000000000a1", control_outcome, [ev], negative_control=True
        )
        results = _write_results(folder, packages, [*passing, control])
        return json.loads(_invoke(str(packages), "--results", str(results)).output)["data"]

    kept, broke = run("fail"), run("pass")

    assert kept["packages"][0]["verdict"] == "complete"
    assert kept["escaped"] == []
    assert broke["packages"][0]["verdict"] == "not_tested"
    assert broke["escaped"] == [
        {"package": "pkg-00000000000a", "requirement": "req-00000000000000a1"}
    ]


def test_results_for_unknown_ids_are_listed_not_dropped(packages, tmp_path):
    results = _write_results(
        tmp_path / "run",
        packages,
        [
            _result("pkg-00000000000a", "req-ffffffffffffffff", "pass"),
            _result("pkg-ffffffffffff", "req-00000000000000a1", "fail"),
        ],
    )

    data = json.loads(_invoke(str(packages), "--results", str(results)).output)["data"]

    assert data["unknown_results"] == [
        {
            "package": "pkg-00000000000a",
            "requirement": "req-ffffffffffffffff",
            "reason": "unknown_requirement",
        },
        {
            "package": "pkg-ffffffffffff",
            "requirement": "req-00000000000000a1",
            "reason": "unknown_package",
        },
    ]


def test_results_recorded_against_another_packages_output_are_refused(packages, tmp_path):
    results = _every_verdict(tmp_path / "run", packages)
    other = _write_packages(tmp_path / "other")
    (other / "pkg-00000000000a.json").write_text(
        (other / "pkg-00000000000a.json")
        .read_text(encoding="utf-8")
        .replace("form_script", "plugin_step"),
        encoding="utf-8",
    )

    run = _invoke(str(other), "--results", str(results))

    assert run.exit_code == 1
    assert "another packages output" in json.loads(run.output)["error"]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"schema_version": 2}, "schema_version 2 is not supported"),
        (
            {
                "results": [
                    {"package": "p", "requirement": "r", "outcome": "passed", "evidence": []}
                ]
            },
            "outcome",
        ),
        *(
            (
                {"results": [_result("p", "r", "pass", [{"path": path, "sha256": "0" * 64}])]},
                "relative path inside",
            )
            for path in ("../x", "a/../b", "/x", "a\\b", "C:x", ".", "./", ".//", "a\0b", "")
        ),
        (
            {"results": [_result("p", "r", "pass", environment={"profile": 1, "org": "o"})]},
            "results[0].environment.profile",
        ),
    ],
)
def test_a_malformed_results_file_is_refused(packages, tmp_path, change, match):
    top = dict(change)
    results = _write_results(tmp_path / "run", packages, top.pop("results", []), **top)
    schema = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))

    run = _invoke(str(packages), "--results", str(results))

    assert run.exit_code == 1
    assert match in json.loads(run.output)["error"]
    assert not schema.is_valid(json.loads(results.read_text(encoding="utf-8")))


def test_out_is_deterministic_and_the_results_file_matches_its_schema(packages, tmp_path):
    results = _every_verdict(tmp_path / "run", packages)
    data = json.loads(results.read_text(encoding="utf-8"))
    data["results"][0] |= {
        "deployed": [{"component": "web_resource:new_/scripts/a.js", "sha256": "a" * 64}],
        "open_decisions": ["Confirm the budget lock with the product owner"],
        "negative_control": False,
    }
    results.write_text(json.dumps(data), encoding="utf-8")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(data)

    for name in ("one.json", "two.json"):
        run = _invoke(str(packages), "--results", str(results), "--out", str(tmp_path / name))
        assert run.exit_code == 0, run.output
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()
    report = json.loads((tmp_path / "one.json").read_text(encoding="utf-8"))
    assert report["packages"][0]["open_decisions"] == [
        "Confirm the budget lock with the product owner"
    ]


def test_table_output_names_each_verdict(packages, tmp_path):
    results = _every_verdict(tmp_path / "run", packages)

    run = CliRunner().invoke(cli, ["inventory", "verify", str(packages), "--results", str(results)])

    assert run.exit_code == 0, run.output
    assert "pkg-00000000000b" in run.output
    assert "failed 1, blocked 1, not_tested 3, complete 1" in run.output


def test_reads_back_a_real_packages_output(tmp_path):
    from crm.core.inventory_packages import build_packages
    from crm.core.inventory_source_map import map_source
    from crm.tests.test_inventory_packages import _write_inventory, _write_repo  # noqa: PLC0415

    folder = _write_inventory(tmp_path / "inv")
    result = build_packages(folder, map_source(folder, _write_repo(tmp_path / "repo")))
    write_packages(result, tmp_path / "out")

    read = read_packages(tmp_path / "out")

    assert read.requirements == {
        p.id: tuple(r for r in p.verification if isinstance(r, Requirement))
        for p in result.packages
    }


def test_packages_reports_the_digest_a_results_file_names(tmp_path):
    from crm.core.inventory_source_map import dump_source_map, map_source
    from crm.tests.test_inventory_packages import _write_inventory, _write_repo

    folder = _write_inventory(tmp_path / "inv")
    source_map = tmp_path / "source-map.json"
    source_map.write_text(
        dump_source_map(map_source(folder, _write_repo(tmp_path / "repo"))), encoding="utf-8"
    )
    out = tmp_path / "packages"
    run = CliRunner().invoke(
        cli,
        [
            "--json",
            "inventory",
            "packages",
            str(folder),
            "--source-map",
            str(source_map),
            "--out",
            str(out),
        ],
    )
    sha = json.loads(run.output)["data"]["packages_sha256"]
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"schema_version": 1, "packages_sha256": sha, "results": []}))

    assert _invoke(str(out), "--results", str(results)).exit_code == 0


def test_a_requirement_takes_its_worst_result_and_a_control_may_not_run(packages, tmp_path):
    folder = tmp_path / "run"
    ev = _evidence(folder, "a.txt")
    results = _write_results(
        folder,
        packages,
        [
            _result("pkg-00000000000a", "req-00000000000000a1", "pass", [ev]),
            _result("pkg-00000000000a", "req-00000000000000a1", "not_tested"),
            _result("pkg-00000000000a", "req-00000000000000a2", "pass", [ev]),
            _result("pkg-00000000000a", "req-00000000000000a2", "blocked", negative_control=True),
        ],
    )
    out = tmp_path / "report.json"

    _invoke(str(packages), "--results", str(results), "--out", str(out))

    (package, *_) = json.loads(out.read_text(encoding="utf-8"))["packages"]
    rows = {r["id"]: (r["status"], r["reason"], r["controls"]) for r in package["requirements"]}
    assert rows == {
        "req-00000000000000a1": ("not_tested", "recorded", []),
        "req-00000000000000a2": ("pass", None, ["not_run"]),
    }


def test_evidence_reached_through_a_symlink_out_of_the_folder_is_unverified(packages, tmp_path):
    folder = tmp_path / "run"
    outside = _evidence(tmp_path / "elsewhere", "a.txt")
    (folder / "evidence").mkdir(parents=True)
    (folder / "evidence" / "a.txt").symlink_to(tmp_path / "elsewhere" / outside["path"])
    results = _write_results(
        folder,
        packages,
        [
            _result("pkg-00000000000a", rid, "pass", [outside])
            for rid in ("req-00000000000000a1", "req-00000000000000a2")
        ],
    )

    data = json.loads(_invoke(str(packages), "--results", str(results)).output)["data"]

    assert data["packages"][0]["verdict"] == "not_tested"


def test_duplicate_unknown_results_are_each_listed(packages, tmp_path):
    stray = _result("pkg-ffffffffffff", "req-00000000000000a1", "fail")
    results = _write_results(tmp_path / "run", packages, [stray, stray])

    data = json.loads(_invoke(str(packages), "--results", str(results)).output)["data"]

    assert len(data["unknown_results"]) == 2
