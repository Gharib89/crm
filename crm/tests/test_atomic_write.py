"""Locked atomic-write helper — concurrency safety + create-with-mode (issue #687).

These cover the two secret-at-rest-hardening findings on the shared writer:

* Finding `session.py:208` — a deterministic temp name + a too-late lock let two
  concurrent crm processes collide on one temp file and corrupt state. The fix is
  a per-process-unique temp name and a lock held across the whole write-replace.
* Finding `session.py:130` — the `mode` seam lets secret-bearing files be *created*
  0600 (no create-then-chmod widen window).
"""
# pyright: basic
from __future__ import annotations

import json
import os
import threading

import pytest

from crm.core import session as session_mod


def test_write_succeeds_when_old_deterministic_tmp_name_is_occupied(tmp_path):
    # The old writer used a fixed "<name>.tmp"; occupying that exact name with a
    # directory would break any code still depending on it. The unique-name writer
    # ignores it and still writes cleanly.
    target = tmp_path / "state.json"
    (tmp_path / "state.json.tmp").mkdir()
    session_mod._atomic_write_json(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_concurrent_writers_never_corrupt_state(tmp_path):
    # Many writers hammering the same target must never leave a torn/partial file:
    # every read is complete, valid JSON written by exactly one writer.
    target = tmp_path / "state.json"
    session_mod._atomic_write_json(target, {"writer": -1, "n": 0})
    errors: list[Exception] = []

    def worker(wid: int) -> None:
        try:
            for i in range(60):
                session_mod._atomic_write_json(target, {"writer": wid, "n": i})
        except Exception as exc:  # pragma: no cover - failure path asserts below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    data = json.loads(target.read_text(encoding="utf-8"))
    assert set(data) == {"writer", "n"}
    # Every successful write consumes its temp file via the rename — none leak.
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]


@pytest.mark.skipif(os.name != "posix", reason="file-mode creation only enforced on POSIX")
def test_mode_is_applied_at_creation(tmp_path):
    target = tmp_path / "secret.json"
    session_mod._atomic_write_json(target, {"x": 1}, mode=0o600)
    assert (target.stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="file-mode creation only enforced on POSIX")
def test_default_mode_matches_plain_create(tmp_path):
    # Non-secret writes keep prior (umask-default) permissions — not tightened.
    ref = tmp_path / "ref"
    ref.write_text("x", encoding="utf-8")
    target = tmp_path / "plain.json"
    session_mod._atomic_write_json(target, {"x": 1})
    assert (target.stat().st_mode & 0o777) == (ref.stat().st_mode & 0o777)
