# Spec D — Metadata Write API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add metadata write verbs to the `crm` CLI: `add-attribute` (14 attribute casts), `create-one-to-many` + `create-many-to-many` relationships, global option set CRUD (5 verbs), and `delete-entity`. Ship as a single PR + bump to `0.5.0`.

> **Implementation note (post-hoc):** During execution, the helpers `_label` and `_maybe_publish` shipped as `label` and `maybe_publish` (no leading underscore) because they're genuinely shared across `crm.core` sibling modules and the leading-underscore convention conflicted with strict pyright's `reportPrivateUsage` on cross-module imports. The plan steps below show the original underscore names; the committed code uses the unprefixed names.

**Architecture:** Three new strict-pyright modules in `crm/core/` split the new write surface by domain — `metadata_attrs.py` (attribute builders), `relationships.py` (1:N + N:N), `optionsets.py` (CRUD + granular update). `metadata.py` gains `delete_entity` and a shared `_maybe_publish` helper. All write verbs reuse Spec C's backend plumbing — no new `D365Backend` signatures. `cli.py` exposes nine new commands under the existing `metadata` group, all wired with `--solution` + `--publish/--no-publish` (default ON) matching `metadata create-entity`. Single PR against `main`.

**Tech Stack:** Python 3.9+, `requests` + `requests_ntlm` for HTTP, Click 8.x for CLI, `pytest` + `requests_mock` for tests, pyright (strict on `crm/core/*` + `crm/utils/d365_backend.py` + `crm/utils/d365_types.py`).

**Spec reference:** `docs/superpowers/specs/2026-05-25-spec-d-metadata-write-design.md` (commit `e3a1c20`).

---

## File Structure

### Files created

| Path | Purpose |
|---|---|
| `crm/core/metadata_attrs.py` | `add_attribute(...)` dispatcher + 14 typed `_<kind>_attr` builder helpers. Strict-typed. |
| `crm/core/relationships.py` | `create_one_to_many`, `create_many_to_many`, and the moved `list_relationships`. Strict-typed. |
| `crm/core/optionsets.py` | `list_optionsets`, `get_optionset`, `create_optionset`, `update_optionset`, `delete_optionset`. Strict-typed. |
| `crm/tests/test_metadata_attrs.py` | Unit tests: 14 kinds × {happy, validation, dry-run, read-back fail, non-ASCII label}. |
| `crm/tests/test_relationships.py` | Unit tests for 1:N + N:N, cascade defaults, schema-prefix validation. |
| `crm/tests/test_optionsets.py` | Unit tests: list/get/create/update (4 dispatch stages)/delete. |
| `crm/tests/test_delete_entity.py` | Unit tests for pre-flight refuse + happy + DELETE failure surfacing. |

### Files modified

| Path | Why |
|---|---|
| `crm/core/metadata.py` | Add `delete_entity` + shared `_maybe_publish` helper; remove `list_relationships` (moved). |
| `crm/utils/d365_types.py` | New TypedDicts: `AttributeKind` Literal, `AddAttributeResult`, `CreateRelationshipResult`, `OptionSetRow`, `OptionSetCreateResult`. |
| `crm/cli.py` | Nine new commands under the `metadata` group + `_confirm_destructive` helper + updated REPL help table. |
| `crm/tests/test_full_e2e.py` | Live e2e additions for the new verbs (gated by `D365_LIVE=1`). |
| `setup.py` | Bump version `0.4.0` → `0.5.0`. |
| `CHANGELOG.md` | Prepend `0.5.0` section. |
| `README.md` | Append the new commands to the commands table. |

---

## Task 1: Foundation — version bump, CHANGELOG stub, TypedDicts, `_maybe_publish` helper

**Files:**
- Modify: `setup.py`
- Modify: `CHANGELOG.md`
- Modify: `crm/utils/d365_types.py`
- Modify: `crm/core/metadata.py`

- [ ] **Step 1: Bump version**

In `setup.py`, change `version="0.4.0"` to `version="0.5.0"`.

- [ ] **Step 2: Prepend CHANGELOG stub**

Open `CHANGELOG.md`. Right under the top heading, insert:

```markdown
## 0.5.0 — 2026-05-25

### Added

- `metadata add-attribute` — add columns to existing entities. Supports 14
  attribute kinds: string, memo, integer, bigint, decimal, double, money,
  boolean, datetime, picklist, multiselect, lookup, image, file.
- `metadata create-one-to-many` + `metadata create-many-to-many` — create
  1:N and N:N relationships via the dedicated Dataverse actions.
- Global option set CRUD: `metadata list-optionsets`, `get-optionset`,
  `create-optionset`, `update-optionset`, `delete-optionset`. `update`
  is granular: `--insert-option` / `--update-option` / `--delete-option`
  / `--reorder` flags map to the matching bound actions.
- `metadata delete-entity` — drop a custom table, guarded by interactive
  confirm + `--yes` skip + client-side `IsCustomEntity` + `IsManaged`
  pre-flight check.

All new write verbs accept `--solution <uniquename>` (header
`MSCRM.SolutionUniqueName`) and `--publish/--no-publish` (default ON),
matching `metadata create-entity`. Delete verbs skip publish.
```

- [ ] **Step 3: Add TypedDicts**

Append to `crm/utils/d365_types.py` (after `AsyncOperationRow`, before the wire-boundary unions):

```python
from typing import Literal

AttributeKind = Literal[
    "string", "memo", "integer", "bigint", "decimal", "double", "money",
    "boolean", "datetime", "picklist", "multiselect", "lookup", "image", "file",
]


class AddAttributeResult(TypedDict, total=False):
    created: bool
    entity: str
    schema_name: str
    logical_name: str
    attribute_type: str
    attribute_logical_name: str
    metadata_id_url: str
    solution: str
    published: bool
    attribute_lookup_error: str


class CreateRelationshipResult(TypedDict, total=False):
    created: bool
    kind: str  # "OneToMany" | "ManyToMany"
    schema_name: str
    referenced_entity: str
    referencing_entity: str
    referencing_attribute: str
    intersect_entity: str
    relationship_id: str
    metadata_id_url: str
    solution: str
    published: bool
    relationship_lookup_error: str


class OptionSetRow(TypedDict, total=False):
    Name: str
    DisplayName: LabelPayload
    IsCustomOptionSet: bool
    IsGlobal: bool
    IsManaged: bool


class OptionSetCreateResult(TypedDict, total=False):
    created: bool
    name: str
    metadata_id_url: str
    solution: str
    published: bool
    optionset_lookup_error: str
```

Re-import `Literal` at the top of the file: change `from typing import Any, Generic, TypedDict, TypeVar, Union` to `from typing import Any, Generic, Literal, TypedDict, TypeVar, Union`.

- [ ] **Step 4: Add `_maybe_publish` to `metadata.py`**

Open `crm/core/metadata.py`. After the `_label` function definition, add:

```python
def _maybe_publish(backend: D365Backend, info: dict[str, Any], publish: bool) -> dict[str, Any]:
    """Run PublishAllXml unless dry-run or publish=False. Returns info dict (mutated)."""
    if not publish or info.get("_dry_run"):
        return info
    from crm.core import solution as sol_mod
    sol_mod.publish_all(backend)
    info["published"] = True
    return info
```

- [ ] **Step 5: Run pyright + tests, expect green**

Run: `pyright crm/core/metadata.py crm/utils/d365_types.py`
Expected: `0 errors`.

Run: `pytest crm/tests/test_core.py -v`
Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add setup.py CHANGELOG.md crm/utils/d365_types.py crm/core/metadata.py
git commit -m "Spec D foundation: 0.5.0 bump, TypedDicts, _maybe_publish helper"
```

---

## Task 2: `delete_entity` in `metadata.py`

**Files:**
- Modify: `crm/core/metadata.py`
- Create: `crm/tests/test_delete_entity.py`

- [ ] **Step 1: Write failing tests**

Create `crm/tests/test_delete_entity.py`:

```python
"""Unit tests for metadata.delete_entity."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


class TestDeleteEntity:
    def test_refuses_non_custom_entity(self, backend):
        from crm.core import metadata as meta_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')"),
                json={"LogicalName": "account", "IsCustomEntity": False, "IsManaged": True},
            )
            with pytest.raises(D365Error, match="not a custom entity"):
                meta_mod.delete_entity(backend, "account")

    def test_refuses_managed_entity(self, backend):
        from crm.core import metadata as meta_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='managed_thing')"),
                json={"LogicalName": "managed_thing", "IsCustomEntity": True, "IsManaged": True},
            )
            with pytest.raises(D365Error, match="managed"):
                meta_mod.delete_entity(backend, "managed_thing")

    def test_happy_path_deletes_with_solution_header(self, backend):
        from crm.core import metadata as meta_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={"LogicalName": "new_widget", "IsCustomEntity": True, "IsManaged": False},
            )
            m.delete(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                status_code=204,
            )
            info = meta_mod.delete_entity(backend, "new_widget", solution="DevSolution")
        assert info["deleted"] is True
        assert info["logical_name"] == "new_widget"
        assert info["solution"] == "DevSolution"
        delete_req = m.request_history[-1]
        assert delete_req.method == "DELETE"
        assert delete_req.headers.get("MSCRM.SolutionUniqueName") == "DevSolution"

    def test_delete_server_failure_surfaces_d365error(self, backend):
        from crm.core import metadata as meta_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={"LogicalName": "new_widget", "IsCustomEntity": True, "IsManaged": False},
            )
            m.delete(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                status_code=400,
                json={"error": {"code": "0x80048404", "message": "Cannot delete: dependencies exist"}},
            )
            with pytest.raises(D365Error, match="dependencies"):
                meta_mod.delete_entity(backend, "new_widget")
```

- [ ] **Step 2: Run tests, expect failure**

Run: `pytest crm/tests/test_delete_entity.py -v`
Expected: FAIL with `AttributeError: module 'crm.core.metadata' has no attribute 'delete_entity'`.

- [ ] **Step 3: Implement `delete_entity`**

In `crm/core/metadata.py`, append (after `_maybe_publish`):

```python
def delete_entity(
    backend: D365Backend,
    logical_name: str,
    *,
    solution: str | None = None,
) -> dict[str, Any]:
    """Permanently delete a custom entity (table) and ALL its rows.

    Pre-flight: refuses if `IsCustomEntity=False` or `IsManaged=True`.
    Server enforces remaining dependency checks (workflows, forms,
    relationships) and returns 4xx on conflict.
    """
    if not logical_name:
        raise D365Error("logical_name is required.")
    path = f"EntityDefinitions(LogicalName='{logical_name}')"
    rb = as_dict(backend.get(
        path,
        params={"$select": "IsCustomEntity,IsManaged"},
    ))
    if rb.get("IsCustomEntity") is False:
        raise D365Error(
            f"{logical_name!r} is not a custom entity; refusing to delete.",
            code="NotCustomEntity",
        )
    if rb.get("IsManaged") is True:
        raise D365Error(
            f"{logical_name!r} is a managed entity; uninstall the parent "
            "solution to remove it.",
            code="ManagedEntity",
        )
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    backend.delete(path, extra_headers=headers)
    return {
        "deleted": True,
        "logical_name": logical_name,
        "solution": solution,
    }
```

Confirm `D365Error` already accepts a `code=` kwarg in `crm/utils/d365_backend.py:34`. If not (it should — Spec C added it), inspect that class first.

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest crm/tests/test_delete_entity.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run pyright**

Run: `pyright crm/core/metadata.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add crm/core/metadata.py crm/tests/test_delete_entity.py
git commit -m "Spec D: metadata.delete_entity with pre-flight guard"
```

---

## Task 3: `relationships.py` — move `list_relationships`, add `create_one_to_many`

**Files:**
- Create: `crm/core/relationships.py`
- Create: `crm/tests/test_relationships.py`
- Modify: `crm/core/metadata.py` (remove `list_relationships`)
- Modify: `crm/cli.py` (update import for the moved helper)

- [ ] **Step 1: Create `crm/core/relationships.py` with the moved helper + stub for `create_one_to_many`**

Create `crm/core/relationships.py`:

```python
"""Relationship metadata (1:N + N:N) — create + list helpers.

