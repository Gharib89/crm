# pyright: basic
"""Structural / consistency guards for the cloud-ship GitHub-access migration.

This PR moves the cloud-ship fire's GitHub reads/writes from the `gh` CLI
(gated by the sandbox egress proxy) to the `mcp__github__*` connector, across
three files:

- `.claude/skills/cloud-ship/SKILL.md` — the fire's own instructions
- `docs/agents/cloud-ship-routine.md` — the human-facing routine setup doc
- `scripts/cloud-ship-bootstrap.sh` — covered separately in
  `test_cloud_ship_bootstrap_sh.py`

These are plain markdown/text files with no test harness of their own, so
these are simple content assertions (mirroring the `test_skill_bundle.py`
pattern already used for `crm/skills/SKILL.md`): they guard that the new
MCP-based instructions are present, that superseded `gh`-native snippets were
actually removed (not just supplemented), and that the two docs stay
consistent with each other.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLOUD_SHIP_SKILL = REPO_ROOT / ".claude" / "skills" / "cloud-ship" / "SKILL.md"
CLOUD_SHIP_ROUTINE_DOC = REPO_ROOT / "docs" / "agents" / "cloud-ship-routine.md"
BOOTSTRAP_SH = REPO_ROOT / "scripts" / "cloud-ship-bootstrap.sh"


def _skill_text() -> str:
    return CLOUD_SHIP_SKILL.read_text(encoding="utf-8")


def _routine_text() -> str:
    return CLOUD_SHIP_ROUTINE_DOC.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# .claude/skills/cloud-ship/SKILL.md
# --------------------------------------------------------------------------- #


def test_skill_files_exist():
    assert CLOUD_SHIP_SKILL.is_file()
    assert (CLOUD_SHIP_SKILL.parent / "reference" / "working-standards.md").is_file()


def test_skill_frontmatter_identifies_cloud_ship():
    text = _skill_text()
    assert text.startswith("---\nname: cloud-ship\n")
    assert "description:" in text.splitlines()[2]


def test_github_access_in_a_fire_section_present():
    text = _skill_text()
    assert "## GitHub access in a fire" in text
    assert "mcp__github__" in text
    assert '403 "GitHub access is not enabled for this session"' in text


def test_mapping_table_covers_every_gh_command_a_fire_reaches():
    text = _skill_text()
    for mcp_tool in (
        "mcp__github__issue_read",
        "mcp__github__list_issues",
        "mcp__github__issue_write",
        "mcp__github__add_issue_comment",
        "mcp__github__create_pull_request",
        "mcp__github__pull_request_read",
        "mcp__github__request_copilot_review",
    ):
        assert mcp_tool in text, f"mapping table missing {mcp_tool}"


def test_step2_issue_picker_uses_mcp_not_bare_gh():
    """Step 2's code block must call the MCP issue picker, not `gh issue list`."""
    text = _skill_text()
    assert "mcp__github__list_issues" in text
    assert 'gh issue list --repo Gharib89/crm' not in text
    assert '--search "label:ready-for-agent state:open sort:created-asc"' not in text


def test_step4_blocked_handoff_uses_mcp_not_bare_gh():
    """Step 4's blocked hand-off must use issue_read/issue_write, not gh issue edit/comment."""
    text = _skill_text()
    assert "mcp__github__issue_write" in text
    assert "mcp__github__add_issue_comment" in text
    # The old runnable snippet (with --repo and a bare replacement label list) is gone.
    assert 'gh issue edit "$NUM" --repo Gharib89/crm' not in text
    assert 'gh issue comment "$NUM" --repo Gharib89/crm' not in text


def test_step4_warns_that_issue_write_replaces_the_whole_label_set():
    """#label-clobber regression: issue_write replaces labels wholesale, unlike
    `gh issue edit`'s surgical --add/--remove-label, so the skill must instruct
    reading the current labels first."""
    text = _skill_text()
    assert "**replaces**" in text
    assert "read the current labels first" in text
    assert 'never a bare' in text
    assert '["ready-for-human"]' in text


