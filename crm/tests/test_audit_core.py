# pyright: basic
"""Tests for crm/core/audit.py — append-only JSONL mutation journal."""

from __future__ import annotations

import builtins
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crm.core.audit import _extract_result_id, read, record

FIXED_TS = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
SESSION = "test-session-abc"


@pytest.fixture()
def crm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CRM_HOME at a temp dir so real ~/.crm is never touched."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path))
    return tmp_path


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

    assert set(row.keys()) == {
        "ts",
        "profile",
        "command",
        "target",
        "solution",
        "staged",
        "dry_run",
        "ok",
        "result_id",
    }

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
        result={
            "id": "11111111-2222-3333-4444-555555555555",
            "name": "Acme",
            "secret": "top-secret",
        },
        now=FIXED_TS,
    )
    rows = read(SESSION)
    row = rows[0]
    assert "name" not in row
    assert "secret" not in row
    assert "result" not in row
    assert "payload" not in row


def test_append_two_records(crm_home: Path) -> None:
    record(session=SESSION, profile="p1", command="cmd1", target="t1", result=None, now=FIXED_TS)
    record(session=SESSION, profile="p2", command="cmd2", target="t2", result=None, now=FIXED_TS)
    rows = read(SESSION)
    assert len(rows) == 2
    assert rows[0]["command"] == "cmd1"
    assert rows[1]["command"] == "cmd2"


def test_dry_run_tagged(crm_home: Path) -> None:
    record(
        session=SESSION,
        profile=None,
        command="entity delete",
        target="account",
        result=None,
        dry_run=True,
        now=FIXED_TS,
    )
    rows = read(SESSION)
    assert rows[0]["dry_run"] is True


def test_extract_id_plain(crm_home: Path) -> None:
    guid = "11111111-2222-3333-4444-555555555555"
    assert _extract_result_id({"id": guid}) == guid


def test_extract_id_truthy_check(crm_home: Path) -> None:
    result = _extract_result_id({"id": ""})
    assert result is None


def test_extract_id_guid_in_accountid(crm_home: Path) -> None:
    guid = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
    result = _extract_result_id({"accountid": guid})
    assert result == guid


def test_extract_id_no_id_like_key_returns_none(crm_home: Path) -> None:
    assert _extract_result_id({"name": "Acme", "code": "ABC"}) is None


@pytest.mark.parametrize("non_dict", [None, ["a", "b"]])
def test_extract_id_non_dict_returns_none(non_dict: object) -> None:
    assert _extract_result_id(non_dict) is None


def test_extract_id_non_guid_value_returns_none(crm_home: Path) -> None:
    assert _extract_result_id({"accountid": "not-a-guid"}) is None


def test_extract_id_guid_without_hyphens(crm_home: Path) -> None:
    guid_nohyphen = "11111111222233334444555555555555"
    result = _extract_result_id({"contactid": guid_nohyphen})
    assert result == guid_nohyphen


def test_read_missing_session_returns_empty(crm_home: Path) -> None:
    rows = read("nonexistent-session")
    assert rows == []


def test_tail_returns_last_n(crm_home: Path) -> None:
    for i in range(5):
        record(
            session=SESSION, profile=None, command=f"cmd{i}", target=None, result=None, now=FIXED_TS
        )
    rows = read(SESSION, tail=3)
    assert len(rows) == 3
    assert rows[0]["command"] == "cmd2"
    assert rows[1]["command"] == "cmd3"
    assert rows[2]["command"] == "cmd4"


def test_tail_larger_than_file_returns_all(crm_home: Path) -> None:
    for i in range(3):
        record(
            session=SESSION, profile=None, command=f"cmd{i}", target=None, result=None, now=FIXED_TS
        )
    rows = read(SESSION, tail=100)
    assert len(rows) == 3


def test_malformed_line_skipped(crm_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record(session=SESSION, profile=None, command="good1", target=None, result=None, now=FIXED_TS)
    from crm.core.audit import _journal_path

    path = _journal_path(SESSION)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines.insert(0, "NOT VALID JSON }{{\n")
    path.write_text("".join(lines), encoding="utf-8")

    rows = read(SESSION)
    assert len(rows) == 1
    assert rows[0]["command"] == "good1"


def test_oserror_swallowed_when_home_is_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point CRM_HOME at an existing FILE so audit/ mkdir fails → OSError swallowed."""
    fake_home = tmp_path / "notadir"
    fake_home.write_text("I am a file", encoding="utf-8")
    monkeypatch.setenv("CRM_HOME", str(fake_home))
    record(session=SESSION, profile=None, command="cmd", target=None, result=None, now=FIXED_TS)


def test_oserror_swallowed_open_raises(crm_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch builtins.open to raise OSError; record() must not propagate it."""
    real_open = builtins.open

    def patched_open(file: object, *args: object, **kwargs: object) -> object:
        if isinstance(file, (str, Path)) and str(file).endswith(".jsonl"):
            raise OSError("simulated write failure")
        return real_open(file, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", patched_open)
    record(session=SESSION, profile=None, command="cmd", target=None, result=None, now=FIXED_TS)


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
    expected_keys = [
        "ts",
        "profile",
        "command",
        "target",
        "solution",
        "staged",
        "dry_run",
        "ok",
        "result_id",
    ]
    assert list(parsed.keys()) == expected_keys


def test_tail_zero_returns_empty(crm_home: Path) -> None:
    """``tail <= 0`` is no rows, not all rows via ``rows[-0:]``."""
    for i in range(3):
        record(
            session=SESSION, profile=None, command=f"cmd{i}", target=None, result=None, now=FIXED_TS
        )
    assert read(SESSION, tail=0) == []


def test_tail_negative_returns_empty(crm_home: Path) -> None:
    for i in range(3):
        record(
            session=SESSION, profile=None, command=f"cmd{i}", target=None, result=None, now=FIXED_TS
        )
    assert read(SESSION, tail=-2) == []


def test_session_with_path_separators_confined_to_audit_dir(crm_home: Path) -> None:
    """A user-controlled session name cannot escape the audit directory."""
    from crm.core.audit import _audit_root, _journal_path

    for evil in ("/tmp/pwn", "../../etc/pwn", "a/b/c"):
        path = _journal_path(evil)
        assert path.parent == _audit_root()


def test_evil_session_roundtrips_within_audit_dir(crm_home: Path) -> None:
    """Hermetic: asserts the journal lands inside the audit dir, not a real ``/tmp`` probe."""
    from crm.core.audit import _audit_root, _journal_path

    record(
        session="/tmp/pwn",
        profile=None,
        command="entity create",
        target="accounts",
        result={"id": "x"},
        now=FIXED_TS,
    )
    written = _journal_path("/tmp/pwn")
    assert written.parent == _audit_root()
    assert written.is_file()
    assert [p.name for p in _audit_root().iterdir()] == [written.name]
    rows = read("/tmp/pwn")
    assert len(rows) == 1
    assert rows[0]["command"] == "entity create"


def test_safe_session_maps_all_dots_to_default(crm_home: Path) -> None:
    from crm.core.audit import _safe_session

    assert _safe_session("..") == "default"
    assert _safe_session(".") == "default"
    assert _safe_session("my-session") == "my-session"
