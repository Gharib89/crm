"""`inventory packages` and `assess`: derived usage, `--in-use`, `--exclude-unknown`, `--sort`.

The derivation is offline and works on synthetic records; the CLI tests run over a small
synthetic inventory whose entities carry (or lack) a `usage` as `extract --with-usage` writes it.
"""

# pyright: basic

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core.inventory_packages import Edge
from crm.core.inventory_usage_filter import derive_usage, keeps
from crm.core.inventory_usage_filter import package_usage as _package_usage
from crm.core.usage import Usage

AS_OF = "2026-11-01T10:00:00Z"
LIB = "new_/scripts/lib.js"


def _usage(state: str) -> dict:
    reason = "no record" if state == "unknown" else None
    return {
        "state": state,
        "signal": "record_presence",
        "value": 0 if state == "unused" else None,
        "as_of": AS_OF,
        "reason": reason,
    }


def _rec(kind: str, fields: dict, usage: str | None = None) -> inv.Record:
    key = inv.component_key(kind, fields)
    u = None if usage is None else _usage(usage)
    return inv.Record(kind, key, None, None, "custom", False, "published", fields, (), u)


def _entity(name: str, usage: str | None = None) -> inv.Record:
    return _rec("entity", {"logical_name": name}, usage)


def _form(entity: str, name: str = "Main") -> inv.Record:
    return _rec("form", {"entity": entity, "form_type": 2, "name": name})


def _wr(name: str) -> inv.Record:
    return _rec("web_resource", {"name": name, "web_resource_type": 3})


def _derive(records, edges=()) -> dict[str, Any]:
    """The derived usage by node id; Any, since a test reads `.state` of what it expects set."""
    return derive_usage(records, edges, AS_OF)


def package_usage(members, as_of: str) -> Any:
    return _package_usage(members, as_of)


def test_a_web_resource_a_form_points_at_is_used():
    wr, form = _wr(LIB), _form("new_project")
    edge = Edge(f"form:{form.key}", f"web_resource:{LIB}", "form_library")

    got = _derive([wr, form], [edge])[f"web_resource:{LIB}"]

    assert (got.state, got.signal) == ("used", "referenced_by")
    assert form.key in (got.reason or "")


@pytest.mark.parametrize("source", ["ribbon", "sitemap"])
def test_a_ribbon_or_sitemap_reference_also_counts(source):
    edge = Edge(f"{source}:x", f"web_resource:{LIB}", f"{source}_reference")

    assert _derive([_wr(LIB)], [edge])[f"web_resource:{LIB}"].state == "used"


def test_an_unreferenced_web_resource_is_unknown_never_unused():
    got = _derive([_wr(LIB)])[f"web_resource:{LIB}"]

    assert (got.state, got.signal, got.reason) == ("unknown", "referenced_by", "unreferenced")


def test_another_web_resource_pointing_at_it_does_not_make_it_used():
    edge = Edge("web_resource:new_/page.htm", f"web_resource:{LIB}", "web_resource_reference")

    assert _derive([_wr(LIB)], [edge])[f"web_resource:{LIB}"].state == "unknown"


def test_a_form_of_an_entity_with_no_records_is_unused_at_low_confidence():
    form = _form("new_project")

    got = _derive([_entity("new_project", "unused"), form])[f"form:{form.key}"]

    assert (got.state, got.signal) == ("unused", "entity_rollup")
    assert got.reason is not None and got.reason.startswith("entity has no records")
    assert "low confidence" in got.reason


def test_the_rollup_covers_views_and_the_entity_ribbon():
    view = _rec("view", {"entity": "new_project", "name": "Active"})
    ribbon = _rec("ribbon", {"entity": "new_project"})

    got = _derive([_entity("new_project", "unused"), view, ribbon])

    assert got[f"view:{view.key}"].state == "unused"
    assert got[f"ribbon:{ribbon.key}"].state == "unused"


@pytest.mark.parametrize("entity_usage", ["unknown", "used", None])
def test_an_entity_that_is_not_proven_unused_leaves_its_forms_untouched(entity_usage):
    form = _form("new_project")

    got = _derive([_entity("new_project", entity_usage), form])

    assert got[f"form:{form.key}"] is None


