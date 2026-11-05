"""`crm inventory diff`: compare two migration inventories by component key (ADR 0032)."""

# pyright: basic

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory
from crm.core.inventory_diff import diff_inventories

FIXTURES = Path(__file__).parent / "fixtures" / "inventory"


def _sample(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name / "manifest.json").read_text(encoding="utf-8"))


def _entry(diff, kind: str, key: str):
    (found,) = [e for e in diff.entries if (e.kind, e.key) == (kind, key)]
    return found


def test_legacy_to_v9_gives_changed_unchanged_ambiguous_identity_only_and_not_comparable() -> None:
    legacy = inventory.parse_manifest(_sample("legacy"))
    v9 = inventory.parse_manifest(_sample("v9"))

    diff = diff_inventories(legacy, v9)

    validation = _entry(diff, "web_resource", "new_/scripts/validation.js")
    assert validation.verdict == "changed"
    assert validation.fields == ()
    (artifact,) = validation.artifacts
    assert artifact.path.endswith(".content.js")
    assert artifact.a != artifact.b and artifact.a and artifact.b
    # The same text with a BOM and CRLF on one side only: one normalized hash, so unchanged.
    assert _entry(diff, "web_resource", "new_/scripts/project.js").verdict == "unchanged"
    form = _entry(diff, "form", "new_project|2|Information")
    assert form.verdict == "ambiguous"
    assert form.reason == "the key repeats: 2 on A, 1 on B"
    entity = _entry(diff, "entity", "new_project")
    assert entity.verdict == "identity_only"
    assert entity.ids is not None and entity.ids[0] != entity.ids[1]
    (dependency,) = [e for e in diff.entries if e.kind == "dependency"]
    assert dependency.verdict == "not_comparable"
    assert dependency.reason == "dependency coverage is unsupported on A and complete on B"
    # The assembly's binary lives on disk at the CRM 2011 stage: a gap, not a changed artifact.
    assembly = _entry(diff, "plugin_assembly", "Contoso.Plugins")
    assert assembly.verdict == "not_comparable"
    assert assembly.reason is not None
    assert assembly.reason.startswith("extraction gap on A: assembly registered on disk")


def test_v9_to_cloud_removes_an_attribute_and_a_partial_kind_is_not_comparable() -> None:
    v9 = inventory.parse_manifest(_sample("v9"))
    cloud = inventory.parse_manifest(_sample("cloud"))

    forward = diff_inventories(v9, cloud)
    backward = diff_inventories(cloud, v9)

    assert _entry(forward, "attribute", "new_project|new_legacycode").verdict == "removed"
    assert _entry(backward, "attribute", "new_project|new_legacycode").verdict == "added"
    ribbon = _entry(forward, "ribbon", "")
    assert ribbon.verdict == "not_comparable"
    assert ribbon.reason == "ribbon coverage is complete on A and partial on B"
    assert forward.counts()["attribute"] == {"removed": 1, "unchanged": 1}
    assert backward.counts()["attribute"] == {"added": 1, "unchanged": 1}


def _with(manifest: dict[str, Any], edit) -> inventory.Inventory:
    copied = copy.deepcopy(manifest)
    edit(copied)
    return inventory.parse_manifest(copied)


def _record(manifest: dict[str, Any], kind: str, key: str) -> dict[str, Any]:
    (found,) = [r for r in manifest["records"] if (r["kind"], r["key"]) == (kind, key)]
    return found


def test_a_changed_field_lists_both_values_and_a_guid_change_beside_it() -> None:
    v9 = _sample("v9")

    def edit(m: dict[str, Any]) -> None:
        record = _record(m, "entity", "new_project")
        record["display_name"] = "Programme"
        record["fields"]["is_activity"] = True

    diff = diff_inventories(inventory.parse_manifest(v9), _with(v9, edit))

    entity = _entry(diff, "entity", "new_project")
    assert entity.verdict == "changed"
    old = _record(v9, "entity", "new_project")
    assert [(f.name, f.a, f.b) for f in entity.fields] == [
        ("display_name", old["display_name"], "Programme"),
        ("fields.is_activity", old["fields"].get("is_activity"), True),
    ]
    assert entity.ids is None
    # The GUID differs as well across stages: still a content change, the ids kept as evidence.
    legacy_to_edited = diff_inventories(
        inventory.parse_manifest(_sample("legacy")), _with(v9, edit)
    )
    assert _entry(legacy_to_edited, "entity", "new_project").verdict == "changed"
    assert _entry(legacy_to_edited, "entity", "new_project").ids is not None


