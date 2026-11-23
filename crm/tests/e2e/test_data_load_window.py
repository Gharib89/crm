# pyright: basic
"""E2E tests for `data load-window begin` and `end`.

Works on both targets. Each test publishes a throwaway duplicate rule on
`account`, so the window always holds at least one component this test owns.
`begin` also turns off whatever other custom steps, classic workflows and
published rules the org has on `account`, so every test's teardown runs `end` on
its state file before deleting the rule: the org is left as it was found.
"""

from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


def _ok(result, what):
    assert result.returncode == 0, f"{what} failed:\n{result.stderr}\nstdout: {result.stdout}"
    env = json.loads(result.stdout)
    assert env["ok"], env
    return env["data"]


def _rule_state(backend, rid):
    return backend.get(f"duplicaterules({rid})", params={"$select": "statecode"})["statecode"]


@pytest.fixture
def published_rule(backend, cli, request, unique, ephemeral_solution):
    """A published name-match duplicate rule on account, deleted at teardown."""
    data = _ok(
        cli(
            [
                "--json",
                "dup",
                "create",
                "account",
                "--name",
                f"E2E LoadWindow {unique}",
                "--solution",
                ephemeral_solution,
            ]
        ),
        "dup create",
    )
    rid = data["duplicateruleid"]

    def _cleanup():
        try:
            backend.post("UnpublishDuplicateRule", json_body={"DuplicateRuleId": rid})
        except Exception:
            pass
        try:
            backend.delete(f"duplicaterules({rid})")
        except Exception:
            pass

    request.addfinalizer(_cleanup)
    _ok(
        cli(
            [
                "--json",
                "dup",
                "add-condition",
                rid,
                "--attr",
                "name",
                "--operator",
                "exact",
                "--solution",
                ephemeral_solution,
            ]
        ),
        "dup add-condition",
    )
    _ok(cli(["--json", "dup", "publish", rid, "--wait"]), "dup publish")
    assert _rule_state(backend, rid) == 1
    return rid


@pytest.fixture
def state_file(tmp_path, cli, request, published_rule):
    """The window's state file; teardown restores from it before the rule goes."""
    path = tmp_path / "window.json"

    def _restore():
        if path.exists():
            cli(["--json", "data", "load-window", "end", str(path), "--yes"], check=False)

    request.addfinalizer(_restore)
    return path


def _component(data, rid):
    return next(c for c in data["components"] if c["id"] == rid)


@covers("data load-window begin")
@pytest.mark.slow
def test_load_window_begin(backend, cli, published_rule, state_file):
    """Dry-run previews and writes nothing; begin records and turns off the rule.

    No Microsoft-signed step is ever selected; a custom one, if the org has one,
    is recorded like the rule.
    """
    preview = _ok(
        cli(
            [
                "--json",
                "--dry-run",
                "data",
                "load-window",
                "begin",
                str(state_file),
                "--table",
                "account",
            ]
        ),
        "load-window begin --dry-run",
    )
    assert _component(preview, published_rule)["outcome"] == "would_turn_off"
    assert not state_file.exists()
    assert _rule_state(backend, published_rule) == 1

    data = _ok(
        cli(
            [
                "--json",
                "data",
                "load-window",
                "begin",
                str(state_file),
                "--table",
                "account",
                "--yes",
            ]
        ),
        "load-window begin",
    )
    assert data["counts"]["failed"] == 0, data
    assert _component(data, published_rule)["outcome"] == "turned_off"
    assert _rule_state(backend, published_rule) == 0

    recorded = json.loads(state_file.read_text(encoding="utf-8"))
    assert recorded["org"]["organization_id"]
    assert _component(recorded, published_rule)["outcome"] == "turned_off"
    assert all(c["outcome"] == "turned_off" for c in recorded["components"]), recorded


@covers("data load-window end")
@pytest.mark.slow
def test_load_window_end(backend, cli, published_rule, state_file):
    """End restores what begin turned off, and a re-run skips what is active."""
    _ok(
        cli(
            [
                "--json",
                "data",
                "load-window",
                "begin",
                str(state_file),
                "--table",
                "account",
                "--yes",
            ]
        ),
        "load-window begin",
    )
    assert _rule_state(backend, published_rule) == 0

    data = _ok(cli(["--json", "data", "load-window", "end", str(state_file), "--yes"]), "end")
    assert data["counts"]["failed"] == 0, data
    assert _component(data, published_rule)["outcome"] == "restored"
    assert _rule_state(backend, published_rule) == 1

    again = _ok(
        cli(["--json", "data", "load-window", "end", str(state_file), "--yes"]), "end re-run"
    )
    assert again["counts"]["restored"] == 0, again
    assert (
        again["counts"]["already_active"]
        == len(again["components"]) - again["counts"]["not_turned_off"]
    )
