# pyright: basic
"""Tests for crm/core/audit.py — append-only JSONL mutation journal."""

from __future__ import annotations

import builtins
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from crm.core.audit import _extract_result_id, read, record


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_TS = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
SESSION = "test-session-abc"


@pytest.fixture()
def crm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CRM_HOME at a temp dir so real ~/.crm is never touched."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# record → read roundtrip
# ---------------------------------------------------------------------------


def test_record_read_roundtrip(crm_home: Path) -> None:
    record(
        session=SESSION,
        profile="cloud",
        command="entity create",
        target="account",
        result={"id": "11111111-2222-3333-4444-555555555555"},
        solution="MySolution",
        staged=False,
        dry_run=False,
        ok=True,
        now=FIXED_TS,
    )
    rows = read(SESSION)
    assert len(rows) == 1
    row = rows[0]

    # Exact key set
    assert set(row.keys()) == {"ts", "profile", "command", "target", "solution", "staged", "dry_run", "ok", "result_id"}

    # Values
    assert row["ts"] == FIXED_TS.isoformat()
    assert row["profile"] == "cloud"
    assert row["command"] == "entity create"
    assert row["target"] == "account"
    assert row["solution"] == "MySolution"
    assert row["staged"] is False
    assert row["dry_run"] is False
    assert row["ok"] is True
    assert row["result_id"] == "11111111-2222-3333-4444-555555555555"


def test_no_payload_stored(crm_home: Path) -> None:
    """The request payload must never appear in the journal."""
    record(
        session=SESSION,
        profile="cloud",
        command="entity create",
        target="account",
        result={"id": "11111111-2222-3333-4444-555555555555", "name": "Acme", "secret": "top-secret"},
        now=FIXED_TS,
    )
    rows = read(SESSION)
    row = rows[0]
    # None of the payload keys should be present
    assert "name" not in row
    assert "secret" not in row
    assert "result" not in row
    assert "payload" not in row


# ---------------------------------------------------------------------------
# Append: two records produce two lines
# ---------------------------------------------------------------------------


def test_append_two_records(crm_home: Path) -> None:
    record(session=SESSION, profile="p1", command="cmd1", target="t1", result=None, now=FIXED_TS)
    record(session=SESSION, profile="p2", command="cmd2", target="t2", result=None, now=FIXED_TS)
    rows = read(SESSION)
    assert len(rows) == 2
    assert rows[0]["command"] == "cmd1"
    assert rows[1]["command"] == "cmd2"


def test_append_preserves_first_line(crm_home: Path) -> None:
    record(session=SESSION, profile="p1", command="first", target=None, result=None, now=FIXED_TS)
    record(session=SESSION, profile="p2", command="second", target=None, result=None, now=FIXED_TS)
    rows = read(SESSION)
    assert rows[0]["command"] == "first"


# ---------------------------------------------------------------------------
# dry_run flag
# ---------------------------------------------------------------------------


def test_dry_run_tagged(crm_home: Path) -> None:
    record(session=SESSION, profile=None, command="entity delete", target="account", result=None, dry_run=True, now=FIXED_TS)
    rows = read(SESSION)
    assert rows[0]["dry_run"] is True


# ---------------------------------------------------------------------------
# _extract_result_id
# ---------------------------------------------------------------------------


def test_extract_id_plain(crm_home: Path) -> None:
    guid = "11111111-2222-3333-4444-555555555555"
    assert _extract_result_id({"id": guid}) == guid


def test_extract_id_truthy_check(crm_home: Path) -> None:
    # empty string is falsy — should NOT be returned as id
    result = _extract_result_id({"id": ""})
    assert result is None


def test_extract_id_guid_in_accountid(crm_home: Path) -> None:
    guid = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
    # No "id" key, but "accountid" holds a GUID-ish value
    result = _extract_result_id({"accountid": guid})
    assert result == guid


def test_extract_id_no_id_like_key_returns_none(crm_home: Path) -> None:
    assert _extract_result_id({"name": "Acme", "code": "ABC"}) is None


def test_extract_id_list_returns_none(crm_home: Path) -> None:
    assert _extract_result_id(["a", "b"]) is None


def test_extract_id_none_returns_none(crm_home: Path) -> None:
    assert _extract_result_id(None) is None


def test_extract_id_non_guid_value_returns_none(crm_home: Path) -> None:
    # key ends with "id" but value is not GUID-ish
    assert _extract_result_id({"accountid": "not-a-guid"}) is None


def test_extract_id_guid_without_hyphens(crm_home: Path) -> None:
    # GUID without hyphens should still match
    guid_nohyphen = "11111111222233334444555555555555"
    result = _extract_result_id({"contactid": guid_nohyphen})
    assert result == guid_nohyphen


# ---------------------------------------------------------------------------
# read — missing file returns []
# ---------------------------------------------------------------------------


def test_read_missing_session_returns_empty(crm_home: Path) -> None:
    rows = read("nonexistent-session")
    assert rows == []


# ---------------------------------------------------------------------------
# tail parameter
# ---------------------------------------------------------------------------


