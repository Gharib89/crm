"""Every kind `crm inventory extract` emits passes through both scope passes and the diff's.

One table, `ROWS`, holds a row per case for every kind in `inventory.KINDS`, each marked with
what a stock run, a `--solution contoso` run and a diff of a narrowed side must do with it. A
kind with no row fails `test_every_kind_has_a_scope_row`, so a new kind cannot ship without
scope coverage (#1111, #1162, #1163, #1228). Builds on test_inventory_stock_scope.py, whose
fake org covers the same stock rule end to end.
"""

# pyright: basic
from __future__ import annotations

from typing import Any, NamedTuple

from crm.core import inventory as inv
from crm.core import inventory_diff, inventory_extract

NAMED = "contoso"


class Row(NamedTuple):
    """One component a scope pass judges.

    *stock* marks a platform row; *member* gives it a membership in the named solution. The
    three booleans are the expected outcome: kept by the stock pass, kept by the `--solution`
    pass, kept by the diff's restriction of a whole-org side to a narrowed one.
    """

    kind: str
    label: str
    fields: dict[str, Any]
    stock: bool = False
    member: bool = False
    in_stock_run: bool = True
    in_solution_run: bool = False
    in_diff: bool = False


def _row(kind: str, label: str, **kw: Any) -> Row:
    return Row(kind, label, kw.pop("fields"), **kw)


_STEP = {"message": "Update", "primary_entity": None, "stage": 40, "mode": 0}
_ASM_IN, _ASM_OUT, _ASM_STOCK = ("asm_in", "asm_out", "asm_stock")
_TYPE_KEY = {a: f"{a}|T" for a in (_ASM_IN, _ASM_OUT, _ASM_STOCK)}

