"""Migration inventory manifest v1: schema, reader, writer and normalization (ADR 0032)."""

# pyright: basic

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from crm.core import inventory


def test_text_artifact_with_bom_and_crlf_shares_normalized_hash_but_not_raw_hash() -> None:
    plain = b"function onLoad() {\n  return 1;\n}\n"
    windows = b"\xef\xbb\xbffunction onLoad() {\r\n  return 1;\r\n}\r\n"

    plain_hashes = inventory.artifact_hashes(plain, text=True)
    windows_hashes = inventory.artifact_hashes(windows, text=True)

    assert plain_hashes.sha256 != windows_hashes.sha256
    assert plain_hashes.normalized_sha256 == windows_hashes.normalized_sha256
    assert plain_hashes.normalized_sha256 == hashlib.sha256(plain).hexdigest()
    assert windows_hashes.sha256 == hashlib.sha256(windows).hexdigest()


_TABS = '<TabDisplayRules><TabDisplayRule Id="T1"/><TabDisplayRule Id="T2"/></TabDisplayRules>'


def _ribbon(
    enable: str = '<EnableRule Id="A"><X Y="1"/></EnableRule><EnableRule Id="B"/>',
    display: str = '<DisplayRule Id="D1"/><DisplayRule Id="D2"/>',
    commands: str = '<CommandDefinition Id="C1"/><CommandDefinition Id="C2"/>',
    tabs: str = _TABS,
) -> bytes:
    return (
        "<RibbonDiffXml><CommandDefinitions>"
        f"{commands}</CommandDefinitions><RuleDefinitions>{tabs}"
        f"<DisplayRules>{display}</DisplayRules><EnableRules>{enable}</EnableRules>"
        "</RuleDefinitions></RibbonDiffXml>"
    ).encode()


def _ribbon_hashes(data: bytes) -> inventory.ArtifactHashes:
    return inventory.artifact_hashes(data, text=True, ribbon=True)


def test_ribbon_sibling_order_changes_the_raw_hash_but_not_the_normalized_one() -> None:
    a = _ribbon()
    b = _ribbon(
        enable='<EnableRule Id="B"/><EnableRule Id="A"><X Y="1"/></EnableRule>',
        display='<DisplayRule Id="D2"/><DisplayRule Id="D1"/>',
        commands='<CommandDefinition Id="C2"/><CommandDefinition Id="C1"/>',
    )

    assert _ribbon_hashes(a).sha256 != _ribbon_hashes(b).sha256
    assert _ribbon_hashes(a).normalized_sha256 == _ribbon_hashes(b).normalized_sha256
    assert _ribbon_hashes(a).sha256 == hashlib.sha256(a).hexdigest()


@pytest.mark.parametrize(
    "changed",
    [
        _ribbon(enable='<EnableRule Id="A"><X Y="2"/></EnableRule><EnableRule Id="B"/>'),
        _ribbon(enable='<EnableRule Id="A"><X Y="1"/></EnableRule>'),
        _ribbon(display='<DisplayRule Id="D1"/><DisplayRule Id="D2"/><DisplayRule Id="D3"/>'),
        _ribbon(tabs=_TABS.replace("T1", "TX").replace("T2", "T1").replace("TX", "T2")),
    ],
    ids=["attribute value", "element removed", "element added", "order outside the three parents"],
)
def test_a_real_ribbon_change_changes_the_normalized_hash(changed: bytes) -> None:
    assert _ribbon_hashes(changed).normalized_sha256 != _ribbon_hashes(_ribbon()).normalized_sha256


def test_same_tag_same_id_siblings_hash_by_content_in_either_order() -> None:
    one = '<EnableRule Id="A"><V n="1"/></EnableRule>'
    two = '<EnableRule Id="A"><V n="2"/></EnableRule>'

    forward = _ribbon_hashes(_ribbon(enable=one + two)).normalized_sha256
    backward = _ribbon_hashes(_ribbon(enable=two + one)).normalized_sha256

    assert forward == backward
    assert forward != _ribbon_hashes(_ribbon(enable=one)).normalized_sha256


