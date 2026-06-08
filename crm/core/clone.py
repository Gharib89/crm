"""Clone a custom entity over the Web API.

Skeleton clone = build_entity_spec(source, with_relationships=False) ->
retarget_spec(...) -> apply_spec. Lookup columns ride along as kind=lookup
attributes (recreated pointing at the same parents); forms and workflows are
layered on top by `clone_entity`, which also emits a constant ribbon note (the
ribbon has no Web API write path). No XML surgery, no solutionpackager.
"""

from __future__ import annotations

from typing import Any

from crm.core.apply import apply_spec
from crm.core.export_spec import build_entity_spec
from crm.core.forms import clone_form_to_entity, read_entity_forms
from crm.core.solution import _validate_customization_prefix, publish_all  # pyright: ignore[reportPrivateUsage]
from crm.utils.d365_backend import D365Backend


def retarget_spec(
    spec: dict[str, Any],
    *,
    new_schema: str,
    display: str | None = None,
) -> None:
    """Rename the entity in a ``build_entity_spec`` result in place.

    - Entity ``schema_name`` -> ``new_schema``; ``display_name`` -> ``display``
      or ``"<source display> (Clone)"``; ``display_collection_name`` dropped so
      ``create_entity`` re-derives it from the new display.
    - Everything else is left untouched: attribute logical names are per-entity,
      so the clone reuses them (and the views' column bindings) verbatim. Lookup
      columns ride along as ``kind=lookup`` attributes carrying ``target_entity``
      — ``apply_spec`` recreates each on the clone pointing at the same parent,
      so there is no relationship handling here. The spec is built with
      ``with_relationships=False``, so there is no ``relationships`` key to touch.
    """
    entity = spec["entities"][0]
    if display is None:
        display = f"{entity['display_name']} (Clone)"
    entity["schema_name"] = new_schema
    entity["display_name"] = display
    entity.pop("display_collection_name", None)


_RIBBON_NOTE = (
    "Ribbon not cloned: RibbonDiffXml has no Web API write path. If the source "
    "has a custom command bar, redeploy it onto the clone via solution import."
)


def _count_kind(entries: list[dict[str, Any]], kind: str) -> int:
    return sum(1 for e in entries if e.get("kind") == kind)


def clone_entity(
    backend: D365Backend,
    source: str,
    new_schema_name: str,
    *,
    display: str | None = None,
    with_forms: bool = False,
    with_views: bool = False,
    with_workflows: bool = False,
    solution: str | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """Clone a custom entity under a new schema name, purely over the Web API.

    Bare clone = entity + custom attributes (lookups included — they ride along
    as kind=lookup attributes pointing at the same parents) + reused global
    option sets. Forms / views / workflows are opt-in. The ribbon is
    noted (``ribbon_note``), never written. Relationships where the source is the
    parent side, N:N, and cascade behavior are not cloned (see module docs).

    Returns ``{created, source, logical_name, schema_name, counts,
    skipped_workflows, ribbon_note, apply}``.
    """
    prefix, _, _ = new_schema_name.partition("_")
    _validate_customization_prefix(prefix)

    spec = build_entity_spec(
        backend, source, with_views=with_views, with_relationships=False)
    retarget_spec(spec, new_schema=new_schema_name, display=display)

    apply_result = apply_spec(backend, spec, solution=solution, stage_only=not publish)

    applied = apply_result.get("applied", [])
    out: dict[str, Any] = {
        "created": apply_result.get("ok", False),
        "source": source,
        "logical_name": new_schema_name.lower(),
        "schema_name": new_schema_name,
        "counts": {
            "attributes": _count_kind(applied, "attribute"),
            "views": _count_kind(applied, "view"),
            "forms": 0,
            "workflows": 0,
        },
        "skipped_workflows": [],
        "ribbon_note": _RIBBON_NOTE,
        "apply": apply_result,
    }
    if not apply_result.get("ok"):
        return out

    clone_logical = new_schema_name.lower()
    if with_forms:
        forms_done = 0
        for form in read_entity_forms(backend, source):
            clone_form_to_entity(backend, form, clone_logical, solution=solution)
            forms_done += 1
        out["counts"]["forms"] = forms_done
        if forms_done and publish and (not backend or not backend.dry_run):
            publish_all(backend)

    return out
