"""`crm inventory check-applied`: a repository carries the patches of a convert ledger."""

# pyright: basic

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory_assess as assess
from crm.core.inventory_convert import LEDGER_NAME
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import dump_source_map, map_source, read_source_map
from crm.tests.test_inventory_convert import (
    ALONE_JS,
    ALONE_NAME,
    CTX_NAME,
    MIXED_JS,
    MIXED_NAME,
    World,
)


class Applied:
    """A convert output whose three repository-matched patches are applied to the repo."""

    def __init__(self, root: Path) -> None:
        world = World(root)
        self.repo, self.out = root / "repo", world.out
        for name, text in ((ALONE_NAME, ALONE_JS), (MIXED_NAME, MIXED_JS)):
            path = self.repo / "WebResources" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        world.source_map.write_text(
            dump_source_map(map_source(world.inventory, self.repo)), encoding="utf-8"
        )
        world.packages = root / "packages-matched"
        write_packages(
            build_packages(
                world.inventory,
                read_source_map(world.source_map),
                None,
                assess.read_findings(world.findings),
            ),
            world.packages,
        )
        world.run(with_source_map=True)
        self.entries = {
            e["key"]: e
            for e in json.loads((self.out / LEDGER_NAME).read_text(encoding="utf-8"))["entries"]
            if e["status"] == "patched" and e["repo_path"]
        }
        for entry in self.entries.values():
            patched = (self.out / entry["files"]["patched"]).read_bytes()
            (self.repo / entry["repo_path"]).write_bytes(patched)

    def file(self, key: str) -> Path:
        return self.repo / self.entries[key]["repo_path"]

    def run(self, *flags: str):
        args = [*flags, "inventory", "check-applied", str(self.out), "--repo", str(self.repo)]
        return CliRunner().invoke(cli, args)


@pytest.fixture
def applied(tmp_path: Path) -> Applied:
    return Applied(tmp_path)


def _rows(res) -> dict[str, dict]:
    return {r["key"]: r for r in json.loads(res.output)["data"]["rows"]}


def _snapshot(*roots: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}


def test_every_patch_applied_exits_0_and_lists_manual_review_as_not_checkable(applied):
    assert set(applied.entries) == {CTX_NAME, ALONE_NAME, MIXED_NAME}
    res = applied.run("--json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)["data"]
    assert data["all_applied"] is True and data["counts"]["applied"] == 3
    assert data["counts"]["not_checkable"] >= 1
    rows = _rows(res)
    assert rows["new_/html/page.htm"]["status"] == "not_checkable"
    assert rows["new_/html/page.htm"]["reason"]


def test_not_applied_diverged_and_missing_exit_1_and_name_the_right_entries(applied):
    original = applied.entries[ALONE_NAME]
    # not applied: the repository still holds the extracted text
    applied.file(ALONE_NAME).write_text(ALONE_JS, encoding="utf-8")
    # diverged: edited further after the patch
    applied.file(MIXED_NAME).write_text(
        applied.file(MIXED_NAME).read_text(encoding="utf-8") + "// later edit\n", encoding="utf-8"
    )
    applied.file(CTX_NAME).unlink()
    res = applied.run("--json")
    assert res.exit_code == 1, res.output
    envelope = json.loads(res.output)
    assert envelope["ok"] is False
    rows = _rows(res)
    assert rows[ALONE_NAME]["status"] == "not_applied"
    assert rows[MIXED_NAME]["status"] == "diverged"
    assert rows[CTX_NAME]["status"] == "missing"
    assert rows[ALONE_NAME]["path"] == original["repo_path"]


def test_human_output_exits_1_and_lists_the_rows_that_are_not_applied(applied):
    applied.file(ALONE_NAME).write_text(ALONE_JS, encoding="utf-8")
    res = applied.run()
    assert res.exit_code == 1 and "not_applied" in res.output and ALONE_NAME in res.output


def test_crlf_and_a_byte_order_mark_still_count_as_applied(applied):
    file = applied.file(CTX_NAME)
    text = file.read_bytes().decode("utf-8").replace("\n", "\r\n")
    file.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    res = applied.run("--json")
    assert res.exit_code == 0, res.output
    assert _rows(res)[CTX_NAME]["status"] == "applied"


def test_a_missing_ledger_and_an_empty_folder_are_usage_errors(applied, tmp_path):
    (applied.out / LEDGER_NAME).unlink()
    assert applied.run().exit_code == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    args = ["inventory", "check-applied", str(empty), "--repo", str(applied.repo)]
    res = CliRunner().invoke(cli, args)
    assert res.exit_code == 2 and "ledger" in res.output


def test_an_unknown_ledger_schema_version_is_a_usage_error(applied):
    ledger = applied.out / LEDGER_NAME
    data = json.loads(ledger.read_text(encoding="utf-8"))
    ledger.write_text(json.dumps({**data, "schema_version": 99}), encoding="utf-8")
    res = applied.run()
    assert res.exit_code == 2 and "schema_version 99" in res.output


def test_the_repository_and_the_convert_output_are_byte_identical_after_the_run(applied):
    applied.file(ALONE_NAME).write_text(ALONE_JS, encoding="utf-8")
    applied.file(CTX_NAME).unlink()
    before = _snapshot(applied.repo, applied.out)
    assert applied.run("--json").exit_code == 1
    assert applied.run().exit_code == 1
    assert _snapshot(applied.repo, applied.out) == before