def test_nested_unordered_lists_are_ordered_before_their_parent_siblings_are_keyed() -> None:
    def command(*rule_ids: str) -> str:
        rules = "".join(f'<EnableRule Id="{i}"/>' for i in rule_ids)
        return f'<CommandDefinition Id="C"><EnableRules>{rules}</EnableRules></CommandDefinition>'

    forward = _ribbon(commands=command("a", "b") + command("a", "c"))
    # Only the nested rule order differs; keyed before its own rules are sorted, the first
    # command would sort after the second.
    nested_reordered = _ribbon(commands=command("b", "a") + command("a", "c"))

    assert _ribbon_hashes(forward).normalized_sha256 == (
        _ribbon_hashes(nested_reordered).normalized_sha256
    )


def test_a_ribbon_that_is_not_xml_falls_back_to_the_text_normalization() -> None:
    broken = b"\xef\xbb\xbf<RibbonDiffXml>\r\n<oops>"

    assert _ribbon_hashes(broken) == inventory.artifact_hashes(broken, text=True)


def test_only_a_ribbon_is_canonicalized() -> None:
    a, b = _ribbon(), _ribbon(display='<DisplayRule Id="D2"/><DisplayRule Id="D1"/>')

    assert (
        inventory.artifact_hashes(a, text=True).normalized_sha256
        != inventory.artifact_hashes(b, text=True).normalized_sha256
    )


def test_component_key_joins_the_kind_key_fields_in_table_order() -> None:
    form = {"name": "Information", "entity": "new_project", "form_type": 2, "extra": "ignored"}

    assert inventory.component_key("form", form) == "new_project|2|Information"
    assert inventory.component_key("web_resource", {"name": "new_/scripts/project.js"}) == (
        "new_/scripts/project.js"
    )


def test_component_key_escapes_the_separator_so_two_keys_never_collide() -> None:
    left = inventory.component_key("view", {"entity": "account|x", "name": "y"})
    right = inventory.component_key("view", {"entity": "account", "name": "x|y"})

    assert left == "account%7Cx|y"
    assert right == "account|x%7Cy"
    assert inventory.component_key("view", {"entity": "a%7C", "name": "b"}) == "a%257C|b"


def test_component_key_fills_the_named_default_for_an_application_ribbon_and_default_sitemap() -> (
    None
):
    assert inventory.component_key("ribbon", {"entity": None}) == "application"
    assert inventory.component_key("ribbon", {"entity": "account"}) == "account"
    assert inventory.component_key("sitemap", {"unique_name": None}) == "default"


def test_component_key_nests_a_member_key_as_one_escaped_part() -> None:
    member = inventory.component_key(
        "form", {"entity": "account", "form_type": 2, "name": "Account"}
    )
    membership = {"solution": "ContosoCore", "member_kind": "form", "member_key": member}

    assert inventory.component_key("solution_membership", membership) == (
        "ContosoCore|form|account%7C2%7CAccount"
    )


def test_component_key_refuses_a_missing_key_field_and_an_unknown_kind() -> None:
    with pytest.raises(inventory.InventoryError, match="form.*form_type"):
        inventory.component_key("form", {"entity": "account", "name": "Account"})
    with pytest.raises(inventory.InventoryError, match="unknown kind 'gadget'"):
        inventory.component_key("gadget", {"name": "x"})


def test_artifact_path_is_a_readable_slug_plus_a_short_hash_of_the_full_key() -> None:
    path = inventory.artifact_path("web_resource", "new_/scripts/Project.js", "content.js")

    assert re.fullmatch(
        r"artifacts/web_resource/new_-scripts-project\.js-[0-9a-f]{12}\.content\.js", path
    )
    assert path == inventory.artifact_path("web_resource", "new_/scripts/Project.js", "content.js")


def test_artifact_path_differs_for_keys_that_slug_alike() -> None:
    slash = inventory.artifact_path("web_resource", "new_/a.js", "content.js")
    dash = inventory.artifact_path("web_resource", "new_-a.js", "content.js")
    upper = inventory.artifact_path("web_resource", "new_/A.js", "content.js")

    assert len({slash, dash, upper}) == 3


def test_artifact_path_takes_a_discriminator_for_records_sharing_an_ambiguous_key() -> None:
    first = inventory.artifact_path(
        "form", "new_project|2|Information", "form.xml", discriminator="a"
    )
    second = inventory.artifact_path(
        "form", "new_project|2|Information", "form.xml", discriminator="b"
    )

    assert first != second


def test_binary_artifact_uses_the_raw_hash_for_both() -> None:
    data = b"\xef\xbb\xbfMZ\r\n\x00\x01"

    hashes = inventory.artifact_hashes(data, text=False)

    assert hashes.sha256 == hashes.normalized_sha256 == hashlib.sha256(data).hexdigest()


