"""`crm inventory assess`: compatibility rules over a migration inventory."""

# pyright: basic

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import jsonschema
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core import inventory_assess as assess

SCHEMAS = Path(assess.__file__).parent.parent / "schemas"
ORG = "https://crm.example.test/Org"
XRM_PAGE_JS = (
    "function onLoad() {\n  var name = Xrm.Page.getAttribute('name');\n  return name;\n}\n"
)
CLEAN_JS = "function onLoad(ctx) {\n  return ctx.getFormContext();\n}\n"


def _record(kind: str, fields: dict, folder: Path, *, files=()) -> inv.Record:
    key = inv.component_key(kind, fields)
    artifacts = []
    for name, data, text in files:
        path = inv.artifact_path(kind, key, name)
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_bytes(data)
        hashes = inv.artifact_hashes(data, text=text)
        content = "text" if text else "binary"
        artifacts.append(inv.Artifact(path, content, hashes.sha256, hashes.normalized_sha256))
    return inv.Record(kind, key, None, None, "custom", False, "published", fields, tuple(artifacts))


def _script(name: str, content: str, folder: Path) -> inv.Record:
    fields = {"name": name, "web_resource_type": 3}
    return _record("web_resource", fields, folder, files=[("content.js", content.encode(), True)])


PROCESSES = (
    ("Intake dialog", 1),
    ("Approve project", 0),
    ("Project flow", 4),
    ("Cloud flow", 5),
)


OOB_XAML = (
    "<Activity><mxswa:ActivityReference "
    'AssemblyQualifiedName="Microsoft.Crm.Workflow.Activities.CreateEntity, '
    'Microsoft.Crm.Workflow, Version=9.0.0.0" /></Activity>'
)


def _process(name: str, category: int, folder: Path, xaml: str | None = OOB_XAML) -> inv.Record:
    """A process record; a classic workflow (category 0) carries *xaml* as its text artifact."""
    fields = {"category": category, "primary_entity": "new_project", "name": name}
    files = [("workflow.xaml", xaml.encode(), True)] if category == 0 and xaml is not None else ()
    return _record("process", fields, folder, files=files)


def _assembly(name: str, folder: Path, **fields) -> inv.Record:
    return _record("plugin_assembly", {"name": name, **fields}, folder)


def _write_inventory(folder: Path, *, version: str = "5.0.9690.4376", gaps=()) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    records = [
        _script("new_/scripts/legacy.js", XRM_PAGE_JS, folder),
        _script("new_/scripts/clean.js", CLEAN_JS, folder),
        _record(
            "web_resource",
            {"name": "new_/img/logo.png", "web_resource_type": 5},
            folder,
            files=[("content.png", b"\x89PNG", False)],
        ),
        _assembly("Contoso.Gac", folder, source_type=2, isolation_mode=2),
        _assembly("Contoso.Open", folder, source_type=0, isolation_mode=1),
        _assembly("Contoso.Sandboxed", folder, source_type=0, isolation_mode=2),
        _assembly("Contoso.Unrecorded", folder, source_type=0),
        _record("entity", {"logical_name": "new_project"}, folder),
        _record("view", {"entity": "new_project", "name": "Active Projects"}, folder),
        *(_process(name, category, folder) for name, category in PROCESSES),
    ]
    counts: dict[str, int] = {}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    coverage = {k: inv.KindCoverage("complete", 1, n, False, ()) for k, n in counts.items()}
    coverage["view"] = inv.KindCoverage("partial", 1, 1, True, ("page 2 failed",))
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", ORG, None, version),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.91.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage=coverage,
        gaps=(
            inv.ExtractionGap(
                "plugin_assembly", "Contoso.Gac", None, "the assembly binary is stored in the GAC"
            ),
            inv.ExtractionGap(None, None, "external integrations", "outside the org"),
            *gaps,
        ),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


@pytest.fixture
def inventory(tmp_path: Path) -> Path:
    return _write_inventory(tmp_path / "inv")


def _findings(result, key: str) -> list:
    return [f for f in result.findings if f.key == key]


def _classes(result, key: str) -> set[str]:
    return {f.cls for f in _findings(result, key)}


