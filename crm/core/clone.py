"""Clone a custom entity over the Web API.

Skeleton clone = build_entity_spec(source, with_relationships=False) ->
retarget_spec(...) -> apply_spec. Lookup columns ride along as kind=lookup
attributes (recreated pointing at the same parents); forms and workflows are
layered on top by `clone_entity`, which also emits a constant ribbon note (the
ribbon has no Web API write path). No XML surgery, no solutionpackager.
"""

from __future__ import annotations

from typing import Any


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