ROWS: tuple[Row, ...] = (
    # entities: a member entity is held whole; a stock one stays only as a kept child's parent
    _row(
        "entity",
        "ent_in",
        fields={"logical_name": "ent_in"},
        member=True,
        in_solution_run=True,
        in_diff=True,
    ),
    _row("entity", "ent_out", fields={"logical_name": "ent_out"}),
    _row(
        "entity", "ent_stock", fields={"logical_name": "ent_stock"}, stock=True, in_stock_run=False
    ),
    _row("entity", "ent_ref", fields={"logical_name": "ent_ref"}, stock=True),
    _row(
        "attribute",
        "attr_in",
        fields={"entity": "ent_in", "logical_name": "a"},
        in_solution_run=True,
        in_diff=True,
    ),
    _row("attribute", "attr_out", fields={"entity": "ent_out", "logical_name": "a"}),
    _row(
        "attribute",
        "attr_stock",
        fields={"entity": "ent_out", "logical_name": "s"},
        stock=True,
        in_stock_run=False,
    ),
    _row("attribute", "attr_on_ref", fields={"entity": "ent_ref", "logical_name": "a"}),
    _row(
        "relationship",
        "rel_in",
        fields={
            "schema_name": "rel_in",
            "referenced_entity": "ent_in",
            "referencing_entity": "ent_out",
        },
        in_solution_run=True,
        in_diff=True,
    ),
    _row(
        "relationship",
        "rel_out",
        fields={
            "schema_name": "rel_out",
            "referenced_entity": "ent_out",
            "referencing_entity": "ent_out",
        },
    ),
    _row(
        "relationship",
        "rel_stock",
        fields={
            "schema_name": "rel_stock",
            "referenced_entity": "ent_out",
            "referencing_entity": "ent_out",
        },
        stock=True,
        in_stock_run=False,
    ),
    _row(
        "global_option_set",
        "gos_in",
        fields={"name": "gos_in"},
        member=True,
        in_solution_run=True,
        in_diff=True,
    ),
    _row("global_option_set", "gos_out", fields={"name": "gos_out"}),
    _row(
        "global_option_set",
        "gos_stock",
        fields={"name": "gos_stock"},
        stock=True,
        in_stock_run=False,
    ),
    _row(
        "local_option_set",
        "los_in",
        fields={"entity": "ent_in", "attribute": "a"},
        in_solution_run=True,
        in_diff=True,
    ),
    _row("local_option_set", "los_out", fields={"entity": "ent_out", "attribute": "a"}),
    _row(
        "form",
        "form_in",
        fields={"entity": "ent_in", "form_type": 2, "name": "f"},
        in_solution_run=True,
        in_diff=True,
    ),
    _row("form", "form_out", fields={"entity": "ent_out", "form_type": 2, "name": "f"}),
    _row(
        "form",
        "form_stock",
        fields={"entity": "ent_out", "form_type": 2, "name": "s"},
        stock=True,
        in_stock_run=False,
    ),
    _row(
        "view",
        "view_in",
        fields={"entity": "ent_in", "name": "v"},
        in_solution_run=True,
        in_diff=True,
    ),
    _row("view", "view_out", fields={"entity": "ent_out", "name": "v"}),
    _row(
        "view",
        "view_stock",
        fields={"entity": "ent_out", "name": "s"},
        stock=True,
        in_stock_run=False,
    ),
    # the application ribbon (entity None) shares the key "" with the default sitemap and the
    # application ribbon diff: each kind is judged on its own, never by the key alone
    _row("ribbon", "rib_in", fields={"entity": "ent_in"}, in_solution_run=True, in_diff=True),
    _row("ribbon", "rib_app", fields={"entity": None}),
    _row("ribbon", "rib_stock", fields={"entity": "ent_out"}, stock=True, in_stock_run=False),
    _row(
        "sitemap",
        "site_in",
        fields={"unique_name": "site"},
        member=True,
        in_solution_run=True,
        in_diff=True,
    ),
    _row("sitemap", "site_default", fields={"unique_name": None}),
    _row(
        "sitemap",
        "site_stock",
        fields={"unique_name": "msdyn_site"},
        stock=True,
        in_stock_run=False,
    ),
    _row("ribbon_diff", "rd_in", fields={"entity": "ent_in"}, in_solution_run=True, in_diff=True),
    _row("ribbon_diff", "rd_app", fields={"entity": None}, in_solution_run=True),
    _row(
        "ribbon_diff",
        "rd_stock",
        fields={"entity": "ent_out"},
        stock=True,
        in_stock_run=False,
        in_solution_run=True,
    ),
    _row(
        "web_resource",
        "wr_in",
        fields={"name": "wr_in"},
        member=True,
        in_solution_run=True,
        in_diff=True,
    ),
    _row("web_resource", "wr_out", fields={"name": "wr_out"}),
    _row("web_resource", "wr_stock", fields={"name": "wr_stock"}, stock=True, in_stock_run=False),
    # a plug-in type follows its assembly; a step and its image are kept only by membership
    _row(
        "plugin_assembly",
        _ASM_IN,
        fields={"name": _ASM_IN},
        member=True,
        in_solution_run=True,
        in_diff=True,
    ),
    _row("plugin_assembly", _ASM_OUT, fields={"name": _ASM_OUT}),
    _row(
        "plugin_assembly", _ASM_STOCK, fields={"name": _ASM_STOCK}, stock=True, in_stock_run=False
    ),
    _row(
        "plugin_type",
        "type_in",
        fields={"assembly": _ASM_IN, "type_name": "T"},
        in_solution_run=True,
    ),
    _row("plugin_type", "type_out", fields={"assembly": _ASM_OUT, "type_name": "T"}),
    _row(
        "plugin_type",
        "type_stock",
        fields={"assembly": _ASM_STOCK, "type_name": "T"},
        stock=True,
        in_stock_run=False,
    ),
    _row(
        "step",
        "step_in",
        fields={**_STEP, "plugin_type": _TYPE_KEY[_ASM_IN], "name": "in"},
        member=True,
        in_solution_run=True,
        in_diff=True,
    ),
    _row("step", "step_out", fields={**_STEP, "plugin_type": _TYPE_KEY[_ASM_OUT], "name": "out"}),
    _row(
        "step",
        "step_stock",
        fields={**_STEP, "plugin_type": _TYPE_KEY[_ASM_STOCK], "name": "s"},
        stock=True,
        in_stock_run=False,
    ),
    _row(
        "step_image",
        "img_in",
        fields={"step": "step_in", "alias": "pre", "image_type": 0},
        member=True,
        in_solution_run=True,
        in_diff=True,
    ),
    _row("step_image", "img_out", fields={"step": "step_out", "alias": "pre", "image_type": 0}),
    _row(
        "step_image",
        "img_stock",
        fields={"step": "step_stock", "alias": "pre", "image_type": 0},
        stock=True,
        in_stock_run=False,
    ),
    _row(
        "process",
        "proc_in",
        fields={"category": 0, "primary_entity": None, "name": "in"},
        member=True,
        in_solution_run=True,
        in_diff=True,
    ),
    _row("process", "proc_out", fields={"category": 0, "primary_entity": None, "name": "out"}),
    _row(
        "process",
        "proc_stock",
        fields={"category": 0, "primary_entity": None, "name": "s"},
        stock=True,
        in_stock_run=False,
    ),
    _row(
        "report",
        "rep_in",
        fields={"name": "rep_in"},
        member=True,
        in_solution_run=True,
        in_diff=True,
    ),
    _row("report", "rep_out", fields={"name": "rep_out"}),
    _row("report", "rep_stock", fields={"name": "rep_stock"}, stock=True, in_stock_run=False),
    _row(
        "solution", "sol_named", fields={"unique_name": NAMED}, in_solution_run=True, in_diff=True
    ),
    _row("solution", "sol_other", fields={"unique_name": "other"}),
)