def test_xrm_page_is_deprecated_with_its_location(inventory):
    result = assess.assess(inventory, "online")
    (hit,) = [f for f in _findings(result, "new_/scripts/legacy.js") if f.rule]
    assert (hit.cls, hit.rule) == ("deprecated", "web-resource-xrm-page")
    record = next(r for r in inv.read_inventory(inventory).records if r.key == hit.key)
    (artifact,) = record.artifacts
    assert hit.evidence is not None
    assert hit.evidence.path == artifact.path and hit.evidence.sha256 == artifact.sha256
    assert hit.evidence.locations == ((2, 14),)
    assert hit.evidence.match_count == 1


def test_a_gac_assembly_is_a_blocker_online_and_its_gap_is_unknown(inventory):
    result = assess.assess(inventory, "online")
    gac = _findings(result, "Contoso.Gac")
    assert {(f.cls, f.rule) for f in gac} >= {("blocker", "assembly-storage-online")}
    (gap,) = [f for f in gac if f.cls == "unknown" and f.rule is None]
    assert gap.reason == "extraction gap: the assembly binary is stored in the GAC"


def test_isolation_none_is_a_blocker_online_and_an_unrecorded_field_is_unknown(inventory):
    result = assess.assess(inventory, "online")
    assert ("blocker", "assembly-isolation-none") in {
        (f.cls, f.rule) for f in _findings(result, "Contoso.Open")
    }
    (unknown,) = [
        f for f in _findings(result, "Contoso.Unrecorded") if f.rule == "assembly-isolation-none"
    ]
    assert unknown.cls == "unknown" and unknown.rule == "assembly-isolation-none"
    assert unknown.reason == "field isolation_mode not recorded"
    # No source map: its project rules were not run, so the assembly is not `clear` (#1292).
    assert _classes(result, "Contoso.Sandboxed") == {"unknown"}


def test_targets_select_the_rules(inventory):
    result = assess.assess(inventory, "onprem-9.1")
    assert "blocker" not in _classes(result, "Contoso.Gac")
    assert "blocker" not in _classes(result, "Contoso.Open")


UNCOVERED = [
    {"kind": "entity", "records": 1, "reason": "no rule"},
    {"kind": "view", "records": 1, "reason": "no rule"},
]


def test_a_kind_no_rule_covers_rolls_up_to_one_entry_per_kind(inventory):
    result = assess.assess(inventory, "online")
    assert not _findings(result, "new_project")
    assert result.summary()["uncovered_kinds"] == UNCOVERED
    data = json.loads(assess.dump_findings(result))
    assert data["uncovered_kinds"] == UNCOVERED
    assert not [f for f in data["findings"] if f["reason"] == "no rule" and f["kind"] == "entity"]
    assert "entity" not in result.summary()["counts"]["by_kind"]


def test_a_covered_kind_record_no_rule_selects_keeps_its_own_no_rule_finding(inventory):
    result = assess.assess(inventory, "online")
    (logo,) = _findings(result, "new_/img/logo.png")
    assert (logo.cls, logo.reason, logo.rule) == ("unknown", "no rule", None)
    assert "web_resource" not in {u["kind"] for u in result.summary()["uncovered_kinds"]}


def test_an_uncovered_kind_record_keeps_its_gap_and_coverage_unknowns(tmp_path):
    gap = inv.ExtractionGap("entity", "new_project", None, "metadata page failed")
    result = assess.assess(_write_inventory(tmp_path / "inv", gaps=(gap,)), "online")
    (entity,) = _findings(result, "new_project")
    assert (entity.cls, entity.reason) == ("unknown", "extraction gap: metadata page failed")
    reasons = {f.reason for f in _findings(result, "new_project|Active Projects")}
    assert reasons == {"kind coverage partial"}
    assert result.summary()["uncovered_kinds"] == UNCOVERED


def test_a_findings_file_round_trips_its_uncovered_kinds(inventory, tmp_path):
    path = tmp_path / "findings.json"
    path.write_text(assess.dump_findings(assess.assess(inventory, "online")), encoding="utf-8")
    assert assess.read_findings(path).uncovered_kinds == {"entity": 1, "view": 1}


def test_a_version_1_findings_file_is_refused_with_a_rerun_hint(inventory, tmp_path):
    data = json.loads(assess.dump_findings(assess.assess(inventory, "online")))
    data["schema_version"] = 1
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(inv.InventoryError, match="re-run `crm inventory assess`"):
        assess.read_findings(path)


