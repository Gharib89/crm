# pyright: basic
"""Regression tests for `_touch_session` best-effort persistence (issue #719).

A read-only command must not fail just because its optional session breadcrumb
(`current_entity_set` / `last_query`) cannot be written — e.g. under an
unwritable `CRM_HOME` or a read-only sandbox filesystem.
"""
from __future__ import annotations

import json
from typing import cast

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands._helpers import _touch_session

pytestmark = pytest.mark.usefixtures("isolated_home")


class _StubCtx:
    def __init__(self, session_name="test-session"):
        self.session_name = session_name


def _ctx(session_name="test-session") -> CLIContext:
    return cast(CLIContext, _StubCtx(session_name))


class TestTouchSessionHelper:
    def test_swallows_oserror_from_save(self, monkeypatch):
        def _boom(*_a, **_k):
            raise OSError(30, "Read-only file system")

        monkeypatch.setattr("crm.core.session.save_session", _boom)
        # Must not raise — best-effort persistence.
        _touch_session(_ctx(), "accounts",
                       last_query={"type": "odata", "filter": None})

    def test_persists_normally_when_writable(self):
        from crm.core import session as session_mod

        _touch_session(_ctx("sess-ok"), "accounts")
        state = session_mod.load_session("sess-ok")
        assert state["current_entity_set"] == "accounts"


class TestReadCommandSurvivesUnwritableSession:
    def test_query_stays_successful_when_session_save_fails(
        self, make_fake_backend, inject_backend, monkeypatch
    ):
        inject_backend(make_fake_backend(responses={"get": {
            "@odata.context": "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts",
            "value": [{"accountid": "00000000-0000-0000-0000-000000000001",
                       "name": "Contoso Ltd"}],
        }}))

        def _boom(*_a, **_k):
            raise OSError(30, "Read-only file system")

        monkeypatch.setattr("crm.core.session.save_session", _boom)

        result = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"][0]["accountid"] == "00000000-0000-0000-0000-000000000001"