def test_a_form_whose_entity_is_not_in_the_inventory_is_untouched():
    form = _form("account")

    assert _derive([form])[f"form:{form.key}"] is None


def test_a_record_with_its_own_usage_keeps_it():
    form = _rec("form", {"entity": "new_project", "form_type": 2, "name": "Main"}, "used")

    got = _derive([_entity("new_project", "unused"), form])[f"form:{form.key}"]

    assert got.state == "used" and got.signal == "record_presence"


def test_the_application_ribbon_has_no_entity_to_roll_up():
    ribbon = _rec("ribbon", {"entity": None})

    assert _derive([_entity("new_project", "unused"), ribbon])[f"ribbon:{ribbon.key}"] is None


@pytest.mark.parametrize("flags", [(False, False), (True, False), (False, True), (True, True)])
def test_a_row_with_no_usage_is_always_kept(flags):
    assert keeps(None, in_use=flags[0], exclude_unknown=flags[1])


def test_the_flags_drop_by_state():
    unused, unknown, used = (Usage(s, "s", None, AS_OF) for s in ("unused", "unknown", "used"))

    assert keeps(unused, in_use=False, exclude_unknown=False)
    assert not keeps(unused, in_use=True, exclude_unknown=False)
    assert keeps(unknown, in_use=True, exclude_unknown=False)
    assert not keeps(unknown, in_use=False, exclude_unknown=True)
    assert keeps(used, in_use=True, exclude_unknown=True)


def test_a_package_is_used_if_a_member_is_and_unused_only_if_every_member_is():
    used, unused, unknown = (Usage(s, "s", None, AS_OF) for s in ("used", "unused", "unknown"))

    assert package_usage([unused, used, None], AS_OF).state == "used"
    assert package_usage([unused, unused], AS_OF).state == "unused"
    assert package_usage([unused, unknown], AS_OF).state == "unknown"


def test_a_member_that_was_not_read_keeps_a_package_from_reading_unused():
    unused = Usage("unused", "s", None, AS_OF)

    assert package_usage([unused, None], AS_OF).state == "unknown"


def test_a_package_with_no_member_usage_has_none():
    assert package_usage([None, None], AS_OF) is None


# ── The commands ──────────────────────────────────────────────────────────────

JS = "var Lib = {};\n"
FORM_XML = f'<form><formLibraries><Library name="{LIB}" /></formLibraries></form>'


def _write(folder: Path, records: list[inv.Record], files: dict[str, str], gaps=()) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for r in records:
        content = files.get(r.kind + ":" + r.key)
        arts = ()
        if content is not None:
            name = "content.js" if r.kind == "web_resource" else "form.xml"
            path = inv.artifact_path(r.kind, r.key, name)
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_bytes(content.encode())
            h = inv.artifact_hashes(content.encode(), text=True)
            arts = (inv.Artifact(path, "text", h.sha256, h.normalized_sha256),)
        out.append(inv.Record(**{**r.__dict__, "artifacts": arts}))
    counts: dict[str, int] = {}
    for r in out:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("cloud", "https://crm.example.test/Org", None, "9.2.0.0000"),
        extracted_at=AS_OF,
        cli_version="1.96.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(out),
        coverage={k: inv.KindCoverage("complete", 1, n, False, ()) for k, n in counts.items()},
        gaps=tuple(gaps),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


def _inventory(tmp_path: Path, *, usage: bool) -> Path:
    """Four packages: used (a live form's library, a dead form's script), unknown, unused."""
    u = (lambda s: s) if usage else (lambda s: None)
    live_form, dead_form = _form("new_live"), _form("new_dead")
    records = [
        _entity("new_live", u("used")),
        _entity("new_dead", u("unused")),
        live_form,
        dead_form,
        _wr(LIB),
        _wr("new_/scripts/orphan.js"),
        _wr("new_/scripts/dead.js"),
        _rec("plugin_assembly", {"name": "Contoso.Dead"}, u("unused")),
    ]
    files = {
        f"form:{live_form.key}": FORM_XML,
        f"form:{dead_form.key}": FORM_XML.replace(LIB, "new_/scripts/dead.js"),
    }
    gap = inv.ExtractionGap("plugin_assembly", "Contoso.Dead", None, "stored on disk")
    return _write(tmp_path / "inv", records, files, [gap])