def _manifest() -> dict[str, Any]:
    """A minimal valid v1 manifest: one entity and one form, both kinds covered."""
    return {
        "schema_version": 1,
        "source": {
            "platform": "onprem",
            "org_url": "https://internalcrm.contoso.local/Contoso",
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "version": "9.1.0.0000",
        },
        "extracted_at": "2026-11-04T10:00:00Z",
        "cli_version": "1.83.0",
        "scope": {"mode": "org", "solutions": []},
        "publication": "published",
        "records": [
            {
                "kind": "entity",
                "key": "account",
                "id": None,
                "display_name": "Account",
                "origin": "system",
                "managed": True,
                "publication_state": "published",
                "fields": {"logical_name": "account"},
                "artifacts": [],
            },
            {
                "kind": "form",
                "key": "account|2|Account",
                "id": "00000000-0000-0000-0000-000000000002",
                "display_name": "Account",
                "origin": "system",
                "managed": True,
                "publication_state": "published",
                "fields": {"entity": "account", "form_type": 2, "name": "Account"},
                "artifacts": [
                    {
                        "path": "artifacts/form/account-2-account-0123456789ab.form.xml",
                        "content": "text",
                        "sha256": "a" * 64,
                        "normalized_sha256": "b" * 64,
                    }
                ],
            },
        ],
        "coverage": {
            "entity": {
                "status": "complete",
                "pages": 1,
                "records": 1,
                "truncated": False,
                "errors": [],
            },
            "form": {
                "status": "complete",
                "pages": 1,
                "records": 1,
                "truncated": False,
                "errors": [],
            },
        },
        "gaps": [],
    }


def test_reader_returns_typed_records_and_ignores_fields_it_does_not_know() -> None:
    manifest = _manifest()
    manifest["future_header"] = {"x": 1}
    manifest["records"][1]["future_field"] = True
    manifest["coverage"]["form"]["future_field"] = 3

    inv = inventory.parse_manifest(manifest)

    assert inv.schema_version == 1
    assert inv.source.platform == "onprem"
    assert [(r.kind, r.key) for r in inv.records] == [
        ("entity", "account"),
        ("form", "account|2|Account"),
    ]
    assert inv.records[1].artifacts[0].content == "text"
    assert inv.coverage["form"].status == "complete"


def test_reader_refuses_an_unknown_schema_version_naming_both_versions() -> None:
    manifest = {"schema_version": 2, "records": "a shape v1 does not know"}

    with pytest.raises(inventory.InventoryError, match=r"schema_version 2.*schema_version 1"):
        inventory.parse_manifest(manifest)


