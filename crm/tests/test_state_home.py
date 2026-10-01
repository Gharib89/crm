"""The state home (GLOSSARY.md): one resolver and the state-file primitives (#995).

An unset or empty ``CRM_HOME`` means ``~/.crm`` for every piece of crm state. The
resolver creates nothing; each writer creates its own directory on write.
"""

# pyright: basic
from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.utils.d365_backend import ConnectionProfile


@pytest.fixture
def empty_crm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """``CRM_HOME=""``, a fake home directory, and the cwd moved to an empty dir."""
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CRM_HOME", "")
    monkeypatch.chdir(cwd)
    return home, cwd


def test_empty_crm_home_writes_every_state_file_under_home_dot_crm(empty_crm_home, tmp_path):
    from crm.commands import completion_registry, skill_registry
    from crm.core import audit, hints, metadata_cache, session, update
    from crm.utils.d365_backend import _oauth_cache_path

    home, cwd = empty_crm_home
    profile = ConnectionProfile(
        name="p1", url="https://crm.contoso.local/org", domain="D", username="u"
    )

    session.save_profile(profile)
    session.save_session({"active_profile": "p1"})
    hints.mark_seen("profile_add")
    audit.record(session="default", profile="p1", command="entity get", target=None, result=None)
    update.write_cache("1.0.0", now=0.0)
    metadata_cache.write_definitions(profile, [], now=0.0)
    completion_registry.write_marker("bash", "/x/crm.bash", "1.0.0")
    skill_registry.record_install("claude", str(tmp_path / "skills" / "crm"), "1.0.0")

    assert list(cwd.iterdir()) == []
    state = home / ".crm"
    for rel in (
        "profiles/p1.json",
        "sessions/default.json",
        "hints_seen.json",
        "audit/default.jsonl",
        "update-check.json",
        "cache/p1/entitydefs.json",
        "completion.json",
        "installed-skills.json",
    ):
        assert (state / rel).is_file(), rel
    assert _oauth_cache_path() == str(state / "msal_token_cache.json")


def test_empty_crm_home_cli_run_leaves_cwd_untouched(empty_crm_home):
    from crm.cli import cli

    home, cwd = empty_crm_home
    result = CliRunner().invoke(cli, ["--json", "profile", "list"])

    assert result.exit_code == 0, result.output
    assert list(cwd.iterdir()) == []
    # A read creates no directory, not even the state home itself.
    assert not (home / ".crm").exists()


# ── The leaf module, directly ───────────────────────────────────────────


@pytest.mark.parametrize("value", [None, ""])
def test_unset_and_empty_crm_home_both_mean_home_dot_crm(value, tmp_path, monkeypatch):
    from crm.core.state_home import state_home

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    if value is None:
        monkeypatch.delenv("CRM_HOME", raising=False)
    else:
        monkeypatch.setenv("CRM_HOME", value)

    assert state_home() == tmp_path / ".crm"


def test_crm_home_tilde_is_expanded(tmp_path, monkeypatch):
    from crm.core.state_home import state_home

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CRM_HOME", "~/elsewhere")

    assert state_home() == tmp_path / "elsewhere"


def test_resolving_creates_no_directory(tmp_path, monkeypatch):
    from crm.core.state_home import state_home

    monkeypatch.setenv("CRM_HOME", str(tmp_path / "state"))

    assert state_home() == tmp_path / "state"
    assert not (tmp_path / "state").exists()


def test_atomic_write_creates_the_parent_directory(tmp_path):
    from crm.core.state_home import atomic_write

    target = tmp_path / "a" / "b" / "f.bin"
    atomic_write(target, b"\x00data")

    assert target.read_bytes() == b"\x00data"


def test_failed_write_keeps_the_old_file_and_leaves_no_temp(tmp_path, monkeypatch):
    from crm.core import state_home as state_home_mod

    target = tmp_path / "state.json"
    state_home_mod.atomic_write_json(target, {"a": 1})
    old = target.read_bytes()

    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(state_home_mod.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        state_home_mod.atomic_write_json(target, {"a": 2})

    assert target.read_bytes() == old
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
@pytest.mark.parametrize("lock", [False, True])
def test_mode_is_applied_from_the_first_byte(tmp_path, monkeypatch, lock):
    from crm.core import state_home as state_home_mod

    target = tmp_path / "secret.json"
    modes: list[int] = []
    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        # The temp file is still being written: its mode is already final.
        modes.append(os.fstat(fd).st_mode & 0o777)
        real_fsync(fd)

    monkeypatch.setattr(state_home_mod.os, "fsync", spy_fsync)
    state_home_mod.atomic_write(target, b"s3cret", mode=0o600, lock=lock)

    assert modes == [0o600]
    assert (target.stat().st_mode & 0o777) == 0o600


def test_read_json_returns_none_for_a_missing_file(tmp_path):
    from crm.core.state_home import read_json

    assert read_json(tmp_path / "absent.json") is None


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe\x00junk"])
def test_read_json_returns_none_for_corrupt_content(tmp_path, raw):
    from crm.core.state_home import read_json

    p = tmp_path / "bad.json"
    p.write_bytes(raw)

    assert read_json(p) is None


def test_read_json_round_trips_a_write(tmp_path):
    from crm.core.state_home import atomic_write_json, read_json

    p = tmp_path / "s.json"
    atomic_write_json(p, {"b": [1, 2], "a": None})

    assert read_json(p) == {"a": None, "b": [1, 2]}


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs POSIX perms and a non-root user"
)
def test_read_json_propagates_permission_error(tmp_path):
    from crm.core.state_home import read_json

    p = tmp_path / "locked.json"
    p.write_text("{}", encoding="utf-8")
    p.chmod(0)
    try:
        with pytest.raises(PermissionError):
            read_json(p)
    finally:
        p.chmod(0o600)
