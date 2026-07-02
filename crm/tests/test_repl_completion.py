"""Unit tests for REPL command/flag/profile completion (issue #654).

`complete_entity_token` (the pre-existing entity-name slot logic) is already
covered by `TestCompleteEntityToken` in test_metadata_cache.py; these tests
cover the newly added command-name, flag-name, Choice-value, and profile-name
completion that `complete_repl_line` composes on top of it.
"""
# pyright: basic
from __future__ import annotations

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from crm.commands.repl import MetadataCache, _ReplCompleter, complete_repl_line

pytestmark = pytest.mark.usefixtures("isolated_home")


class TestGroupAndCommandNames:
    def test_prefix_completes_top_level_command(self):
        assert complete_repl_line("ent", [], [], []) == ["entity"]

    def test_trailing_space_after_group_completes_verbs(self):
        verbs = complete_repl_line("entity ", [], [], [])
        assert verbs is not None
        assert "get" in verbs
        assert "create" in verbs

    def test_unresolvable_first_token_yields_nothing(self):
        # A typo'd group name must not fall back to suggesting root commands.
        assert complete_repl_line("bogus verb ", [], [], []) is None

    def test_unresolvable_second_token_yields_nothing(self):
        assert complete_repl_line("entity bogus ", [], [], []) is None


class TestFlagNames:
    def test_dash_dash_lists_flags_including_secondary_form(self):
        flags = complete_repl_line("entity get --", [], [], [])
        assert flags is not None
        assert "--annotations" in flags
        assert "--no-annotations" in flags

    def test_prefix_filters_out_secondary_form(self):
        assert complete_repl_line("entity get --anno", [], [], []) == ["--annotations"]

    def test_root_level_flag_prefix(self):
        flags = complete_repl_line("--pro", [], [], [])
        assert flags == ["--profile"]


class TestChoiceValues:
    def test_subcommand_choice_param(self):
        # completion install --shell <TAB> -> the shell choices, declared order.
        assert complete_repl_line("completion install --shell ", [], [], []) == [
            "zsh", "bash", "fish", "powershell",
        ]

    def test_root_level_choice_param(self):
        assert complete_repl_line("--log-level ", [], [], []) == [
            "debug", "info", "warning", "error",
        ]

    def test_non_choice_option_value_yields_nothing(self):
        # --select is a bare TEXT option; no values to suggest.
        assert complete_repl_line("entity get --select ", [], [], []) is None

    def test_unrecognized_option_value_yields_nothing(self):
        assert complete_repl_line("entity get --bogus-flag ", [], [], []) is None


class TestProfileNames:
    def test_completes_after_bare_profile_flag(self):
        assert complete_repl_line("--profile ", [], [], ["dev", "prod"]) == ["dev", "prod"]

    def test_prefix_filters_profile_names(self):
        assert complete_repl_line("--profile pr", [], [], ["dev", "prod"]) == ["prod"]

    def test_position_blind_after_a_subcommand(self):
        # The REPL never validates the full Click option graph (unlike OS-shell
        # completion, which is scoped to wherever --profile is actually declared).
        assert complete_repl_line(
            "entity get --profile ", [], [], ["dev", "prod"]
        ) == ["dev", "prod"]


class TestEntitySlotUnchanged:
    def test_entity_slot_completion_still_applies(self):
        assert complete_repl_line(
            "entity get acc", ["account"], ["accounts"], []
        ) == ["accounts"]


def _completions(completer: _ReplCompleter, text: str) -> list[str]:
    doc = Document(text=text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(doc, CompleteEvent())]


class TestReplCompleter:
    """The prompt_toolkit wiring around complete_repl_line."""

    def test_yields_command_name_completions(self):
        def failing_backend():
            raise RuntimeError("no profile configured")

        completer = _ReplCompleter(failing_backend, MetadataCache())
        assert _completions(completer, "ent") == ["entity"]

    def test_backend_failure_does_not_block_flag_completion(self):
        # A broken/absent backend must not silently kill unrelated completion
        # paths (command names, flags, profiles) that never touch it.
        def failing_backend():
            raise RuntimeError("no profile configured")

        completer = _ReplCompleter(failing_backend, MetadataCache())
        out = _completions(completer, "entity get --anno")
        assert out == ["--annotations"]

    def test_command_chain_failure_does_not_crash_completion(self, monkeypatch):
        # A lazy-import failure inside _resolve_command_chain (surfaced as a
        # click.ClickException by _LazyJsonAwareGroup.get_command) must not
        # escape get_completions — completion must never raise (PR #660 review).
        import crm.commands.repl as repl_mod

        def boom(line, logical, sets, profiles):
            raise RuntimeError("boom")

        monkeypatch.setattr(repl_mod, "complete_repl_line", boom)
        completer = _ReplCompleter(lambda: object(), MetadataCache())
        assert _completions(completer, "entity") == []

    def test_entity_slot_completion_uses_backend_names(self):
        calls = {"n": 0}

        def backend():
            calls["n"] += 1
            return object()

        cache = MetadataCache()
        cache._logical = ["account"]
        cache._set_names = ["accounts"]
        completer = _ReplCompleter(backend, cache)
        assert _completions(completer, "entity get acc") == ["accounts"]
        assert calls["n"] == 1