@pytest.mark.parametrize("target", ["online", "onprem-9.1"])
def test_a_dialog_is_deprecated_and_the_other_processes_are_clear(inventory, target):
    result = assess.assess(inventory, target)
    (hit,) = _findings(result, "1|new_project|Intake dialog")
    assert (hit.cls, hit.rule, hit.reason) == ("deprecated", "process-dialog", None)
    assert hit.evidence is not None and (hit.evidence.field, hit.evidence.value) == ("category", 1)
    for name, category in PROCESSES[1:]:
        (other,) = _findings(result, f"{category}|new_project|{name}")
        assert (other.cls, other.rule, other.reason) == ("clear", None, None)


def _names_replacements_without_conversion(fix: str) -> bool:
    replaces = re.match(r"Rebuild the dialog as a business process flow .*or a canvas app", fix)
    return bool(replaces) and "there is no automatic conversion" in fix


def test_the_dialog_fix_prose_check_rejects_its_negation():
    negated = (
        "Do not rebuild the dialog as a business process flow or a canvas app; "
        "there is an automatic conversion."
    )
    assert not _names_replacements_without_conversion(negated)


def test_the_dialog_rule_names_the_replacements_and_its_sources(inventory):
    data = json.loads(assess.dump_findings(assess.assess(inventory, "online")))
    rule = data["rules_hit"]["process-dialog"]
    assert _names_replacements_without_conversion(rule["fix"])
    assert any("replace-dialogs" in s for s in rule["sources"])
    assert any("important-changes-coming" in s for s in rule["sources"])


def test_the_dialog_rule_is_deprecated_from_the_2011_source():
    (rule,) = [r for r in assess.load_rules().rules if r.id == "process-dialog"]
    assert (rule.cls, rule.kind, rule.source_from) == ("deprecated", "process", "5.0")


def test_partial_coverage_gives_unknown_for_its_components(inventory):
    result = assess.assess(inventory, "online")
    reasons = {f.reason for f in _findings(result, "new_project|Active Projects")}
    assert "kind coverage partial" in reasons


def test_every_component_of_a_covered_kind_gets_a_finding(inventory):
    result = assess.assess(inventory, "online")
    keys = {(r.kind, r.key) for r in inv.read_inventory(inventory).records if r.kind != "entity"}
    assert keys == {(f.kind, f.key) for f in result.findings}
    assert _classes(result, "new_/scripts/clean.js") == {"clear"}
    assert [g["description"] for g in result.unattached_gaps] == ["external integrations"]


def test_a_source_outside_a_rule_range_skips_that_rule(tmp_path):
    folder = _write_inventory(tmp_path / "v9", version="9.0.2.3034")
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    for rule in data["rules"]:
        if rule["id"] == "web-resource-xrm-page":
            rule["source"]["before"] = "9.0"
    rules = assess.parse_rules(data)
    result = assess.assess(folder, "online", rules=rules)
    assert _classes(result, "new_/scripts/legacy.js") == {"clear"}
    assert "deprecated" in _classes(
        assess.assess(tmp_path / "v9", "online"), "new_/scripts/legacy.js"
    )


def _content_pattern(pattern: str):
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    rule = next(r for r in data["rules"] if r["id"] == "web-resource-xrm-page")
    rule["detection"]["pattern"] = pattern
    parsed = assess.parse_rules(data)
    detection = next(r for r in parsed.rules if r.id == "web-resource-xrm-page").detection
    assert isinstance(detection, assess.ContentPattern)
    return detection.pattern


@pytest.mark.parametrize(
    ("source", "hit"),
    [
        ("a.b", True),
        ("a /* c */ . b", True),
        ("a // c\n . b", True),
        ("a /* c\n d */ .b", True),
        ("a /* c . b", False),
        ("a / b", False),
    ],
    ids=["none", "block", "line", "multi-line", "unclosed", "slash"],
)
def test_the_gap_token_spans_whitespace_and_comments(source, hit):
    assert bool(_content_pattern(r"a<gap>\.<gap>b").search(source)) is hit


def test_the_gap_token_does_not_backtrack_on_a_run_of_slashes():
    started = time.monotonic()
    assert not _content_pattern(r"\.<gap>b").search("x." + "/" * 40)
    assert time.monotonic() - started < 1