def test_a_key_the_other_side_names_in_an_extraction_gap_is_not_a_removal() -> None:
    v9 = _sample("v9")

    def edit(m: dict[str, Any]) -> None:
        m["records"] = [r for r in m["records"] if r["key"] != "new_/scripts/validation.js"]
        m["coverage"]["web_resource"]["records"] -= 1
        m["gaps"].append(
            {
                "kind": "web_resource",
                "key": "new_/scripts/validation.js",
                "description": None,
                "reason": "content not readable",
            }
        )

    diff = diff_inventories(inventory.parse_manifest(v9), _with(v9, edit))

    entry = _entry(diff, "web_resource", "new_/scripts/validation.js")
    assert entry.verdict == "not_comparable"
    assert entry.reason == "extraction gap on B: content not readable"
    assert diff.summary()["gaps"]["b"][-1]["key"] == "new_/scripts/validation.js"


def test_the_summary_splits_verdicts_by_origin_and_states_platforms_and_scopes() -> None:
    legacy = inventory.parse_manifest(_sample("legacy"))
    v9 = inventory.parse_manifest(_sample("v9"))

    summary = diff_inventories(legacy, v9).summary()

    by_origin = summary["by_origin"]
    assert by_origin["system"] == {"unchanged": 4}
    assert by_origin["custom"] == {
        "changed": 1,
        "identity_only": 1,
        "ambiguous": 1,
        "not_comparable": 1,
        "unchanged": 13,
    }
    header = summary["header"]
    assert (header["a"]["platform"], header["b"]["platform"]) == ("crm2011", "onprem")
    assert header["same_platform"] is False and header["same_scope"] is True
    assert "not proof" in header["basis"]
    assert [g["reason"] for g in summary["gaps"]["a"]][0].startswith("ISV pages")


@pytest.fixture
def no_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty CRM_HOME: the verb must run without any saved profile or org.
    monkeypatch.setenv("CRM_HOME", str(tmp_path / "home"))


def _diff_cli(*args: str):
    return CliRunner().invoke(cli, ["--json", "inventory", "diff", *args])


def test_cli_returns_counts_per_kind_and_verdict_and_exits_0_on_differences(no_profile) -> None:
    result = _diff_cli(str(FIXTURES / "v9"), str(FIXTURES / "cloud"))

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["counts"]["attribute"] == {"removed": 1, "unchanged": 1}
    assert data["counts"]["ribbon"] == {"not_comparable": 1}
    assert data["header"]["b"]["platform"] == "cloud"
    assert "entries" not in data and data["out"] is None


def test_cli_out_is_byte_identical_across_runs_and_swapping_mirrors_added_and_removed(
    no_profile, tmp_path: Path
) -> None:
    v9, cloud = str(FIXTURES / "v9"), str(FIXTURES / "cloud")
    first, second, swapped = (tmp_path / n for n in ("1.json", "2.json", "swapped.json"))

    for out, pair in ((first, (v9, cloud)), (second, (v9, cloud)), (swapped, (cloud, v9))):
        result = _diff_cli(*pair, "--out", str(out))
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["out"] == str(out)

    assert first.read_bytes() == second.read_bytes()
    forward = {
        (e["kind"], e["key"]): e["verdict"] for e in json.loads(first.read_text())["entries"]
    }
    backward = {
        (e["kind"], e["key"]): e["verdict"] for e in json.loads(swapped.read_text())["entries"]
    }
    mirror = {"added": "removed", "removed": "added"}
    assert forward[("attribute", "new_project|new_legacycode")] == "removed"
    assert backward == {k: mirror.get(v, v) for k, v in forward.items()}


