# pyright: basic
"""Tests for crm/core/update.py — version compare, update-check cache, self-update."""
from __future__ import annotations

from pathlib import Path

import pytest

import crm.core.update as update_mod
from crm.core.update import (
    compare_versions,
    fetch_latest_version,
    UpdateError,
    check_for_update,
    cleanup_stale_updates,
    emit_pending_notice,
    is_check_enabled,
    parse_sha256sums,
    pending_notice,
    perform_update,
    platform_archive,
    read_cache,
    refresh_cache,
    run_background_check,
    should_refresh,
    swap_bundle,
    verify_sha256,
    write_cache,
)


@pytest.fixture()
def crm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CRM_HOME", str(tmp_path))
    return tmp_path


class TestCompareVersions:
    """Tuple-int semver compare, tolerant of a leading `v`."""

    @pytest.mark.parametrize(
        "current,latest,expected_sign",
        [
            ("2.9.0", "2.9.1", -1),   # patch behind
            ("2.9.0", "2.10.0", -1),  # numeric, not lexical (10 > 9)
            ("v2.10.0", "2.9.0", 1),  # ahead, v-prefix tolerated on either side
            ("2.9.0", "v2.9.0", 0),   # equal despite v-prefix mismatch
            ("2.9.0", "3.0.0", -1),   # major behind
        ],
    )
    def test_sign(self, current: str, latest: str, expected_sign: int) -> None:
        result = compare_versions(current, latest)
        assert (result > 0) - (result < 0) == expected_sign


class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


class TestFetchLatestVersion:
    """GET <base>/latest/VERSION, trimmed; any error returns None (fail-silent)."""

    def test_success_hits_latest_version_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, object] = {}

        def fake_get(url: str, timeout: float | None = None, **kw: object) -> _Resp:
            seen["url"] = url
            seen["timeout"] = timeout
            return _Resp("v3.1.4\n")

        monkeypatch.setattr(update_mod.requests, "get", fake_get)
        assert fetch_latest_version("https://r2.example/base") == "v3.1.4"
        assert seen["url"] == "https://r2.example/base/latest/VERSION"
        assert seen["timeout"] is not None  # a bounded timeout is always passed

    @pytest.mark.parametrize("exc", ["timeout", "conn", "http500"])
    def test_any_network_error_is_silent_none(
        self, monkeypatch: pytest.MonkeyPatch, exc: str
    ) -> None:
        import requests

        def fake_get(url: str, timeout: float | None = None, **kw: object) -> _Resp:
            if exc == "timeout":
                raise requests.Timeout("slow")
            if exc == "conn":
                raise requests.ConnectionError("dead endpoint")
            return _Resp("nope", status=500)

        monkeypatch.setattr(update_mod.requests, "get", fake_get)
        assert fetch_latest_version("https://dead.invalid") is None


_DAY = 86400.0


class TestCache:
    """<CRM_HOME>/update-check.json roundtrip + 24h throttle + atomic write."""

    def test_roundtrip(self, crm_home: Path) -> None:
        write_cache("v3.2.1", now=1000.0)
        cache = read_cache()
        assert cache is not None
        assert cache["latest"] == "v3.2.1"
        assert cache["checked_at"] == 1000.0

    def test_missing_cache_reads_none(self, crm_home: Path) -> None:
        assert read_cache() is None

    def test_corrupt_cache_reads_none(self, crm_home: Path) -> None:
        (crm_home / "update-check.json").write_text("{not json", encoding="utf-8")
        assert read_cache() is None

    def test_write_is_atomic_no_tmp_leftover(self, crm_home: Path) -> None:
        write_cache("v1.0.0", now=1.0)
        assert list(crm_home.glob("*.tmp*")) == []

    def test_should_refresh_when_no_cache(self, crm_home: Path) -> None:
        assert should_refresh(now=10_000.0) is True

    def test_should_not_refresh_within_ttl(self, crm_home: Path) -> None:
        write_cache("v3.2.1", now=10_000.0)
        assert should_refresh(now=10_000.0 + _DAY - 1) is False

    def test_should_refresh_after_ttl(self, crm_home: Path) -> None:
        write_cache("v3.2.1", now=10_000.0)
        assert should_refresh(now=10_000.0 + _DAY + 1) is True


