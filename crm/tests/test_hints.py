"""Tests for next-step hints (crm/core/hints.py) and their CLI wiring.

Next-step hints are show-once success-path guidance rendered in human/REPL mode
only (never in the JSON envelope). See issue #657 and CONTEXT.md "Next-step hint".
"""

# pyright: basic
from __future__ import annotations

import pytest
from click.testing import CliRunner

_WHOAMI = {
    "UserId": "00000000-0000-0000-0000-000000000001",
    "BusinessUnitId": "00000000-0000-0000-0000-0000000000bb",
    "OrganizationId": "00000000-0000-0000-0000-0000000000cc",
}


@pytest.fixture
def crm_home(isolated_home, monkeypatch):
    """Isolated CRM_HOME (via the suite's ``isolated_home``, which also disables
    .env autoload and snapshots the environment) with CRM_NO_HINTS cleared so a
    stray outer-env value can't skew the show-once assertions.
    """
    monkeypatch.delenv("CRM_NO_HINTS", raising=False)
    return isolated_home


class TestTakeHint:
    def test_fires_once_then_never_again(self, crm_home):
        from crm.core import hints

        first = hints.take_hint("profile_add")
        assert first is not None and first == hints.HINTS["profile_add"]
        assert hints.take_hint("profile_add") is None

    def test_unknown_id_returns_none(self, crm_home):
        from crm.core import hints

        assert hints.take_hint("not_a_real_hint") is None

    @pytest.mark.parametrize("value", ["1", "", "0", "false"])
    def test_disabled_env_suppresses_and_touches_no_store(self, crm_home, monkeypatch, value):
        from crm.core import hints

        # Any value — including an empty string a shell can set (CRM_NO_HINTS=) —
        # disables hints and writes nothing to the seen-store (issue #657).
        monkeypatch.setenv("CRM_NO_HINTS", value)
        assert hints.take_hint("profile_add") is None
        assert not hints._seen_path().exists()

    def test_corrupt_store_is_tolerated(self, crm_home):
        from crm.core import hints

        p = hints._seen_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not valid json", encoding="utf-8")
        # Neither reading the store nor firing a hint may raise.
        assert hints.load_seen() == set()
        assert hints.take_hint("profile_add") == hints.HINTS["profile_add"]

    def test_unwritable_store_does_not_crash(self, crm_home, monkeypatch, tmp_path):
        from crm.core import hints

        # A file where CRM_HOME's parent dir is expected → mkdir/write raises
        # OSError. A hint is optional UX and must never fail the command; the
        # text still returns, the failure is swallowed.
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("CRM_HOME", str(blocker / "nested"))
        assert hints.take_hint("profile_add") == hints.HINTS["profile_add"]


class TestContextHintGate:
    """CLIContext.hint applies the human/JSON/TTY gate around take_hint."""

    def _ctx(self):
        from crm.cli import CLIContext

        return CLIContext()

    def test_human_tty_prints_once(self, crm_home, monkeypatch, capsys):
        from crm.commands import _tty
        from crm.core import hints

        monkeypatch.setattr(_tty, "_stdout_is_tty", lambda: True)
        ctx = self._ctx()
        ctx.json_mode = False

        ctx.hint("profile_add")
        assert hints.HINTS["profile_add"] in capsys.readouterr().out
        ctx.hint("profile_add")
        assert hints.HINTS["profile_add"] not in capsys.readouterr().out

    def test_json_mode_never_fires_or_writes_store(self, crm_home, monkeypatch, capsys):
        from crm.commands import _tty
        from crm.core import hints

        monkeypatch.setattr(_tty, "_stdout_is_tty", lambda: True)
        ctx = self._ctx()
        ctx.json_mode = True

        ctx.hint("profile_add")
        assert capsys.readouterr().out == ""
        assert not hints._seen_path().exists()

    def test_non_tty_never_fires_or_writes_store(self, crm_home, monkeypatch, capsys):
        from crm.commands import _tty
        from crm.core import hints

        monkeypatch.setattr(_tty, "_stdout_is_tty", lambda: False)
        ctx = self._ctx()
        ctx.json_mode = False

        ctx.hint("profile_add")
        assert capsys.readouterr().out == ""
        assert not hints._seen_path().exists()


class TestProfileAddHintEndToEnd:
    """The acceptance-cited path: `crm profile add` through the CLI (issue #657)."""

    def _add_profile_args(self):
        return [
            "profile",
            "add",
            "--url",
            "https://crm.contoso.local/contoso",
            "--username",
            "alice",
            "--domain",
            "CONTOSO",
            "--password",
            "pw",
            "--name",
            "contoso",
            "--yes",
        ]

    def test_prints_hint_once_on_tty(self, crm_home, monkeypatch):
        import requests_mock

        from crm.cli import cli
        from crm.commands import _tty
        from crm.core import hints

        monkeypatch.setattr(_tty, "_stdout_is_tty", lambda: True)
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            first = runner.invoke(cli, self._add_profile_args())
            second = runner.invoke(cli, self._add_profile_args())
        assert first.exit_code == 0, first.output
        assert hints.HINTS["profile_add"] in first.output
        assert hints.HINTS["profile_add"] not in second.output

    def test_json_never_prints_hint(self, crm_home, monkeypatch):
        import requests_mock

        from crm.cli import cli
        from crm.commands import _tty
        from crm.core import hints

        monkeypatch.setattr(_tty, "_stdout_is_tty", lambda: True)
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, ["--json", *self._add_profile_args()])
        assert result.exit_code == 0, result.output
        assert hints.HINTS["profile_add"] not in result.output
        assert not hints._seen_path().exists()

    def test_crm_no_hints_suppresses(self, crm_home, monkeypatch):
        import requests_mock

        from crm.cli import cli
        from crm.commands import _tty
        from crm.core import hints

        monkeypatch.setattr(_tty, "_stdout_is_tty", lambda: True)
        monkeypatch.setenv("CRM_NO_HINTS", "1")
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, self._add_profile_args())
        assert result.exit_code == 0, result.output
        assert hints.HINTS["profile_add"] not in result.output
        assert not hints._seen_path().exists()
