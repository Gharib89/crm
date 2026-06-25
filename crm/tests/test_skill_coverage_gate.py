# crm/tests/test_skill_coverage_gate.py
# pyright: basic
"""Offline skill-coverage gate (#569).

Reconciles the shipped skill tree (`crm/skills/`) against the live CLI catalogue
at **group** granularity: no cited command may be dead, and every real top-level
group must be cited or explicitly waived. Complements — does not replace — the
structural guards in `test_skill_bundle.py`.
"""
from __future__ import annotations

from pathlib import Path

from crm.tests.skill_coverage import (
    WAIVED,
    catalogue,
    dead_references,
    parse_citations,
    unrouted_groups,
)

# Catalogue is stable across the module; compute once.
_TOP_LEVEL, _GROUPS, _LEAVES = catalogue()


def _write_skill(tmp_path: Path, body: str) -> list[Path]:
    """Materialize a one-file synthetic skill tree for parser/gate demos."""
    f = tmp_path / "SKILL.md"
    f.write_text(body, encoding="utf-8")
    return [f]


# ── Catalogue sanity (the lazy loader must actually yield commands) ──────────
def test_catalogue_drives_the_lazy_loader():
    # A naive `.commands` recursion on the lazy root would yield 0; guard that.
    assert len(_LEAVES) > 100
    assert "metadata add-attribute" in _LEAVES
    assert {"entity", "metadata", "query"} <= _GROUPS


# ── Parser false-positive cases (pin the anchoring rules) ────────────────────
def test_prose_mentioning_a_verb_is_not_a_citation(tmp_path):
    files = _write_skill(tmp_path, "You can create accounts and query them freely.\n")
    groups, pairs = parse_citations(files)
    assert groups == set()
    assert pairs == set()


def test_fenced_non_crm_command_is_ignored(tmp_path):
    files = _write_skill(tmp_path, "```bash\ngit status\nrm -rf build\n```\n")
    groups, pairs = parse_citations(files)
    assert groups == set()
    assert pairs == set()


def test_bare_or_partial_crm_token_is_not_a_citation(tmp_path):
    # `crm` alone, and the `crmworx` near-miss, must not anchor.
    files = _write_skill(
        tmp_path, "Install the `crm` binary. The `crmworx query odata` tool differs.\n"
    )
    groups, pairs = parse_citations(files)
    assert groups == set()
    assert pairs == set()


def test_genuine_inline_citation_is_parsed(tmp_path):
    files = _write_skill(tmp_path, "Run `crm query odata --top 5` to list records.\n")
    groups, pairs = parse_citations(files)
    assert groups == {"query"}
    assert pairs == {("query", "odata")}


def test_leading_global_options_do_not_hide_the_group(tmp_path):
    # `crm --json connection whoami` must still anchor on `connection`.
    files = _write_skill(tmp_path, "Check `crm --json connection whoami` first.\n")
    groups, pairs = parse_citations(files)
    assert groups == {"connection"}
    assert pairs == {("connection", "whoami")}


def test_top_level_command_argument_is_not_a_verb(tmp_path):
    # `describe` is a single top-level command; `[group]` / `metadata` after it
    # is an argument, not a sub-verb — so no dead `(describe, …)` pair appears.
    files = _write_skill(tmp_path, "Use `crm describe metadata` to inspect a group.\n")
    groups, pairs = parse_citations(files)
    assert groups == {"describe"}
    assert pairs == set()


def test_fenced_crm_line_among_noise_is_parsed(tmp_path):
    body = "```bash\n# a comment\ncrm --dry-run entity create contacts --data '{}'\nok\n```\n"
    files = _write_skill(tmp_path, body)
    groups, pairs = parse_citations(files)
    assert groups == {"entity"}
    # `contacts` is the entity-set argument, not a sub-verb.
    assert pairs == {("entity", "create")}


# ── Invariant 1: dead-reference (against the real skill tree) ────────────────
def test_no_dead_references_in_shipped_skill():
    _, cited_pairs = parse_citations()
    dead = dead_references(cited_pairs, _LEAVES)
    assert not dead, f"skill cites command(s) absent from the CLI: {sorted(dead)}"


# ── Invariant 2: group completeness (against the real skill tree) ────────────
def test_every_group_is_cited_or_waived():
    cited_groups, _ = parse_citations()
    unrouted = unrouted_groups(cited_groups, _GROUPS)
    assert not unrouted, (
        f"top-level group(s) neither cited by the skill nor WAIVED: {sorted(unrouted)}"
    )


# ── WAIVED hygiene: no stale waivers, each carries a reason ──────────────────
def test_no_stale_waivers():
    stale = {g for g in WAIVED if g not in _GROUPS}
    assert not stale, f"WAIVED names that are no longer real groups: {sorted(stale)}"


def test_every_waiver_has_a_reason():
    assert all(reason.strip() for reason in WAIVED.values())


# ── Synthetic failures: the gate fires both ways (demoable) ──────────────────
def test_gate_catches_a_synthetic_dead_citation(tmp_path):
    files = _write_skill(tmp_path, "Run `crm metadata totally-fake-verb` now.\n")
    _, pairs = parse_citations(files)
    dead = dead_references(pairs, _LEAVES)
    assert "metadata totally-fake-verb" in dead


def test_gate_catches_a_synthetic_unrouted_group(tmp_path):
    # A skill that cites only `entity` leaves every other real group unrouted.
    files = _write_skill(tmp_path, "Only `crm entity create` is documented here.\n")
    cited_groups, _ = parse_citations(files)
    unrouted = unrouted_groups(cited_groups, _GROUPS)
    assert "metadata" in unrouted  # a real, non-waived group goes uncited
    assert "entity" not in unrouted