def test_read_inventory_reports_malformed_utf8_as_an_inventory_error(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_bytes(b'{"schema_version": 1, "x": "\xff"}')

    with pytest.raises(inventory.InventoryError, match="cannot read"):
        inventory.read_inventory(tmp_path)


def _drop_key(m: dict[str, Any]) -> None:
    del m["records"][0]["key"]


def _wrong_key(m: dict[str, Any]) -> None:
    m["records"][1]["key"] = "account|2|Other"


def _drop_coverage(m: dict[str, Any]) -> None:
    del m["coverage"]["form"]


def _complete_truncated(m: dict[str, Any]) -> None:
    m["coverage"]["entity"]["truncated"] = True


def _count_mismatch(m: dict[str, Any]) -> None:
    m["coverage"]["entity"]["records"] = 2


def _foreign_artifact_path(m: dict[str, Any]) -> None:
    m["records"][1]["artifacts"][0]["path"] = "artifacts/view/../form/x.form.xml"


def _shared_artifact_path(m: dict[str, Any]) -> None:
    form = m["records"][1]
    twin = {**form, "id": "00000000-0000-0000-0000-000000000003"}
    m["records"].append(twin)
    m["coverage"]["form"]["records"] = 2


def _credentials_in_url(m: dict[str, Any]) -> None:
    m["source"]["org_url"] = "https://alice:secret@internalcrm.contoso.local/Contoso"


def _bad_origin(m: dict[str, Any]) -> None:
    m["records"][0]["origin"] = "custom-ish"


def _int_key_part(m: dict[str, Any]) -> None:
    m["records"][0]["fields"]["logical_name"] = 7
    m["records"][0]["key"] = "7"


def _ftp_url(m: dict[str, Any]) -> None:
    m["source"]["org_url"] = "ftp://internalcrm.contoso.local/Contoso"


def _upper_case_scheme(m: dict[str, Any]) -> None:
    m["source"]["org_url"] = "HTTPS://internalcrm.contoso.local/Contoso"


def _empty_nullable_key_part(m: dict[str, Any]) -> None:
    m["records"][0].update(kind="sitemap", key="default", fields={"unique_name": ""})
    m["coverage"]["sitemap"] = m["coverage"].pop("entity")


def _handlers_not_a_list(m: dict[str, Any]) -> None:
    m["records"][1]["fields"]["handlers"] = 5


def _handler_with_empty_event(m: dict[str, Any]) -> None:
    handler = {"event": "", "attribute": None, "function": "f", "library": "l", "enabled": True}
    m["records"][1]["fields"]["handlers"] = [handler]


def _credentials_after_a_query(m: dict[str, Any]) -> None:
    m["source"]["org_url"] = "https://internalcrm.contoso.local?a@b"


def _malformed_ipv6_url(m: dict[str, Any]) -> None:
    m["source"]["org_url"] = "https://[::1/Con\ntoso"


def _extracted_at_not_a_date_time(m: dict[str, Any]) -> None:
    m["extracted_at"] = "yesterday"


def _binary_artifact_with_two_hashes(m: dict[str, Any]) -> None:
    m["records"][1]["artifacts"][0]["content"] = "binary"


@pytest.mark.parametrize(
    ("mutate", "message", "schema_rejects"),
    [
        (_drop_key, r"records\[0\].*key", True),
        (_wrong_key, r"key 'account\|2\|Other'.*'account\|2\|Account'", False),
        (_drop_coverage, r"no coverage entry.*form", False),
        (_complete_truncated, r"entity.*complete.*truncated", True),
        (_count_mismatch, r"entity.*2 records.*holds 1", False),
        (_foreign_artifact_path, r"artifacts/form/", True),
        (_shared_artifact_path, r"shared by two records", False),
        (_credentials_in_url, r"org_url.*credentials", True),
        (_bad_origin, r"origin", True),
        (_int_key_part, r"entity\.logical_name must be a non-empty string", True),
        (_ftp_url, r"org_url must be an http\(s\) URL", True),
        (_upper_case_scheme, r"org_url must be an http\(s\) URL", True),
        (
            _empty_nullable_key_part,
            r"sitemap\.unique_name must be a non-empty string or null",
            True,
        ),
        (_handlers_not_a_list, r"fields\.handlers must be an array", True),
        (_handler_with_empty_event, r"handlers\[0\]\.event must be a non-empty string", True),
        (_credentials_after_a_query, r"org_url.*credentials", True),
        (_malformed_ipv6_url, r"org_url must be an http\(s\) URL", True),
        (_extracted_at_not_a_date_time, r"extracted_at must be an RFC 3339 date-time", True),
        (_binary_artifact_with_two_hashes, r"binary.*normalized_sha256 must equal", False),
    ],
)
def test_reader_refuses_a_manifest_that_breaks_the_contract(
    mutate: Callable[[dict[str, Any]], None], message: str, schema_rejects: bool
) -> None:
    manifest = _manifest()
    mutate(manifest)

    with pytest.raises(inventory.InventoryError, match=message):
        inventory.parse_manifest(manifest)
    assert (_schema_errors(manifest) != []) == schema_rejects


def test_component_key_types_each_part_so_null_and_empty_never_collide() -> None:
    step = {"message": "Create", "stage": 40, "mode": 0, "plugin_type": "A|B", "name": "s"}

    assert (
        inventory.component_key("step", {**step, "primary_entity": None}) == "Create||40|0|A%7CB|s"
    )
    with pytest.raises(
        inventory.InventoryError, match=r"step\.primary_entity must be a non-empty string or null"
    ):
        inventory.component_key("step", {**step, "primary_entity": ""})
    with pytest.raises(inventory.InventoryError, match=r"step\.stage must be an integer"):
        inventory.component_key("step", {**step, "primary_entity": None, "stage": "40"})


def test_artifact_path_for_a_name_with_dots_round_trips_through_the_writer() -> None:
    manifest = _manifest()
    record = manifest["records"][0]
    record.update(
        kind="web_resource",
        key="new_/lib/jquery..min.js",
        fields={"name": "new_/lib/jquery..min.js"},
    )
    path = inventory.artifact_path("web_resource", record["key"], "content.js")
    record["artifacts"] = [
        {"path": path, "content": "text", "sha256": "a" * 64, "normalized_sha256": "a" * 64}
    ]
    del manifest["coverage"]["entity"]
    manifest["coverage"]["web_resource"] = {
        "status": "complete",
        "pages": 1,
        "records": 1,
        "truncated": False,
        "errors": [],
    }

    text = inventory.dump_manifest(inventory.parse_manifest(manifest))

    assert _schema_errors(json.loads(text)) == []


def test_writer_sorts_coverage_errors_and_refuses_values_json_cannot_carry() -> None:
    first, second = _manifest(), _manifest()
    first["coverage"]["form"].update(status="partial", errors=["b", "a"])
    second["coverage"]["form"].update(status="partial", errors=["a", "b"])
    nan = _manifest()
    nan["records"][0]["fields"]["ratio"] = float("nan")

    assert inventory.dump_manifest(inventory.parse_manifest(first)) == inventory.dump_manifest(
        inventory.parse_manifest(second)
    )
    with pytest.raises(inventory.InventoryError, match="JSON"):
        inventory.dump_manifest(inventory.parse_manifest(nan))


def test_reader_keeps_both_records_of_an_ambiguous_key_and_reports_them() -> None:
    manifest = _manifest()
    twin = copy.deepcopy(manifest["records"][1])
    twin["id"] = "00000000-0000-0000-0000-000000000003"
    twin["artifacts"][0]["path"] = "artifacts/form/account-2-account-ba9876543210.form.xml"
    manifest["records"].append(twin)
    manifest["coverage"]["form"]["records"] = 2

    inv = inventory.parse_manifest(manifest)

    assert len(inv.records) == 3
    assert list(inv.ambiguous()) == [("form", "account|2|Account")]
    assert [r.id for r in inv.ambiguous()[("form", "account|2|Account")]] == [
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000003",
    ]


def test_writer_is_byte_identical_for_records_in_any_order_apart_from_header_times() -> None:
    first = _manifest()
    second = _manifest()
    second["records"].reverse()
    second["extracted_at"] = "2026-11-05T09:30:00Z"

    one = inventory.dump_manifest(inventory.parse_manifest(first))
    two = inventory.dump_manifest(inventory.parse_manifest(second))

    differing = [(a, b) for a, b in zip(one.splitlines(), two.splitlines(), strict=True) if a != b]
    assert differing == [
        ('  "extracted_at": "2026-11-04T10:00:00Z",', '  "extracted_at": "2026-11-05T09:30:00Z",')
    ]
    assert one.endswith("}\n")


def test_written_manifest_reads_back_to_the_same_inventory(tmp_path: Path) -> None:
    inv = inventory.parse_manifest(_manifest())
    (tmp_path / "manifest.json").write_text(inventory.dump_manifest(inv), encoding="utf-8")

    assert inventory.read_inventory(tmp_path) == inv


FIXTURES = Path(__file__).parent / "fixtures" / "inventory"
SCHEMA_FILE = (
    Path(inventory.__file__).parent.parent / "schemas" / "inventory-manifest-v1.schema.json"
)
SAMPLES = ("legacy", "v9", "cloud")


def _schema_errors(manifest: Any) -> list[str]:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(manifest)]


