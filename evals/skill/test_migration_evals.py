"""Offline tests for the crm-migration evals (#1067): the task spec's `skill` and `fixture`
fields, their isolation wiring, the five feasibility tasks' answer keys against the real
`crm inventory` output, and the worked example's reproducibility.

    pytest evals/skill/test_migration_evals.py
"""

from __future__ import annotations

import json
import re
import stat
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from evals.skill import isolation
from evals.skill.taskspec import evaluate_feasibility, parse_task_file

HERE = Path(__file__).parent
TASKS = sorted((HERE / "tasks").glob("migration-*.md"))
PROCESS = HERE / "fixtures" / "migration-process"
RESULTS = HERE / "fixtures" / "migration-results"
EXAMPLE = HERE / "examples" / "migration-process"

TASK_IDS = {
    "migration-source-inventory-coverage",
    "migration-ambiguous-code-mapping",
    "migration-missing-capability-unknown",
    "migration-supported-remediation",
    "migration-blocked-verification",
}


def _cli(*args: str) -> dict:
    run = CliRunner().invoke(cli, ["--json", *args])
    assert run.exit_code == 0, run.output
    return json.loads(run.output)["data"]


def _run_pipeline(out: Path, results_packages: Path, *, scenarios: bool) -> dict[str, Path]:
    """Run the four offline stages over the process fixture into ``out``."""
    inv = str(PROCESS / "inventory")
    smap, findings, packages, report = (
        out / "source-map.json",
        out / "findings.json",
        out / "packages",
        out / "verification-report.json",
    )
    _cli("inventory", "map-source", inv, "--repo", str(PROCESS / "repo"), "--out", str(smap))
    _cli("inventory", "assess", inv, "--target", "online", "--out", str(findings))
    extra = ["--scenarios", str(PROCESS / "scenarios.json")] if scenarios else []
    _cli(
        "inventory",
        "packages",
        inv,
        "--source-map",
        str(smap),
        *extra,
        "--findings",
        str(findings),
        "--out",
        str(packages),
    )
    _cli(
        "inventory",
        "verify",
        str(results_packages),
        "--results",
        str(RESULTS / "results.json"),
        "--out",
        str(report),
    )
    return {"map": smap, "findings": findings, "packages": packages, "report": report}


def _task_by_id() -> dict:
    return {parse_task_file(p).id: parse_task_file(p) for p in TASKS}


# --- the task spec ----------------------------------------------------------


def test_skill_defaults_to_crm_with_no_fixture():
    spec = parse_task_file(HERE / "tasks" / "records-create-verify.md")
    assert (spec.skill, spec.fixture) == ("crm", None)


def test_skill_crm_migration_and_fixture_parse(tmp_path):
    spec = parse_task_file(
        _feasibility(tmp_path, skill="crm-migration", fixture="migration-process")
    )
    assert (spec.skill, spec.fixture) == ("crm-migration", "migration-process")


def _feasibility(tmp_path: Path, **front: str) -> Path:
    path = tmp_path / "t.md"
    extra = "".join(f"{k}: {v}\n" for k, v in front.items())
    path.write_text(
        "---\nid: t\ndomain: migration\ntarget: either\nkind: feasibility\n"
        "answer_key:\n  ok: true\nevidence:\n  - a reason\ncleanup: []\n"
        f"{extra}---\n\nprompt\n",
        encoding="utf-8",
    )
    return path


def test_unknown_skill_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="skill"):
        parse_task_file(_feasibility(tmp_path, skill="nope"))


@pytest.mark.parametrize("fixture", ["missing-folder", "../tasks", "a/b"])
def test_fixture_must_be_an_existing_plain_folder_name(tmp_path, fixture):
    with pytest.raises(ValueError, match="fixture"):
        parse_task_file(_feasibility(tmp_path, fixture=fixture))


# --- isolation --------------------------------------------------------------


