"""Migration inventory manifest v1: schema, reader, writer and normalization (ADR 0032)."""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_drop_key, r"records\[0\].*key"),
        (_wrong_key, r"key 'account\|2\|Other'.*'account\|2\|Account'"),
        (_drop_coverage, r"no coverage entry.*form"),
        (_complete_truncated, r"entity.*complete.*truncated"),
        (_count_mismatch, r"entity.*2 records.*holds 1"),
        (_foreign_artifact_path, r"artifacts/form/"),
        (_shared_artifact_path, r"shared by two records"),
        (_credentials_in_url, r"org_url.*credentials"),
        (_bad_origin, r"origin"),
    ],
)
def test_reader_refuses_a_manifest_that_breaks_the_contract(
    mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    manifest = _manifest()
    mutate(manifest)

    with pytest.raises(inventory.InventoryError, match=message):
        inventory.parse_manifest(manifest)


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
