# crm/tests/test_skill_lint_gate.py
# pyright: basic
"""Offline skill-lint gate (#889, ADR 0028 Machine A).

Four deterministic conformance rules over the shipped skill tree (`crm/skills/`):
self-containment, internal link integrity, thinness budgets, and the frontmatter
contract. Each rule fails on a synthetic tree that violates it, passes on a clean
one, and offers a reasoned waiver escape hatch. Complements — does not overlap —
the CLI-reconciliation checks in `test_skill_coverage_gate.py`.
"""

from __future__ import annotations

from pathlib import Path

from crm.tests.skill_lint import (
    DESCRIPTION_MAX_CHARS,
    FRONTMATTER_WAIVERS,
    LINK_WAIVERS,
    REFERENCE_MAX_LINES,
    ROUTER_MAX_LINES,
    SELF_CONTAINMENT_WAIVERS,
    THINNESS_WAIVERS,
    check_frontmatter,
    check_link_integrity,
    check_self_containment,
    check_thinness,
    skill_tree,
    stale_thinness_waivers,
)

# Real shipped tree, discovered once (via the coverage gate's shared helper).
_ROUTER, _REFERENCES = skill_tree()

# ── Fixture-tree builder ─────────────────────────────────────────────────────
_CLEAN_ROUTER = (
    "---\n"
    "name: crm\n"
    "description: A clean test skill.\n"
    "---\n\n"
    "# crm\n\n"
    "Routing: reference/alpha.md and reference/beta.md.\n"
)
_CLEAN_REFS = {
    "alpha.md": "# Alpha\n\nContent.\n",
    "beta.md": "# Beta\n\nSee reference/alpha.md for more.\n",
}


def _make_tree(tmp_path: Path, router: str, refs: dict[str, str]) -> Path:
    """Materialize a synthetic skill tree; return its directory."""
    root = tmp_path / "skills"
    (root / "reference").mkdir(parents=True)
    (root / "SKILL.md").write_text(router, encoding="utf-8")
    for name, body in refs.items():
        (root / "reference" / name).write_text(body, encoding="utf-8")
    return root


def _lines(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(n)) + "\n"


# ── Rule 1: self-containment ─────────────────────────────────────────────────
def test_self_containment_clean_passes(tmp_path):
    d = _make_tree(tmp_path, _CLEAN_ROUTER, _CLEAN_REFS)
    router, refs = skill_tree(d)
    assert check_self_containment(router, refs) == []


def test_self_containment_flags_repo_path(tmp_path):
    refs = {**_CLEAN_REFS, "alpha.md": "# Alpha\n\nSee docs/adr/0001.md for details.\n"}
    d = _make_tree(tmp_path, _CLEAN_ROUTER, refs)
    router, files = skill_tree(d)
    violations = check_self_containment(router, files)
    assert any("docs/adr" in v for v in violations)


def test_self_containment_flags_escaping_link(tmp_path):
    router = _CLEAN_ROUTER + "\nSee [context](../../CONTEXT.md).\n"
    d = _make_tree(tmp_path, router, _CLEAN_REFS)
    r, files = skill_tree(d)
    violations = check_self_containment(r, files)
    assert any("not self-contained" in v for v in violations)


def test_self_containment_flags_unshipped_in_dir_link(tmp_path):
    # A link that resolves inside the skill dir but names a file that doesn't
    # ship (a repo path or dead link) is not self-contained either.
    router = _CLEAN_ROUTER + "\nSee [notes](notes/todo.md).\n"
    d = _make_tree(tmp_path, router, _CLEAN_REFS)
    r, files = skill_tree(d)
    assert any("not self-contained" in v for v in check_self_containment(r, files))


def test_self_containment_allows_external_url(tmp_path):
    router = _CLEAN_ROUTER + "\nSee [tools](https://example.com/pkg).\n"
    d = _make_tree(tmp_path, router, _CLEAN_REFS)
    r, files = skill_tree(d)
    assert check_self_containment(r, files) == []


def test_self_containment_waiver(tmp_path):
    refs = {**_CLEAN_REFS, "alpha.md": "# Alpha\n\nSee docs/adr/0001.md.\n"}
    d = _make_tree(tmp_path, _CLEAN_ROUTER, refs)
    r, files = skill_tree(d)
    assert check_self_containment(r, files, waived={"alpha.md": "test waiver"}) == []


# ── Rule 2: internal link integrity ──────────────────────────────────────────
def test_link_integrity_clean_passes(tmp_path):
    d = _make_tree(tmp_path, _CLEAN_ROUTER, _CLEAN_REFS)
    router, refs = skill_tree(d)
    assert check_link_integrity(router, refs) == []


def test_link_integrity_flags_orphan(tmp_path):
    refs = {**_CLEAN_REFS, "gamma.md": "# Gamma\n\nUncited.\n"}
    d = _make_tree(tmp_path, _CLEAN_ROUTER, refs)
    router, files = skill_tree(d)
    violations = check_link_integrity(router, files)
    assert any("orphan" in v and "gamma.md" in v for v in violations)


def test_link_integrity_flags_dangling_pointer(tmp_path):
    router = _CLEAN_ROUTER + "\nAlso reference/missing.md.\n"
    d = _make_tree(tmp_path, router, _CLEAN_REFS)
    r, refs = skill_tree(d)
    violations = check_link_integrity(r, refs)
    assert any("dangling" in v and "missing.md" in v for v in violations)


def test_link_integrity_waiver(tmp_path):
    refs = {**_CLEAN_REFS, "gamma.md": "# Gamma\n\nUncited.\n"}
    d = _make_tree(tmp_path, _CLEAN_ROUTER, refs)
    router, files = skill_tree(d)
    assert check_link_integrity(router, files, waived={"gamma.md": "test waiver"}) == []


