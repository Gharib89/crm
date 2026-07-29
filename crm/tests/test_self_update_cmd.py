# pyright: basic
"""Command-level tests for `crm self-update`."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

import crm.commands.self_update as self_update_mod
import crm.core.update as update_mod
from crm.cli import cli
from crm.commands import completion_registry, skill_registry


@pytest.fixture(autouse=True)
def _no_update_check(isolated_home, monkeypatch):
    # isolated_home handles CRM_HOME / CRM_DOTENV; suppress the auto update
    # check so command runs are deterministic (cf. crm/cli.py).
    monkeypatch.setenv("CRM_NO_UPDATE_CHECK", "1")


class TestCheck:
    """--check reports versions without modifying anything, both output modes."""

    def test_human(self, monkeypatch):
        monkeypatch.setattr(
            update_mod,
            "check_for_update",
            lambda *a, **k: {"current": "2.9.0", "latest": "v3.0.0", "update_available": True},
        )
        result = CliRunner().invoke(cli, ["self-update", "--check"])
        assert result.exit_code == 0
        assert "3.0.0" in result.output

    def test_json_envelope(self, monkeypatch):
        monkeypatch.setattr(
            update_mod,
            "check_for_update",
            lambda *a, **k: {"current": "2.9.0", "latest": "v3.0.0", "update_available": True},
        )
        result = CliRunner().invoke(cli, ["--json", "self-update", "--check"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["update_available"] is True

    def test_network_failure_is_clean_error(self, monkeypatch):
        def boom(*a, **k):
            raise update_mod.UpdateError("network unreachable")

        monkeypatch.setattr(update_mod, "check_for_update", boom)
        result = CliRunner().invoke(cli, ["--json", "self-update", "--check"])
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False


def _force_method(monkeypatch, method, *, current="2.9.0", latest="v3.0.0", available=True):
    """Force a non-frozen install method + a canned latest-version check (no network)."""
    monkeypatch.setattr(update_mod, "detect_install_method", lambda: method)
    monkeypatch.setattr(
        update_mod,
        "check_for_update",
        lambda *a, **k: {"current": current, "latest": latest, "update_available": available},
    )


class TestMethodAwareUpdate:
    """Non-frozen `self-update` is install-method-aware (issue #872): auto-run for
    uv/pipx (consent-gated), printed guidance for editable/pip-git/unknown.
    """

    def test_editable_prints_guidance_never_runs(self, monkeypatch):
        _force_method(monkeypatch, "editable")
        ran = {"n": 0}
        monkeypatch.setattr(update_mod, "run_upgrade", lambda *a, **k: ran.__setitem__("n", 1))
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["install_method"] == "editable"
        assert data["executed"] is False
        assert data["reason"] == "manual-install-method"
        assert data["command"] == "git pull && pip install -e ."
        assert ran["n"] == 0

    def test_unknown_never_executes(self, monkeypatch):
        _force_method(monkeypatch, "unknown")
        ran = {"n": 0}
        monkeypatch.setattr(update_mod, "run_upgrade", lambda *a, **k: ran.__setitem__("n", 1))
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        data = json.loads(result.output)["data"]
        assert data["executed"] is False
        assert ran["n"] == 0
        assert "git+https://github.com/Gharib89/crm@v3.0.0" in data["command"]

    def test_json_no_tty_without_yes_prints_but_does_not_execute(self, monkeypatch):
        _force_method(monkeypatch, "uv-tool")
        ran = {"n": 0}
        monkeypatch.setattr(update_mod, "run_upgrade", lambda *a, **k: ran.__setitem__("n", 1))
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["executed"] is False
        assert data["reason"] == "no-tty-without-yes"
        assert data["command"].startswith("uv tool install --force ")
        assert ran["n"] == 0

    def test_uv_tool_autoruns_with_yes(self, monkeypatch):
        _force_method(monkeypatch, "uv-tool")
        seen = {}

        def fake_run(argv):
            seen["argv"] = argv
            return 0

        monkeypatch.setattr(update_mod, "run_upgrade", fake_run)
        monkeypatch.setattr(
            "crm.commands.self_update._post_upgrade_refresh",
            lambda: {"skills": [], "completion": None},
        )
        result = CliRunner().invoke(cli, ["--json", "self-update", "--yes"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["executed"] is True
        assert data["exit_status"] == 0
        assert seen["argv"] == [
            "uv",
            "tool",
            "install",
            "--force",
            "git+https://github.com/Gharib89/crm@v3.0.0",
        ]

    def test_pipx_autoruns_with_yes(self, monkeypatch):
        _force_method(monkeypatch, "pipx")
        seen = {}

        def fake_run(argv):
            seen["argv"] = argv
            return 0

        monkeypatch.setattr(update_mod, "run_upgrade", fake_run)
        monkeypatch.setattr("crm.commands.self_update._post_upgrade_refresh", lambda: None)
        result = CliRunner().invoke(cli, ["--json", "self-update", "--yes"])
        assert result.exit_code == 0
        assert seen["argv"][0:2] == ["pipx", "install"]

    def test_tty_prompt_yes_runs(self, monkeypatch):
        _force_method(monkeypatch, "uv-tool")
        monkeypatch.setattr("crm.commands.self_update._stdin_is_tty", lambda: True)
        ran = {"n": 0}
        monkeypatch.setattr(update_mod, "run_upgrade", lambda argv: ran.__setitem__("n", 1) or 0)
        monkeypatch.setattr("crm.commands.self_update._post_upgrade_refresh", lambda: None)
        result = CliRunner().invoke(cli, ["self-update"], input="y\n")
        assert result.exit_code == 0
        assert ran["n"] == 1

    def test_tty_prompt_no_declines(self, monkeypatch):
        # Interactive decline (human mode, stdin is a TTY, user answers "n"): the
        # upgrade must not run, and the command prints the manual upgrade guidance.
        _force_method(monkeypatch, "uv-tool")
        monkeypatch.setattr("crm.commands.self_update._stdin_is_tty", lambda: True)
        ran = {"n": 0}
        monkeypatch.setattr(update_mod, "run_upgrade", lambda argv: ran.__setitem__("n", 1) or 0)
        result = CliRunner().invoke(cli, ["self-update"], input="n\n")
        assert result.exit_code == 0
        assert ran["n"] == 0
        assert "To upgrade, run:" in result.output

    def test_tool_not_on_path_falls_back_to_print(self, monkeypatch):
        _force_method(monkeypatch, "uv-tool")

        def boom(argv):
            raise FileNotFoundError("uv")

        monkeypatch.setattr(update_mod, "run_upgrade", boom)
        result = CliRunner().invoke(cli, ["--json", "self-update", "--yes"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["executed"] is False
        assert data["reason"] == "tool-not-on-path"

    def test_up_to_date_makes_no_change(self, monkeypatch):
        _force_method(monkeypatch, "uv-tool", available=False, latest="v2.9.0")
        ran = {"n": 0}
        monkeypatch.setattr(update_mod, "run_upgrade", lambda *a, **k: ran.__setitem__("n", 1))
        result = CliRunner().invoke(cli, ["--json", "self-update", "--yes"])
        data = json.loads(result.output)["data"]
        assert data["update_available"] is False
        assert data["reason"] == "up-to-date"
        assert ran["n"] == 0

    def test_nonzero_exit_is_clean_error_with_payload(self, monkeypatch):
        _force_method(monkeypatch, "uv-tool")
        monkeypatch.setattr(update_mod, "run_upgrade", lambda argv: 1)
        result = CliRunner().invoke(cli, ["--json", "self-update", "--yes"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["ok"] is False
        # The error envelope still carries the execution details for scripters.
        assert payload["data"]["executed"] is True
        assert payload["data"]["exit_status"] == 1
        assert payload["data"]["install_method"] == "uv-tool"

    def test_refresh_unavailable_after_upgrade_warns(self, monkeypatch):
        _force_method(monkeypatch, "uv-tool")
        monkeypatch.setattr(update_mod, "run_upgrade", lambda argv: 0)
        monkeypatch.setattr("crm.commands.self_update._post_upgrade_refresh", lambda: None)
        result = CliRunner().invoke(cli, ["--json", "self-update", "--yes"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["ok"] is True
        # A failed post-upgrade refresh surfaces as a structured warning, not silence.
        assert any("refresh" in w.lower() for w in payload["meta"]["warnings"])

    def test_network_failure_is_clean_error(self, monkeypatch):
        monkeypatch.setattr(update_mod, "detect_install_method", lambda: "uv-tool")

        def boom(*a, **k):
            raise update_mod.UpdateError("network unreachable")

        monkeypatch.setattr(update_mod, "check_for_update", boom)
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False

    def test_post_upgrade_refresh_invoked_after_success(self, monkeypatch):
        _force_method(monkeypatch, "uv-tool")
        monkeypatch.setattr(update_mod, "run_upgrade", lambda argv: 0)
        called = {"n": 0}
        monkeypatch.setattr(
            "crm.commands.self_update._post_upgrade_refresh",
            lambda: (
                called.__setitem__("n", called["n"] + 1)
                or {"skills": [{"status": "refreshed"}], "completion": None}
            ),
        )
        result = CliRunner().invoke(cli, ["--json", "self-update", "--yes"])
        assert result.exit_code == 0
        assert called["n"] == 1
        assert json.loads(result.output)["data"]["skills"] == [{"status": "refreshed"}]


class TestRefreshOnlyEntry:
    """The hidden `--refresh-only` entry re-syncs skills without upgrading."""

    def test_refresh_only_does_not_upgrade(self, monkeypatch):
        monkeypatch.setattr(update_mod, "detect_install_method", lambda: "uv-tool")
        ran = {"check": 0, "run": 0}
        monkeypatch.setattr(
            update_mod, "check_for_update", lambda *a, **k: ran.__setitem__("check", 1)
        )
        monkeypatch.setattr(update_mod, "run_upgrade", lambda *a, **k: ran.__setitem__("run", 1))
        result = CliRunner().invoke(cli, ["--json", "self-update", "--refresh-only"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["refreshed"] is True
        assert ran == {"check": 0, "run": 0}  # never re-enters upgrade → no reinstall loop


class TestEligibilityIsFailSilent:
    """The notice gate must never break a command (e.g. closed/detached stderr)."""

    def test_isatty_raising_is_treated_as_not_tty(self, monkeypatch):
        import sys

        from crm.cli import _update_check_eligible

        class _BadStderr:
            def isatty(self):
                raise ValueError("I/O operation on closed file")

        monkeypatch.setattr(sys, "stderr", _BadStderr())
        # Must not raise; a stderr that can't report TTY-ness → skip the check.
        assert _update_check_eligible(json_mode=False) is False


class TestDeferredResultSurfacedOnTheNextRun:
    """The finisher applies the swap after `self-update` has exited, so the outcome can
    only be reported by a later command (#937).
    """

    def _record(self, ok: bool = True, **fields) -> None:
        import os
        from pathlib import Path

        record = {
            "ok": ok,
            "at": 1.0,
            "error": None if ok else "Could not replace the installed files.",
            "warnings": [],
            "from_version": "2.9.0",
            "to_version": "3.0.0",
        }
        record.update(fields)
        root = Path(os.environ["CRM_HOME"])
        root.mkdir(parents=True, exist_ok=True)
        (root / "update-result.json").write_text(json.dumps(record), encoding="utf-8")

    def test_the_file_cli_probes_for_is_the_one_the_finisher_writes(self):
        """`crm.cli._deferred_update_recorded` repeats the record's filename rather than
        importing the update module (a cost every command would pay). Nothing else pins
        the two together, so a rename would silence the notice with a green suite.
        """
        import crm.cli as cli_mod

        assert cli_mod._deferred_update_recorded() is False
        update_mod.result_path().parent.mkdir(parents=True, exist_ok=True)
        update_mod.result_path().write_text("{}", encoding="utf-8")
        assert cli_mod._deferred_update_recorded() is True

    def test_an_unrelated_command_reports_the_failure(self, monkeypatch):
        """`CRM_NO_UPDATE_CHECK` is set by this module's autouse fixture: opting out of
        update *checks* must not silence the news that an update failed.
        """
        import crm.cli as cli_mod

        self._record(ok=False)
        # CliRunner's streams are never TTYs, so the gate itself is unit-tested in
        # test_update_core; this asserts the wiring behind it.
        monkeypatch.setattr(cli_mod, "_deferred_result_eligible", lambda json_mode: not json_mode)

        result = CliRunner().invoke(cli, ["describe", "profile"])

        assert result.exit_code == 0
        assert "could not be applied" in result.stderr
        assert "still 2.9.0" in result.stderr

    def test_json_mode_keeps_the_envelope_clean_and_the_record_intact(self, monkeypatch):
        import os

        import crm.cli as cli_mod

        self._record(ok=False)
        # CliRunner's streams are never TTYs, so the gate itself is unit-tested in
        # test_update_core; this asserts the wiring behind it.
        monkeypatch.setattr(cli_mod, "_deferred_result_eligible", lambda json_mode: not json_mode)

        result = CliRunner().invoke(cli, ["--json", "describe", "profile"])

        assert result.exit_code == 0
        assert "could not be applied" not in result.stderr
        json.loads(result.stdout)  # a valid envelope, unpolluted
        assert (__import__("pathlib").Path(os.environ["CRM_HOME"]) / "update-result.json").exists()

    def test_nothing_recorded_means_nothing_printed(self, monkeypatch):
        import crm.cli as cli_mod

        monkeypatch.setattr(cli_mod, "_deferred_result_eligible", lambda json_mode: not json_mode)

        result = CliRunner().invoke(cli, ["describe", "profile"])

        assert result.exit_code == 0
        assert "could not be applied" not in result.stderr


class TestNoticeSuppressedForSelfUpdate:
    """The passive upgrade notice must not fire after `self-update` runs — the running
    process still reports the pre-update version, so the notice would tell the user to
    upgrade to the version they just installed.
    """

    @pytest.fixture
    def _force_eligible(self, monkeypatch):
        import crm.cli as cli_mod

        monkeypatch.setattr(cli_mod, "_update_check_eligible", lambda *a, **k: True)
        calls = []
        monkeypatch.setattr(update_mod, "emit_pending_notice", lambda *a, **k: calls.append(k))
        return calls

    def test_self_update_does_not_emit_notice(self, monkeypatch, _force_eligible):
        _force_method(monkeypatch, "editable")
        result = CliRunner().invoke(cli, ["self-update"])
        assert result.exit_code == 0
        assert _force_eligible == []

    def test_other_command_still_emits_notice(self, _force_eligible):
        result = CliRunner().invoke(cli, ["describe", "profile"])
        assert result.exit_code == 0
        assert len(_force_eligible) == 1


class TestSkillRefresh:
    """Non-`--check` self-update re-syncs recorded skills; `--check` never does."""

    def test_pip_path_refreshes_recorded_skill(self, tmp_path, monkeypatch):
        from crm.commands import skill_registry as reg

        _force_method(monkeypatch, "editable")
        dest = tmp_path / "claude-skill"
        dest.mkdir()
        reg.record_install("claude", str(dest), "0.0.1")  # stale → must refresh

        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        skills = payload["data"]["skills"]
        assert [s["status"] for s in skills] == ["refreshed"]
        assert (dest / "SKILL.md").exists()  # real bundled skill copied in
        assert reg.read_skills()[0]["installed_version"] == update_mod.current_version()

    def test_global_refresh_failure_surfaces_error_not_silence(self, monkeypatch):
        # An unexpected refresh failure (e.g. unreadable registry) must surface in
        # data.skills as an error, not be silently dropped — and must not fail the
        # command (the binary side already succeeded).
        _force_method(monkeypatch, "editable")

        def boom(*a, **k):
            raise PermissionError("registry unreadable")

        monkeypatch.setattr("crm.commands.skill_registry.refresh_skills", boom)
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        skills = json.loads(result.output)["data"]["skills"]
        assert any(s.get("status") == "error" for s in skills)

    def test_check_does_not_touch_skills(self, monkeypatch):
        spy = {"calls": 0}
        monkeypatch.setattr(
            update_mod,
            "check_for_update",
            lambda *a, **k: {"current": "2.9.0", "latest": "v3.0.0", "update_available": True},
        )
        monkeypatch.setattr(
            "crm.commands.skill_registry.refresh_skills",
            lambda *a, **k: spy.__setitem__("calls", spy["calls"] + 1),
        )
        result = CliRunner().invoke(cli, ["--json", "self-update", "--check"])
        assert result.exit_code == 0
        assert spy["calls"] == 0

    def test_frozen_refresh_uses_new_version(self, monkeypatch, tmp_path):
        install = tmp_path / "crm"
        # Mimic the swapped bundle layout: skills under _internal/crm/skills.
        skills = install / "_internal" / "crm" / "skills"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("NEW", encoding="utf-8")
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(update_mod, "install_dir", lambda: install)
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        monkeypatch.setattr(
            update_mod,
            "perform_update",
            lambda *a, **k: {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"},
        )
        seen = {}
        monkeypatch.setattr(
            "crm.commands.skill_registry.refresh_skills",
            lambda version, src: seen.update(version=version) or [],
        )
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        assert (
            seen["version"] == "3.0.0"
        )  # to_version, already v-stripped — not the old running version

    def test_frozen_up_to_date_uses_current_version(self, monkeypatch, tmp_path):
        install = tmp_path / "crm"
        skills = install / "_internal" / "crm" / "skills"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("SAME", encoding="utf-8")
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(update_mod, "install_dir", lambda: install)
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        monkeypatch.setattr(
            update_mod,
            "perform_update",
            lambda *a, **k: {
                "updated": False,
                "current": "3.0.0",
                "latest": "v3.0.0",
                "reason": "up-to-date",
            },
        )
        seen = {}
        monkeypatch.setattr(
            "crm.commands.skill_registry.refresh_skills",
            lambda version, src: seen.update(version=version) or [],
        )
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        assert seen.get("version") == update_mod.current_version()  # never ""


class TestCompletionRefresh:
    """Non-`--check` self-update re-syncs a CLI-installed completion script; absent a
    marker it leaves completion untouched, and a refresh failure never fails the update.
    """

    def _seed_marker(self, shell="zsh", version="0.0.1"):
        from crm.commands import completion_registry as creg

        path = creg.default_script_path(shell)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("OLD", encoding="utf-8")
        creg.write_marker(shell, str(path), version)
        return path

    def test_pip_path_refreshes_recorded_completion(self, monkeypatch):
        from crm.commands import completion_registry as creg

        _force_method(monkeypatch, "editable")
        path = self._seed_marker(version="0.0.1")  # stale → must refresh

        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        comp = json.loads(result.output)["data"]["completion"]
        assert comp["status"] == "refreshed"
        assert path.read_text(encoding="utf-8").splitlines()[0] == "#compdef crm"
        marker = creg.read_marker()
        assert marker is not None and marker["installed_version"] == update_mod.current_version()

    def test_no_marker_leaves_completion_untouched(self, monkeypatch):
        from crm.commands import completion_registry as creg

        _force_method(monkeypatch, "editable")
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        assert "completion" not in json.loads(result.output)["data"]
        assert creg.read_marker() is None

    def test_refresh_failure_does_not_fail_update(self, monkeypatch):
        _force_method(monkeypatch, "editable")
        self._seed_marker(version="0.0.1")

        def boom(*a, **k):
            raise RuntimeError("template render blew up")

        monkeypatch.setattr("crm.commands.completion_registry.generate_source", boom)
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0  # binary side succeeded; completion failure surfaced only
        assert json.loads(result.output)["data"]["completion"]["status"] == "error"

    def test_frozen_refresh_shells_out_to_new_binary(self, monkeypatch, tmp_path):
        install = tmp_path / "crm"
        skills = install / "_internal" / "crm" / "skills"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("NEW", encoding="utf-8")
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(update_mod, "install_dir", lambda: install)
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        monkeypatch.setattr(
            update_mod,
            "perform_update",
            lambda *a, **k: {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"},
        )
        path = self._seed_marker(version="2.9.0")
        seen = {}

        def fake_via_binary(shell, binary):
            seen["shell"], seen["binary"] = shell, binary
            return "#compdef crm\n# regenerated by NEW binary\n"

        # The frozen branch must use the new binary, NOT the in-process (old code) renderer.
        monkeypatch.setattr("crm.commands.completion_registry.generate_via_binary", fake_via_binary)
        monkeypatch.setattr(
            "crm.commands.completion_registry.generate_source",
            lambda *a, **k: pytest.fail("frozen refresh must not use in-process renderer"),
        )
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        assert json.loads(result.output)["data"]["completion"]["status"] == "refreshed"
        assert "regenerated by NEW binary" in path.read_text(encoding="utf-8")
        assert seen["shell"] == "zsh"

    def test_malformed_marker_does_not_crash_human_update(self, monkeypatch):
        # A hand-edited marker with a non-string script_path must not crash the
        # human-mode status emit and break self-update's never-raise guarantee.
        from crm.commands import completion_registry as creg

        _force_method(monkeypatch, "editable")
        creg.marker_path().write_text(
            json.dumps({"shell": "zsh", "script_path": 123, "installed_version": "0.0.1"}),
            encoding="utf-8",
        )
        result = CliRunner().invoke(cli, ["self-update"])  # human mode
        assert result.exit_code == 0

    def test_check_does_not_touch_completion(self, monkeypatch):
        spy = {"calls": 0}
        monkeypatch.setattr(
            update_mod,
            "check_for_update",
            lambda *a, **k: {"current": "2.9.0", "latest": "v3.0.0", "update_available": True},
        )
        self._seed_marker(version="0.0.1")
        monkeypatch.setattr(
            "crm.commands.completion_registry.generate_source",
            lambda *a, **k: spy.__setitem__("calls", spy["calls"] + 1),
        )
        result = CliRunner().invoke(cli, ["--json", "self-update", "--check"])
        assert result.exit_code == 0
        assert spy["calls"] == 0


class TestFrozenUpdate:
    """Frozen install runs the swap; surfaces a clean error on failure."""

    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(
            update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm")
        )
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        monkeypatch.setattr(
            update_mod,
            "perform_update",
            lambda *a, **k: {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"},
        )
        result = CliRunner().invoke(cli, ["self-update"])
        assert result.exit_code == 0
        assert "3.0.0" in result.output

    def test_human_mode_confirms_the_upgrade(self, monkeypatch):
        """The upgrade landing is the point of the command — say so in a sentence,
        the way the up-to-date path already does, not only as raw payload fields.
        """
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(
            update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm")
        )
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        monkeypatch.setattr(
            update_mod,
            "perform_update",
            lambda *a, **k: {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"},
        )
        result = CliRunner().invoke(cli, ["self-update"])
        assert result.exit_code == 0
        assert "Updated crm 2.9.0 -> 3.0.0" in result.stdout

    def test_human_mode_reports_already_current(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(
            update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm")
        )
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        monkeypatch.setattr(
            update_mod,
            "perform_update",
            lambda *a, **k: {
                "updated": False,
                "current": "3.0.0",
                "latest": "v3.0.0",
                "reason": "up-to-date",
            },
        )
        result = CliRunner().invoke(cli, ["self-update"])
        assert result.exit_code == 0
        assert "crm is up to date (3.0.0)." in result.stdout

    def test_json_mode_payload_is_unchanged(self, monkeypatch):
        """The confirmation is human-mode only; the --json contract keeps its shape."""
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(
            update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm")
        )
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        monkeypatch.setattr(
            update_mod,
            "perform_update",
            lambda *a, **k: {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"},
        )
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)["data"]
        assert data["updated"] is True
        assert data["from_version"] == "2.9.0"
        assert data["to_version"] == "3.0.0"
        assert "Updated crm" not in result.stdout

    def test_progress_shown_in_human_mode(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(
            update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm")
        )
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)

        def fake_update(*a, progress=None, **k):
            if progress:
                progress("Downloading crm v3.0.0...")
                progress("Verifying checksum...")
                progress("Installing...")
            return {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"}

        monkeypatch.setattr(update_mod, "perform_update", fake_update)
        result = CliRunner().invoke(cli, ["self-update"])
        assert result.exit_code == 0
        assert "Downloading" in result.output
        assert "Verifying" in result.output
        assert "Installing" in result.output

    def test_progress_absent_in_json_mode(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(
            update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm")
        )
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)

        def fake_update(*a, progress=None, **k):
            assert progress is None, "progress callback must not be set in json mode"
            return {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"}

        monkeypatch.setattr(update_mod, "perform_update", fake_update)
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0

    def test_checksum_failure_exits_nonzero(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(
            update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm")
        )
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)

        def boom(*a, **k):
            raise update_mod.UpdateError("Checksum mismatch; install left untouched.")

        monkeypatch.setattr(update_mod, "perform_update", boom)
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False


class TestFrozenUpdateDeferred:
    """Windows stages the payload and hands the swap to a detached finisher (#937)."""

    @pytest.fixture(autouse=True)
    def _frozen_pending(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(
            update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm")
        )
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        monkeypatch.setattr(
            update_mod,
            "perform_update",
            lambda *a, **k: {
                "updated": False,
                "pending": True,
                "reason": "swap-deferred",
                "from_version": "2.9.0",
                "to_version": "3.0.0",
            },
        )

    def test_human_mode_says_the_update_is_staged_not_done(self):
        """Claiming "Updated crm ..." here would be a lie — nothing has been replaced
        when this line prints.
        """
        result = CliRunner().invoke(cli, ["self-update"])
        assert result.exit_code == 0
        assert "Staged crm 3.0.0" in result.stdout
        assert "Updated crm" not in result.stdout

    def test_json_reports_pending_without_claiming_an_update(self):
        """A scripter keying off `updated` must not read a staged payload as done."""
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)["data"]
        assert data["updated"] is False
        assert data["pending"] is True
        assert data["reason"] == "swap-deferred"
        assert data["to_version"] == "3.0.0"

    def test_does_not_refresh_skills_from_an_install_that_has_not_changed(
        self, monkeypatch
    ) -> None:
        """The new bundle is not in place yet, so refreshing now would copy the OLD
        skill tree over the recorded one. The finisher does it after the swap.
        """
        called: list[object] = []
        monkeypatch.setattr(
            skill_registry, "refresh_skills", lambda *a, **k: called.append(a) or []
        )
        result = CliRunner().invoke(cli, ["self-update"])
        assert result.exit_code == 0
        assert called == []

    def test_says_an_earlier_run_already_staged_it(self, monkeypatch):
        """Saying "Staged crm 3.0.0" here would read as "this run did it" — but this run
        declined to stage, and pointing at the earlier run is what explains the silence.
        """
        monkeypatch.setattr(
            update_mod,
            "perform_update",
            lambda *a, **k: {
                "updated": False,
                "pending": True,
                "reason": "swap-already-staged",
                "from_version": "2.9.0",
                "to_version": "3.0.0",
            },
        )
        result = CliRunner().invoke(cli, ["self-update"])
        assert result.exit_code == 0
        assert "already staged by an earlier run" in result.stdout


class TestFinisherEntry:
    """`--finish-update` — the hidden entry the detached finisher runs itself as."""

    def test_applies_the_handoff_and_reports_the_record(self, monkeypatch, tmp_path):
        handoff = tmp_path / "handoff.json"
        handoff.write_text("{}", encoding="utf-8")
        seen: list[tuple[object, object]] = []

        def fake_finish(path, *, refresh=None):
            seen.append((path, refresh))
            return {"ok": True, "to_version": "3.0.0", "warnings": []}

        monkeypatch.setattr(update_mod, "finish_deferred_swap", fake_finish)

        result = CliRunner().invoke(cli, ["--json", "self-update", "--finish-update", str(handoff)])

        assert result.exit_code == 0
        assert json.loads(result.stdout)["data"]["to_version"] == "3.0.0"
        path, refresh = seen[0]
        assert path == handoff
        assert refresh is not None, "the finisher must re-sync skills/completion after the swap"

    def test_a_failed_swap_exits_nonzero(self, monkeypatch, tmp_path):
        handoff = tmp_path / "handoff.json"
        handoff.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(
            update_mod,
            "finish_deferred_swap",
            lambda path, **k: {"ok": False, "error": "locked", "warnings": []},
        )

        result = CliRunner().invoke(cli, ["--json", "self-update", "--finish-update", str(handoff)])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert "locked" in payload["error"]

    def test_never_reaches_the_upgrade_path(self, monkeypatch, tmp_path):
        """It runs as the staged build; re-entering the upgrade would download again."""
        handoff = tmp_path / "handoff.json"
        handoff.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(
            update_mod, "finish_deferred_swap", lambda path, **k: {"ok": True, "warnings": []}
        )

        def unreachable(*a, **k):
            raise AssertionError("the finisher must not run an update")

        monkeypatch.setattr(update_mod, "perform_update", unreachable)
        monkeypatch.setattr(update_mod, "check_for_update", unreachable)

        result = CliRunner().invoke(cli, ["--json", "self-update", "--finish-update", str(handoff)])
        assert result.exit_code == 0


class TestFinisherRefresh:
    """What the finisher re-syncs once the new bundle is in place."""

    def test_reports_a_skill_refresh_failure_as_a_warning(self, monkeypatch, tmp_path):
        """A silent stale skill tree is what this guards against — the finisher has no
        console, so the failure has to travel to the next run as text.
        """
        skills = tmp_path / "crm" / "skills"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            skill_registry, "refresh_skills", lambda *a, **k: [{"status": "error", "error": "nope"}]
        )
        monkeypatch.setattr(completion_registry, "refresh_completion", lambda *a, **k: None)

        warnings = self_update_mod._finisher_refresh("3.0.0", tmp_path)

        assert warnings == ["Could not refresh the installed crm agent skill: nope"]

    def test_clean_refresh_reports_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(completion_registry, "refresh_completion", lambda *a, **k: None)

        assert self_update_mod._finisher_refresh("3.0.0", tmp_path) == []
