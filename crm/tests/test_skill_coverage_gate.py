# pyright: basic
"""Offline skill-coverage gate (#569).

Reconciles the shipped skill tree (`crm/skills/`) against the live CLI catalogue
at **group** granularity: no cited command may be dead, and every real top-level
group must be cited or explicitly waived. Complements — does not replace — the
structural guards in `test_skill_bundle.py`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from crm.tests.skill_coverage import (
    MIGRATION_SKILL_DIR,
    WAIVED,
    catalogue,
    dead_references,
    parse_citations,
    skill_files,
    unrouted_groups,
)

_TOP_LEVEL, _GROUPS, _LEAVES = catalogue()


def _write_skill(tmp_path: Path, body: str) -> list[Path]:
    """Materialize a one-file synthetic skill tree for parser/gate demos."""
    f = tmp_path / "SKILL.md"
    f.write_text(body, encoding="utf-8")
    return [f]


def test_catalogue_drives_the_lazy_loader():
    """A naive `.commands` recursion on the lazy root would yield 0 commands.

    The top-level names are a strict superset of the groups: bare leaf commands
    (`crm describe`, ...) exist and are out of the completeness gate's scope.
    """
    assert len(_LEAVES) > 100
    assert "metadata add-attribute" in _LEAVES
    assert {"entity", "metadata", "query"} <= _GROUPS
    assert _GROUPS < _TOP_LEVEL


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
    files = _write_skill(tmp_path, "Check `crm --json connection whoami` first.\n")
    groups, pairs = parse_citations(files)
    assert groups == {"connection"}
    assert pairs == {("connection", "whoami")}


def test_top_level_command_argument_is_not_a_verb(tmp_path):
    files = _write_skill(tmp_path, "Use `crm describe metadata` to inspect a group.\n")
    groups, pairs = parse_citations(files)
    assert groups == {"describe"}
    assert pairs == set()


def test_fenced_crm_line_among_noise_is_parsed(tmp_path):
    body = "```bash\n# a comment\ncrm --dry-run entity create contacts --data '{}'\nok\n```\n"
    files = _write_skill(tmp_path, body)
    groups, pairs = parse_citations(files)
    assert groups == {"entity"}
    assert pairs == {("entity", "create")}


def test_no_dead_references_in_shipped_skill():
    _, cited_pairs = parse_citations()
    dead = dead_references(cited_pairs, _LEAVES)
    assert not dead, f"skill cites command(s) absent from the CLI: {sorted(dead)}"


def test_no_dead_references_in_migration_skill():
    _, cited_pairs = parse_citations(skill_files(MIGRATION_SKILL_DIR))
    dead = dead_references(cited_pairs, _LEAVES)
    assert not dead, f"crm-migration skill cites command(s) absent from the CLI: {sorted(dead)}"


def test_dead_reference_planted_in_the_migration_tree_fails(tmp_path):
    """#1062: the gate must walk the migration tree, not only `crm/skills/`."""
    tree = tmp_path / "migration-skill"
    shutil.copytree(MIGRATION_SKILL_DIR, tree)
    router = tree / "SKILL.md"
    router.write_text(
        router.read_text(encoding="utf-8") + "\nRun `crm inventory totally-fake-verb`.\n",
        encoding="utf-8",
    )
    _, pairs = parse_citations(skill_files(tree))
    assert dead_references(pairs, _LEAVES) == {"inventory totally-fake-verb"}


def test_every_group_is_cited_or_waived():
    cited_groups, _ = parse_citations()
    unrouted = unrouted_groups(cited_groups, _GROUPS)
    assert not unrouted, (
        f"top-level group(s) neither cited by the skill nor WAIVED: {sorted(unrouted)}"
    )


def test_no_stale_waivers():
    stale = {g for g in WAIVED if g not in _GROUPS}
    assert not stale, f"WAIVED names that are no longer real groups: {sorted(stale)}"


def test_every_waiver_has_a_reason():
    assert all(reason.strip() for reason in WAIVED.values())


def test_gate_catches_a_synthetic_dead_citation(tmp_path):
    files = _write_skill(tmp_path, "Run `crm metadata totally-fake-verb` now.\n")
    _, pairs = parse_citations(files)
    dead = dead_references(pairs, _LEAVES)
    assert "metadata totally-fake-verb" in dead


def test_gate_catches_a_synthetic_unrouted_group(tmp_path):
    files = _write_skill(tmp_path, "Only `crm entity create` is documented here.\n")
    cited_groups, _ = parse_citations(files)
    unrouted = unrouted_groups(cited_groups, _GROUPS)
    assert "metadata" in unrouted
    assert "entity" not in unrouted


def test_a_cited_subgroup_of_real_leaves_is_not_dead():
    """`data load-window` is a subgroup.

    The parser reads `group verb` pairs, so a cited `crm data load-window begin`
    arrives as the pair (data, load-window).
    """
    leaves = frozenset({"data load-window begin", "data load-window end"})
    assert dead_references({("data", "load-window")}, leaves) == set()
    assert dead_references({("data", "load")}, leaves) == {"data load"}
