# pyright: basic
"""Command-level tests for `crm self-update`."""
from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

import crm.core.update as update_mod
from crm.cli import cli


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path):
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(tmp_path / ".crm")
    os.environ["CRM_DOTENV"] = str(tmp_path / "noop.env")
    # Never let the passive notice fire during these tests.
    os.environ["CRM_NO_UPDATE_CHECK"] = "1"
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


class TestCheck:
    """--check reports versions without modifying anything, both output modes."""

    def test_human(self, monkeypatch):
        monkeypatch.setattr(
            update_mod, "check_for_update",
            lambda *a, **k: {"current": "2.9.0", "latest": "v3.0.0", "update_available": True},
        )
        result = CliRunner().invoke(cli, ["self-update", "--check"])
        assert result.exit_code == 0
        assert "3.0.0" in result.output

    def test_json_envelope(self, monkeypatch):
        monkeypatch.setattr(
            update_mod, "check_for_update",
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


class TestPipInstall:
    """Non-frozen install must not touch the filesystem; points at pip."""

    def test_directs_to_pip(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        called = {"perform": 0}
        monkeypatch.setattr(
            update_mod, "perform_update",
            lambda *a, **k: called.__setitem__("perform", called["perform"] + 1),
        )
        result = CliRunner().invoke(cli, ["self-update"])
        assert result.exit_code == 0
        assert "pip install -U" in result.output
        assert called["perform"] == 0


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


class TestNoticeSuppressedForSelfUpdate:
    """The passive upgrade notice must not fire after `self-update` runs — the running
    process still reports the pre-update version, so the notice would tell the user to
    upgrade to the version they just installed."""

    @pytest.fixture
    def _force_eligible(self, monkeypatch):
        import crm.cli as cli_mod
        monkeypatch.setattr(cli_mod, "_update_check_eligible", lambda *a, **k: True)
        calls = []
        monkeypatch.setattr(update_mod, "emit_pending_notice", lambda *a, **k: calls.append(k))
        return calls

    def test_self_update_does_not_emit_notice(self, monkeypatch, _force_eligible):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        monkeypatch.setattr(update_mod, "perform_update", lambda *a, **k: None)
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

        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
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
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)

        def boom(*a, **k):
            raise PermissionError("registry unreadable")

        monkeypatch.setattr("crm.commands.skill_registry.refresh_skills", boom)
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        skills = json.loads(result.output)["data"]["skills"]
        assert any(s.get("status") == "error" for s in skills)

    def test_check_does_not_touch_skills(self, monkeypatch):
        spy = {"calls": 0}
        monkeypatch.setattr(update_mod, "check_for_update",
                            lambda *a, **k: {"current": "2.9.0", "latest": "v3.0.0", "update_available": True})
        monkeypatch.setattr("crm.commands.skill_registry.refresh_skills",
                            lambda *a, **k: spy.__setitem__("calls", spy["calls"] + 1))
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
        monkeypatch.setattr(update_mod, "perform_update",
                            lambda *a, **k: {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"})
        seen = {}
        monkeypatch.setattr("crm.commands.skill_registry.refresh_skills",
                            lambda version, src: seen.update(version=version) or [])
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        assert seen["version"] == "3.0.0"  # to_version, already v-stripped — not the old running version

    def test_frozen_up_to_date_uses_current_version(self, monkeypatch, tmp_path):
        install = tmp_path / "crm"
        skills = install / "_internal" / "crm" / "skills"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("SAME", encoding="utf-8")
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(update_mod, "install_dir", lambda: install)
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        monkeypatch.setattr(update_mod, "perform_update",
                            lambda *a, **k: {"updated": False, "current": "3.0.0", "latest": "v3.0.0",
                                             "reason": "up-to-date"})
        seen = {}
        monkeypatch.setattr("crm.commands.skill_registry.refresh_skills",
                            lambda version, src: seen.update(version=version) or [])
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        assert seen.get("version") == update_mod.current_version()  # never ""


class TestCompletionRefresh:
    """Non-`--check` self-update re-syncs a CLI-installed completion script; absent a
    marker it leaves completion untouched, and a refresh failure never fails the update."""

    def _seed_marker(self, shell="zsh", version="0.0.1"):
        from crm.commands import completion_registry as creg

        path = creg.default_script_path(shell)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("OLD", encoding="utf-8")
        creg.write_marker(shell, str(path), version)
        return path

    def test_pip_path_refreshes_recorded_completion(self, monkeypatch):
        from crm.commands import completion_registry as creg

        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
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

        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        assert "completion" not in json.loads(result.output)["data"]
        assert creg.read_marker() is None

    def test_refresh_failure_does_not_fail_update(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
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
        monkeypatch.setattr(update_mod, "perform_update",
                            lambda *a, **k: {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"})
        path = self._seed_marker(version="2.9.0")
        seen = {}

        def fake_via_binary(shell, binary):
            seen["shell"], seen["binary"] = shell, binary
            return "#compdef crm\n# regenerated by NEW binary\n"

        # The frozen branch must use the new binary, NOT the in-process (old code) renderer.
        monkeypatch.setattr("crm.commands.completion_registry.generate_via_binary", fake_via_binary)
        monkeypatch.setattr("crm.commands.completion_registry.generate_source",
                            lambda *a, **k: pytest.fail("frozen refresh must not use in-process renderer"))
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0
        assert json.loads(result.output)["data"]["completion"]["status"] == "refreshed"
        assert "regenerated by NEW binary" in path.read_text(encoding="utf-8")
        assert seen["shell"] == "zsh"

    def test_malformed_marker_does_not_crash_human_update(self, monkeypatch):
        # A hand-edited marker with a non-string script_path must not crash the
        # human-mode status emit and break self-update's never-raise guarantee.
        from crm.commands import completion_registry as creg

        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        creg.marker_path().write_text(
            json.dumps({"shell": "zsh", "script_path": 123, "installed_version": "0.0.1"}),
            encoding="utf-8",
        )
        result = CliRunner().invoke(cli, ["self-update"])  # human mode
        assert result.exit_code == 0

    def test_check_does_not_touch_completion(self, monkeypatch):
        spy = {"calls": 0}
        monkeypatch.setattr(update_mod, "check_for_update",
                            lambda *a, **k: {"current": "2.9.0", "latest": "v3.0.0", "update_available": True})
        self._seed_marker(version="0.0.1")
        monkeypatch.setattr("crm.commands.completion_registry.generate_source",
                            lambda *a, **k: spy.__setitem__("calls", spy["calls"] + 1))
        result = CliRunner().invoke(cli, ["--json", "self-update", "--check"])
        assert result.exit_code == 0
        assert spy["calls"] == 0


class TestFrozenUpdate:
    """Frozen install runs the swap; surfaces a clean error on failure."""

    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm"))
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        monkeypatch.setattr(
            update_mod, "perform_update",
            lambda *a, **k: {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"},
        )
        result = CliRunner().invoke(cli, ["self-update"])
        assert result.exit_code == 0
        assert "3.0.0" in result.output

    def test_progress_shown_in_human_mode(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm"))
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)
        captured: list[str] = []

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
        monkeypatch.setattr(update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm"))
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)

        def fake_update(*a, progress=None, **k):
            assert progress is None, "progress callback must not be set in json mode"
            return {"updated": True, "from_version": "2.9.0", "to_version": "3.0.0"}

        monkeypatch.setattr(update_mod, "perform_update", fake_update)
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 0

    def test_checksum_failure_exits_nonzero(self, monkeypatch):
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        monkeypatch.setattr(update_mod, "install_dir", lambda: __import__("pathlib").Path("/tmp/crm"))
        monkeypatch.setattr(update_mod, "cleanup_stale_updates", lambda *a, **k: None)

        def boom(*a, **k):
            raise update_mod.UpdateError("Checksum mismatch; install left untouched.")

        monkeypatch.setattr(update_mod, "perform_update", boom)
        result = CliRunner().invoke(cli, ["--json", "self-update"])
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False