def test_cli_refuses_an_unreadable_schema_version_naming_both_versions(
    no_profile, tmp_path: Path
) -> None:
    newer = tmp_path / "newer"
    newer.mkdir()
    (newer / "manifest.json").write_text(
        (FIXTURES / "invalid" / "unknown-schema-version.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = _diff_cli(str(FIXTURES / "v9"), str(newer))

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert "A has schema_version 2 and B has schema_version 3" in error


def test_cli_human_mode_prints_a_table_per_kind(no_profile) -> None:
    result = CliRunner().invoke(
        cli, ["inventory", "diff", str(FIXTURES / "legacy"), str(FIXTURES / "v9")]
    )

    assert result.exit_code == 0, result.output
    assert "web_resource" in result.output and "identity_only" in result.output


def test_a_gap_on_a_key_both_sides_hold_still_reports_a_field_change() -> None:
    v9 = _sample("v9")

    def edit(m: dict[str, Any]) -> None:
        _record(m, "web_resource", "new_/scripts/validation.js")["display_name"] = "Checks"
        m["gaps"].append(
            {
                "kind": "web_resource",
                "key": "new_/scripts/validation.js",
                "description": None,
                "reason": "content not readable",
            }
        )

    diff = diff_inventories(inventory.parse_manifest(v9), _with(v9, edit))

    entry = _entry(diff, "web_resource", "new_/scripts/validation.js")
    assert entry.verdict == "changed"
    assert [f.name for f in entry.fields] == ["display_name"]


def test_an_artifact_on_one_side_with_no_gap_is_a_change_with_a_null_hash() -> None:
    v9 = _sample("v9")
    path = _record(v9, "web_resource", "new_/scripts/validation.js")["artifacts"][0]["path"]

    def edit(m: dict[str, Any]) -> None:
        _record(m, "web_resource", "new_/scripts/validation.js")["artifacts"] = []

    diff = diff_inventories(inventory.parse_manifest(v9), _with(v9, edit))

    entry = _entry(diff, "web_resource", "new_/scripts/validation.js")
    assert entry.verdict == "changed"
    assert [(c.path, c.b) for c in entry.artifacts] == [(path, None)]


def test_a_kind_with_no_coverage_entry_or_failed_on_both_sides_is_listed_as_incomplete() -> None:
    v9 = _sample("v9")

    def drop_views(m: dict[str, Any]) -> None:
        m["records"] = [r for r in m["records"] if r["kind"] != "view"]
        del m["coverage"]["view"]
        m["coverage"]["sitemap"] = m["coverage"]["sitemap"] | {"status": "failed"}

    def fail_sitemap(m: dict[str, Any]) -> None:
        m["records"] = [r for r in m["records"] if r["kind"] != "sitemap"]
        m["coverage"]["sitemap"] = m["coverage"]["sitemap"] | {"status": "failed", "records": 0}

    a = _with(v9, drop_views)
    diff = diff_inventories(a, _with(v9, fail_sitemap))

    assert _entry(diff, "view", "new_project|Active Projects").reason == (
        "view coverage is absent on A and complete on B"
    )
    assert diff.incomplete() == {
        "sitemap": {"a": "failed", "b": "failed"},
        "view": {"a": "absent", "b": "complete"},
    }
    assert diff.counts()["sitemap"] == {"not_comparable": 1}


def test_a_key_whose_origin_flipped_counts_under_the_same_origin_both_ways() -> None:
    v9 = _sample("v9")

    def edit(m: dict[str, Any]) -> None:
        _record(m, "entity", "account")["origin"] = "custom"

    a, b = inventory.parse_manifest(v9), _with(v9, edit)

    assert diff_inventories(a, b).by_origin() == diff_inventories(b, a).by_origin()
    assert _entry(diff_inventories(a, b), "entity", "account").origin == "custom"


def _folder(tmp_path: Path, name: str, text: str) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "manifest.json").write_text(text, encoding="utf-8")
    return folder


@pytest.mark.parametrize(
    ("b_text", "message"),
    [
        (None, "cannot read"),
        ("[]", "B ("),
        ('{"schema_version": "1"}', "A has schema_version 2 and B has schema_version '1'"),
    ],
)
def test_cli_exits_1_when_b_holds_no_readable_manifest(
    no_profile, tmp_path: Path, b_text: str | None, message: str
) -> None:
    b = tmp_path / "empty" if b_text is None else _folder(tmp_path, "b", b_text)

    result = _diff_cli(str(FIXTURES / "v9"), str(b))

    assert result.exit_code == 1
    assert message in json.loads(result.output)["error"]


def test_cli_exits_1_when_out_cannot_be_written(no_profile, tmp_path: Path) -> None:
    out = tmp_path / "missing-dir" / "diff.json"

    result = _diff_cli(str(FIXTURES / "v9"), str(FIXTURES / "cloud"), "--out", str(out))

    assert result.exit_code == 1
    assert json.loads(result.output)["error"].startswith(f"--out {out}:")


def test_a_key_both_sides_gap_with_nothing_differing_is_not_comparable() -> None:
    v9 = _sample("v9")

    def edit(m: dict[str, Any]) -> None:
        _record(m, "web_resource", "new_/scripts/validation.js")["artifacts"] = []
        m["gaps"].append(
            {
                "kind": "web_resource",
                "key": "new_/scripts/validation.js",
                "description": None,
                "reason": "content not readable",
            }
        )

    gapped = _with(v9, edit)
    entry = _entry(diff_inventories(gapped, gapped), "web_resource", "new_/scripts/validation.js")

    assert entry.verdict == "not_comparable"
    assert entry.reason == (
        "extraction gap on A: content not readable; extraction gap on B: content not readable"
    )


@pytest.mark.parametrize("side", ["A", "B"])
def test_a_key_one_side_gap_with_nothing_differing_is_not_comparable(side: str) -> None:
    v9 = _sample("v9")

    def edit(m: dict[str, Any]) -> None:
        m["gaps"].append(
            {
                "kind": "web_resource",
                "key": "new_/scripts/project.js",
                "description": None,
                "reason": "content not readable",
            }
        )

    gapped, plain = _with(v9, edit), inventory.parse_manifest(v9)
    a, b = (gapped, plain) if side == "A" else (plain, gapped)
    entry = _entry(diff_inventories(a, b), "web_resource", "new_/scripts/project.js")

    assert entry.verdict == "not_comparable"
    assert entry.reason == f"extraction gap on {side}: content not readable"


def _with_name_collisions(version: int) -> dict[str, Any]:
    """The v9 sample plus an entity `application`, its ribbon and a sitemap named `default`.

    At v1 the application ribbon and default sitemap carry the keys `application` and
    `default` (the collision #1070 fixes); at v2 they carry "".
    """
    manifest = _sample("v9")
    null_key = {"ribbon": "application", "sitemap": "default"}
    for record in manifest["records"]:
        if version == 1 and record["kind"] in null_key:
            record["key"] = null_key[record["kind"]]
    extra = [
        ("entity", "application", {"logical_name": "application"}),
        ("ribbon", "application", {"entity": "application"}),
        ("sitemap", "default", {"unique_name": "default"}),
    ]
    for kind, key, fields in extra:
        manifest["records"].append(
            {"kind": kind, "key": key, "id": None, "display_name": None, "origin": "custom",
             "managed": False, "publication_state": "published", "fields": fields,
             "artifacts": []}
        )  # fmt: skip
        manifest["coverage"][kind]["records"] += 1
    # An artifact's file name carries its key's slug and digest, so v1 names the null
    # records' files `application-...` and `default-...`, and gives the entity ribbon and
    # the sitemap named `default` (which shared the key) a discriminator.
    for record in manifest["records"]:
        if record["kind"] not in null_key:
            continue
        if not record["artifacts"]:
            digest = "ab" * 32
            record["artifacts"] = [
                {"content": "text", "normalized_sha256": digest, "sha256": digest, "path": ""}
            ]
        collides = version == 1 and None not in record["fields"].values()
        for art in record["artifacts"]:
            art["path"] = inventory.artifact_path(
                record["kind"],
                record["key"],
                f"{record['kind']}.xml",
                discriminator="source-id" if collides else None,
            )
    manifest["schema_version"] = version
    return manifest


def test_name_collisions_get_distinct_keys_and_diff_against_themselves_unchanged() -> None:
    v2 = inventory.parse_manifest(_with_name_collisions(2))

    keys = {(r.kind, r.key) for r in v2.records}
    assert {
        ("ribbon", ""),
        ("ribbon", "application"),
        ("sitemap", ""),
        ("sitemap", "default"),
    } <= keys
    assert ("entity", "application") in keys
    assert not v2.ambiguous()
    diff = diff_inventories(v2, v2)
    assert {e.verdict for e in diff.entries if e.kind in ("ribbon", "sitemap")} == {"unchanged"}


def test_a_v1_manifest_holding_the_collision_upgrades_and_diffs_cleanly_against_v2() -> None:
    v1 = inventory.parse_manifest(_with_name_collisions(1))
    v2 = inventory.parse_manifest(_with_name_collisions(2))

    assert v1.schema_version == 2
    assert not v1.ambiguous()
    diff = diff_inventories(v1, v2)
    assert {e.verdict for e in diff.entries} == {"unchanged"}
    assert _entry(diff, "ribbon", "").verdict == "unchanged"
    assert _entry(diff, "sitemap", "").verdict == "unchanged"


def test_artifacts_pair_by_name_when_a_key_change_moves_their_file_path() -> None:
    v1 = inventory.parse_manifest(_with_name_collisions(1))
    v2 = inventory.parse_manifest(_with_name_collisions(2))

    def null_ribbon_paths(inv: inventory.Inventory) -> set[str]:
        return {
            a.path for r in inv.records if r.kind == "ribbon" and r.fields["entity"] is None
            for a in r.artifacts
        }  # fmt: skip

    assert null_ribbon_paths(v1).isdisjoint(null_ribbon_paths(v2))
    diff = diff_inventories(v1, v2)
    assert all(not e.artifacts for e in diff.entries)
    assert _entry(diff, "ribbon", "application").verdict == "unchanged"