def test_an_artifact_changed_since_extraction_is_unknown(inventory):
    record = next(
        r for r in inv.read_inventory(inventory).records if r.key == "new_/scripts/clean.js"
    )
    (inventory / record.artifacts[0].path).write_text("Xrm.Page;\n", encoding="utf-8")
    result = assess.assess(inventory, "online")
    reasons = {f.reason for f in _findings(result, "new_/scripts/clean.js")}
    assert reasons == {"artifact changed since extraction: " + record.artifacts[0].path}


def test_every_shipped_rule_has_a_range_and_a_learn_source():
    rules = assess.load_rules()
    assert rules.rules
    for rule in rules.rules:
        assert rule.source_from, rule.id
        assert rule.targets, rule.id
        assert any(s.startswith("https://learn.microsoft.com/") for s in rule.sources), rule.id
    assert len({r.id for r in rules.rules}) == len(rules.rules)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda r: r.pop("source"), "source"),
        (lambda r: r.update(sources=["https://example.test/blog"]), "Microsoft Learn"),
        (lambda r: r.update(targets=[]), "targets"),
        (lambda r: r.update({"class": "fatal"}), "class"),
    ],
)
def test_a_rule_without_range_or_provenance_is_refused(change, match):
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    change(data["rules"][0])
    with pytest.raises(inv.InventoryError, match=match):
        assess.parse_rules(data)


def test_rules_and_findings_validate_against_their_schemas(inventory):
    rules_schema = json.loads((SCHEMAS / "compatibility-rules-v1.schema.json").read_text())
    jsonschema.validate(json.loads(assess.RULES_PATH.read_text(encoding="utf-8")), rules_schema)
    findings_schema = json.loads((SCHEMAS / "compatibility-findings-v2.schema.json").read_text())
    text = assess.dump_findings(assess.assess(inventory, "online"))
    jsonschema.validate(json.loads(text), findings_schema)


def test_two_runs_differ_only_in_the_header_time(inventory):
    first = json.loads(assess.dump_findings(assess.assess(inventory, "online")))
    second = json.loads(assess.dump_findings(assess.assess(inventory, "online")))
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second
    stamp = "2026-11-05T00:00:00Z"
    one = assess.dump_findings(assess.assess(inventory, "online"), generated_at=stamp)
    two = assess.dump_findings(assess.assess(inventory, "online"), generated_at=stamp)
    assert one == two


def test_findings_name_their_inventory_and_rules(inventory):
    data = json.loads(assess.dump_findings(assess.assess(inventory, "online")))
    assert data["schema_version"] == 2 and data["target"] == "online"
    assert data["inventory"]["extracted_at"] == "2026-11-01T10:00:00Z"
    assert data["inventory"]["platform"] == "crm2011"
    assert len(data["inventory"]["manifest_sha256"]) == 64
    assert data["rules"]["version"] == assess.load_rules().version
    assert "web-resource-xrm-page" in data["rules_hit"]
    keys = [(f["kind"], f["key"]) for f in data["findings"]]
    assert keys == sorted(keys)


def test_cli_writes_findings_and_counts_with_blockers_exiting_0(inventory, tmp_path):
    out = tmp_path / "findings.json"
    res = CliRunner().invoke(
        cli,
        ["--json", "inventory", "assess", str(inventory), "--target", "online", "--out", str(out)],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)["data"]
    assert data["out"] == str(out) and out.exists()
    assert data["counts"]["by_class"]["blocker"] >= 2
    assert "entity" not in data["counts"]["by_kind"]
    assert data["uncovered_kinds"] == UNCOVERED


def test_cli_refuses_an_unknown_schema_version(inventory, tmp_path):
    manifest = json.loads((inventory / inv.MANIFEST_NAME).read_text())
    manifest["schema_version"] = 5
    (inventory / inv.MANIFEST_NAME).write_text(json.dumps(manifest))
    out = tmp_path / "findings.json"
    res = CliRunner().invoke(
        cli,
        ["--json", "inventory", "assess", str(inventory), "--target", "online", "--out", str(out)],
    )
    assert res.exit_code == 1 and "schema_version" in res.output
    assert not out.exists()