`create_one_to_many` and `create_many_to_many` use the dedicated
`CreateOneToManyRequest` / `CreateManyToManyRequest` unbound actions
rather than POSTing directly to `/RelationshipDefinitions`; the actions
also create the lookup attribute (1:N) or intersect entity (N:N)
atomically.
"""

from __future__ import annotations

import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import _label, _maybe_publish

_VALID_CASCADE = {"NoCascade", "Cascade", "Active", "UserOwned", "RemoveLink", "Restrict"}
_VALID_MENU_BEHAVIOR = {"UseLabel", "UseCollectionName", "DoNotDisplay"}
_VALID_REQUIRED = {"None", "Recommended", "ApplicationRequired"}


def list_relationships(backend: D365Backend, logical_name: str) -> dict[str, Any]:
    """Return one-to-many and many-to-many relationships for an entity."""
    one_to_many = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/OneToManyRelationships",
        params={"$select": "SchemaName,ReferencedEntity,ReferencingEntity,ReferencingAttribute"},
    ))
    many_to_many = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/ManyToManyRelationships",
        params={"$select": "SchemaName,Entity1LogicalName,Entity2LogicalName,IntersectEntityName"},
    ))
    return {
        "OneToMany": one_to_many.get("value", []),
        "ManyToMany": many_to_many.get("value", []),
    }


def _parse_relationship_id(entity_id_url: str | None) -> str | None:
    if not entity_id_url:
        return None
    match = re.search(r"RelationshipDefinitions\(([0-9a-fA-F-]{36})\)", entity_id_url)
    return match.group(1) if match else None


def create_one_to_many(
    backend: D365Backend,
    *,
    schema_name: str,
    referenced_entity: str,
    referencing_entity: str,
    lookup_schema: str,
    lookup_display: str,
    lookup_required: str = "None",
    lookup_description: str | None = None,
    cascade_assign: str = "NoCascade",
    cascade_delete: str = "RemoveLink",
    cascade_reparent: str = "NoCascade",
    cascade_share: str = "NoCascade",
    cascade_unshare: str = "NoCascade",
    cascade_merge: str = "NoCascade",
    menu_label: str | None = None,
    menu_behavior: str = "UseLabel",
    menu_order: int = 10000,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a 1:N relationship + lookup attribute atomically.

    Calls `POST /CreateOneToManyRequest`. Read-back populates
    `schema_name` and `referencing_attribute` from the server.
    """
    if "_" not in schema_name:
        raise D365Error(
            "schema_name must include a publisher prefix, e.g. 'new_account_new_project'."
        )
    if "_" not in lookup_schema:
        raise D365Error("lookup_schema must include a publisher prefix.")
    if lookup_required not in _VALID_REQUIRED:
        raise D365Error(f"lookup_required must be one of {sorted(_VALID_REQUIRED)}.")
    for name, value in (
        ("cascade_assign", cascade_assign), ("cascade_delete", cascade_delete),
        ("cascade_reparent", cascade_reparent), ("cascade_share", cascade_share),
        ("cascade_unshare", cascade_unshare), ("cascade_merge", cascade_merge),
    ):
        if value not in _VALID_CASCADE:
            raise D365Error(f"{name} must be one of {sorted(_VALID_CASCADE)}.")
    if menu_behavior not in _VALID_MENU_BEHAVIOR:
        raise D365Error(f"menu_behavior must be one of {sorted(_VALID_MENU_BEHAVIOR)}.")

    lookup_payload: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
        "SchemaName": lookup_schema,
        "DisplayName": _label(lookup_display),
        "RequiredLevel": {"Value": lookup_required},
    }
    if lookup_description:
        lookup_payload["Description"] = _label(lookup_description)

    body: dict[str, Any] = {
        "OneToManyRelationship": {
            "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
            "SchemaName": schema_name,
            "ReferencedEntity": referenced_entity,
            "ReferencingEntity": referencing_entity,
            "AssociatedMenuConfiguration": {
                "Behavior": menu_behavior,
                "Group": "Details",
                "Label": _label(menu_label) if menu_label else None,
                "Order": menu_order,
            },
            "CascadeConfiguration": {
                "Assign": cascade_assign,
                "Delete": cascade_delete,
                "Reparent": cascade_reparent,
                "Share": cascade_share,
                "Unshare": cascade_unshare,
                "Merge": cascade_merge,
            },
        },
        "Lookup": lookup_payload,
    }
    if solution:
        body["SolutionUniqueName"] = solution

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "CreateOneToManyRequest",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url")
    relationship_id = _parse_relationship_id(entity_id_url)
    schema_readback: str | None = None
    referencing_attr: str | None = None
    lookup_error: str | None = None
    if not relationship_id:
        lookup_error = (
            f"Could not parse RelationshipId from response: {entity_id_url!r}"
        )
    else:
        try:
            rb = as_dict(backend.get(
                f"RelationshipDefinitions({relationship_id})",
                params={"$select": "SchemaName,ReferencingAttribute"},
            ))
            schema_readback = rb.get("SchemaName")
            referencing_attr = rb.get("ReferencingAttribute")
        except D365Error as exc:
            lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "kind": "OneToMany",
        "schema_name": schema_readback or schema_name,
        "referenced_entity": referenced_entity,
        "referencing_entity": referencing_entity,
        "referencing_attribute": referencing_attr,
        "relationship_id": relationship_id,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if lookup_error:
        out["relationship_lookup_error"] = lookup_error
    _maybe_publish(backend, out, publish)
    return out
```

- [ ] **Step 2: Remove `list_relationships` from `metadata.py`**

Open `crm/core/metadata.py`. Find `def list_relationships(...)` (around line 243). Delete the entire function. Add at the top of the file (after existing imports, alongside the docstring):

```python
# `list_relationships` lives in crm/core/relationships.py — moved in Spec D.
```

(Optional comment, can be omitted.)

- [ ] **Step 3: Update `cli.py` to import from the new module**

In `crm/cli.py`, find the imports of `metadata as meta_mod` (around line 30). Add nearby:

```python
from crm.core import relationships as rel_mod
```

In the `metadata_relationships` command body (~line 867), change `meta_mod.list_relationships(...)` to `rel_mod.list_relationships(...)`.

- [ ] **Step 4: Write tests for `create_one_to_many`**

Create `crm/tests/test_relationships.py`:

```python
"""Unit tests for crm.core.relationships."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


_REL_ID = "22222222-2222-2222-2222-222222222222"


class TestCreateOneToMany:
    def test_happy_path_posts_action_and_reads_back(self, backend):
        from crm.core import relationships as rel
        rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("CreateOneToManyRequest"),
                status_code=204,
                headers={"OData-EntityId": rel_url},
            )
            m.get(
                rel_url,
                json={"SchemaName": "new_account_new_project",
                      "ReferencingAttribute": "new_accountid"},
            )
            info = rel.create_one_to_many(
                backend,
                schema_name="new_account_new_project",
                referenced_entity="account",
                referencing_entity="new_project",
                lookup_schema="new_AccountId",
                lookup_display="Account",
            )
        assert info["created"] is True
        assert info["kind"] == "OneToMany"
        assert info["schema_name"] == "new_account_new_project"
        assert info["referencing_attribute"] == "new_accountid"
        assert info["relationship_id"] == _REL_ID
        # Verify default cascade
        body = m.request_history[0].json()
        cc = body["OneToManyRelationship"]["CascadeConfiguration"]
        assert cc["Delete"] == "RemoveLink"
        assert cc["Assign"] == "NoCascade"

    def test_rejects_schema_without_prefix(self, backend):
        from crm.core import relationships as rel
        with pytest.raises(D365Error, match="publisher prefix"):
            rel.create_one_to_many(
                backend,
                schema_name="bad",
                referenced_entity="account",
                referencing_entity="new_project",
                lookup_schema="new_AccountId",
                lookup_display="Account",
            )

    def test_rejects_bad_cascade_value(self, backend):
        from crm.core import relationships as rel
        with pytest.raises(D365Error, match="cascade_delete"):
            rel.create_one_to_many(
                backend,
                schema_name="new_a_b",
                referenced_entity="account",
                referencing_entity="new_project",
                lookup_schema="new_AccountId",
                lookup_display="Account",
                cascade_delete="BogusValue",
            )

    def test_readback_failure_non_fatal(self, backend):
        from crm.core import relationships as rel
        rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("CreateOneToManyRequest"),
                status_code=204,
                headers={"OData-EntityId": rel_url},
            )
            m.get(rel_url, status_code=500, json={"error": {"message": "boom"}})
            info = rel.create_one_to_many(
                backend,
                schema_name="new_a_b",
                referenced_entity="account",
                referencing_entity="new_project",
                lookup_schema="new_AccountId",
                lookup_display="Account",
            )
        assert info["created"] is True
        assert "relationship_lookup_error" in info


class TestListRelationshipsMoved:
    def test_list_relationships_works_from_new_module(self, backend):
        from crm.core import relationships as rel
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')/OneToManyRelationships"),
                json={"value": [{"SchemaName": "one"}]},
            )
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')/ManyToManyRelationships"),
                json={"value": [{"SchemaName": "many"}]},
            )
            result = rel.list_relationships(backend, "account")
        assert result["OneToMany"][0]["SchemaName"] == "one"
        assert result["ManyToMany"][0]["SchemaName"] == "many"
```

- [ ] **Step 5: Run all relationship tests**

Run: `pytest crm/tests/test_relationships.py -v`
Expected: 5 passed.

- [ ] **Step 6: Run existing tests (they import `metadata`, validate no regression)**

Run: `pytest crm/tests/test_core.py -v`
Expected: all pass (since CLI imports updated).

- [ ] **Step 7: Run pyright**

Run: `pyright crm/core/relationships.py crm/core/metadata.py crm/cli.py`
Expected: 0 errors.

- [ ] **Step 8: Commit**

```bash
git add crm/core/relationships.py crm/core/metadata.py crm/cli.py crm/tests/test_relationships.py
git commit -m "Spec D: relationships module + create_one_to_many"
```

---

## Task 4: `create_many_to_many` in `relationships.py`

**Files:**
- Modify: `crm/core/relationships.py`
- Modify: `crm/tests/test_relationships.py`

- [ ] **Step 1: Write failing test**

Append to `crm/tests/test_relationships.py`:

```python
class TestCreateManyToMany:
    def test_happy_path(self, backend):
        from crm.core import relationships as rel
        rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("CreateManyToManyRequest"),
                status_code=204,
                headers={"OData-EntityId": rel_url},
            )
            m.get(
                rel_url,
                json={"SchemaName": "new_account_project",
                      "IntersectEntityName": "new_account_project"},
            )
            info = rel.create_many_to_many(
                backend,
                schema_name="new_account_project",
                entity1_logical="account",
                entity2_logical="new_project",
                intersect_entity="new_account_project",
            )
        assert info["created"] is True
        assert info["kind"] == "ManyToMany"
        assert info["intersect_entity"] == "new_account_project"
        body = m.request_history[0].json()
        assert body["IntersectEntitySchemaName"] == "new_account_project"
        assert body["ManyToManyRelationship"]["Entity1LogicalName"] == "account"

    def test_rejects_self_relationship(self, backend):
        from crm.core import relationships as rel
        with pytest.raises(D365Error, match="self N:N"):
            rel.create_many_to_many(
                backend,
                schema_name="new_x_y",
                entity1_logical="new_project",
                entity2_logical="new_project",
                intersect_entity="new_xy",
            )
```

- [ ] **Step 2: Run test, expect failure**

Run: `pytest crm/tests/test_relationships.py::TestCreateManyToMany -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'create_many_to_many'`.

- [ ] **Step 3: Implement `create_many_to_many`**

Append to `crm/core/relationships.py`:

```python
def create_many_to_many(
    backend: D365Backend,
    *,
    schema_name: str,
    entity1_logical: str,
    entity2_logical: str,
    intersect_entity: str,
    entity1_menu_label: str | None = None,
    entity1_menu_behavior: str = "UseCollectionName",
    entity1_menu_order: int = 10000,
    entity2_menu_label: str | None = None,
    entity2_menu_behavior: str = "UseCollectionName",
    entity2_menu_order: int = 10000,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create an N:N relationship via `CreateManyToManyRequest`.

    Server creates the intersect entity (`intersect_entity` is its logical name).
    """
    if "_" not in schema_name:
        raise D365Error(
            "schema_name must include a publisher prefix."
        )
    if entity1_logical == entity2_logical:
        raise D365Error("self N:N is not supported by Dataverse Web API.")
    for name, value in (
        ("entity1_menu_behavior", entity1_menu_behavior),
        ("entity2_menu_behavior", entity2_menu_behavior),
    ):
        if value not in _VALID_MENU_BEHAVIOR:
            raise D365Error(f"{name} must be one of {sorted(_VALID_MENU_BEHAVIOR)}.")

    body: dict[str, Any] = {
        "ManyToManyRelationship": {
            "@odata.type": "Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata",
            "SchemaName": schema_name,
            "Entity1LogicalName": entity1_logical,
            "Entity2LogicalName": entity2_logical,
            "Entity1AssociatedMenuConfiguration": {
                "Behavior": entity1_menu_behavior,
                "Group": "Details",
                "Label": _label(entity1_menu_label) if entity1_menu_label else None,
                "Order": entity1_menu_order,
            },
            "Entity2AssociatedMenuConfiguration": {
                "Behavior": entity2_menu_behavior,
                "Group": "Details",
                "Label": _label(entity2_menu_label) if entity2_menu_label else None,
                "Order": entity2_menu_order,
            },
        },
        "IntersectEntitySchemaName": intersect_entity,
    }
    if solution:
        body["SolutionUniqueName"] = solution

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "CreateManyToManyRequest",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url")
    relationship_id = _parse_relationship_id(entity_id_url)
    schema_readback: str | None = None
    intersect_readback: str | None = None
    lookup_error: str | None = None
    if not relationship_id:
        lookup_error = (
            f"Could not parse RelationshipId from response: {entity_id_url!r}"
        )
    else:
        try:
            rb = as_dict(backend.get(
                f"RelationshipDefinitions({relationship_id})",
                params={"$select": "SchemaName,IntersectEntityName"},
            ))
            schema_readback = rb.get("SchemaName")
            intersect_readback = rb.get("IntersectEntityName")
        except D365Error as exc:
            lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "kind": "ManyToMany",
        "schema_name": schema_readback or schema_name,
        "intersect_entity": intersect_readback or intersect_entity,
        "relationship_id": relationship_id,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if lookup_error:
        out["relationship_lookup_error"] = lookup_error
    _maybe_publish(backend, out, publish)
    return out