def _sample(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name / "manifest.json").read_text(encoding="utf-8"))


def test_schema_file_is_a_valid_draft_2020_12_schema_whose_kinds_match_the_reader() -> None:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"] == {"const": inventory.SCHEMA_VERSION}
    assert schema["$defs"]["record"]["properties"]["kind"]["enum"] == list(inventory.KINDS)
    required = {
        rule["if"]["properties"]["kind"]["const"]: rule["then"]["properties"]["fields"]["required"]
        for rule in schema["$defs"]["record"]["allOf"]
    }
    assert required == {kind: list(names) for kind, names in inventory.KEY_FIELDS.items()}
    specs = {
        (rule["if"]["properties"]["kind"]["const"], name): spec
        for rule in schema["$defs"]["record"]["allOf"]
        for name, spec in rule["then"]["properties"]["fields"]["properties"].items()
        if name in inventory.KEY_FIELDS[rule["if"]["properties"]["kind"]["const"]]
    }
    for (kind, name), spec in specs.items():
        expected: dict[str, Any] = {"type": "string", "minLength": 1}
        if name in inventory.INTEGER_KEY_FIELDS:
            expected = {"type": "integer"}
        elif (kind, name) in inventory.NULLABLE_KEY_FIELDS:
            expected = {"type": ["string", "null"], "minLength": 1}
        assert {k: v for k, v in spec.items() if k != "description"} == expected, (kind, name)


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_manifest_validates_against_the_schema_and_the_reader(name: str) -> None:
    manifest = _sample(name)

    assert _schema_errors(manifest) == []
    inv = inventory.read_inventory(FIXTURES / name)
    assert inventory.dump_manifest(inv) == (FIXTURES / name / "manifest.json").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_artifact_files_match_their_recorded_hashes(name: str) -> None:
    inv = inventory.read_inventory(FIXTURES / name)

    on_disk = {
        p.relative_to(FIXTURES / name).as_posix()
        for p in (FIXTURES / name / "artifacts").rglob("*")
        if p.is_file()
    }
    recorded = {a.path: a for r in inv.records for a in r.artifacts}
    assert on_disk == set(recorded)
    for path, artifact in recorded.items():
        hashes = inventory.artifact_hashes(
            (FIXTURES / name / path).read_bytes(), text=artifact.content == "text"
        )
        assert (hashes.sha256, hashes.normalized_sha256) == (
            artifact.sha256,
            artifact.normalized_sha256,
        ), path