#: Edges are built from rows, so they are named by label: (kind, dependent, required) or a
#: membership of a solution in a row.
EDGES: tuple[Row, ...] = (
    Row(
        "solution_membership",
        "mem_other",
        {"solution": "other", "member_kind": "web_resource", "member_key": "wr_out"},
    ),
    Row(
        "dependency",
        "dep_kept",
        {
            "dependent_kind": "web_resource",
            "dependent_key": "wr_in",
            "required_kind": "web_resource",
            "required_key": "wr_out",
            "dependency_type": 2,
        },
        in_solution_run=True,
        in_diff=True,
    ),
    Row(
        "dependency",
        "dep_stock_edge",
        {
            "dependent_kind": "web_resource",
            "dependent_key": "wr_in",
            "required_kind": "web_resource",
            "required_key": "wr_stock",
            "dependency_type": 2,
        },
        in_stock_run=False,
        in_solution_run=True,
        in_diff=True,
    ),
    Row(
        "dependency",
        "dep_out",
        {
            "dependent_kind": "web_resource",
            "dependent_key": "wr_out",
            "required_kind": "web_resource",
            "required_key": "wr_in",
            "dependency_type": 2,
        },
    ),
)


def _key(kind: str, fields: dict[str, Any]) -> str:
    return inv.component_key(kind, fields)


def _membership(row: Row) -> Row:
    fields = {"solution": NAMED, "member_kind": row.kind, "member_key": _key(row.kind, row.fields)}
    return Row(
        "solution_membership",
        f"mem_{row.label}",
        fields,
        stock=row.stock,
        in_stock_run=row.in_stock_run,
        in_solution_run=True,
        in_diff=True,
    )


def _all_rows() -> list[Row]:
    members = [_membership(r) for r in ROWS if r.member]
    return [*ROWS, *members, *EDGES]


def _builder(tmp_path, *, stock_scope: bool) -> inventory_extract._Builder:
    checkpoint = inventory_extract.Checkpoint(tmp_path)
    return inventory_extract._Builder(checkpoint, tmp_path, {}, "web-api", stock_scope)


def _fill(builder: inventory_extract._Builder) -> dict[tuple[str, str], Row]:
    labels: dict[tuple[str, str], Row] = {}
    for row in _all_rows():
        origin = "system" if row.stock else "custom"
        builder.add(row.kind, None, row.fields, None, origin, row.stock or None, [], None,
                    row.stock)  # fmt: skip
        labels[(row.kind, _key(row.kind, row.fields))] = row
    return labels


def _kept(builder: inventory_extract._Builder, labels: dict) -> set[tuple[str, str]]:
    return {labels[(p.kind, p.key)].label for p in builder.pending}


def _uncovered() -> list[str]:
    return sorted(set(inv.KINDS) - {r.kind for r in _all_rows()})


def test_every_kind_has_a_scope_row():
    assert not _uncovered(), f"no scope row for {_uncovered()}"


def test_every_row_builds_a_valid_component_key():
    for row in _all_rows():
        _key(row.kind, row.fields)


def test_the_stock_pass_keeps_exactly_what_each_kind_marks_kept(tmp_path):
    builder = _builder(tmp_path, stock_scope=True)
    labels = _fill(builder)
    builder.drop_stock()
    want = {r.label for r in _all_rows() if r.in_stock_run}
    assert _kept(builder, labels) == want


def test_the_solution_pass_keeps_exactly_what_each_kind_marks_kept(tmp_path):
    builder = _builder(tmp_path, stock_scope=False)
    labels = _fill(builder)
    builder.scope((NAMED,))
    want = {r.label for r in _all_rows() if r.in_solution_run}
    assert _kept(builder, labels) == want


def _record(row: Row) -> inv.Record:
    origin = "system" if row.stock else "custom"
    key = _key(row.kind, row.fields)
    return inv.Record(row.kind, key, None, None, origin, None, "published", row.fields, ())


def _side(rows: list[Row], scope: inv.Scope) -> inv.Inventory:
    return inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", "https://crm.example.test/Org", None, "5.0.9690.4376"),
        extracted_at="2026-11-12T10:00:00Z",
        cli_version="1.0.0",
        scope=scope,
        publication="published",
        records=tuple(_record(r) for r in rows),
        coverage={},
        gaps=(),
    )


def test_the_diff_pass_carries_each_kind_a_narrowed_target_dropped():
    # The narrowed target keeps only the solution and its memberships; the whole-org source
    # holds every row. Each row the diff owes a comparison must come back as a compared key.
    all_rows = _all_rows()
    target_rows = [r for r in all_rows if r.kind in ("solution", "solution_membership")
                   and r.in_solution_run]  # fmt: skip
    narrowed = _side(target_rows, inv.Scope("solutions", (NAMED,)))
    whole = _side(all_rows, inv.Scope("org", ()))
    keys, missing = inventory_diff._restricted_keys(narrowed, whole)
    assert missing == ()
    labels = {(r.kind, _key(r.kind, r.fields)): r.label for r in all_rows}
    got = {labels[k] for k in keys}
    want = {r.label for r in all_rows if r.in_diff}
    assert got == want


def test_a_new_kind_with_no_scope_row_is_reported(monkeypatch):
    monkeypatch.setattr(inv, "KINDS", (*inv.KINDS, "gadget"))
    assert _uncovered() == ["gadget"]