@pytest.fixture
def fake_crm(tmp_path) -> str:
    """A `crm` on disk that runs this checkout, so `skill install` offers crm-migration."""
    root = isolation.repo_root()
    path = tmp_path / "bin" / "crm"
    path.parent.mkdir()
    path.write_text(
        f'#!/bin/sh\nPYTHONPATH={root} exec {sys.executable} -m crm "$@"\n', encoding="utf-8"
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_provision_installs_only_the_named_skill(fake_crm):
    iso = isolation.provision_isolation(fake_crm, skill="crm-migration")
    try:
        assert (iso.skill_dir / "SKILL.md").is_file()
        assert iso.skill_dir.name == "crm-migration"
        assert [p.name for p in (iso.home / ".claude" / "skills").iterdir()] == ["crm-migration"]
        assert isolation.verify_isolation(iso)["skill-installed"]
    finally:
        iso.cleanup()


def test_provision_copies_the_fixture_into_the_work_dir():
    iso = isolation.provision_isolation(install_skill=False, fixture=PROCESS)
    try:
        assert (iso.work / "inventory" / "manifest.json").is_file()
        assert (iso.work / "repo" / "README.md").is_file()
        isolation.verify_isolation(iso, expect_skill=False)  # no repo markers rode along
    finally:
        iso.cleanup()


# --- the five tasks ---------------------------------------------------------


def test_the_five_migration_tasks_load():
    specs = _task_by_id()
    assert set(specs) == TASK_IDS
    for spec in specs.values():
        assert spec.is_feasibility and spec.skill == "crm-migration"
        assert spec.fixture in ("migration-process", "migration-results")
        assert spec.answer_key and spec.evidence


def test_answer_keys_match_the_real_pipeline(tmp_path):
    specs = _task_by_id()
    manifest = json.loads((PROCESS / "inventory" / "manifest.json").read_text(encoding="utf-8"))
    made = _run_pipeline(tmp_path, RESULTS / "packages", scenarios=False)
    smap, findings, packages, report = (
        made["map"],
        made["findings"],
        made["packages"],
        made["report"],
    )

    incomplete = sorted(k for k, v in manifest["coverage"].items() if v["status"] != "complete")
    lookup = next(
        c
        for c in json.loads(smap.read_text(encoding="utf-8"))["components"]
        if c["key"] == "new_/scripts/lookup.js"
    )
    got = {
        f["key"]: f
        for f in json.loads(findings.read_text(encoding="utf-8"))["findings"]
        if f["class"] != "clear"
    }
    legacy = got["new_/scripts/legacy.js"]
    pkg = next(
        p
        for p in json.loads((packages / "index.json").read_text(encoding="utf-8"))["packages"]
        for m in json.loads((packages / p["file"]).read_text(encoding="utf-8"))["members"]
        if m == "web_resource:new_/scripts/legacy.js"
    )
    verdicts = [p["verdict"] for p in json.loads(report.read_text(encoding="utf-8"))["packages"]]

    key = {i: s.answer_key for i, s in specs.items()}
    assert key["migration-source-inventory-coverage"] == {
        "inventory_complete": False,
        "incomplete_kinds": incomplete,
        "incomplete_kind_count": len(incomplete),
        "gap_count": len(manifest["gaps"]),
    }
    assert lookup["status"] == "ambiguous"
    assert key["migration-ambiguous-code-mapping"]["candidate_files"] == lookup["files"]
    assert key["migration-ambiguous-code-mapping"]["candidate_count"] == len(lookup["files"])
    assembly = {f["class"] for f in _findings_for(findings, "Contoso.Plugins")}
    assert assembly == {"unknown"}
    assert key["migration-missing-capability-unknown"]["assembly_class"] == "unknown"
    assert key["migration-supported-remediation"] == {
        "finding_class": legacy["class"],
        "rule_id": legacy["rule"],
        "package_id": pkg["id"],
    }
    blocked = key["migration-blocked-verification"]
    assert blocked["failed_packages"] == verdicts.count("failed")
    assert blocked["blocked_packages"] == verdicts.count("blocked")
    assert "complete" not in verdicts


def _findings_for(path: Path, key: str) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))["findings"]
    return [f for f in data if f["key"] == key]


def test_claiming_completion_fails_the_verification_task():
    key = _task_by_id()["migration-blocked-verification"].answer_key
    honest = {**key}
    ok, _ = evaluate_feasibility(honest, key)
    assert ok
    ok, reason = evaluate_feasibility({**key, "complete": True}, key)
    assert not ok and "complete" in reason


# --- the worked example -----------------------------------------------------


def _strip_times(text: str) -> str:
    return re.sub(r'"generated_at": "[^"]*"', '"generated_at": "-"', text)


def test_worked_example_reproduces_byte_for_byte(tmp_path):
    made = _run_pipeline(tmp_path, EXAMPLE / "packages", scenarios=True)
    smap, findings, packages, report = (
        made["map"],
        made["findings"],
        made["packages"],
        made["report"],
    )

    assert _strip_times(findings.read_text("utf-8")) == _strip_times(
        (EXAMPLE / "findings.json").read_text("utf-8")
    )
    assert smap.read_bytes() == (EXAMPLE / "source-map.json").read_bytes()
    for made in sorted(packages.iterdir()):
        assert made.read_bytes() == (EXAMPLE / "packages" / made.name).read_bytes(), made.name
    assert sorted(p.name for p in packages.iterdir()) == sorted(
        p.name for p in (EXAMPLE / "packages").iterdir()
    )
    assert report.read_bytes() == (EXAMPLE / "verification-report.json").read_bytes()


def test_results_fixture_carries_the_example_packages():
    for made in sorted((EXAMPLE / "packages").iterdir()):
        assert made.read_bytes() == (RESULTS / "packages" / made.name).read_bytes(), made.name


# --- the fixtures stay synthetic (public repo) ------------------------------

_GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_URL = re.compile(r"https?://[^\s\"'<>)]+")


def _text_files():
    for root in (PROCESS, RESULTS, EXAMPLE, *TASKS):
        paths = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        yield from paths


def test_fixtures_hold_no_guid_and_only_reserved_test_hosts():
    seen = 0
    for path in _text_files():
        text = path.read_text(encoding="utf-8")
        seen += 1
        assert not _GUID.search(text), f"GUID in {path}"
        for url in _URL.findall(text):
            host = url.split("/")[2]
            assert host.endswith(".test") or host in ("github.com", "learn.microsoft.com"), (
                f"{url} in {path}"
            )
    assert seen > 20