class TestCheckEnabled:
    """Hard guards: human TTY only, never json/CI/opt-out."""

    def _enabled(
        self,
        *,
        json_mode: bool = False,
        stderr_isatty: bool = True,
        env: dict[str, str] | None = None,
    ) -> bool:
        return is_check_enabled(
            json_mode=json_mode,
            stderr_isatty=stderr_isatty,
            env=env if env is not None else {},
        )

    def test_enabled_on_human_tty(self) -> None:
        assert self._enabled() is True

    def test_disabled_under_json(self) -> None:
        assert self._enabled(json_mode=True) is False

    def test_disabled_when_stderr_not_tty(self) -> None:
        assert self._enabled(stderr_isatty=False) is False

    def test_disabled_when_ci_set(self) -> None:
        assert self._enabled(env={"CI": "true"}) is False

    def test_disabled_when_optout_set(self) -> None:
        assert self._enabled(env={"CRM_NO_UPDATE_CHECK": "1"}) is False


class TestPendingNotice:
    """Cache-only (no network): message iff cached latest is newer than current."""

    def test_none_when_no_cache(self, crm_home: Path) -> None:
        assert pending_notice(current="2.9.0") is None

    def test_none_when_up_to_date(self, crm_home: Path) -> None:
        write_cache("v2.9.0", now=1.0)
        assert pending_notice(current="2.9.0") is None

    def test_message_when_newer_frozen(self, crm_home: Path) -> None:
        write_cache("v3.0.0", now=1.0)
        msg = pending_notice(current="2.9.0", frozen=True)
        assert msg is not None
        assert "3.0.0" in msg and "self-update" in msg

    def test_message_when_newer_pip(self, crm_home: Path) -> None:
        write_cache("v3.0.0", now=1.0)
        msg = pending_notice(current="2.9.0", frozen=False)
        assert msg is not None
        assert "pip install -U" in msg


class TestRefreshCache:
    """Background-thread body (sync): probe + persist; silent on failure."""

    def test_writes_cache_on_success(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "v9.9.9")
        refresh_cache(now=555.0)
        cache = read_cache()
        assert cache is not None and cache["latest"] == "v9.9.9"
        assert cache["checked_at"] == 555.0

    def test_no_cache_write_on_network_failure(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: None)
        refresh_cache(now=555.0)
        assert read_cache() is None

    def test_write_error_is_swallowed_in_background(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # CRM_HOME on a read-only fs: the daemon thread must never raise.
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "v9.9.9")
        def boom(*a, **k):
            raise OSError("read-only filesystem")
        monkeypatch.setattr(update_mod, "write_cache", boom)
        refresh_cache(now=1.0)  # must not raise


class TestSha256Sums:
    """Mirror the install scripts' SHA256SUMS contract (two-space, CRLF-tolerant)."""

    def test_parse_picks_named_archive(self) -> None:
        body = (
            "aaa111  crm-linux-x86_64.tar.gz\r\n"
            "bbb222  crm-windows-x86_64.zip\n"
        )
        sums = parse_sha256sums(body)
        assert sums["crm-linux-x86_64.tar.gz"] == "aaa111"
        assert sums["crm-windows-x86_64.zip"] == "bbb222"

    def test_verify_matches_case_insensitively(self) -> None:
        data = b"hello world"
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        assert verify_sha256(data, digest.upper()) is True
        assert verify_sha256(data, digest) is True

    def test_verify_rejects_mismatch(self) -> None:
        assert verify_sha256(b"payload", "deadbeef") is False

    @pytest.mark.parametrize(
        "platform,expected",
        [("linux", "crm-linux-x86_64.tar.gz"), ("win32", "crm-windows-x86_64.zip")],
    )
    def test_platform_archive(
        self, monkeypatch: pytest.MonkeyPatch, platform: str, expected: str
    ) -> None:
        monkeypatch.setattr(update_mod.sys, "platform", platform)
        assert platform_archive() == expected