def test_tail_returns_last_n(crm_home: Path) -> None:
    for i in range(5):
        record(session=SESSION, profile=None, command=f"cmd{i}", target=None, result=None, now=FIXED_TS)
    rows = read(SESSION, tail=3)
    assert len(rows) == 3
    assert rows[0]["command"] == "cmd2"
    assert rows[1]["command"] == "cmd3"
    assert rows[2]["command"] == "cmd4"


def test_tail_larger_than_file_returns_all(crm_home: Path) -> None:
    for i in range(3):
        record(session=SESSION, profile=None, command=f"cmd{i}", target=None, result=None, now=FIXED_TS)
    rows = read(SESSION, tail=100)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# Malformed line is skipped
# ---------------------------------------------------------------------------


def test_malformed_line_skipped(crm_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record(session=SESSION, profile=None, command="good1", target=None, result=None, now=FIXED_TS)
    # Manually inject a corrupt line in the middle
    from crm.core.audit import _journal_path
    path = _journal_path(SESSION)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines.insert(0, "NOT VALID JSON }{{\n")  # prepend malformed
    path.write_text("".join(lines), encoding="utf-8")

    rows = read(SESSION)
    # Only the valid line should be returned
    assert len(rows) == 1
    assert rows[0]["command"] == "good1"


# ---------------------------------------------------------------------------
# OSError is swallowed
# ---------------------------------------------------------------------------


def test_oserror_swallowed_when_home_is_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point CRM_HOME at an existing FILE so audit/ mkdir fails → OSError swallowed."""
    fake_home = tmp_path / "notadir"
    fake_home.write_text("I am a file", encoding="utf-8")
    monkeypatch.setenv("CRM_HOME", str(fake_home))
    # Must not raise
    record(session=SESSION, profile=None, command="cmd", target=None, result=None, now=FIXED_TS)


def test_oserror_swallowed_open_raises(crm_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch builtins.open to raise OSError; record() must not propagate it."""
    real_open = builtins.open

    def patched_open(file: object, *args: object, **kwargs: object) -> object:
        if isinstance(file, (str, Path)) and str(file).endswith(".jsonl"):
            raise OSError("simulated write failure")
        return real_open(file, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", patched_open)
    # Must not raise
    record(session=SESSION, profile=None, command="cmd", target=None, result=None, now=FIXED_TS)


# ---------------------------------------------------------------------------
# Key order is preserved
# ---------------------------------------------------------------------------


def test_key_order_in_journal(crm_home: Path) -> None:
    record(
        session=SESSION,
        profile="cloud",
        command="entity update",
        target="contact",
        result=None,
        solution="Sol",
        staged=True,
        dry_run=False,
        ok=True,
        now=FIXED_TS,
    )
    from crm.core.audit import _journal_path
    raw_line = _journal_path(SESSION).read_text(encoding="utf-8").strip()
    parsed = json.loads(raw_line)
    expected_keys = ["ts", "profile", "command", "target", "solution", "staged", "dry_run", "ok", "result_id"]
    assert list(parsed.keys()) == expected_keys


# ---------------------------------------------------------------------------
# tail <= 0 is no rows (not all rows via rows[-0:])
# ---------------------------------------------------------------------------


def test_tail_zero_returns_empty(crm_home: Path) -> None:
    for i in range(3):
        record(session=SESSION, profile=None, command=f"cmd{i}", target=None, result=None, now=FIXED_TS)
    assert read(SESSION, tail=0) == []


def test_tail_negative_returns_empty(crm_home: Path) -> None:
    for i in range(3):
        record(session=SESSION, profile=None, command=f"cmd{i}", target=None, result=None, now=FIXED_TS)
    assert read(SESSION, tail=-2) == []


# ---------------------------------------------------------------------------
# A user-controlled session name cannot escape the audit directory
# ---------------------------------------------------------------------------


def test_session_with_path_separators_confined_to_audit_dir(crm_home: Path) -> None:
    from crm.core.audit import _audit_root, _journal_path

    for evil in ("/tmp/pwn", "../../etc/pwn", "a/b/c"):
        path = _journal_path(evil)
        # The journal stays a direct child of <CRM_HOME>/audit/, never elsewhere.
        assert path.parent == _audit_root()


def test_evil_session_roundtrips_within_audit_dir(crm_home: Path) -> None:
    record(session="/tmp/pwn", profile=None, command="entity create",
           target="accounts", result={"id": "x"}, now=FIXED_TS)
    # Nothing was written outside the audit dir...
    assert not Path("/tmp/pwn.jsonl").exists()
    # ...and the same (sanitized) session reads its own line back.
    rows = read("/tmp/pwn")
    assert len(rows) == 1
    assert rows[0]["command"] == "entity create"


def test_safe_session_maps_all_dots_to_default(crm_home: Path) -> None:
    from crm.core.audit import _safe_session

    assert _safe_session("..") == "default"
    assert _safe_session(".") == "default"
    assert _safe_session("my-session") == "my-session"  # ordinary names untouched