# ── Rule 3: thinness budgets ─────────────────────────────────────────────────
def test_thinness_clean_passes(tmp_path):
    d = _make_tree(tmp_path, _CLEAN_ROUTER, _CLEAN_REFS)
    router, refs = skill_tree(d)
    assert check_thinness(router, refs) == []


def test_thinness_flags_fat_reference(tmp_path):
    refs = {**_CLEAN_REFS, "alpha.md": _lines(REFERENCE_MAX_LINES + 10)}
    d = _make_tree(tmp_path, _CLEAN_ROUTER, refs)
    router, files = skill_tree(d)
    violations = check_thinness(router, files)
    assert any("alpha.md" in v for v in violations)


def test_thinness_flags_fat_router(tmp_path):
    router = _CLEAN_ROUTER + _lines(ROUTER_MAX_LINES + 10)
    d = _make_tree(tmp_path, router, _CLEAN_REFS)
    r, refs = skill_tree(d)
    assert any("SKILL.md" in v for v in check_thinness(r, refs))


def test_thinness_waiver(tmp_path):
    refs = {**_CLEAN_REFS, "alpha.md": _lines(REFERENCE_MAX_LINES + 10)}
    d = _make_tree(tmp_path, _CLEAN_ROUTER, refs)
    router, files = skill_tree(d)
    assert check_thinness(router, files, waived={"alpha.md": "test waiver"}) == []


def test_stale_thinness_waiver_detected(tmp_path):
    # A waiver for an under-budget file is stale and must be reported.
    d = _make_tree(tmp_path, _CLEAN_ROUTER, _CLEAN_REFS)
    router, refs = skill_tree(d)
    assert stale_thinness_waivers(router, refs, waived={"alpha.md": "stale"}) == {"alpha.md"}


# ── Rule 4: frontmatter contract ─────────────────────────────────────────────
def test_frontmatter_clean_passes(tmp_path):
    d = _make_tree(tmp_path, _CLEAN_ROUTER, _CLEAN_REFS)
    router, _ = skill_tree(d)
    assert check_frontmatter(router) == []


def test_frontmatter_flags_missing_block(tmp_path):
    d = _make_tree(tmp_path, "# crm\n\nNo frontmatter.\n", _CLEAN_REFS)
    router, _ = skill_tree(d)
    assert any("frontmatter" in v for v in check_frontmatter(router))


def test_frontmatter_flags_invalid_yaml(tmp_path):
    router = "---\nname: crm\ndescription: [1, 2\n---\n\n# crm\n"
    d = _make_tree(tmp_path, router, _CLEAN_REFS)
    r, _ = skill_tree(d)
    assert any("invalid YAML" in v for v in check_frontmatter(r))


def test_frontmatter_flags_wrong_name(tmp_path):
    router = _CLEAN_ROUTER.replace("name: crm", "name: wrong")
    d = _make_tree(tmp_path, router, _CLEAN_REFS)
    r, _ = skill_tree(d)
    assert any("name" in v for v in check_frontmatter(r))


def test_frontmatter_flags_missing_description(tmp_path):
    router = "---\nname: crm\n---\n\n# crm\n\nreference/alpha.md reference/beta.md\n"
    d = _make_tree(tmp_path, router, _CLEAN_REFS)
    r, _ = skill_tree(d)
    assert any("description" in v for v in check_frontmatter(r))


def test_frontmatter_flags_overlong_description(tmp_path):
    long_desc = "x" * (DESCRIPTION_MAX_CHARS + 1)
    router = f"---\nname: crm\ndescription: {long_desc}\n---\n\n# crm\n"
    d = _make_tree(tmp_path, router, _CLEAN_REFS)
    r, _ = skill_tree(d)
    assert any("chars" in v for v in check_frontmatter(r))


def test_frontmatter_waiver(tmp_path):
    router = _CLEAN_ROUTER.replace("name: crm", "name: wrong")
    d = _make_tree(tmp_path, router, _CLEAN_REFS)
    r, _ = skill_tree(d)
    assert check_frontmatter(r, waived={"SKILL.md": "test waiver"}) == []


# ── The real shipped tree passes every rule ──────────────────────────────────
def test_real_tree_self_contained():
    assert check_self_containment(_ROUTER, _REFERENCES) == []


def test_real_tree_link_integrity():
    assert check_link_integrity(_ROUTER, _REFERENCES) == []


def test_real_tree_thinness():
    # Passes because the over-budget references are covered by THINNESS_WAIVERS.
    assert check_thinness(_ROUTER, _REFERENCES) == []


def test_real_tree_frontmatter():
    assert check_frontmatter(_ROUTER) == []


# ── Waiver hygiene ───────────────────────────────────────────────────────────
def test_no_stale_thinness_waivers():
    assert stale_thinness_waivers(_ROUTER, _REFERENCES) == set()


def test_every_waiver_has_a_reason():
    for waivers in (
        SELF_CONTAINMENT_WAIVERS,
        LINK_WAIVERS,
        THINNESS_WAIVERS,
        FRONTMATTER_WAIVERS,
    ):
        assert all(reason.strip() for reason in waivers.values())


def test_containment_and_frontmatter_waiver_keys_name_real_files():
    # Guards the self-containment and frontmatter waiver dicts against stale/typo
    # keys (both are keyed by a shipped file name). LINK_WAIVERS is excluded: a
    # dangling-pointer waiver legitimately names an absent file.
    real = {_ROUTER.name} | {r.name for r in _REFERENCES}
    for waivers in (SELF_CONTAINMENT_WAIVERS, FRONTMATTER_WAIVERS):
        unknown = {key for key in waivers if key not in real}
        assert not unknown, f"waiver key(s) not naming a real skill file: {sorted(unknown)}"