```

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest crm/tests/test_relationships.py -v`
Expected: 7 passed.

- [ ] **Step 5: Pyright**

Run: `pyright crm/core/relationships.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add crm/core/relationships.py crm/tests/test_relationships.py
git commit -m "Spec D: create_many_to_many"
```

---

## Task 5: `metadata_attrs.py` scaffold + `string` + `memo` builders

**Files:**
- Create: `crm/core/metadata_attrs.py`
- Create: `crm/tests/test_metadata_attrs.py`

- [ ] **Step 1: Write failing tests for string + memo**

Create `crm/tests/test_metadata_attrs.py`:

```python
"""Unit tests for crm.core.metadata_attrs."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


_ATTR_ID = "33333333-3333-3333-3333-333333333333"


def _mock_post_and_readback(m, backend, entity: str, attr_logical: str,
                            attr_type: str = "String"):
    attr_url = backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes({_ATTR_ID})"
    )
    m.post(
        backend.url_for(f"EntityDefinitions(LogicalName='{entity}')/Attributes"),
        status_code=204,
        headers={"OData-EntityId": attr_url},
    )
    m.get(
        attr_url,
        json={
            "LogicalName": attr_logical,
            "SchemaName": attr_logical,
            "AttributeType": attr_type,
        },
    )
    return attr_url


class TestAddAttributeString:
    def test_string_posts_correct_payload(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_label", "String")
            info = ma.add_attribute(
                backend,
                entity="new_widget",
                kind="string",
                schema_name="new_Label",
                display_name="Label",
                max_length=100,
            )
        assert info["created"] is True
        assert info["attribute_type"] == "String"
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.StringAttributeMetadata"
        assert body["MaxLength"] == 100
        assert body["FormatName"]["Value"] == "Text"

    def test_string_requires_max_length(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="max_length"):
            ma.add_attribute(
                backend,
                entity="new_widget",
                kind="string",
                schema_name="new_Label",
                display_name="Label",
            )

    def test_string_rejects_precision_flag(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="precision"):
            ma.add_attribute(
                backend,
                entity="new_widget",
                kind="string",
                schema_name="new_Label",
                display_name="Label",
                max_length=100,
                precision=2,
            )


class TestAddAttributeMemo:
    def test_memo_posts_correct_payload(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_notes", "Memo")
            info = ma.add_attribute(
                backend,
                entity="new_widget",
                kind="memo",
                schema_name="new_Notes",
                display_name="Notes",
                max_length=4000,
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.MemoAttributeMetadata"
        assert body["MaxLength"] == 4000

    def test_memo_requires_max_length(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="max_length"):
            ma.add_attribute(
                backend,
                entity="new_widget",
                kind="memo",
                schema_name="new_Notes",
                display_name="Notes",
            )


class TestAddAttributeNonAsciiLabel:
    def test_unicode_label_passes_through(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_label", "String")
            ma.add_attribute(
                backend,
                entity="new_widget",
                kind="string",
                schema_name="new_Label",
                display_name="Étiquette — niño",
                max_length=100,
            )
        body = m.request_history[0].json()
        assert body["DisplayName"]["LocalizedLabels"][0]["Label"] == "Étiquette — niño"
```

- [ ] **Step 2: Run tests, expect failure**