def _source_map(folder: Path, tmp_path: Path) -> Path:
    """A repo holding every script, so each web resource is mapped and heads a package."""
    repo = tmp_path / "repo"
    for name in (LIB, "new_/scripts/orphan.js", "new_/scripts/dead.js"):
        (repo / "WebResources" / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / "WebResources" / name).write_text(JS, encoding="utf-8")
    path = tmp_path / "map.json"
    args = ["inventory", "map-source", str(folder), "--repo", str(repo), "--out", str(path)]
    run = CliRunner().invoke(cli, args)
    assert run.exit_code == 0, run.output
    return path


def _packages(folder, tmp_path, *flags, out="out"):
    args = ["--json", "inventory", "packages", str(folder), "--source-map"]
    args += [str(_source_map(folder, tmp_path)), "--out", str(tmp_path / out), *flags]
    run = CliRunner().invoke(cli, args)
    assert run.exit_code == 0, run.output
    return json.loads(run.output)["data"], tmp_path / out


def _members(out: Path) -> dict[str, list[str]]:
    index = json.loads((out / "index.json").read_text())
    return {
        p["id"]: json.loads((out / p["file"]).read_text())["members"] for p in index["packages"]
    }


def test_packages_show_a_usage_that_says_why(tmp_path):
    folder = _inventory(tmp_path, usage=True)

    _, out = _packages(folder, tmp_path)
    index = json.loads((out / "index.json").read_text())

    states = {p["usage"]["state"] for p in index["packages"]}
    assert states <= {"used", "unused", "unknown"}
    assert all(p["usage"]["signal"] for p in index["packages"])
    assert all((out / p["file"]).exists() for p in index["packages"])


def test_in_use_drops_a_package_proven_unused_and_keeps_the_rest(tmp_path):
    folder = _inventory(tmp_path, usage=True)
    every, _ = _packages(folder, tmp_path, out="all")

    kept, out = _packages(folder, tmp_path, "--in-use")

    assert kept["packages"] == every["packages"] - 1
    assert kept["usage_filtered"] == 1
    index = json.loads((out / "index.json").read_text())
    assert "unused" not in {p["usage"]["state"] for p in index["packages"]}


def test_exclude_unknown_with_in_use_keeps_only_used_packages(tmp_path):
    folder = _inventory(tmp_path, usage=True)
    in_use, _ = _packages(folder, tmp_path, "--in-use", out="a")

    strict, out = _packages(folder, tmp_path, "--in-use", "--exclude-unknown", out="b")

    assert strict["packages"] <= in_use["packages"]
    index = json.loads((out / "index.json").read_text())
    assert {p["usage"]["state"] for p in index["packages"]} <= {"used"}


def test_in_use_on_a_manifest_without_usage_loses_no_package(tmp_path):
    folder = _inventory(tmp_path, usage=False)
    every, _ = _packages(folder, tmp_path, out="all")

    filtered, _ = _packages(folder, tmp_path, "--in-use", out="f")

    assert filtered["packages"] == every["packages"]
    assert filtered["usage_filtered"] == 0


def test_exclude_unknown_on_a_manifest_without_usage_drops_only_the_derived_unknown(tmp_path):
    folder = _inventory(tmp_path, usage=False)
    every, _ = _packages(folder, tmp_path, out="all")

    strict, out = _packages(folder, tmp_path, "--in-use", "--exclude-unknown", out="f")

    assert strict["usage_filtered"] == 1  # the unreferenced script's package
    assert strict["packages"] == every["packages"] - 1
    index = json.loads((out / "index.json").read_text())
    assert all(p["usage"]["state"] == "used" for p in index["packages"] if "usage" in p)


def test_sort_by_usage_puts_used_packages_first(tmp_path):
    folder = _inventory(tmp_path, usage=True)

    _, out = _packages(folder, tmp_path, "--sort", "usage")
    index = json.loads((out / "index.json").read_text())

    rank = {"used": 0, "unknown": 1, "unused": 2}
    ranks = [rank[p["usage"]["state"]] if p.get("usage") else 1 for p in index["packages"]]
    assert ranks == sorted(ranks)