def test_cli_refuses_an_out_inside_the_inventory(inventory):
    out = inventory / "findings.json"
    res = CliRunner().invoke(
        cli,
        ["--json", "inventory", "assess", str(inventory), "--target", "online", "--out", str(out)],
    )
    assert res.exit_code == 1 and not out.exists()


def test_an_unreadable_source_version_makes_ruled_components_unknown(tmp_path):
    folder = _write_inventory(tmp_path / "unk", version="unknown")
    result = assess.assess(folder, "online")
    (legacy,) = _findings(result, "new_/scripts/legacy.js")
    assert (legacy.cls, legacy.rule) == ("unknown", None)
    assert legacy.reason == "source version 'unknown' is not a dotted version"
    assert not _findings(result, "new_project")
    assert result.summary()["uncovered_kinds"] == UNCOVERED


def test_a_version_with_a_suffix_is_not_dotted():
    with pytest.raises(inv.InventoryError, match="dotted"):
        assess._version("9.0x", "source.from")


def test_a_field_rule_matches_on_type_as_well_as_value(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    record = _assembly("Contoso.Odd", folder, source_type=0, isolation_mode=True)
    rules = [r for r in assess.load_rules().rules if r.id == "assembly-isolation-none"]
    assert [f.cls for f in assess._assess_record(record, rules, folder)] == ["clear"]


def test_a_list_valued_field_hit_is_assessed(tmp_path):
    folder = _write_inventory(tmp_path / "l")
    manifest = json.loads((folder / inv.MANIFEST_NAME).read_text())
    for record in manifest["records"]:
        if record["key"] == "Contoso.Open":
            record["fields"]["isolation_mode"] = [1]
    (folder / inv.MANIFEST_NAME).write_text(json.dumps(manifest))
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    for rule in data["rules"]:
        if rule["id"] == "assembly-isolation-none":
            rule["detection"]["values"] = [[1]]
    result = assess.assess(folder, "online", rules=assess.parse_rules(data))
    (hit,) = [f for f in _findings(result, "Contoso.Open") if f.cls == "blocker"]
    assert hit.evidence == assess.FieldEvidence("isolation_mode", [1])


@pytest.mark.parametrize(
    ("content", "hit"),
    [
        ("var url = Xrm.Page.context.getServerUrl();\n", True),
        ("var url = GetGlobalContext().getServerUrl ();\n", True),
        ("function getServerUrl() { return '/'; }\nvar url = getServerUrl();\n", False),
    ],
)
def test_get_server_url_matches_the_context_method_only(tmp_path, content, hit):
    folder = tmp_path / "s"
    folder.mkdir()
    record = _script("new_/scripts/url.js", content, folder)
    rules = [r for r in assess.load_rules().rules if r.id == "web-resource-get-server-url"]
    found = assess._assess_record(record, rules, folder)
    assert ([f.cls for f in found] == ["deprecated"]) is hit


# ── Registrations: form handlers and ribbon actions (#1101) ─────────────────

LEGACY = "new_/scripts/legacy.js"
CLEAN = "new_/scripts/clean.js"


def _handler(event, function, library, *, passed=None, enabled="true", attribute=None) -> str:
    flag = "" if passed is None else f' passExecutionContext="{str(passed).lower()}"'
    attr = "" if attribute is None else f' attribute="{attribute}"'
    return (
        f'<event name="{event}"{attr}><Handlers><Handler functionName="{function}" '
        f'libraryName="{library}" enabled="{enabled}"{flag} /></Handlers></event>'
    )


def _form_xml(*events: str, internal: str = "") -> bytes:
    return (f"<form><events>{''.join(events)}{internal}</events></form>").encode()


def _form(name: str, xml: bytes, folder: Path) -> inv.Record:
    fields = {"entity": "account", "form_type": 2, "name": name}
    return _record("form", fields, folder, files=[("form.xml", xml, True)])


def _diff(entity: str, *actions: str, folder: Path) -> inv.Record:
    commands = "".join(
        f'<CommandDefinition Id="cmd{i}"><Actions>{a}</Actions></CommandDefinition>'
        for i, a in enumerate(actions)
    )
    xml = f"<RibbonDiffXml><CommandDefinitions>{commands}</CommandDefinitions></RibbonDiffXml>"
    return _record(
        "ribbon_diff", {"entity": entity}, folder, files=[("ribbon-diff.xml", xml.encode(), True)]
    )


def _js(library: str, function: str, *params: str) -> str:
    inner = "".join(f'<CrmParameter Value="{p}" />' for p in params)
    return (
        f'<JavaScriptFunction Library="$webresource:{library}" FunctionName="{function}">'
        f"{inner}</JavaScriptFunction>"
    )


def _js_raw(library: str, function: str) -> str:
    return f'<JavaScriptFunction Library="{library}" FunctionName="{function}" />'


def _registrations(folder: Path, records: list[inv.Record], *, version="5.0.9690.4376") -> Path:
    records = [_script(LEGACY, XRM_PAGE_JS, folder), _script(CLEAN, CLEAN_JS, folder), *records]
    counts: dict[str, int] = {}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", ORG, None, version),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.101.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage={k: inv.KindCoverage("complete", 1, n, False, ()) for k, n in counts.items()},
        gaps=(),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


def _rule_findings(result, key: str, rule: str) -> list:
    return [f for f in _findings(result, key) if f.rule == rule]


FORM_RULE = "form-handler-no-execution-context"
RIBBON_RULE = "ribbon-action-no-primary-control"


def test_a_handler_without_the_context_on_an_xrm_page_library_is_a_finding(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    xml = _form_xml(
        _handler("onload", "Legacy.onLoad", LEGACY, passed=False),
        _handler("onsave", "Legacy.onSave", LEGACY, passed=True),
        _handler("onchange", "Legacy.onName", LEGACY, passed=False, attribute="name"),
        _handler("onload", "Clean.onLoad", CLEAN, passed=False),
        _handler("onload", "Legacy.off", LEGACY, passed=False, enabled="false"),
    )
    form = _form("Information", xml, folder)
    result = assess.assess(_registrations(folder, [form]), "online")
    (hit,) = _rule_findings(result, form.key, FORM_RULE)
    assert (hit.cls, hit.reason) == ("deprecated", None)
    evidence = hit.evidence
    assert evidence.path == form.artifacts[0].path and evidence.sha256 == form.artifacts[0].sha256
    assert evidence.match_count == 2
    assert evidence.registrations == (
        {
            "event": "onchange",
            "attribute": "name",
            "function": "Legacy.onName",
            "library": LEGACY,
            "passExecutionContext": False,
        },
        {
            "event": "onload",
            "attribute": None,
            "function": "Legacy.onLoad",
            "library": LEGACY,
            "passExecutionContext": False,
        },
    )


def test_a_missing_flag_reads_as_not_passing_and_internal_handlers_are_left_alone(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    xml = _form_xml(
        _handler("onload", "Legacy.onLoad", LEGACY),
        internal='<event name="onsave"><InternalHandlers><Handler functionName="x" '
        f'libraryName="{LEGACY}" passExecutionContext="false" /></InternalHandlers></event>',
    )
    form = _form("Information", xml, folder)
    result = assess.assess(_registrations(folder, [form]), "online")
    (hit,) = _rule_findings(result, form.key, FORM_RULE)
    assert hit.evidence.match_count == 1
    assert hit.evidence.registrations[0]["passExecutionContext"] is False


def test_handlers_that_pass_the_context_or_use_a_clean_library_are_clear(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    passing = _form(
        "Passing", _form_xml(_handler("onload", "Legacy.onLoad", LEGACY, passed=True)), folder
    )
    clean = _form(
        "Clean", _form_xml(_handler("onload", "Clean.onLoad", CLEAN, passed=False)), folder
    )
    none = _form("Empty", _form_xml(), folder)
    result = assess.assess(_registrations(folder, [passing, clean, none]), "online")
    for form in (passing, clean, none):
        assert _classes(result, form.key) == {"clear"}, form.key


def test_a_library_the_inventory_cannot_judge_makes_the_form_unknown(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    absent = _form(
        "Absent",
        _form_xml(_handler("onload", "M.f", "new_/scripts/missing.js", passed=False)),
        folder,
    )
    image = _record(
        "web_resource",
        {"name": "new_/img/a.png", "web_resource_type": 5},
        folder,
        files=[("content.png", b"\x89PNG", False)],
    )
    typed = _form(
        "Typed", _form_xml(_handler("onload", "M.f", "new_/img/a.png", passed=False)), folder
    )
    result = assess.assess(_registrations(folder, [absent, image, typed]), "online")
    (a,) = _findings(result, absent.key)
    assert (a.cls, a.rule) == ("unknown", FORM_RULE)
    assert a.reason == "library new_/scripts/missing.js is not in the inventory"
    (t,) = _findings(result, typed.key)
    assert (t.cls, t.rule) == ("unknown", FORM_RULE)
    assert t.reason == "library new_/img/a.png is not a script web resource"


def test_an_unreadable_form_artifact_is_unknown(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    form = _form("Broken", b"<form><events>", folder)
    result = assess.assess(_registrations(folder, [form]), "online")
    found = _findings(result, form.key)
    assert found and all(f.cls == "unknown" for f in found)
    assert all("not well-formed XML" in f.reason for f in found)


def test_a_library_artifact_changed_since_extraction_makes_the_form_unknown(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    form = _form("Information", _form_xml(_handler("onload", "L.f", LEGACY, passed=False)), folder)
    _registrations(folder, [form])
    record = next(r for r in inv.read_inventory(folder).records if r.key == LEGACY)
    (folder / record.artifacts[0].path).write_text("// edited\n", encoding="utf-8")
    result = assess.assess(folder, "online")
    (found,) = _findings(result, form.key)
    assert found.cls == "unknown"
    assert found.reason == f"library {LEGACY}: artifact changed since extraction: " + (
        record.artifacts[0].path
    )


def test_a_ribbon_action_without_primary_control_on_an_xrm_page_library_is_a_finding(tmp_path):
    folder = tmp_path / "r"
    folder.mkdir()
    diff = _diff(
        "account",
        _js(LEGACY, "Legacy.approve"),
        _js(LEGACY, "Legacy.reject", "FirstPrimaryItemId", "SelectedControl"),
        _js(LEGACY, "Legacy.ok", "PrimaryControl"),
        _js(CLEAN, "Clean.go"),
        '<Url Address="/main.aspx" />',
        _js_raw("/_static/_common/scripts/RibbonActions.js", "Mscrm.go"),
        folder=folder,
    )
    result = assess.assess(_registrations(folder, [diff]), "online")
    (hit,) = _rule_findings(result, diff.key, RIBBON_RULE)
    assert hit.cls == "deprecated"
    assert hit.evidence.match_count == 2
    assert hit.evidence.registrations == (
        {
            "command": "cmd0",
            "function": "Legacy.approve",
            "library": LEGACY,
            "crm_parameters": [],
        },
        {
            "command": "cmd1",
            "function": "Legacy.reject",
            "library": LEGACY,
            "crm_parameters": ["FirstPrimaryItemId", "SelectedControl"],
        },
    )


def test_ribbon_actions_that_pass_primary_control_are_clear(tmp_path):
    folder = tmp_path / "r"
    folder.mkdir()
    diff = _diff("account", _js(LEGACY, "Legacy.ok", "PrimaryControl"), folder=folder)
    result = assess.assess(_registrations(folder, [diff]), "online")
    assert _classes(result, diff.key) == {"clear"}


def test_a_ribbon_library_missing_from_the_inventory_is_unknown(tmp_path):
    folder = tmp_path / "r"
    folder.mkdir()
    diff = _diff("account", _js("new_/scripts/gone.js", "Gone.f"), folder=folder)
    result = assess.assess(_registrations(folder, [diff]), "online")
    # Each ribbon rule that reads the library reports it unknown on its own.
    found = [f for f in _findings(result, diff.key) if f.rule == RIBBON_RULE]
    assert [(f.cls, f.reason) for f in found] == [
        ("unknown", "library new_/scripts/gone.js is not in the inventory")
    ]


def test_registration_findings_round_trip_and_validate(tmp_path):
    folder = tmp_path / "rt"
    folder.mkdir()
    form = _form("Information", _form_xml(_handler("onload", "L.f", LEGACY, passed=False)), folder)
    diff = _diff("account", _js(LEGACY, "Legacy.approve"), folder=folder)
    text = assess.dump_findings(assess.assess(_registrations(folder, [form, diff]), "online"))
    schema = json.loads((SCHEMAS / "compatibility-findings-v2.schema.json").read_text())
    jsonschema.validate(json.loads(text), schema)
    out = tmp_path / "findings.json"
    out.write_text(text, encoding="utf-8")
    back = assess.read_findings(out)
    hits = {f.rule: f for f in back.findings if f.rule in (FORM_RULE, RIBBON_RULE)}
    form_ev = hits[FORM_RULE].evidence
    ribbon_ev = hits[RIBBON_RULE].evidence
    assert isinstance(form_ev, assess.RegistrationEvidence)
    assert isinstance(ribbon_ev, assess.RegistrationEvidence)
    assert form_ev.registrations[0]["function"] == "L.f"
    assert ribbon_ev.registrations[0]["command"] == "cmd0"
    assert form_ev.match_count == 1


@pytest.mark.parametrize(
    ("kind", "rule_id", "change", "match"),
    [
        ("form", FORM_RULE, lambda r: r["detection"].update(library_rule="nope"), "library_rule"),
        (
            "form",
            FORM_RULE,
            lambda r: r["detection"].update(library_rule="assembly-storage-online"),
            "library_rule",
        ),
        ("form", FORM_RULE, lambda r: r.update(kind="view"), "form_handler_context"),
        ("ribbon_diff", RIBBON_RULE, lambda r: r.update(kind="form"), "ribbon_action_parameter"),
        ("ribbon_diff", RIBBON_RULE, lambda r: r["detection"].pop("parameter"), "parameter"),
    ],
)
def test_a_malformed_registration_rule_is_refused(kind, rule_id, change, match):
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    change(next(r for r in data["rules"] if r["id"] == rule_id))
    with pytest.raises(inv.InventoryError, match=match):
        assess.parse_rules(data)


def test_the_registration_rules_cite_learn():
    rules = {r.id: r for r in assess.load_rules().rules}
    for rule_id, kind in ((FORM_RULE, "form"), (RIBBON_RULE, "ribbon_diff")):
        rule = rules[rule_id]
        assert rule.kind == kind and rule.cls == "deprecated"
        assert all(s.startswith("https://learn.microsoft.com/") for s in rule.sources)


def test_a_registration_judged_without_its_library_rule_is_unknown_not_a_crash(tmp_path):
    form = _form("Main", _form_xml(_handler("onload", "L.f", LEGACY)), tmp_path)
    rules = [r for r in assess.load_rules().rules if r.id == FORM_RULE]
    found = assess._assess_record(form, rules, tmp_path)
    assert [f.cls for f in found] == ["unknown"]
    assert "is not loaded" in (found[0].reason or "")


def test_a_library_name_shared_by_two_web_resources_is_unknown_in_either_order(tmp_path):
    legacy = _script(LEGACY, XRM_PAGE_JS, tmp_path / "a")
    other = _script(LEGACY, CLEAN_JS, tmp_path / "b")
    form = _form("Main", _form_xml(_handler("onload", "L.f", LEGACY)), tmp_path)
    rules = {r.id: r for r in assess.load_rules().rules}
    selected = [rules[FORM_RULE]]
    for order in ((legacy, other), (other, legacy)):
        libs = assess._Libraries(order, tmp_path, tuple(rules.values()))
        found = assess._assess_record(form, selected, tmp_path, libs)
        assert [f.cls for f in found] == ["unknown"]
        assert "more than one web resource" in (found[0].reason or "")


def test_a_ribbon_action_naming_a_library_by_path_or_unknown_form_is_judged_not_skipped(tmp_path):
    diff = _diff(
        "account",
        _js_raw("/WebResources/" + LEGACY, "Legacy.byPath"),
        _js_raw("/org/WebResources/" + CLEAN, "Clean.byPath"),
        _js_raw("https://example.invalid/other.js", "Other.go"),
        _js_raw("/_static/_common/scripts/RibbonActions.js", "Mscrm.go"),
        folder=tmp_path,
    )
    result = assess.assess(_registrations(tmp_path, [diff]), "online")
    found = _rule_findings(result, diff.key, RIBBON_RULE)
    by_cls = {f.cls: f for f in found}
    assert by_cls["deprecated"].evidence.match_count == 1
    assert by_cls["deprecated"].evidence.registrations[0]["function"] == "Legacy.byPath"
    assert (
        by_cls["unknown"].reason
        == "library https://example.invalid/other.js is not in the inventory"
    )
    assert len(found) == 2