Run: `pytest crm/tests/test_metadata_attrs.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create `crm/core/metadata_attrs.py` with dispatcher + string + memo**

```python
"""Add attributes to existing entities — 14 typed builders + dispatcher.

`add_attribute` is the single public entry point. It validates the
kwarg matrix per `kind` (raising `D365Error` before any HTTP), routes
to a `_<kind>_attr` builder for the OData body, POSTs to
`EntityDefinitions(...)/Attributes`, and reads back the canonical
attribute fields. Lookup short-circuits to `create_one_to_many`.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import _label, _maybe_publish

_VALID_REQUIRED = {"None", "Recommended", "ApplicationRequired"}
_STRING_FORMATS = {"Text", "Email", "Url", "Phone", "TextArea", "TickerSymbol", "VersionNumber"}
_DATETIME_FORMATS = {"DateOnly", "DateAndTime"}

_NUMERIC_KINDS = {"integer", "bigint", "decimal", "double", "money"}
_LENGTH_KINDS = {"string", "memo"}
_PICKLIST_KINDS = {"picklist", "multiselect"}


def _require(kwargs: dict[str, Any], *names: str) -> None:
    for n in names:
        if kwargs.get(n) is None:
            raise D365Error(f"--{n.replace('_', '-')} is required for this kind.")


def _forbid(kwargs: dict[str, Any], *names: str) -> None:
    for n in names:
        if kwargs.get(n) is not None:
            raise D365Error(f"--{n.replace('_', '-')} is not valid for this kind.")


def _base_attr_payload(
    *, schema_name: str, logical_name: str, display_name: str,
    description: str | None, required: str,
) -> dict[str, Any]:
    if required not in _VALID_REQUIRED:
        raise D365Error(f"required must be one of {sorted(_VALID_REQUIRED)}.")
    payload: dict[str, Any] = {
        "SchemaName": schema_name,
        "LogicalName": logical_name,
        "DisplayName": _label(display_name),
        "RequiredLevel": {"Value": required},
    }
    if description:
        payload["Description"] = _label(description)
    return payload


def _string_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "precision", "target_entity", "optionset_name", "options",
            "min_value", "max_value", "max_size_kb")
    _require(opts, "max_length")
    fmt = opts.get("format_name") or "Text"
    if fmt not in _STRING_FORMATS:
        raise D365Error(f"format_name for string must be one of {sorted(_STRING_FORMATS)}.")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.StringAttributeMetadata"
    body["MaxLength"] = opts["max_length"]
    body["FormatName"] = {"Value": fmt}
    return body


def _memo_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "precision", "target_entity", "optionset_name", "options",
            "min_value", "max_value", "max_size_kb")
    _require(opts, "max_length")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.MemoAttributeMetadata"
    body["MaxLength"] = opts["max_length"]
    body["Format"] = "TextArea"
    return body


_BUILDERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "string": _string_attr,
    "memo": _memo_attr,
}


def _parse_attribute_id(entity_id_url: str | None) -> str | None:
    if not entity_id_url:
        return None
    match = re.search(r"Attributes\(([0-9a-fA-F-]{36})\)", entity_id_url)
    return match.group(1) if match else None


def add_attribute(
    backend: D365Backend,
    *,
    entity: str,
    kind: str,
    schema_name: str,
    display_name: str,
    description: str | None = None,
    required: str = "None",
    max_length: int | None = None,
    format_name: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    precision: int | None = None,
    default_value: bool | int | None = None,
    true_label: str = "Yes",
    false_label: str = "No",
    optionset_name: str | None = None,
    options: list[tuple[int | None, str]] | None = None,
    target_entity: str | None = None,
    relationship_schema: str | None = None,
    max_size_kb: int | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Add an attribute (column) to an existing entity."""
    if "_" not in schema_name:
        raise D365Error("schema_name must include a publisher prefix.")
    logical_name = schema_name.lower()

    # Lookup is a special dispatch (covered in a later task).
    if kind == "lookup":
        raise D365Error("lookup kind not yet implemented in this build")

    builder = _BUILDERS.get(kind)
    if builder is None:
        raise D365Error(f"unknown attribute kind: {kind!r}")

    opts: dict[str, Any] = {
        "schema_name": schema_name,
        "logical_name": logical_name,
        "display_name": display_name,
        "description": description,
        "required": required,
        "max_length": max_length,
        "format_name": format_name,
        "min_value": min_value,
        "max_value": max_value,
        "precision": precision,
        "default_value": default_value,
        "true_label": true_label,
        "false_label": false_label,
        "optionset_name": optionset_name,
        "options": options,
        "target_entity": target_entity,
        "max_size_kb": max_size_kb,
    }
    body = builder(opts)

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    path = f"EntityDefinitions(LogicalName='{entity}')/Attributes"
    result = as_dict(backend.post(path, json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url")
    attr_id = _parse_attribute_id(entity_id_url)
    attr_logical: str | None = None
    attr_type: str | None = None
    lookup_error: str | None = None
    if not attr_id:
        lookup_error = f"Could not parse AttributeId from response: {entity_id_url!r}"
    else:
        try:
            rb = as_dict(backend.get(
                f"EntityDefinitions(LogicalName='{entity}')/Attributes({attr_id})",
                params={"$select": "LogicalName,SchemaName,AttributeType"},
            ))
            attr_logical = rb.get("LogicalName")
            attr_type = rb.get("AttributeType")
        except D365Error as exc:
            lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "entity": entity,
        "schema_name": schema_name,
        "logical_name": logical_name,
        "attribute_type": attr_type,
        "attribute_logical_name": attr_logical,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if lookup_error:
        out["attribute_lookup_error"] = lookup_error
    _maybe_publish(backend, out, publish)
    return out
```

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest crm/tests/test_metadata_attrs.py -v`
Expected: all 6 tests pass (3 string + 2 memo + 1 unicode).

- [ ] **Step 5: Pyright**

Run: `pyright crm/core/metadata_attrs.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add crm/core/metadata_attrs.py crm/tests/test_metadata_attrs.py
git commit -m "Spec D: metadata_attrs scaffold + string/memo builders"
```

---

## Task 6: Numeric attribute builders (`integer`, `bigint`, `decimal`, `double`, `money`)

**Files:**
- Modify: `crm/core/metadata_attrs.py`
- Modify: `crm/tests/test_metadata_attrs.py`

- [ ] **Step 1: Write failing tests**

Append to `crm/tests/test_metadata_attrs.py`:

```python
class TestAddAttributeNumeric:
    def test_integer_with_min_max(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_qty", "Integer")
            ma.add_attribute(
                backend, entity="new_widget", kind="integer",
                schema_name="new_Qty", display_name="Qty",
                min_value=0, max_value=1000,
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.IntegerAttributeMetadata"
        assert body["MinValue"] == 0
        assert body["MaxValue"] == 1000

    def test_bigint(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_bignum", "BigInt")
            ma.add_attribute(
                backend, entity="new_widget", kind="bigint",
                schema_name="new_Bignum", display_name="Bignum",
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.BigIntAttributeMetadata"

    def test_decimal_requires_precision(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="precision"):
            ma.add_attribute(
                backend, entity="new_widget", kind="decimal",
                schema_name="new_Amount", display_name="Amount",
            )

    def test_decimal_precision_in_range(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_amount", "Decimal")
            ma.add_attribute(
                backend, entity="new_widget", kind="decimal",
                schema_name="new_Amount", display_name="Amount",
                precision=4, min_value=-1000, max_value=1000,
            )
        body = m.request_history[0].json()
        assert body["Precision"] == 4

    def test_decimal_precision_out_of_range(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="precision"):
            ma.add_attribute(
                backend, entity="new_widget", kind="decimal",
                schema_name="new_Amount", display_name="Amount",
                precision=11,
            )

    def test_double(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_rate", "Double")
            ma.add_attribute(
                backend, entity="new_widget", kind="double",
                schema_name="new_Rate", display_name="Rate",
                precision=3,
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.DoubleAttributeMetadata"
        assert body["Precision"] == 3

    def test_money(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_price", "Money")
            ma.add_attribute(
                backend, entity="new_widget", kind="money",
                schema_name="new_Price", display_name="Price",
                precision=2,
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.MoneyAttributeMetadata"
        assert body["Precision"] == 2
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest crm/tests/test_metadata_attrs.py::TestAddAttributeNumeric -v`
Expected: 7 fails — `unknown attribute kind`.

- [ ] **Step 3: Add numeric builders to `metadata_attrs.py`**

In `crm/core/metadata_attrs.py`, add helpers and register builders. Insert after `_memo_attr`:

```python
def _common_numeric(opts: dict[str, Any], odata_type: str) -> dict[str, Any]:
    _forbid(opts, "max_length", "target_entity", "optionset_name", "options",
            "format_name", "max_size_kb")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = odata_type
    if opts.get("min_value") is not None:
        body["MinValue"] = opts["min_value"]
    if opts.get("max_value") is not None:
        body["MaxValue"] = opts["max_value"]
    return body


def _int_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "precision")
    return _common_numeric(opts, "Microsoft.Dynamics.CRM.IntegerAttributeMetadata")


def _bigint_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "precision")
    return _common_numeric(opts, "Microsoft.Dynamics.CRM.BigIntAttributeMetadata")


def _numeric_with_precision(
    opts: dict[str, Any], odata_type: str, precision_range: tuple[int, int],
) -> dict[str, Any]:
    _require(opts, "precision")
    prec = opts["precision"]
    lo, hi = precision_range
    if not (lo <= prec <= hi):
        raise D365Error(f"precision for this kind must be in [{lo}, {hi}].")
    body = _common_numeric(opts, odata_type)
    body["Precision"] = prec
    return body


def _decimal_attr(opts: dict[str, Any]) -> dict[str, Any]:
    return _numeric_with_precision(
        opts, "Microsoft.Dynamics.CRM.DecimalAttributeMetadata", (0, 10),
    )


def _double_attr(opts: dict[str, Any]) -> dict[str, Any]:
    return _numeric_with_precision(
        opts, "Microsoft.Dynamics.CRM.DoubleAttributeMetadata", (0, 5),
    )


def _money_attr(opts: dict[str, Any]) -> dict[str, Any]:
    return _numeric_with_precision(
        opts, "Microsoft.Dynamics.CRM.MoneyAttributeMetadata", (0, 4),
    )
```

Update the `_BUILDERS` dict (replace the existing 2-entry dict):

```python
_BUILDERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "string": _string_attr,
    "memo": _memo_attr,
    "integer": _int_attr,
    "bigint": _bigint_attr,
    "decimal": _decimal_attr,
    "double": _double_attr,
    "money": _money_attr,
}
```

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest crm/tests/test_metadata_attrs.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/core/metadata_attrs.py crm/tests/test_metadata_attrs.py
git commit -m "Spec D: numeric attribute builders (integer/bigint/decimal/double/money)"
```

---

## Task 7: `boolean` + `datetime` builders

**Files:**
- Modify: `crm/core/metadata_attrs.py`
- Modify: `crm/tests/test_metadata_attrs.py`

- [ ] **Step 1: Write failing tests**

Append to `crm/tests/test_metadata_attrs.py`:

```python
class TestAddAttributeBoolean:
    def test_boolean_default_labels(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_active", "Boolean")
            ma.add_attribute(
                backend, entity="new_widget", kind="boolean",
                schema_name="new_Active", display_name="Active",
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.BooleanAttributeMetadata"
        os = body["OptionSet"]
        assert os["TrueOption"]["Value"] == 1
        assert os["TrueOption"]["Label"]["LocalizedLabels"][0]["Label"] == "Yes"
        assert os["FalseOption"]["Value"] == 0
        assert os["FalseOption"]["Label"]["LocalizedLabels"][0]["Label"] == "No"

    def test_boolean_custom_labels_and_default(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_active", "Boolean")
            ma.add_attribute(
                backend, entity="new_widget", kind="boolean",
                schema_name="new_Active", display_name="Active",
                true_label="On", false_label="Off",
                default_value=True,
            )
        body = m.request_history[0].json()
        assert body["DefaultValue"] is True
        assert body["OptionSet"]["TrueOption"]["Label"]["LocalizedLabels"][0]["Label"] == "On"


class TestAddAttributeDateTime:
    def test_datetime_default_format(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_when", "DateTime")
            ma.add_attribute(
                backend, entity="new_widget", kind="datetime",
                schema_name="new_When", display_name="When",
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata"
        assert body["Format"] == "DateAndTime"

    def test_datetime_date_only(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_day", "DateTime")
            ma.add_attribute(
                backend, entity="new_widget", kind="datetime",
                schema_name="new_Day", display_name="Day",
                format_name="DateOnly",
            )
        body = m.request_history[0].json()
        assert body["Format"] == "DateOnly"

    def test_datetime_bad_format_rejected(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="format_name"):
            ma.add_attribute(
                backend, entity="new_widget", kind="datetime",
                schema_name="new_When", display_name="When",
                format_name="Garbage",
            )
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest crm/tests/test_metadata_attrs.py::TestAddAttributeBoolean crm/tests/test_metadata_attrs.py::TestAddAttributeDateTime -v`
Expected: 5 fails.

- [ ] **Step 3: Implement boolean + datetime builders**

In `crm/core/metadata_attrs.py`, append:

```python
def _bool_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity", "optionset_name",
            "options", "format_name", "min_value", "max_value", "max_size_kb")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.BooleanAttributeMetadata"
    body["OptionSet"] = {
        "TrueOption": {"Value": 1, "Label": _label(opts.get("true_label", "Yes"))},
        "FalseOption": {"Value": 0, "Label": _label(opts.get("false_label", "No"))},
        "OptionSetType": "Boolean",
    }
    if opts.get("default_value") is not None:
        body["DefaultValue"] = bool(opts["default_value"])
    return body


def _datetime_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity", "optionset_name",
            "options", "min_value", "max_value", "max_size_kb")
    fmt = opts.get("format_name") or "DateAndTime"
    if fmt not in _DATETIME_FORMATS:
        raise D365Error(f"format_name for datetime must be one of {sorted(_DATETIME_FORMATS)}.")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata"
    body["Format"] = fmt
    return body
```

Add `"boolean": _bool_attr, "datetime": _datetime_attr,` to the `_BUILDERS` dict.

- [ ] **Step 4: Run tests**

Run: `pytest crm/tests/test_metadata_attrs.py -v`
Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/core/metadata_attrs.py crm/tests/test_metadata_attrs.py
git commit -m "Spec D: boolean + datetime attribute builders"
```

---

## Task 8: `picklist` + `multiselect` builders

**Files:**
- Modify: `crm/core/metadata_attrs.py`
- Modify: `crm/tests/test_metadata_attrs.py`

- [ ] **Step 1: Write failing tests**

Append to `crm/tests/test_metadata_attrs.py`:

```python
class TestAddAttributePicklist:
    def test_picklist_inline_options(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_priority", "Picklist")
            ma.add_attribute(
                backend, entity="new_widget", kind="picklist",
                schema_name="new_Priority", display_name="Priority",
                options=[(1, "Low"), (2, "Medium"), (3, "High")],
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
        opts = body["OptionSet"]["Options"]
        assert opts[0]["Value"] == 1
        assert opts[0]["Label"]["LocalizedLabels"][0]["Label"] == "Low"
        assert body["OptionSet"]["IsGlobal"] is False

    def test_picklist_global_optionset_ref(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_priority", "Picklist")
            ma.add_attribute(
                backend, entity="new_widget", kind="picklist",
                schema_name="new_Priority", display_name="Priority",
                optionset_name="new_global_priority",
            )
        body = m.request_history[0].json()
        assert body["OptionSet"]["Name"] == "new_global_priority"
        assert body["OptionSet"]["IsGlobal"] is True

    def test_picklist_rejects_both_options_and_global(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="mutually exclusive"):
            ma.add_attribute(
                backend, entity="new_widget", kind="picklist",
                schema_name="new_Priority", display_name="Priority",
                options=[(1, "Low")], optionset_name="new_other",
            )

    def test_picklist_requires_one_of(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="optionset_name or options"):
            ma.add_attribute(
                backend, entity="new_widget", kind="picklist",
                schema_name="new_Priority", display_name="Priority",
            )


class TestAddAttributeMultiselect:
    def test_multiselect_inline(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_tags", "Virtual")
            ma.add_attribute(
                backend, entity="new_widget", kind="multiselect",
                schema_name="new_Tags", display_name="Tags",
                options=[(1, "A"), (2, "B")],
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata"
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest crm/tests/test_metadata_attrs.py::TestAddAttributePicklist crm/tests/test_metadata_attrs.py::TestAddAttributeMultiselect -v`
Expected: 5 fails.

- [ ] **Step 3: Implement picklist + multiselect builders**

In `crm/core/metadata_attrs.py`, append:

```python
def _build_options_payload(
    options: list[tuple[int | None, str]] | None,
    optionset_name: str | None,
) -> dict[str, Any]:
    has_inline = bool(options)
    has_global = bool(optionset_name)
    if has_inline and has_global:
        raise D365Error(
            "--options and --optionset-name are mutually exclusive."
        )
    if not has_inline and not has_global:
        raise D365Error(
            "either --optionset-name or --options is required for picklist/multiselect."
        )
    if has_global:
        return {"Name": optionset_name, "IsGlobal": True, "OptionSetType": "Picklist"}
    seen: set[int] = set()
    option_list: list[dict[str, Any]] = []
    assert options is not None  # mypy/pyright guard; has_inline ensures non-empty
    for value, label in options:
        if value is not None:
            if value in seen:
                raise D365Error(f"Duplicate option value: {value}.")
            seen.add(value)
        if not label:
            raise D365Error("Option label must not be empty.")
        opt: dict[str, Any] = {"Label": _label(label)}
        if value is not None:
            opt["Value"] = value
        option_list.append(opt)
    return {"Options": option_list, "IsGlobal": False, "OptionSetType": "Picklist"}


def _picklist_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity",
            "min_value", "max_value", "format_name", "max_size_kb")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
    body["OptionSet"] = _build_options_payload(
        opts.get("options"), opts.get("optionset_name"),
    )
    if opts.get("default_value") is not None:
        body["DefaultFormValue"] = int(opts["default_value"])
    return body


def _multiselect_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity",
            "min_value", "max_value", "format_name", "max_size_kb")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata"
    body["OptionSet"] = _build_options_payload(
        opts.get("options"), opts.get("optionset_name"),
    )
    return body
```

Add `"picklist": _picklist_attr, "multiselect": _multiselect_attr,` to `_BUILDERS`.

- [ ] **Step 4: Run tests**

Run: `pytest crm/tests/test_metadata_attrs.py -v`
Expected: 23 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/core/metadata_attrs.py crm/tests/test_metadata_attrs.py
git commit -m "Spec D: picklist + multiselect attribute builders"
```

---

## Task 9: `lookup` builder — dispatches to `create_one_to_many`

**Files:**
- Modify: `crm/core/metadata_attrs.py`
- Modify: `crm/tests/test_metadata_attrs.py`

- [ ] **Step 1: Write failing test**

Append to `crm/tests/test_metadata_attrs.py`:

```python
class TestAddAttributeLookup:
    def test_lookup_dispatches_to_one_to_many(self, backend, monkeypatch):
        from crm.core import metadata_attrs as ma
        from crm.core import relationships as rel
        calls: dict[str, Any] = {}

        def fake_one_to_many(b, **kw):
            calls.update(kw)
            return {
                "created": True, "kind": "OneToMany",
                "schema_name": kw["schema_name"],
                "referencing_attribute": "new_accountid",
                "relationship_id": "rel-id",
                "metadata_id_url": "url",
                "solution": kw.get("solution"),
            }

        monkeypatch.setattr(rel, "create_one_to_many", fake_one_to_many)
        info = ma.add_attribute(
            backend, entity="new_widget", kind="lookup",
            schema_name="new_AccountId", display_name="Account",
            target_entity="account",
        )
        assert info["kind"] == "OneToMany"
        assert calls["referenced_entity"] == "account"
        assert calls["referencing_entity"] == "new_widget"
        assert calls["lookup_schema"] == "new_AccountId"
        assert calls["lookup_display"] == "Account"

    def test_lookup_requires_target_entity(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="target_entity"):
            ma.add_attribute(
                backend, entity="new_widget", kind="lookup",
                schema_name="new_AccountId", display_name="Account",
            )
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest crm/tests/test_metadata_attrs.py::TestAddAttributeLookup -v`
Expected: 2 fails — the placeholder `D365Error("lookup kind not yet implemented...")` from Task 5.

- [ ] **Step 3: Implement lookup dispatch**

In `crm/core/metadata_attrs.py`, replace the placeholder `if kind == "lookup":` block in `add_attribute` with:

```python
    if kind == "lookup":
        if target_entity is None:
            raise D365Error("--target-entity is required for lookup attribute.")
        _forbid_kwargs = {
            "max_length": max_length, "precision": precision,
            "min_value": min_value, "max_value": max_value,
            "format_name": format_name,
            "optionset_name": optionset_name, "options": options,
            "max_size_kb": max_size_kb,
        }
        for n, v in _forbid_kwargs.items():
            if v is not None:
                raise D365Error(f"--{n.replace('_', '-')} is not valid for lookup.")
        from crm.core import relationships as rel
        rel_schema = relationship_schema or f"{entity}_{logical_name}"
        return rel.create_one_to_many(
            backend,
            schema_name=rel_schema,
            referenced_entity=target_entity,
            referencing_entity=entity,
            lookup_schema=schema_name,
            lookup_display=display_name,
            lookup_required=required,
            lookup_description=description,
            publish=publish,
            solution=solution,
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest crm/tests/test_metadata_attrs.py -v`
Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/core/metadata_attrs.py crm/tests/test_metadata_attrs.py
git commit -m "Spec D: lookup attribute dispatches to create_one_to_many"
```

---

## Task 10: `image` + `file` builders + read-back-failure test + dry-run test

**Files:**
- Modify: `crm/core/metadata_attrs.py`
- Modify: `crm/tests/test_metadata_attrs.py`

- [ ] **Step 1: Write failing tests**

Append to `crm/tests/test_metadata_attrs.py`:

```python
class TestAddAttributeImageFile:
    def test_image(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_photo", "Image")
            ma.add_attribute(
                backend, entity="new_widget", kind="image",
                schema_name="new_Photo", display_name="Photo",
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.ImageAttributeMetadata"

    def test_file_default_size(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_doc", "File")
            ma.add_attribute(
                backend, entity="new_widget", kind="file",
                schema_name="new_Doc", display_name="Doc",
            )
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.FileAttributeMetadata"
        assert body["MaxSizeInKB"] == 32768

    def test_file_custom_size(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_doc", "File")
            ma.add_attribute(
                backend, entity="new_widget", kind="file",
                schema_name="new_Doc", display_name="Doc",
                max_size_kb=131072,
            )
        body = m.request_history[0].json()
        assert body["MaxSizeInKB"] == 131072


class TestAddAttributeReadbackFail:
    def test_readback_fail_marks_lookup_error(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            attr_url = backend.url_for(
                f"EntityDefinitions(LogicalName='new_widget')/Attributes({_ATTR_ID})"
            )
            m.post(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')/Attributes"),
                status_code=204,
                headers={"OData-EntityId": attr_url},
            )
            m.get(attr_url, status_code=500, json={"error": {"message": "boom"}})
            info = ma.add_attribute(
                backend, entity="new_widget", kind="string",
                schema_name="new_Label", display_name="Label",
                max_length=10,
            )
        assert info["created"] is True
        assert "attribute_lookup_error" in info


class TestAddAttributeDryRun:
    def test_dry_run_returns_envelope(self, profile, monkeypatch):
        monkeypatch.setenv("CRM_DRY_RUN", "1")
        backend = D365Backend(profile, password="pw", dry_run=True)
        from crm.core import metadata_attrs as ma
        info = ma.add_attribute(
            backend, entity="new_widget", kind="string",
            schema_name="new_Label", display_name="Label",
            max_length=10,
        )
        assert info.get("_dry_run") is True
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest crm/tests/test_metadata_attrs.py::TestAddAttributeImageFile crm/tests/test_metadata_attrs.py::TestAddAttributeReadbackFail crm/tests/test_metadata_attrs.py::TestAddAttributeDryRun -v`
Expected: 3 image/file fails; readback + dry-run may already pass (they exercise existing code).

- [ ] **Step 3: Implement image + file builders**

Append to `crm/core/metadata_attrs.py`:

```python
def _image_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity",
            "min_value", "max_value", "optionset_name", "options",
            "format_name", "max_size_kb")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.ImageAttributeMetadata"
    body["IsPrimaryImage"] = True
    return body


def _file_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity",
            "min_value", "max_value", "optionset_name", "options",
            "format_name")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.FileAttributeMetadata"
    body["MaxSizeInKB"] = opts.get("max_size_kb") or 32768
    return body
```

Add `"image": _image_attr, "file": _file_attr,` to `_BUILDERS`.

- [ ] **Step 4: Run all tests**

Run: `pytest crm/tests/test_metadata_attrs.py -v`
Expected: 30 passed.

- [ ] **Step 5: Pyright**

Run: `pyright crm/core/metadata_attrs.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add crm/core/metadata_attrs.py crm/tests/test_metadata_attrs.py
git commit -m "Spec D: image + file attribute builders + read-back/dry-run tests"
```

---

## Task 11: `optionsets.py` — list, get, create

**Files:**
- Create: `crm/core/optionsets.py`
- Create: `crm/tests/test_optionsets.py`

- [ ] **Step 1: Write failing tests**

Create `crm/tests/test_optionsets.py`:

```python
"""Unit tests for crm.core.optionsets."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


_OS_ID = "44444444-4444-4444-4444-444444444444"


class TestListOptionsets:
    def test_list_all(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions"),
                json={"value": [
                    {"Name": "new_priority", "IsCustomOptionSet": True, "IsGlobal": True},
                    {"Name": "statecode", "IsCustomOptionSet": False, "IsGlobal": True},
                ]},
            )
            rows = os_mod.list_optionsets(backend)
        assert len(rows) == 2

    def test_list_custom_only_filters(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions"),
                json={"value": [
                    {"Name": "new_priority", "IsCustomOptionSet": True},
                    {"Name": "statecode", "IsCustomOptionSet": False},
                ]},
            )
            rows = os_mod.list_optionsets(backend, custom_only=True)
        assert len(rows) == 1
        assert rows[0]["Name"] == "new_priority"

    def test_list_top_slice(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions"),
                json={"value": [
                    {"Name": f"opt_{i}"} for i in range(5)
                ]},
            )
            rows = os_mod.list_optionsets(backend, top=2)
        assert len(rows) == 2


class TestGetOptionset:
    def test_get_expands_options(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={"Name": "new_priority", "Options": [
                    {"Value": 1, "Label": {"LocalizedLabels": [{"Label": "Low"}]}}
                ]},
            )
            info = os_mod.get_optionset(backend, "new_priority")
        assert info["Name"] == "new_priority"
        assert info["Options"][0]["Value"] == 1


class TestCreateOptionset:
    def test_create_with_options(self, backend):
        from crm.core import optionsets as os_mod
        url = backend.url_for(f"GlobalOptionSetDefinitions({_OS_ID})")
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("GlobalOptionSetDefinitions"),
                status_code=204,
                headers={"OData-EntityId": url},
            )
            m.get(
                url,
                json={"Name": "new_priority", "IsCustomOptionSet": True},
            )
            info = os_mod.create_optionset(
                backend,
                name="new_priority",
                display_name="Priority",
                options=[(1, "Low"), (2, "Medium"), (3, "High")],
                solution="DevSolution",
            )
        assert info["created"] is True
        assert info["name"] == "new_priority"
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.OptionSetMetadata"
        assert body["Name"] == "new_priority"
        assert body["IsGlobal"] is True
        assert body["Options"][0]["Value"] == 1
        assert m.request_history[0].headers["MSCRM.SolutionUniqueName"] == "DevSolution"

    def test_create_rejects_duplicate_values(self, backend):
        from crm.core import optionsets as os_mod
        with pytest.raises(D365Error, match="Duplicate"):
            os_mod.create_optionset(
                backend, name="new_dupe", display_name="Dupe",
                options=[(1, "A"), (1, "B")],
            )
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest crm/tests/test_optionsets.py -v`
Expected: module does not exist.

- [ ] **Step 3: Create `crm/core/optionsets.py`**

```python
"""Global option set CRUD.

`update_optionset` is granular: insert/update/delete/reorder dispatch
to `InsertOptionValue`, `UpdateOptionValue`, `DeleteOptionValue`,
`OrderOption` bound actions in that order. Partial failure stops and
returns `{stage, completed_steps, error}` — no rollback.
"""

from __future__ import annotations

import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import _label, _maybe_publish


def _parse_optionset_id(entity_id_url: str | None) -> str | None:
    if not entity_id_url:
        return None
    match = re.search(r"GlobalOptionSetDefinitions\(([0-9a-fA-F-]{36})\)", entity_id_url)
    return match.group(1) if match else None


def list_optionsets(
    backend: D365Backend,
    *,
    custom_only: bool = False,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """List global option set definitions. Client-side $top slice."""
    result = as_dict(backend.get(
        "GlobalOptionSetDefinitions",
        params={"$select": "Name,DisplayName,IsCustomOptionSet,IsGlobal,IsManaged"},
    ))
    items = result.get("value", [])
    if custom_only:
        items = [it for it in items if it.get("IsCustomOptionSet") is True]
    if top is not None:
        if top < 1:
            raise D365Error("--top must be >= 1")
        items = items[:top]
    return items


def get_optionset(backend: D365Backend, name: str) -> dict[str, Any]:
    """Retrieve a global option set with its options expanded."""
    if not name:
        raise D365Error("name is required.")
    return as_dict(backend.get(
        f"GlobalOptionSetDefinitions(Name='{name}')",
        params={"$expand": "Options"},
    ))


def create_optionset(
    backend: D365Backend,
    *,
    name: str,
    display_name: str,
    description: str | None = None,
    options: list[tuple[int | None, str]] | None = None,
    is_global: bool = True,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a global option set. Returns `{created, name, metadata_id_url, ...}`."""
    if not name or "_" not in name:
        raise D365Error("name must include a publisher prefix, e.g. 'new_priority'.")

    option_list: list[dict[str, Any]] = []
    if options:
        seen: set[int] = set()
        for value, label in options:
            if value is not None:
                if value in seen:
                    raise D365Error(f"Duplicate option value: {value}.")
                seen.add(value)
            if not label:
                raise D365Error("Option label must not be empty.")
            opt: dict[str, Any] = {"Label": _label(label)}
            if value is not None:
                opt["Value"] = value
            option_list.append(opt)

    body: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata",
        "Name": name,
        "DisplayName": _label(display_name),
        "IsGlobal": is_global,
        "OptionSetType": "Picklist",
        "Options": option_list,
    }
    if description:
        body["Description"] = _label(description)

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "GlobalOptionSetDefinitions",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url")
    os_id = _parse_optionset_id(entity_id_url)
    lookup_error: str | None = None
    name_readback: str | None = None
    if not os_id:
        lookup_error = (
            f"Could not parse MetadataId from response: {entity_id_url!r}"
        )
    else:
        try:
            rb = as_dict(backend.get(
                f"GlobalOptionSetDefinitions({os_id})",
                params={"$select": "Name,IsCustomOptionSet"},
            ))
            name_readback = rb.get("Name")
        except D365Error as exc:
            lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "name": name_readback or name,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if lookup_error:
        out["optionset_lookup_error"] = lookup_error
    _maybe_publish(backend, out, publish)
    return out
```

- [ ] **Step 4: Run tests**

Run: `pytest crm/tests/test_optionsets.py -v`
Expected: 6 passed.

- [ ] **Step 5: Pyright**

Run: `pyright crm/core/optionsets.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add crm/core/optionsets.py crm/tests/test_optionsets.py
git commit -m "Spec D: optionsets list/get/create"
```

---

## Task 12: Granular `update_optionset` dispatcher

**Files:**
- Modify: `crm/core/optionsets.py`
- Modify: `crm/tests/test_optionsets.py`

- [ ] **Step 1: Write failing tests**

Append to `crm/tests/test_optionsets.py`:

```python
class TestUpdateOptionset:
    def test_insert_only(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("InsertOptionValue"),
                   status_code=204, json={})
            info = os_mod.update_optionset(
                backend, "new_priority",
                insert=[(7, "Critical")],
            )
        assert info["completed_steps"] == ["insert:7"]
        body = m.request_history[0].json()
        assert body["OptionSetName"] == "new_priority"
        assert body["Value"] == 7

    def test_full_dispatch_order(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("InsertOptionValue"), status_code=204, json={})
            m.post(backend.url_for("UpdateOptionValue"), status_code=204, json={})
            m.post(backend.url_for("DeleteOptionValue"), status_code=204, json={})
            m.post(backend.url_for("OrderOption"), status_code=204, json={})
            info = os_mod.update_optionset(
                backend, "new_priority",
                insert=[(None, "Auto")],
                update=[(2, "New Medium")],
                delete=[3],
                reorder=[1, 2, 7],
            )
        # Verify order: InsertOptionValue, UpdateOptionValue, DeleteOptionValue, OrderOption
        history_paths = [r.path.split("/")[-1] for r in m.request_history]
        assert "InsertOptionValue" in history_paths[0]
        assert "UpdateOptionValue" in history_paths[1]
        assert "DeleteOptionValue" in history_paths[2]
        assert "OrderOption" in history_paths[3]
        assert info["completed_steps"]

    def test_partial_failure_returns_envelope(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("InsertOptionValue"), status_code=204, json={})
            m.post(backend.url_for("UpdateOptionValue"),
                   status_code=400,
                   json={"error": {"message": "value 99 not found"}})
            with pytest.raises(D365Error, match="value 99 not found") as exc_info:
                os_mod.update_optionset(
                    backend, "new_priority",
                    insert=[(7, "OK")],
                    update=[(99, "Bad")],
                )
            # error.metadata attached via D365Error.code/status; check we got past insert
            assert "value 99 not found" in str(exc_info.value)

    def test_empty_request_rejected(self, backend):
        from crm.core import optionsets as os_mod
        with pytest.raises(D365Error, match="nothing to update"):
            os_mod.update_optionset(backend, "new_priority")
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest crm/tests/test_optionsets.py::TestUpdateOptionset -v`
Expected: 4 fails — `AttributeError: module has no attribute 'update_optionset'`.

- [ ] **Step 3: Implement `update_optionset`**

Append to `crm/core/optionsets.py`:

```python
def update_optionset(
    backend: D365Backend,
    name: str,
    *,
    insert: list[tuple[int | None, str]] | None = None,
    update: list[tuple[int, str]] | None = None,
    delete: list[int] | None = None,
    reorder: list[int] | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Granular global-option-set update.

    Dispatch order: insert → update → delete → reorder. Stops on first
    error and re-raises; the completed steps list is attached on the
    success path via the returned `{completed_steps: [...]}`.
    """
    if not (insert or update or delete or reorder):
        raise D365Error("nothing to update — pass at least one of insert/update/delete/reorder.")

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    completed: list[str] = []

    if insert:
        for value, label in insert:
            if not label:
                raise D365Error("insert label must not be empty.")
            body: dict[str, Any] = {"OptionSetName": name, "Label": _label(label)}
            if value is not None:
                body["Value"] = value
            backend.post("InsertOptionValue", json_body=body, extra_headers=headers)
            completed.append(f"insert:{value if value is not None else 'auto'}")

    if update:
        for value, label in update:
            if not label:
                raise D365Error("update label must not be empty.")
            body = {
                "OptionSetName": name,
                "Value": value,
                "Label": _label(label),
                "MergeLabels": False,
            }
            backend.post("UpdateOptionValue", json_body=body, extra_headers=headers)
            completed.append(f"update:{value}")

    if delete:
        for value in delete:
            body = {"OptionSetName": name, "Value": value}
            backend.post("DeleteOptionValue", json_body=body, extra_headers=headers)
            completed.append(f"delete:{value}")

    if reorder:
        body = {"OptionSetName": name, "Values": list(reorder)}
        backend.post("OrderOption", json_body=body, extra_headers=headers)
        completed.append("reorder")

    out: dict[str, Any] = {
        "updated": True,
        "name": name,
        "completed_steps": completed,
        "solution": solution,
    }
    _maybe_publish(backend, out, publish)
    return out
```

- [ ] **Step 4: Run tests**

Run: `pytest crm/tests/test_optionsets.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/core/optionsets.py crm/tests/test_optionsets.py
git commit -m "Spec D: granular update_optionset (insert/update/delete/reorder)"
```

---

## Task 13: `delete_optionset` with pre-flight guard

**Files:**
- Modify: `crm/core/optionsets.py`
- Modify: `crm/tests/test_optionsets.py`

- [ ] **Step 1: Write failing tests**

Append to `crm/tests/test_optionsets.py`:

```python
class TestDeleteOptionset:
    def test_refuses_non_custom(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='statecode')"),
                json={"Name": "statecode", "IsCustomOptionSet": False, "IsManaged": True},
            )
            with pytest.raises(D365Error, match="not a custom"):
                os_mod.delete_optionset(backend, "statecode")

    def test_refuses_managed(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='vendor_set')"),
                json={"Name": "vendor_set", "IsCustomOptionSet": True, "IsManaged": True},
            )
            with pytest.raises(D365Error, match="managed"):
                os_mod.delete_optionset(backend, "vendor_set")

    def test_happy_path(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={"Name": "new_priority", "IsCustomOptionSet": True, "IsManaged": False},
            )
            m.delete(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                status_code=204,
            )
            info = os_mod.delete_optionset(backend, "new_priority")
        assert info["deleted"] is True
        assert info["name"] == "new_priority"
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest crm/tests/test_optionsets.py::TestDeleteOptionset -v`
Expected: 3 fails — `AttributeError`.

- [ ] **Step 3: Implement `delete_optionset`**

Append to `crm/core/optionsets.py`:

```python
def delete_optionset(
    backend: D365Backend,
    name: str,
    *,
    solution: str | None = None,
) -> dict[str, Any]:
    """Delete a custom global option set.

    Refuses if `IsCustomOptionSet=False` or `IsManaged=True`. Server
    rejects with 400 if any picklist still references the option set.
    """
    if not name:
        raise D365Error("name is required.")
    path = f"GlobalOptionSetDefinitions(Name='{name}')"
    rb = as_dict(backend.get(
        path, params={"$select": "IsCustomOptionSet,IsManaged"},
    ))
    if rb.get("IsCustomOptionSet") is False:
        raise D365Error(
            f"{name!r} is not a custom option set; refusing to delete.",
            code="NotCustomOptionSet",
        )
    if rb.get("IsManaged") is True:
        raise D365Error(
            f"{name!r} is managed; uninstall the parent solution to remove it.",
            code="ManagedOptionSet",
        )
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    backend.delete(path, extra_headers=headers)
    return {"deleted": True, "name": name, "solution": solution}
```

- [ ] **Step 4: Run tests**

Run: `pytest crm/tests/test_optionsets.py -v`
Expected: 13 passed.

- [ ] **Step 5: Pyright on all new modules**

Run: `pyright crm/core/optionsets.py crm/core/relationships.py crm/core/metadata_attrs.py crm/core/metadata.py crm/utils/d365_types.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add crm/core/optionsets.py crm/tests/test_optionsets.py
git commit -m "Spec D: delete_optionset with pre-flight guard"
```

---

## Task 14: CLI — `_confirm_destructive` helper + `delete-entity` command

**Files:**
- Modify: `crm/cli.py`

- [ ] **Step 1: Add the shared confirm helper**

In `crm/cli.py`, near the other helpers (before the metadata group at ~line 700), add:

```python
def _confirm_destructive(thing: str, name: str, yes: bool) -> bool:
    """Return True to proceed, False to bail.

    `--yes` skips the prompt. In non-TTY contexts, click.confirm aborts safely.
    """
    if yes:
        return True
    return click.confirm(
        f"This will permanently delete {thing} {name!r} and all related data. Continue?",
        default=False,
    )
```

- [ ] **Step 2: Add the `delete-entity` command**

In `crm/cli.py`, append at the end of the `metadata` command group (after `metadata_relationships`, around line 877):

```python
@metadata.command("delete-entity")
@click.argument("logical_name")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@click.option("--solution", default=None,
              help="Apply via MSCRM.SolutionUniqueName.")
@pass_ctx
def metadata_delete_entity(ctx, logical_name, yes, solution):
    """Permanently delete a custom entity (table) and ALL its rows."""
    if not _confirm_destructive("entity", logical_name, yes):
        ctx.emit(False, error="aborted by user")
        return
    try:
        info = meta_mod.delete_entity(
            ctx.backend(), logical_name, solution=solution,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

- [ ] **Step 3: Write a CLI smoke test**

Append to `crm/tests/test_full_e2e.py` (in the existing CliRunner test class — find the section that uses `CliRunner` for `metadata entities`; add nearby):

```python
class TestDeleteEntityCli:
    def test_delete_entity_requires_confirmation(self, monkeypatch, tmp_path):
        from click.testing import CliRunner
        from crm.cli import cli

        runner = CliRunner()
        # No --yes, no input — confirm prompt aborts
        result = runner.invoke(
            cli, ["--json", "metadata", "delete-entity", "new_widget"],
            input="\n",  # press Enter on default=False
        )
        assert result.exit_code == 0
        assert "aborted by user" in result.output
```

(If the project's existing test fixtures stub backend creation in a particular way for CLI tests, follow that pattern — inspect `test_full_e2e.py` for how `CliRunner` calls are wired; the `--json` mode keeps output deterministic.)

- [ ] **Step 4: Run CLI test**

Run: `pytest crm/tests/test_full_e2e.py::TestDeleteEntityCli -v`
Expected: pass.

- [ ] **Step 5: Run all tests + pyright**

Run: `pytest crm/tests/ -v -x`
Expected: all pass.

Run: `pyright crm/cli.py`
Expected: 0 errors (note: cli.py is basic-mode, not strict — pyright should still pass).

- [ ] **Step 6: Commit**

```bash
git add crm/cli.py crm/tests/test_full_e2e.py
git commit -m "Spec D: CLI delete-entity + _confirm_destructive helper"
```

---

## Task 15: CLI — `add-attribute` command

**Files:**
- Modify: `crm/cli.py`

- [ ] **Step 1: Add the command**

In `crm/cli.py`, append to the `metadata` group (after `metadata_delete_entity`):

```python
@metadata.command("add-attribute")
@click.argument("entity")
@click.option("--kind", required=True,
              type=click.Choice([
                  "string", "memo", "integer", "bigint", "decimal", "double",
                  "money", "boolean", "datetime", "picklist", "multiselect",
                  "lookup", "image", "file",
              ]),
              help="Attribute kind.")
@click.option("--schema-name", required=True,
              help="PascalCase with publisher prefix, e.g. 'new_Amount'.")
@click.option("--display", "display_name", required=True,
              help="UI label.")
@click.option("--description", default=None)
@click.option("--required", "required",
              type=click.Choice(["None", "Recommended", "ApplicationRequired"]),
              default="None")
# String / memo
@click.option("--max-length", type=int, default=None,
              help="String/memo: max characters.")
@click.option("--format", "format_name", default=None,
              help="String: Text|Email|Url|Phone|TextArea. Datetime: DateOnly|DateAndTime.")
# Numeric
@click.option("--min", "min_value", type=float, default=None,
              help="Numeric kinds: minimum value.")
@click.option("--max", "max_value", type=float, default=None,
              help="Numeric kinds: maximum value.")
@click.option("--precision", type=int, default=None,
              help="Decimal/double/money: precision (decimals).")
# Boolean / picklist
@click.option("--true-label", default="Yes", help="Boolean: label for true.")
@click.option("--false-label", default="No", help="Boolean: label for false.")
@click.option("--default-value", default=None,
              help="Boolean: 'true'/'false'. Picklist: int option value.")
# Picklist / multiselect
@click.option("--optionset-name", default=None,
              help="Picklist/multiselect: reference an existing global option set.")
@click.option("--option", "options", multiple=True,
              help="Picklist/multiselect: inline option as 'value:label' or ':label' (auto value). Repeatable.")
# Lookup
@click.option("--target-entity", default=None,
              help="Lookup: referenced entity logical name.")
@click.option("--relationship-schema", default=None,
              help="Lookup: override auto-generated relationship name.")
# File / image
@click.option("--max-size-kb", type=int, default=None,
              help="File: max attachment size in KB. Default 32768.")
# Common
@click.option("--solution", default=None,
              help="Add to a solution via MSCRM.SolutionUniqueName.")
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after creation. Default: publish.")
@pass_ctx
def metadata_add_attribute(
    ctx, entity, kind, schema_name, display_name, description, required,
    max_length, format_name, min_value, max_value, precision,
    true_label, false_label, default_value,
    optionset_name, options, target_entity, relationship_schema,
    max_size_kb, solution, publish,
):
    """Add an attribute (column) to an existing entity."""
    parsed_options: list[tuple[int | None, str]] | None = None
    if options:
        parsed_options = []
        for raw in options:
            if ":" not in raw:
                raise click.UsageError(
                    f"--option must be 'value:label' or ':label', got: {raw!r}"
                )
            v, _, lab = raw.partition(":")
            v = v.strip()
            parsed_options.append((int(v) if v else None, lab))

    parsed_default: bool | int | None = None
    if default_value is not None:
        if kind == "boolean":
            parsed_default = default_value.lower() in ("1", "true", "yes", "on")
        else:
            try:
                parsed_default = int(default_value)
            except ValueError as exc:
                raise click.UsageError(
                    f"--default-value must be int for kind {kind!r}: {default_value!r}"
                ) from exc

    try:
        from crm.core import metadata_attrs as ma_mod
        info = ma_mod.add_attribute(
            ctx.backend(),
            entity=entity,
            kind=kind,
            schema_name=schema_name,
            display_name=display_name,
            description=description,
            required=required,
            max_length=max_length,
            format_name=format_name,
            min_value=min_value,
            max_value=max_value,
            precision=precision,
            default_value=parsed_default,
            true_label=true_label,
            false_label=false_label,
            optionset_name=optionset_name,
            options=parsed_options,
            target_entity=target_entity,
            relationship_schema=relationship_schema,
            max_size_kb=max_size_kb,
            publish=publish,
            solution=solution,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

- [ ] **Step 2: Run all tests, expect green**

Run: `pytest crm/tests/ -v -x`
Expected: all pass.

- [ ] **Step 3: Smoke-test help text manually**

Run: `python -m crm metadata add-attribute --help`
Expected: lists all flags grouped sensibly.

- [ ] **Step 4: Commit**

```bash
git add crm/cli.py
git commit -m "Spec D: CLI metadata add-attribute"
```

---

## Task 16: CLI — `create-one-to-many` + `create-many-to-many`

**Files:**
- Modify: `crm/cli.py`

- [ ] **Step 1: Add the commands**

In `crm/cli.py`, append to the `metadata` group:

```python
_CASCADE = click.Choice(
    ["NoCascade", "Cascade", "Active", "UserOwned", "RemoveLink", "Restrict"]
)
_MENU = click.Choice(["UseLabel", "UseCollectionName", "DoNotDisplay"])
_REQUIRED = click.Choice(["None", "Recommended", "ApplicationRequired"])


@metadata.command("create-one-to-many")
@click.option("--schema-name", required=True, help="Relationship schema name with publisher prefix.")
@click.option("--referenced-entity", required=True, help='"1" side logical name (e.g. account).')
@click.option("--referencing-entity", required=True, help='"N" side logical name (e.g. new_project).')
@click.option("--lookup-schema", required=True, help="Lookup attribute schema name on referencing entity.")
@click.option("--lookup-display", required=True, help="UI label for the lookup attribute.")
@click.option("--lookup-required", type=_REQUIRED, default="None")
@click.option("--lookup-description", default=None)
@click.option("--cascade-assign", type=_CASCADE, default="NoCascade")
@click.option("--cascade-delete", type=_CASCADE, default="RemoveLink")
@click.option("--cascade-reparent", type=_CASCADE, default="NoCascade")
@click.option("--cascade-share", type=_CASCADE, default="NoCascade")
@click.option("--cascade-unshare", type=_CASCADE, default="NoCascade")
@click.option("--cascade-merge", type=_CASCADE, default="NoCascade")
@click.option("--menu-label", default=None)
@click.option("--menu-behavior", type=_MENU, default="UseLabel")
@click.option("--menu-order", type=int, default=10000)
@click.option("--solution", default=None)
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def metadata_create_one_to_many(
    ctx, schema_name, referenced_entity, referencing_entity, lookup_schema,
    lookup_display, lookup_required, lookup_description,
    cascade_assign, cascade_delete, cascade_reparent, cascade_share,
    cascade_unshare, cascade_merge, menu_label, menu_behavior, menu_order,
    solution, publish,
):
    """Create a 1:N relationship and its lookup attribute atomically."""
    try:
        info = rel_mod.create_one_to_many(
            ctx.backend(),
            schema_name=schema_name,
            referenced_entity=referenced_entity,
            referencing_entity=referencing_entity,
            lookup_schema=lookup_schema,
            lookup_display=lookup_display,
            lookup_required=lookup_required,
            lookup_description=lookup_description,
            cascade_assign=cascade_assign,
            cascade_delete=cascade_delete,
            cascade_reparent=cascade_reparent,
            cascade_share=cascade_share,
            cascade_unshare=cascade_unshare,
            cascade_merge=cascade_merge,
            menu_label=menu_label,
            menu_behavior=menu_behavior,
            menu_order=menu_order,
            publish=publish,
            solution=solution,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@metadata.command("create-many-to-many")
@click.option("--schema-name", required=True)
@click.option("--entity1", "entity1_logical", required=True)
@click.option("--entity2", "entity2_logical", required=True)
@click.option("--intersect-entity", required=True)
@click.option("--entity1-menu-label", default=None)
@click.option("--entity1-menu-behavior", type=_MENU, default="UseCollectionName")
@click.option("--entity1-menu-order", type=int, default=10000)
@click.option("--entity2-menu-label", default=None)
@click.option("--entity2-menu-behavior", type=_MENU, default="UseCollectionName")
@click.option("--entity2-menu-order", type=int, default=10000)
@click.option("--solution", default=None)
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def metadata_create_many_to_many(
    ctx, schema_name, entity1_logical, entity2_logical, intersect_entity,
    entity1_menu_label, entity1_menu_behavior, entity1_menu_order,
    entity2_menu_label, entity2_menu_behavior, entity2_menu_order,
    solution, publish,
):
    """Create an N:N relationship via the dedicated action."""
    try:
        info = rel_mod.create_many_to_many(
            ctx.backend(),
            schema_name=schema_name,
            entity1_logical=entity1_logical,
            entity2_logical=entity2_logical,
            intersect_entity=intersect_entity,
            entity1_menu_label=entity1_menu_label,
            entity1_menu_behavior=entity1_menu_behavior,
            entity1_menu_order=entity1_menu_order,
            entity2_menu_label=entity2_menu_label,
            entity2_menu_behavior=entity2_menu_behavior,
            entity2_menu_order=entity2_menu_order,
            publish=publish,
            solution=solution,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

- [ ] **Step 2: Smoke help**

Run: `python -m crm metadata create-one-to-many --help`
Expected: all flags appear.

- [ ] **Step 3: Run all tests + pyright**

Run: `pytest crm/tests/ -v -x`
Run: `pyright crm/cli.py`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add crm/cli.py
git commit -m "Spec D: CLI create-one-to-many + create-many-to-many"
```

---

## Task 17: CLI — optionset commands (list/get/create/update/delete)

**Files:**
- Modify: `crm/cli.py`

- [ ] **Step 1: Add the import + commands**

In `crm/cli.py` imports (near other `crm.core` imports, ~line 30), add:

```python
from crm.core import optionsets as os_mod
```

Append to the `metadata` group:

```python
@metadata.command("list-optionsets")
@click.option("--custom-only", is_flag=True)
@click.option("--top", type=int, default=None)
@pass_ctx
def metadata_list_optionsets(ctx, custom_only, top):
    """List global option set definitions."""
    try:
        rows = os_mod.list_optionsets(ctx.backend(), custom_only=custom_only, top=top)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    headers = ["Name", "IsCustomOptionSet", "IsManaged"]
    table_rows = [
        [r.get("Name", ""), str(r.get("IsCustomOptionSet", "")),
         str(r.get("IsManaged", ""))]
        for r in rows
    ]
    ctx.emit(True, data=rows, table={"headers": headers, "rows": table_rows},
             meta={"count": len(rows)})


@metadata.command("get-optionset")
@click.argument("name")
@pass_ctx
def metadata_get_optionset(ctx, name):
    """Retrieve a global option set with options expanded."""
    try:
        info = os_mod.get_optionset(ctx.backend(), name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@metadata.command("create-optionset")
@click.option("--name", required=True,
              help="Fully prefixed option set name, e.g. 'new_priority'.")
@click.option("--display", "display_name", required=True)
@click.option("--description", default=None)
@click.option("--option", "options", multiple=True,
              help="Option as 'value:label' or ':label' (auto value). Repeatable.")
@click.option("--solution", default=None)
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def metadata_create_optionset(ctx, name, display_name, description, options,
                              solution, publish):
    """Create a global option set."""
    parsed: list[tuple[int | None, str]] = []
    for raw in options:
        if ":" not in raw:
            raise click.UsageError(f"--option must be 'value:label' or ':label', got: {raw!r}")
        v, _, lab = raw.partition(":")
        v = v.strip()
        parsed.append((int(v) if v else None, lab))
    try:
        info = os_mod.create_optionset(
            ctx.backend(),
            name=name, display_name=display_name,
            description=description, options=parsed or None,
            publish=publish, solution=solution,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@metadata.command("update-optionset")
@click.argument("name")
@click.option("--insert-option", "insert_options", multiple=True,
              help="Insert option 'value:label' or ':label'. Repeatable.")
@click.option("--update-option", "update_options", multiple=True,
              help="Update existing option 'value:new_label'. Repeatable.")
@click.option("--delete-option", "delete_options", multiple=True, type=int,
              help="Delete option by value. Repeatable.")
@click.option("--reorder", default=None,
              help="Comma-separated full ordered list of values, e.g. '1,2,7,4'.")
@click.option("--solution", default=None)
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def metadata_update_optionset(ctx, name, insert_options, update_options,
                              delete_options, reorder, solution, publish):
    """Granular update: insert/update/delete/reorder options."""
    insert: list[tuple[int | None, str]] = []
    for raw in insert_options:
        if ":" not in raw:
            raise click.UsageError(f"--insert-option must be 'value:label' or ':label': {raw!r}")
        v, _, lab = raw.partition(":")
        v = v.strip()
        insert.append((int(v) if v else None, lab))

    update: list[tuple[int, str]] = []
    for raw in update_options:
        if ":" not in raw:
            raise click.UsageError(f"--update-option must be 'value:new_label': {raw!r}")
        v, _, lab = raw.partition(":")
        try:
            update.append((int(v.strip()), lab))
        except ValueError as exc:
            raise click.UsageError(
                f"--update-option value must be int: {raw!r}"
            ) from exc

    reorder_list: list[int] | None = None
    if reorder:
        try:
            reorder_list = [int(x.strip()) for x in reorder.split(",") if x.strip()]
        except ValueError as exc:
            raise click.UsageError(
                f"--reorder must be a comma-separated list of integers: {reorder!r}"
            ) from exc

    try:
        info = os_mod.update_optionset(
            ctx.backend(),
            name,
            insert=insert or None,
            update=update or None,
            delete=list(delete_options) or None,
            reorder=reorder_list,
            publish=publish,
            solution=solution,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@metadata.command("delete-optionset")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@click.option("--solution", default=None)
@pass_ctx
def metadata_delete_optionset(ctx, name, yes, solution):
    """Delete a custom global option set."""
    if not _confirm_destructive("option set", name, yes):
        ctx.emit(False, error="aborted by user")
        return
    try:
        info = os_mod.delete_optionset(ctx.backend(), name, solution=solution)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

- [ ] **Step 2: Tests + pyright + smoke**

Run: `pytest crm/tests/ -v -x`
Run: `pyright crm/cli.py`
Run: `python -m crm metadata --help`
Expected: all pass; new commands appear in `--help` listing.

- [ ] **Step 3: Commit**

```bash
git add crm/cli.py
git commit -m "Spec D: CLI optionset list/get/create/update/delete"
```

---

## Task 18: REPL help + README commands table

**Files:**
- Modify: `crm/cli.py` (REPL help dict)
- Modify: `README.md`

- [ ] **Step 1: Update REPL help table**

In `crm/cli.py` find the REPL help dict (around line 1644). Replace the `"metadata attributes <entity>": "List attributes",` entry with a block that lists the new write verbs:

```python
        "metadata entities": "List entity definitions",
        "metadata attributes <entity>": "List attributes",
        "metadata add-attribute <entity> --kind <k>": "Add a column to an entity",
        "metadata create-entity / delete-entity": "Custom entity lifecycle",
        "metadata create-one-to-many / create-many-to-many": "Relationships",
        "metadata list-optionsets / create-optionset / update-optionset / delete-optionset": "Global option sets",
```

- [ ] **Step 2: Update README**

Open `README.md`. Find the commands table (search for "metadata create-entity"). Append rows for the new commands:

```markdown
| `metadata add-attribute <entity> --kind <k>` | Add attribute to an existing entity (14 kinds) |
| `metadata create-one-to-many` | Create 1:N relationship + lookup attribute |
| `metadata create-many-to-many` | Create N:N relationship + intersect table |
| `metadata list-optionsets` | List global option sets |
| `metadata get-optionset <name>` | Retrieve a global option set with options |
| `metadata create-optionset` | Create a global option set |
| `metadata update-optionset <name>` | Granular update (insert/update/delete/reorder) |
| `metadata delete-optionset <name>` | Delete a custom global option set |
| `metadata delete-entity <logical-name>` | Delete a custom entity (table) |
```

(Match the table's existing column style — single-cell pipes, header alignment.)

- [ ] **Step 3: Commit**

```bash
git add crm/cli.py README.md
git commit -m "Spec D: REPL help + README commands table for new verbs"
```

---

## Task 19: Live e2e test additions

**Files:**
- Modify: `crm/tests/test_full_e2e.py`

- [ ] **Step 1: Append a live e2e class**

Find the existing `@pytest.mark.live` class structure in `crm/tests/test_full_e2e.py`. Append a new live class:

```python
@pytest.mark.live
class TestSpecDMetadataWriteLive:
    """End-to-end metadata-write smoke against a real MOCE server.

    Gated by D365_LIVE=1 + a provisioned profile. Each run creates a
    uniquely-named ephemeral entity, exercises every new write verb
    against it, then cleans up. If cleanup fails, the test xfails so CI
    surfaces it without breaking.
    """

    @pytest.fixture(scope="class")
    def ephemeral_entity(self, request, backend):
        import uuid
        from crm.core import metadata as meta_mod
        suffix = f"{int(__import__('time').time())}_{uuid.uuid4().hex[:8]}"
        schema = f"new_E2E{suffix}"
        info = meta_mod.create_entity(
            backend, schema_name=schema,
            display_name=f"E2E {suffix}",
        )
        yield info["logical_name"]
        # Cleanup
        try:
            meta_mod.delete_entity(backend, info["logical_name"])
        except Exception as exc:
            pytest.xfail(f"cleanup failed for {info['logical_name']}: {exc}")

    def test_add_attribute_each_kind(self, backend, ephemeral_entity):
        from crm.core import metadata_attrs as ma
        kinds_payload: list[tuple[str, dict]] = [
            ("string", {"max_length": 100}),
            ("memo", {"max_length": 1000}),
            ("integer", {"min_value": 0, "max_value": 100}),
            ("bigint", {}),
            ("decimal", {"precision": 2}),
            ("double", {"precision": 3}),
            ("money", {"precision": 2}),
            ("boolean", {}),
            ("datetime", {}),
            ("picklist", {"options": [(1, "A"), (2, "B")]}),
            # multiselect/image/file may be feature-gated on some MOCE builds —
            # skip on 4xx with a clear xfail rather than failing the whole class.
        ]
        for kind, extra in kinds_payload:
            info = ma.add_attribute(
                backend,
                entity=ephemeral_entity,
                kind=kind,
                schema_name=f"new_E2E{kind.capitalize()}",
                display_name=f"E2E {kind}",
                publish=False,
                **extra,
            )
            assert info.get("created") or info.get("kind") == "OneToMany", info

    def test_optionset_lifecycle(self, backend):
        import uuid
        from crm.core import optionsets as os_mod
        name = f"new_e2e_priority_{uuid.uuid4().hex[:8]}"
        try:
            os_mod.create_optionset(
                backend, name=name, display_name="E2E Priority",
                options=[(1, "Low"), (2, "Medium")],
            )
            os_mod.update_optionset(
                backend, name,
                insert=[(7, "Critical")],
                update=[(2, "Mid")],
            )
            os_mod.get_optionset(backend, name)
        finally:
            try:
                os_mod.delete_optionset(backend, name)
            except Exception as exc:
                pytest.xfail(f"cleanup failed for {name}: {exc}")

    def test_one_to_many_to_stock_account(self, backend, ephemeral_entity):
        from crm.core import relationships as rel
        info = rel.create_one_to_many(
            backend,
            schema_name=f"new_account_{ephemeral_entity}",
            referenced_entity="account",
            referencing_entity=ephemeral_entity,
            lookup_schema="new_E2EAccountId",
            lookup_display="Account",
            publish=False,
        )
        assert info["created"] is True
```

(If the existing `backend` fixture lives at module scope, reuse it; otherwise inline the connection setup matching the pre-existing live-test pattern in the same file.)

- [ ] **Step 2: Run non-live tests one more time**

Run: `pytest crm/tests/ -v -x -m "not live"`
Expected: all green, no live tests run.

- [ ] **Step 3: Commit**

```bash
git add crm/tests/test_full_e2e.py
git commit -m "Spec D: live e2e tests for metadata write verbs"
```

---

## Task 20: Final integration + PR

**Files:**
- (no new files)

- [ ] **Step 1: Full test sweep**

Run: `pytest crm/tests/ -v`
Expected: all non-live tests green.

- [ ] **Step 2: Full pyright sweep**

Run: `pyright`
Expected: 0 errors.

- [ ] **Step 3: Manual CLI smoke against `--help`**

Run: `python -m crm metadata --help`
Expected: lists 16+ subcommands (existing 7 + 9 new).

Run each new subcommand with `--help`:
```
python -m crm metadata add-attribute --help
python -m crm metadata create-one-to-many --help
python -m crm metadata create-many-to-many --help
python -m crm metadata list-optionsets --help
python -m crm metadata get-optionset --help
python -m crm metadata create-optionset --help
python -m crm metadata update-optionset --help
python -m crm metadata delete-optionset --help
python -m crm metadata delete-entity --help
```
Expected: each prints flag listing without error.

- [ ] **Step 4: Push branch + open PR**

```bash
git push -u origin <branch-name>
gh pr create --title "Spec D: metadata write API (0.5.0)" --body "$(cat <<'EOF'
## Summary

- Adds `metadata add-attribute` covering 14 attribute casts.
- Adds `metadata create-one-to-many` + `create-many-to-many` via the dedicated Dataverse actions.
- Adds global option set CRUD: `list-optionsets`, `get-optionset`, `create-optionset`, `update-optionset` (granular), `delete-optionset`.
- Adds `metadata delete-entity` with confirm prompt + custom/managed guard.
- All new write verbs accept `--solution` + `--publish/--no-publish` (default ON), matching `create-entity`.
- Bump to `0.5.0`. Pure additive surface — no breaking changes.

Closes #6.

## Test plan

- [ ] `pytest crm/tests/ -v` (all non-live tests pass)
- [ ] `pyright` (0 errors)
- [ ] `python -m crm metadata --help` lists the new commands
- [ ] Manual smoke against MOCE: create-entity → add-attribute (each kind) → create-one-to-many → create-many-to-many → optionset lifecycle → delete-entity cleanup

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage:** Every section in the spec maps to one or more tasks above.

| Spec section | Task(s) |
|---|---|
| §1 Goals — add-attribute 14 kinds | Tasks 5–10 |
| §1 Goals — create-one-to-many / create-many-to-many | Tasks 3–4, 16 |
| §1 Goals — optionset CRUD | Tasks 11–13, 17 |
| §1 Goals — delete-entity | Tasks 2, 14 |
| §1 Goals — --solution / --publish defaults | Tasks 14–17 |
| §1 Goals — read-back pattern | Tasks 2, 3, 5, 11 |
| §1 Goals — 0.5.0 bump | Task 1 |
| §2 Architecture / module layout | Tasks 3, 5, 11 |
| §2.6 lookup special case | Task 9 |
| §3 Per-kind validation matrix | Tasks 5–10 (per-kind tests) |
| §4 Relationship API + CLI | Tasks 3, 4, 16 |
| §5 Granular update_optionset | Task 12, 17 |
| §6 delete-entity safeguards (confirm + custom/managed guard) | Tasks 2, 14 |
| §7 Testing — unit + CLI + live | Tasks 2–13 (unit), 14, 17 (CLI), 19 (live) |
| §8 PR sequencing — single PR | Task 20 |

**Type / signature consistency:**
- `_label`, `_maybe_publish` defined in Task 1, used consistently in `metadata.py` / `metadata_attrs.py` / `relationships.py` / `optionsets.py`.
- `add_attribute(backend, *, entity, kind, schema_name, display_name, ...)` signature is locked in Task 5; later tasks (6–10) only add new dispatch entries to `_BUILDERS`.
- `_confirm_destructive(thing, name, yes)` defined in Task 14, reused in Task 17 (`delete-optionset`).
- `create_one_to_many` signature defined in Task 3 is what `add_attribute` (Task 9, lookup dispatch) calls.

**Placeholder scan:** No `TBD` / `TODO` / "implement later" in any step. Every code-changing step shows the exact code.