def _by_key(name: str) -> dict[tuple[str, str], list[inventory.Record]]:
    out: dict[tuple[str, str], list[inventory.Record]] = {}
    for record in inventory.read_inventory(FIXTURES / name).records:
        out.setdefault((record.kind, record.key), []).append(record)
    return out


def test_samples_carry_the_cases_the_diff_source_map_and_packages_slices_reuse() -> None:
    legacy, v9, cloud = (inventory.read_inventory(FIXTURES / n) for n in SAMPLES)
    legacy_keys, v9_keys, cloud_keys = _by_key("legacy"), _by_key("v9"), _by_key("cloud")

    # A missing component: in legacy and v9, gone from cloud.
    missing = ("attribute", "new_project|new_legacycode")
    assert missing in legacy_keys and missing in v9_keys and missing not in cloud_keys
    # An ambiguous key: two legacy forms with the same entity, type and name.
    assert list(legacy.ambiguous()) == [("form", "new_project|2|Information")]
    # A changed artifact: same key, different normalized hash.
    changed = ("web_resource", "new_/scripts/validation.js")
    assert (
        legacy_keys[changed][0].artifacts[0].normalized_sha256
        != v9_keys[changed][0].artifacts[0].normalized_sha256
    )
    # The same text with a BOM and CRLF (legacy) and without (v9): one normalized hash, two raw.
    same = ("web_resource", "new_/scripts/project.js")
    old, new = legacy_keys[same][0].artifacts[0], v9_keys[same][0].artifacts[0]
    assert old.normalized_sha256 == new.normalized_sha256 and old.sha256 != new.sha256
    # An unsupported kind and a partial, truncated one.
    assert legacy.coverage["dependency"].status == "unsupported"
    assert cloud.coverage["ribbon"].status == "partial" and cloud.coverage["ribbon"].truncated
    # Extraction gaps, by key and by description.
    assert {(g.kind, g.key) for g in legacy.gaps if g.key} == {
        ("plugin_assembly", "Contoso.Plugins")
    }
    assert any(g.kind is None and g.description for g in v9.gaps)


@pytest.mark.parametrize(
    ("name", "schema_refuses", "message"),
    [
        ("unknown-schema-version", True, r"schema_version 2 is not supported.*schema_version 1"),
        # JSON Schema cannot cross-check records against coverage; the reader does.
        ("kind-without-coverage", False, r"no coverage entry for kind 'form'"),
        ("complete-truncated", True, r"coverage\.entity is complete but truncated"),
        ("record-missing-key", True, r"records\[0\] lacks required field 'key'"),
    ],
)
def test_negative_fixture_fails_the_reader_and_where_expressible_the_schema(
    name: str, schema_refuses: bool, message: str
) -> None:
    manifest = json.loads((FIXTURES / "invalid" / f"{name}.json").read_text(encoding="utf-8"))

    assert bool(_schema_errors(manifest)) is schema_refuses
    with pytest.raises(inventory.InventoryError, match=message):
        inventory.parse_manifest(manifest)