def _assess(folder, tmp_path, *flags, out="f.json"):
    args = ["--json", "inventory", "assess", str(folder), "--target", "online"]
    run = CliRunner().invoke(cli, [*args, "--out", str(tmp_path / out), *flags])
    assert run.exit_code == 0, run.output
    return json.loads(run.output)["data"], json.loads((tmp_path / out).read_text())


def _keys(findings: dict) -> set[str]:
    return {f"{f['kind']}:{f['key']}" for f in findings["findings"]}


def test_assess_findings_carry_the_derived_usage(tmp_path):
    folder = _inventory(tmp_path, usage=True)

    _, findings = _assess(folder, tmp_path)

    by = {f"{f['kind']}:{f['key']}": f for f in findings["findings"]}
    assert by[f"web_resource:{LIB}"]["usage"]["state"] == "used"
    assert by["web_resource:new_/scripts/orphan.js"]["usage"]["reason"] == "unreferenced"
    assert by["entity:new_dead"]["usage"]["state"] == "unused"


def test_assess_in_use_drops_the_findings_of_unused_rows(tmp_path):
    folder = _inventory(tmp_path, usage=True)
    _, every = _assess(folder, tmp_path, out="all.json")

    data, kept = _assess(folder, tmp_path, "--in-use")

    assert "entity:new_dead" in _keys(every) and "entity:new_dead" not in _keys(kept)
    assert "web_resource:new_/scripts/orphan.js" in _keys(kept)
    assert data["counts"]["by_usage"]["unused"] == 0


def test_assess_records_how_many_findings_the_flags_dropped(tmp_path):
    folder = _inventory(tmp_path, usage=True)
    every_data, every = _assess(folder, tmp_path, out="all.json")

    data, kept = _assess(folder, tmp_path, "--in-use")

    dropped = len(every["findings"]) - len(kept["findings"])
    assert dropped > 0
    assert data["usage_filtered"] == dropped == kept["usage_filtered"]
    assert every_data["usage_filtered"] == 0 and "usage_filtered" not in every


def test_assess_without_flags_survives_an_unreadable_artifact(tmp_path):
    folder = _inventory(tmp_path, usage=True)
    for path in folder.rglob("*"):
        if path.is_file() and path.name != "manifest.json" and path.suffix in (".js", ".xml"):
            path.unlink()

    data, _ = _assess(folder, tmp_path)

    assert data["counts"]["by_class"]


def test_assess_exclude_unknown_drops_unknown_rows_too(tmp_path):
    folder = _inventory(tmp_path, usage=True)

    _, kept = _assess(folder, tmp_path, "--in-use", "--exclude-unknown")

    assert "web_resource:new_/scripts/orphan.js" not in _keys(kept)
    assert f"web_resource:{LIB}" in _keys(kept)


def test_assess_on_a_manifest_without_usage_loses_no_row_that_has_none(tmp_path):
    folder = _inventory(tmp_path, usage=False)
    _, every = _assess(folder, tmp_path, out="all.json")

    _, kept = _assess(folder, tmp_path, "--in-use", "--exclude-unknown")

    no_usage = {k for k in _keys(every) if not k.startswith("web_resource:")}
    assert no_usage <= _keys(kept)


def test_assess_sort_by_usage_orders_findings_used_first(tmp_path):
    folder = _inventory(tmp_path, usage=True)

    _, findings = _assess(folder, tmp_path, "--sort", "usage")

    rank = {"used": 0, "unknown": 1, "unused": 2}
    ranks = [rank[f["usage"]["state"]] if "usage" in f else 1 for f in findings["findings"]]
    assert ranks == sorted(ranks)


@pytest.mark.parametrize("command", ["packages", "assess"])
def test_exclude_unknown_without_in_use_is_a_usage_error(tmp_path, command):
    folder = _inventory(tmp_path, usage=True)
    args = ["inventory", command, str(folder), "--exclude-unknown"]
    (tmp_path / "m.json").write_text("{}", encoding="utf-8")
    args += (
        ["--target", "online"]
        if command == "assess"
        else ["--source-map", str(tmp_path / "m.json")]
    )
    args += ["--out", str(tmp_path / "o")]

    run = CliRunner().invoke(cli, args)

    assert run.exit_code == 2 and "--exclude-unknown needs --in-use" in run.output