def _make_targz(files: dict[str, bytes]) -> bytes:
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestCheckForUpdate:
    """`--check` data: current, latest, update_available — no fs change."""

    def test_reports_update_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "v3.0.0")
        result = check_for_update()
        assert result == {
            "current": "2.9.0",
            "latest": "v3.0.0",
            "update_available": True,
        }

    def test_reports_up_to_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "v2.9.0")
        assert check_for_update()["update_available"] is False

    def test_raises_when_latest_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: None)
        with pytest.raises(UpdateError):
            check_for_update()


class TestSwapBundle:
    """Atomic in-place dir replacement; old bundle removed (posix) or parked (win)."""

    def test_posix_swap_replaces_contents(self, tmp_path: Path) -> None:
        install = tmp_path / "crm"
        install.mkdir()
        (install / "crm").write_text("OLD", encoding="utf-8")
        new = tmp_path / "staged"
        new.mkdir()
        (new / "crm").write_text("NEW", encoding="utf-8")

        swap_bundle(install, new, windows=False)

        assert (install / "crm").read_text(encoding="utf-8") == "NEW"
        # no leftover staging/old dirs
        assert not new.exists()
        assert list(tmp_path.glob("*.old*")) == []

    def test_windows_swap_parks_old_for_later(self, tmp_path: Path) -> None:
        install = tmp_path / "crm"
        install.mkdir()
        (install / "crm.exe").write_text("OLD", encoding="utf-8")
        new = tmp_path / "staged"
        new.mkdir()
        (new / "crm.exe").write_text("NEW", encoding="utf-8")

        swap_bundle(install, new, windows=True)

        assert (install / "crm.exe").read_text(encoding="utf-8") == "NEW"
        parked = list(tmp_path.glob("crm.old*"))
        assert len(parked) == 1  # running bundle parked, cleaned next run

    def test_cleanup_removes_parked(self, tmp_path: Path) -> None:
        install = tmp_path / "crm"
        install.mkdir()
        (tmp_path / "crm.old-123").mkdir()
        cleanup_stale_updates(install)
        assert list(tmp_path.glob("crm.old*")) == []


class TestPerformUpdate:
    """Full download → verify → swap, driven against a tmp install dir."""

    def _wire(
        self, monkeypatch: pytest.MonkeyPatch, archive: bytes, sums: dict[str, str]
    ) -> None:
        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "v3.0.0")
        monkeypatch.setattr(update_mod, "_download_archive", lambda *a, **k: archive)
        monkeypatch.setattr(update_mod, "_fetch_sha256sums", lambda *a, **k: sums)
        monkeypatch.setattr(update_mod.sys, "platform", "linux")

    def test_happy_path_swaps_bundle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hashlib

        archive = _make_targz({"crm": b"NEW-BINARY", "lib.so": b"x"})
        sums = {"crm-linux-x86_64.tar.gz": hashlib.sha256(archive).hexdigest()}
        self._wire(monkeypatch, archive, sums)

        install = tmp_path / "crm"
        install.mkdir()
        (install / "crm").write_text("OLD", encoding="utf-8")

        result = perform_update(install_dir=install)

        assert result["updated"] is True
        assert (install / "crm").read_bytes() == b"NEW-BINARY"
        assert (install / "lib.so").exists()

    def test_checksum_mismatch_leaves_install_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive = _make_targz({"crm": b"NEW-BINARY"})
        self._wire(monkeypatch, archive, {"crm-linux-x86_64.tar.gz": "deadbeef"})

        install = tmp_path / "crm"
        install.mkdir()
        (install / "crm").write_text("OLD", encoding="utf-8")

        with pytest.raises(UpdateError, match="(?i)checksum"):
            perform_update(install_dir=install)

        assert (install / "crm").read_text(encoding="utf-8") == "OLD"
        assert not list(tmp_path.glob("*.new*"))

    def test_up_to_date_makes_no_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "v2.9.0")
        install = tmp_path / "crm"
        install.mkdir()
        (install / "crm").write_text("OLD", encoding="utf-8")

        result = perform_update(install_dir=install)

        assert result["updated"] is False
        assert (install / "crm").read_text(encoding="utf-8") == "OLD"

    def test_download_network_error_becomes_update_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import requests
        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "v3.0.0")
        monkeypatch.setattr(update_mod.sys, "platform", "linux")

        def boom(url, **kw):
            raise requests.ConnectionError("dead")
        monkeypatch.setattr(update_mod.requests, "get", boom)

        install = tmp_path / "crm"; install.mkdir()
        (install / "crm").write_text("OLD", encoding="utf-8")
        with pytest.raises(UpdateError):
            perform_update(install_dir=install)
        assert (install / "crm").read_text(encoding="utf-8") == "OLD"

    def test_zip_slip_member_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hashlib
        archive = _make_targz({"../evil": b"pwned", "crm": b"x"})
        sums = {"crm-linux-x86_64.tar.gz": hashlib.sha256(archive).hexdigest()}
        self._wire(monkeypatch, archive, sums)

        install = tmp_path / "crm"; install.mkdir()
        (install / "crm").write_text("OLD", encoding="utf-8")
        with pytest.raises(UpdateError, match="(?i)unsafe|traversal|path"):
            perform_update(install_dir=install)
        assert (install / "crm").read_text(encoding="utf-8") == "OLD"
        assert not (tmp_path / "evil").exists()

    def test_swap_oserror_becomes_update_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hashlib
        archive = _make_targz({"crm": b"NEW"})
        sums = {"crm-linux-x86_64.tar.gz": hashlib.sha256(archive).hexdigest()}
        self._wire(monkeypatch, archive, sums)
        def boom(*a, **k):
            raise OSError("rename failed / locked")
        monkeypatch.setattr(update_mod, "swap_bundle", boom)

        install = tmp_path / "crm"; install.mkdir()
        (install / "crm").write_text("OLD", encoding="utf-8")
        with pytest.raises(UpdateError):
            perform_update(install_dir=install)
        # staging cleaned up, no leftover dirs
        assert not list(tmp_path.glob("*.new-*"))


