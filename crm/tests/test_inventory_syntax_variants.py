"""One syntax-variant corpus, run against every transform and source detector of `inventory`.

`fixtures/inventory/syntax-variants/<id>.json` holds, per rule or transform, the same source
written in the variants an AST matcher tends to miss (#1122, #1125, #1126, #1127, #1168, #1227,
#1234): a qualified `window.Xrm`, a comment node inside a chain, arrow and method handlers, a
template substitution, a receiver binding, an implicit `new`, `using static`, a `/WebResources`
URL. Each case pins what the detector finds and what the transform proves or leaves for review.

A new transform, or a new web resource or plug-in source detector, with no corpus file turns
`test_every_*_has_a_corpus_file` red, so it cannot ship without its variants. To add one,
copy a sibling file, write its cases and run the suite; the outcome is the review.
"""

# pyright: basic
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from crm.core import inventory_transforms as transforms
from crm.core.code_parse import parse
from crm.core.inventory_assess import ContentPattern, PluginSource, load_rules
from crm.core.inventory_plugin_source import type_uses

CORPUS = Path(__file__).parent / "fixtures" / "inventory" / "syntax-variants"
VARIANTS = (
    "qualified-window-xrm",
    "comment-node",
    "arrow-handler",
    "method-handler",
    "template-substitution",
    "receiver-binding",
    "implicit-new",
    "using-static",
    "web-resources-url",
)


def _files() -> dict[str, dict[str, Any]]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(CORPUS.glob("*.json"))
    }


importlib.import_module("crm.core.inventory_odata")  # registers the odata-v2 transform
FILES = _files()
RULES = {r.id: r for r in load_rules().rules}
CASES = [(stem, name) for stem, f in FILES.items() for name in f["cases"]]


def _uncovered_rules() -> list[str]:
    owned = {f["rule"] for f in FILES.values() if f.get("rule")}
    return sorted(
        r.id
        for r in RULES.values()
        if isinstance(r.detection, ContentPattern | PluginSource) and r.id not in owned
    )


def _uncovered_transforms() -> list[str]:
    owned = {f["transform"] for f in FILES.values() if f.get("transform")}
    return sorted(set(transforms.TRANSFORMS) - owned)


def test_every_detector_rule_has_a_corpus_file():
    assert not _uncovered_rules(), f"no syntax-variant file for rule(s) {_uncovered_rules()}"


def test_every_registered_transform_has_a_corpus_file():
    assert not _uncovered_transforms(), (
        f"no syntax-variant file for transform(s) {_uncovered_transforms()}"
    )


def test_a_file_without_cases_says_why():
    bare = [s for s, f in FILES.items() if not f["cases"] and not f.get("note")]
    assert not bare, f"{bare} hold no case and no note"


@pytest.mark.parametrize("stem", sorted(FILES))
def test_a_file_names_the_rule_and_transform_the_registry_pairs(stem):
    f = FILES[stem]
    assert stem == (f.get("rule") or f.get("transform"))
    if f.get("rule"):
        assert RULES[f["rule"]].transform == f.get("transform")
    if f.get("transform"):
        assert f["transform"] in transforms.TRANSFORMS


def _apply(source: str, plan: transforms.Plan) -> str:
    out = source.encode()
    for e in reversed(plan.edits):
        out = out[: e.start] + e.text + out[e.end :]
    return out.decode()


def _plan(transform: transforms.Transform, case: dict[str, Any]) -> transforms.Plan:
    root = parse(transform.language, case["source"].encode())
    if transform.needs_handlers:
        return transform.plan(root, case["handlers"])
    return transform.plan(root, None) if transform.needs_target else transform.plan(root)


def _check(stem: str, name: str) -> None:
    f, case = FILES[stem], FILES[stem]["cases"][name]
    rule = RULES[f["rule"]] if f.get("rule") else None
    source = case["source"]
    if f["language"] == "c_sharp":
        found = type_uses(source.encode(), rule.detection.listed)  # pyright: ignore[reportAttributeAccessIssue]
        got = None if found is None else {k: [list(s) for s in v] for k, v in found.items()}
        assert got == case["uses"]
        return
    if rule is not None:
        assert bool(rule.detection.pattern.search(source)) is case["detected"]  # pyright: ignore[reportAttributeAccessIssue]
    if f.get("transform"):
        plan = _plan(transforms.TRANSFORMS[f["transform"]], case)
        assert (len(plan.edits), len(plan.unproven)) == (case["edits"], case["unproven"])
        assert (_apply(source, plan) if plan.edits else None) == case.get("rewritten")


@pytest.mark.parametrize(("stem", "name"), CASES, ids=[f"{s}::{n}" for s, n in CASES])
def test_the_case_outcome_is_pinned(stem, name):
    _check(stem, name)


@pytest.mark.parametrize("variant", VARIANTS)
def test_variant_is_exercised_across_the_corpus(variant):
    tagged = [(s, n) for s, n in CASES if FILES[s]["cases"][n]["variant"] == variant]
    assert tagged, f"no corpus case carries the variant {variant!r}"
    for stem, name in tagged:
        _check(stem, name)


def test_every_case_names_a_known_variant_or_none():
    unknown = {FILES[s]["cases"][n]["variant"] for s, n in CASES} - {*VARIANTS, ""}
    assert not unknown


def test_a_detector_and_transform_that_disagree_carry_a_limit():
    """A regex that misses a spelling its transform handles is a known gap, never silent."""
    silent = [
        f"{s}::{n}"
        for s, n in CASES
        if (c := FILES[s]["cases"][n]).get("detected") is False
        and (c.get("edits") or c.get("unproven"))
        and not c.get("limit")
    ]
    assert not silent, f"{silent} are missed by the detector but handled by the transform"


def test_a_transform_with_no_corpus_file_is_reported(monkeypatch):
    brand_new = transforms.Transform("javascript", (3,), lambda root: transforms.Plan((), ()))
    monkeypatch.setitem(transforms.TRANSFORMS, "brand-new", brand_new)
    assert _uncovered_transforms() == ["brand-new"]


def test_a_detector_rule_with_no_corpus_file_is_reported(monkeypatch):
    monkeypatch.delitem(FILES, "web-resource-keypress")
    assert _uncovered_rules() == ["web-resource-keypress"]