def test_stale_gh_401_troubleshooting_language_is_removed():
    """The old (now-inaccurate) 'gh works unless GH_TOKEN/network policy is wrong'
    guidance must not linger next to the new 403-from-egress-proxy explanation."""
    text = _skill_text()
    assert "if it 401s" not in text
    assert "assume `gh` works" not in text


def test_github_access_section_states_it_outranks_literal_gh_commands():
    text = _skill_text()
    assert "outranks every literal" in text
    assert "docs/agents/issue-tracker.md" in text
    assert "docs/agents/triage-labels.md" in text


def test_merge_gate_commands_documented_as_out_of_fire_path():
    """gh pr merge / pr view --json state,mergedAt / issue view stay unmapped
    on purpose — step 5 stops before merge, so they're explicitly excluded."""
    text = _skill_text()
    assert "out of\na fire's path" in text or "out of a fire's path" in text
    assert "gh pr merge" in text


def test_bounded_polling_language_present():
    """The poll-ceiling guard this PR's predecessor commit hardened must still
    be intact after layering the MCP mapping on top."""
    text = _skill_text()
    assert "never a\ndetached/background monitor" in text or "never a detached/background monitor" in text
    assert "capped number of attempts" in text


def test_mcp_absent_or_denied_is_a_stop_condition():
    text = _skill_text()
    assert "the connector isn't wired" in text
    assert "report\nand STOP" in text or "report and STOP" in text


# --------------------------------------------------------------------------- #
# docs/agents/cloud-ship-routine.md
# --------------------------------------------------------------------------- #


def test_routine_doc_exists_and_references_the_bootstrap_script():
    assert CLOUD_SHIP_ROUTINE_DOC.is_file()
    assert BOOTSTRAP_SH.is_file()
    assert "scripts/cloud-ship-bootstrap.sh" in _routine_text()


def test_network_allowlist_no_longer_requires_api_github_com():
    text = _routine_text()
    assert "gh: pr/issue/label/copilot-rerequest REST calls" not in text
    assert "no\n    `api.github.com` entry is needed" in text or "no `api.github.com` entry is needed" in text


def test_network_allowlist_drops_release_assets_domain():
    """The gh-binary-download domain is gone now that setup no longer installs gh."""
    text = _routine_text()
    assert "release-assets.githubusercontent.com" not in text


def test_setup_script_section_no_longer_installs_gh():
    text = _routine_text()
    assert "GH_VERSION" not in text
    assert "cli/cli/releases/download" not in text
    assert "none required for GitHub" in text


def test_gh_token_is_now_documented_as_fallback_only():
    text = _routine_text()
    assert "now only a fallback\n    credential" in text or "now only a fallback credential" in text
    # The old broad scope list (issues/PR/workflow write) must not remain, since
    # GH_TOKEN's job shrank to a git push/fetch fallback.
    assert "Issues + Workflows (write)" not in text


def test_mcp_connector_documented_as_brokered_and_exempt():
    text = _routine_text()
    assert "brokered through Anthropic" in text
    assert "exempt" in text


def test_relabel_and_comments_attributed_to_mcp_not_gh_token():
    text = _routine_text()
    assert "MCP connector's issue-write tools, not `GH_TOKEN`" in text
    assert "Issues:write (already in the env config)" not in text


def test_label_create_step_documented_as_human_step_outside_the_fire():
    """Creating the `agent-working` label needs real `gh`, which only works for
    a human outside the fire now — the doc must say so, not imply the fire runs it."""
    text = _routine_text()
    idx = text.index("gh label create agent-working")
    preceding = text[:idx]
    assert "human step run **outside** the fire" in preceding


def test_routine_doc_cross_references_the_skills_mapping_table():
    """The doc points at the SKILL.md section by name — that section must
    actually exist (cross-file consistency, not just a dangling reference)."""
    routine_text = _routine_text()
    assert 'cloud-ship SKILL.md' in routine_text
    assert '"GitHub access in a fire"' in routine_text
    assert "## GitHub access in a fire" in _skill_text()