class TestOrchestrators:
    """cli.py-facing glue: background refresh + end-of-run notice, once per process."""

    @pytest.fixture(autouse=True)
    def _reset_once_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(update_mod, "_check_started", False)
        monkeypatch.setattr(update_mod, "_notified", False)

    def test_background_check_runs_when_enabled_and_stale(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "v9.9.9")
        thread = run_background_check(
            json_mode=False, stderr_isatty=True, env={}, now=1.0
        )
        assert thread is not None
        thread.join(timeout=2)
        assert read_cache()["latest"] == "v9.9.9"  # type: ignore[index]

    def test_background_check_skipped_when_disabled(
        self, crm_home: Path
    ) -> None:
        thread = run_background_check(
            json_mode=True, stderr_isatty=True, env={}, now=1.0
        )
        assert thread is None
        assert read_cache() is None

    def test_background_check_skipped_when_fresh(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_cache("v1.0.0", now=1000.0)
        called = {"n": 0}
        monkeypatch.setattr(
            update_mod, "refresh_cache",
            lambda *a, **k: called.__setitem__("n", called["n"] + 1),
        )
        assert run_background_check(
            json_mode=False, stderr_isatty=True, env={}, now=1000.0
        ) is None
        assert called["n"] == 0

    def test_background_check_once_per_process(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "v9.9.9")
        t1 = run_background_check(json_mode=False, stderr_isatty=True, env={}, now=1.0)
        t2 = run_background_check(json_mode=False, stderr_isatty=True, env={}, now=1.0)
        assert t1 is not None and t2 is None

    def test_emit_notice_prints_when_newer(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io as _io

        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        write_cache("v3.0.0", now=1.0)
        stream = _io.StringIO()
        printed = emit_pending_notice(
            json_mode=False, stderr_isatty=True, env={}, stream=stream
        )
        assert printed is True
        assert "3.0.0" in stream.getvalue()

    def test_emit_notice_silent_when_disabled(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io as _io

        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        write_cache("v3.0.0", now=1.0)
        stream = _io.StringIO()
        printed = emit_pending_notice(
            json_mode=True, stderr_isatty=True, env={}, stream=stream
        )
        assert printed is False
        assert stream.getvalue() == ""
