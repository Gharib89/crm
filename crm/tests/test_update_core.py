# pyright: basic
"""Tests for crm/core/update.py — version compare, update-check cache, self-update."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import crm.core.update as update_mod
from crm.core.update import (
    INSTALL_METHODS,
    UpdateError,
    check_for_update,
    cleanup_stale_updates,
    compare_versions,
    detect_install_method,
    emit_pending_notice,
    fetch_latest_version,
    is_auto_run_method,
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
    upgrade_argv,
    upgrade_command,
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
            ("2.9.0", "2.9.1", -1),  # patch behind
            ("2.9.0", "2.10.0", -1),  # numeric, not lexical (10 > 9)
            ("v2.10.0", "2.9.0", 1),  # ahead, v-prefix tolerated on either side
            ("2.9.0", "v2.9.0", 0),  # equal despite v-prefix mismatch
            ("2.9.0", "3.0.0", -1),  # major behind
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
        import requests

        seen: dict[str, object] = {}

        def fake_get(url: str, timeout: float | None = None, **kw: object) -> _Resp:
            seen["url"] = url
            seen["timeout"] = timeout
            return _Resp("v3.1.4\n")

        # update.py imports requests lazily inside the function (#247); patch the
        # real module's get — the local `import requests` binds to the same object.
        monkeypatch.setattr(requests, "get", fake_get)
        assert fetch_latest_version("https://r2.example/base") == "v3.1.4"
        assert seen["url"] == "https://r2.example/base/latest/VERSION"
        assert seen["timeout"] is not None  # a bounded timeout is always passed

    def test_non_semver_payload_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A 200 with junk (proxy login page, "latest", HTML) must not be cached
        # or compared — it would crash compare_versions downstream.
        import requests

        monkeypatch.setattr(
            requests,
            "get",
            lambda *a, **k: _Resp("<html>nope</html>"),
        )
        assert fetch_latest_version("https://r2.example") is None

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

        monkeypatch.setattr(requests, "get", fake_get)
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

    def test_message_is_method_agnostic(self, crm_home: Path) -> None:
        # The notice always points at `crm self-update` — no per-method branch, so
        # it can never drift from what the command actually does (issue #872).
        write_cache("v3.0.0", now=1.0)
        msg = pending_notice(current="2.9.0")
        assert msg is not None
        assert "3.0.0" in msg and "crm self-update" in msg
        assert "pip install" not in msg

    def test_malformed_cached_latest_no_crash(self, crm_home: Path) -> None:
        # A partial write / manual edit / cached junk must not raise (fail-silent).
        (crm_home / "update-check.json").write_text(
            '{"checked_at": 1.0, "latest": "garbage"}', encoding="utf-8"
        )
        assert pending_notice(current="2.9.0") is None

    def test_suppressed_within_24h_of_last_notice(self, crm_home: Path) -> None:
        write_cache("v3.0.0", now=1000.0)
        update_mod.mark_notified(now=1000.0)
        assert pending_notice(current="2.9.0", now=1000.0 + _DAY - 1) is None

    def test_shown_again_after_24h(self, crm_home: Path) -> None:
        write_cache("v3.0.0", now=1000.0)
        update_mod.mark_notified(now=1000.0)
        assert pending_notice(current="2.9.0", now=1000.0 + _DAY + 1) is not None

    def test_new_version_resets_notice_gate(self, crm_home: Path) -> None:
        write_cache("v3.0.0", now=1000.0)
        update_mod.mark_notified(now=1000.0)
        # a refresh that finds a NEWER version drops the gate → notify again
        write_cache("v3.1.0", now=1001.0)
        assert pending_notice(current="2.9.0", now=1001.0) is not None


class TestRefreshCache:
    """Background-thread body (sync): probe + persist; silent on failure."""

    def test_writes_cache_on_success(self, crm_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        body = "aaa111  crm-linux-x86_64.tar.gz\r\nbbb222  crm-windows-x86_64.zip\n"
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


def _make_zip(files: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
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

    def test_malformed_latest_becomes_update_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "garbage")
        with pytest.raises(UpdateError):
            check_for_update()

    def test_raises_when_latest_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: None)
        with pytest.raises(UpdateError):
            check_for_update()


class TestDetectInstallMethod:
    """Path/marker sniffing → the fixed INSTALL_METHODS vocabulary (issue #872)."""

    def test_frozen_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(update_mod, "is_frozen", lambda: True)
        assert detect_install_method() == "frozen"

    def test_uv_tool_by_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        prefix = tmp_path / "share" / "uv" / "tools" / "crm"
        prefix.mkdir(parents=True)
        monkeypatch.setattr(update_mod.sys, "prefix", str(prefix))
        assert detect_install_method() == "uv-tool"

    def test_uv_tool_by_receipt_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        (tmp_path / "uv-receipt.toml").write_text("", encoding="utf-8")
        monkeypatch.setattr(update_mod.sys, "prefix", str(tmp_path))
        assert detect_install_method() == "uv-tool"

    def test_pipx_by_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        prefix = tmp_path / "pipx" / "venvs" / "crm"
        prefix.mkdir(parents=True)
        monkeypatch.setattr(update_mod.sys, "prefix", str(prefix))
        assert detect_install_method() == "pipx"

    def test_pipx_by_metadata_marker(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        (tmp_path / "pipx_metadata.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(update_mod.sys, "prefix", str(tmp_path))
        assert detect_install_method() == "pipx"

    def test_editable_via_direct_url(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        monkeypatch.setattr(update_mod.sys, "prefix", str(tmp_path))  # no uv/pipx marker
        monkeypatch.setattr(
            update_mod, "_read_direct_url", lambda: {"dir_info": {"editable": True}}
        )
        assert detect_install_method() == "editable"

    def test_pip_git_via_direct_url(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        monkeypatch.setattr(update_mod.sys, "prefix", str(tmp_path))
        monkeypatch.setattr(
            update_mod, "_read_direct_url", lambda: {"vcs_info": {"vcs": "git"}, "url": "git+..."}
        )
        assert detect_install_method() == "pip-git"

    def test_non_git_vcs_is_unknown_not_pip_git(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A non-git VCS install must not be labeled pip-git (the upgrade command is a
        # git+ URL); it degrades to unknown rather than emit wrong git guidance.
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        monkeypatch.setattr(update_mod.sys, "prefix", str(tmp_path))
        monkeypatch.setattr(update_mod, "_read_direct_url", lambda: {"vcs_info": {"vcs": "hg"}})
        assert detect_install_method() == "unknown"

    def test_unknown_when_no_signal(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        monkeypatch.setattr(update_mod.sys, "prefix", str(tmp_path))
        monkeypatch.setattr(update_mod, "_read_direct_url", lambda: None)
        assert detect_install_method() == "unknown"

    def test_result_is_always_in_vocabulary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(update_mod, "is_frozen", lambda: False)
        monkeypatch.setattr(update_mod.sys, "prefix", str(tmp_path))
        assert detect_install_method() in INSTALL_METHODS


class TestUpgradeCommand:
    """Command/argv construction: git tag pinned, argv only for auto-run methods."""

    def test_uv_tool_force_reinstall_pinned_to_tag(self) -> None:
        cmd = upgrade_command("uv-tool", "v3.1.0")
        assert cmd == "uv tool install --force git+https://github.com/Gharib89/crm@v3.1.0"

    def test_pipx_force_reinstall_pinned_to_tag(self) -> None:
        cmd = upgrade_command("pipx", "3.1.0")  # bare version normalizes to a v-tag
        assert cmd == "pipx install --force git+https://github.com/Gharib89/crm@v3.1.0"

    def test_pip_git_upgrade(self) -> None:
        assert upgrade_command("pip-git", "v3.1.0") == (
            "pip install -U git+https://github.com/Gharib89/crm@v3.1.0"
        )

    def test_editable_gets_checkout_guidance(self) -> None:
        assert upgrade_command("editable", "v3.1.0") == "git pull && pip install -e ."

    def test_unknown_gets_git_based_pip_command(self) -> None:
        assert upgrade_command("unknown", "v3.1.0") == (
            "pip install -U git+https://github.com/Gharib89/crm@v3.1.0"
        )

    @pytest.mark.parametrize("method", ["uv-tool", "pipx"])
    def test_argv_matches_command_for_auto_methods(self, method: str) -> None:
        argv = upgrade_argv(method, "v3.1.0")
        assert argv is not None
        assert " ".join(argv) == upgrade_command(method, "v3.1.0")

    @pytest.mark.parametrize("method", ["editable", "pip-git", "unknown"])
    def test_argv_none_for_non_auto_methods(self, method: str) -> None:
        assert upgrade_argv(method, "v3.1.0") is None
        assert is_auto_run_method(method) is False

    @pytest.mark.parametrize("method", ["uv-tool", "pipx"])
    def test_is_auto_run_method_true_for_isolated_envs(self, method: str) -> None:
        assert is_auto_run_method(method) is True


class TestRunUpgrade:
    """run_upgrade shells out and returns the exit status; missing tool raises."""

    def test_returns_subprocess_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        class _Completed:
            returncode = 0

        seen: dict[str, object] = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            return _Completed()

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert update_mod.run_upgrade(["uv", "tool", "install", "--force", "x"]) == 0
        assert seen["argv"] == ["uv", "tool", "install", "--force", "x"]

    def test_missing_tool_raises_filenotfound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        def boom(argv, **kw):
            raise FileNotFoundError(argv[0])

        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(FileNotFoundError):
            update_mod.run_upgrade(["pipx", "install", "--force", "x"])


def _sharing_violation(locked: Path | None = None) -> PermissionError:
    """The Windows `ERROR_SHARING_VIOLATION` a scanner's handle produces.

    Built by hand because `winerror` only exists on Windows builds of CPython, and
    the errno alone cannot stand in for it: Windows maps both
    `ERROR_SHARING_VIOLATION` and `ERROR_ACCESS_DENIED` to `EACCES`. `locked` fills
    in the `filename` a real `Path.rename` failure carries, so a test can check the
    error reaches the user naming the file that would not move.
    """
    exc = PermissionError(13, "The process cannot access the file")
    exc.winerror = 32  # pyright: ignore[reportAttributeAccessIssue]
    if locked is not None:
        exc.filename = str(locked)
    return exc


def _bundle(root: Path, marker: str) -> Path:
    """A minimal PyInstaller onedir layout: a launcher plus a nested `_internal` tree."""
    (root / "_internal" / "tcl").mkdir(parents=True)
    (root / "crm.exe").write_text(marker, encoding="utf-8")
    (root / "_internal" / "python313.dll").write_text(marker, encoding="utf-8")
    (root / "_internal" / "tcl" / "init.tcl").write_text(marker, encoding="utf-8")
    return root


class TestSwapBundle:
    """In-place dir replacement; old bundle removed (posix) or parked (win)."""

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

    def test_windows_swap_keeps_the_install_dir_itself(self, tmp_path: Path) -> None:
        """The running `crm.exe` and its loaded DLLs live inside `install_dir`, and
        Windows fails a rename of a directory that contains an open file
        (STATUS_ACCESS_DENIED). So the swap must replace the *contents* and leave
        the directory itself in place — same directory, new payload (#932).
        """
        install = _bundle(tmp_path / "crm", "OLD")
        new = _bundle(tmp_path / "staged", "NEW")
        before = install.stat().st_ino
        assert before != 0, "st_ino unusable on this filesystem; identity check is vacuous"

        swap_bundle(install, new, windows=True)

        assert install.stat().st_ino == before
        assert (install / "crm.exe").read_text(encoding="utf-8") == "NEW"
        assert (install / "_internal" / "python313.dll").read_text(encoding="utf-8") == "NEW"
        assert (install / "_internal" / "tcl" / "init.tcl").read_text(encoding="utf-8") == "NEW"
        assert not new.exists()

    def test_windows_swap_parks_the_whole_old_tree(self, tmp_path: Path) -> None:
        install = _bundle(tmp_path / "crm", "OLD")
        new = _bundle(tmp_path / "staged", "NEW")

        swap_bundle(install, new, windows=True)

        parked = list(tmp_path.glob("crm.old-*"))
        assert len(parked) == 1  # running bundle parked, cleaned next run
        assert (parked[0] / "crm.exe").read_text(encoding="utf-8") == "OLD"
        assert (parked[0] / "_internal" / "python313.dll").read_text(encoding="utf-8") == "OLD"
        assert (parked[0] / "_internal" / "tcl" / "init.tcl").read_text(encoding="utf-8") == "OLD"

    def test_windows_swap_restores_install_when_a_file_cannot_be_moved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file we cannot evacuate (say an AV handle without share-delete) must
        leave the install exactly as it was, and say what to do about it.

        `crm.exe` sorts last, so the `_internal` files have already moved by the
        time it is refused — the undo has real work to do.
        """
        install = _bundle(tmp_path / "crm", "OLD")
        new = _bundle(tmp_path / "staged", "NEW")
        real_rename = Path.rename

        def deny_exe(self: Path, target: Path) -> Path:
            if self.name == "crm.exe":
                raise PermissionError(13, "Permission denied")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", deny_exe)

        with pytest.raises(UpdateError, match="install.ps1"):
            swap_bundle(install, new, windows=True)

        assert (install / "crm.exe").read_text(encoding="utf-8") == "OLD"
        assert (install / "_internal" / "python313.dll").read_text(encoding="utf-8") == "OLD"
        assert (install / "_internal" / "tcl" / "init.tcl").read_text(encoding="utf-8") == "OLD"
        assert list(tmp_path.glob("crm.old-*")) == []

    def test_windows_swap_keeps_the_parked_copy_when_the_undo_also_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If restoring fails too, the install is split across both trees. The parked
        tree holds the only copy of what moved, so it must survive — and the error
        must say where it is instead of claiming an intact install.
        """
        install = _bundle(tmp_path / "crm", "OLD")
        new = _bundle(tmp_path / "staged", "NEW")
        real_rename = Path.rename

        def deny_exe_and_undo(self: Path, target: Path) -> Path:
            if self.name == "crm.exe":  # evacuation stops part-way...
                raise PermissionError(13, "Permission denied")
            if install in target.parents:  # ...and putting the moved files back fails
                raise PermissionError(13, "Permission denied")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", deny_exe_and_undo)

        with pytest.raises(UpdateError, match="restoring the previous install then failed"):
            swap_bundle(install, new, windows=True)

        parked = list(tmp_path.glob("crm.old-*"))
        assert len(parked) == 1
        assert (parked[0] / "_internal" / "python313.dll").read_text(encoding="utf-8") == "OLD"

    def test_windows_swap_restores_install_when_promotion_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Failing *after* the old files are evacuated must still put every one of
        them back — the emptied directory skeleton included.
        """
        install = _bundle(tmp_path / "crm", "OLD")
        new = _bundle(tmp_path / "staged", "NEW")
        real_rename = Path.rename

        def deny_promotion(self: Path, target: Path) -> Path:
            if self.parent == new:
                raise PermissionError(13, "Permission denied")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", deny_promotion)

        with pytest.raises(UpdateError, match="install.ps1"):
            swap_bundle(install, new, windows=True)

        assert (install / "crm.exe").read_text(encoding="utf-8") == "OLD"
        assert (install / "_internal" / "python313.dll").read_text(encoding="utf-8") == "OLD"
        assert list(tmp_path.glob("crm.old-*")) == []

    def test_windows_swap_retries_a_lock_that_clears(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scanner holding a file open releases it a moment later, so a sharing
        violation must be retried rather than treated as terminal (#935).
        """
        install = _bundle(tmp_path / "crm", "OLD")
        new = _bundle(tmp_path / "staged", "NEW")
        real_rename = Path.rename
        monkeypatch.setattr(update_mod, "_LOCK_RETRY_DELAYS", (0.0, 0.0))
        remaining = {"crm.exe": 2}

        def lock_then_release(self: Path, target: Path) -> Path:
            if remaining.get(self.name):
                remaining[self.name] -= 1
                raise _sharing_violation(self)
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", lock_then_release)

        swap_bundle(install, new, windows=True)

        assert (install / "crm.exe").read_text(encoding="utf-8") == "NEW"
        assert (install / "_internal" / "python313.dll").read_text(encoding="utf-8") == "NEW"
        assert not new.exists()

    def test_windows_swap_gives_up_on_a_lock_that_never_clears(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exhausted retry ladder means the handle is persistent, not momentary.
        The install still has to come back intact, and the message has to say the
        lock outlived the window — that is what distinguishes the two causes.
        """
        install = _bundle(tmp_path / "crm", "OLD")
        new = _bundle(tmp_path / "staged", "NEW")
        real_rename = Path.rename
        monkeypatch.setattr(update_mod, "_LOCK_RETRY_DELAYS", (0.0, 0.0))

        def lock_exe_forever(self: Path, target: Path) -> Path:
            if self.name == "crm.exe":
                raise _sharing_violation(self)
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", lock_exe_forever)

        with pytest.raises(UpdateError, match="outlived the retry window") as excinfo:
            swap_bundle(install, new, windows=True)

        assert "install.ps1" in str(excinfo.value)
        # The user has to know *which* file would not move to act on this at all.
        assert str(install / "crm.exe") in str(excinfo.value)
        assert (install / "crm.exe").read_text(encoding="utf-8") == "OLD"
        assert (install / "_internal" / "python313.dll").read_text(encoding="utf-8") == "OLD"
        assert list(tmp_path.glob("crm.old-*")) == []

    def test_windows_swap_does_not_retry_a_permission_denial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A genuine ACL denial is not a race, so it must fail on the first raise
        rather than stalling through the ladder. Windows maps it to the same errno
        as a sharing violation, so only `winerror` can tell them apart.
        """
        install = _bundle(tmp_path / "crm", "OLD")
        new = _bundle(tmp_path / "staged", "NEW")
        real_rename = Path.rename
        attempts = {"crm.exe": 0}

        def deny_exe(self: Path, target: Path) -> Path:
            if self.name == "crm.exe":
                attempts[self.name] += 1
                raise PermissionError(13, "Permission denied")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", deny_exe)

        with pytest.raises(UpdateError, match="install.ps1") as excinfo:
            swap_bundle(install, new, windows=True)

        assert attempts["crm.exe"] == 1
        assert "outlived the retry window" not in str(excinfo.value)

    def test_windows_swap_retries_the_rollback_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Losing the race while putting files back is what would split the install
        across two trees, so the undo waits a lock out as well.
        """
        install = _bundle(tmp_path / "crm", "OLD")
        new = _bundle(tmp_path / "staged", "NEW")
        real_rename = Path.rename
        monkeypatch.setattr(update_mod, "_LOCK_RETRY_DELAYS", (0.0, 0.0))
        locked_undo = {"python313.dll": 1}

        def lock_exe_and_first_undo(self: Path, target: Path) -> Path:
            if self.name == "crm.exe":  # evacuation stops part-way...
                raise _sharing_violation(self)
            if install in target.parents and locked_undo.get(self.name):
                locked_undo[self.name] -= 1  # ...and the first undo attempt is locked
                raise _sharing_violation(self)
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", lock_exe_and_first_undo)

        with pytest.raises(UpdateError, match="intact and still works"):
            swap_bundle(install, new, windows=True)

        assert (install / "_internal" / "python313.dll").read_text(encoding="utf-8") == "OLD"
        assert list(tmp_path.glob("crm.old-*")) == []

    def test_windows_swap_retries_the_skeleton_removal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Clearing the emptied directory skeleton races the same scanner the moves
        do, so it is retried as well — otherwise a lock there both fails an update
        that would have succeeded and makes the "outlived the retry window" wording
        a lie, since no ladder would have run.
        """
        install = _bundle(tmp_path / "crm", "OLD")
        new = _bundle(tmp_path / "staged", "NEW")
        real_rmtree = update_mod.shutil.rmtree
        monkeypatch.setattr(update_mod, "_LOCK_RETRY_DELAYS", (0.0, 0.0))
        remaining = {"count": 1}

        # Forwards *args/**kwargs verbatim: this patch catches every `rmtree` in the
        # module, and the parked-tree cleanup on the failure path passes
        # `ignore_errors=True`. A stub that dropped it would fail this test with a
        # TypeError instead of the behavior under test.
        def lock_then_release(path: Any, *args: Any, **kwargs: Any) -> None:
            if remaining["count"]:
                remaining["count"] -= 1
                raise _sharing_violation(Path(path))
            real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(update_mod.shutil, "rmtree", lock_then_release)

        swap_bundle(install, new, windows=True)

        assert (install / "crm.exe").read_text(encoding="utf-8") == "NEW"
        assert (install / "_internal" / "python313.dll").read_text(encoding="utf-8") == "NEW"

    def test_cleanup_removes_parked(self, tmp_path: Path) -> None:
        install = tmp_path / "crm"
        install.mkdir()
        (tmp_path / "crm.old-123").mkdir()
        cleanup_stale_updates(install)
        assert list(tmp_path.glob("crm.old*")) == []

    def test_cleanup_removes_parked_tree_with_files(self, tmp_path: Path) -> None:
        install = tmp_path / "crm"
        install.mkdir()
        _bundle(tmp_path / "crm.old-123", "OLD")
        cleanup_stale_updates(install)
        assert list(tmp_path.glob("crm.old*")) == []


class TestExtractRejectsLinks:
    """Defense-in-depth: link members can escape `dest` even without `..`."""

    def test_tar_symlink_member_rejected(self, tmp_path: Path) -> None:
        import io
        import tarfile

        from crm.core.update import _extract

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            ti = tarfile.TarInfo("lib")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc"
            tar.addfile(ti)
        with pytest.raises(UpdateError, match="(?i)link|unsafe"):
            _extract("crm-linux-x86_64.tar.gz", buf.getvalue(), tmp_path / "out")
        assert not (tmp_path / "out" / "lib").exists()

    def test_zip_symlink_member_rejected(self, tmp_path: Path) -> None:
        import io
        import zipfile

        from crm.core.update import _extract

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zi = zipfile.ZipInfo("evil")
            zi.external_attr = 0o120777 << 16  # S_IFLNK
            zf.writestr(zi, "/etc/passwd")
        with pytest.raises(UpdateError, match="(?i)link|unsafe"):
            _extract("crm-windows-x86_64.zip", buf.getvalue(), tmp_path / "out")


class TestPerformUpdate:
    """Full download → verify → swap, driven against a tmp install dir."""

    def _wire(self, monkeypatch: pytest.MonkeyPatch, archive: bytes, sums: dict[str, str]) -> None:
        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        monkeypatch.setattr(update_mod, "fetch_latest_version", lambda *a, **k: "v3.0.0")
        monkeypatch.setattr(update_mod, "_download_archive", lambda *a, **k: archive)
        monkeypatch.setattr(update_mod, "_fetch_sha256sums", lambda *a, **k: sums)
        monkeypatch.setattr(update_mod.sys, "platform", "linux")

    def test_happy_path_swaps_bundle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import hashlib

        archive = _make_targz({"crm": b"NEW-BINARY", "lib.so": b"x"})
        sums = {"crm-linux-x86_64.tar.gz": hashlib.sha256(archive).hexdigest()}
        self._wire(monkeypatch, archive, sums)

        install = tmp_path / "crm"
        install.mkdir()
        (install / "crm").write_text("OLD", encoding="utf-8")

        messages: list[str] = []
        result = perform_update(install_dir=install, progress=messages.append)

        assert result["updated"] is True
        assert result["from_version"] == "2.9.0"
        assert result["to_version"] == "3.0.0"
        assert (install / "crm").read_bytes() == b"NEW-BINARY"
        assert (install / "lib.so").exists()
        assert any("Downloading" in m for m in messages)
        assert any("Verifying" in m for m in messages)
        assert any("Installing" in m for m in messages)

    def test_windows_happy_path_swaps_onedir_bundle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole Windows chain: zip archive → onedir layout → in-place swap."""
        import hashlib

        archive = _make_zip({"crm.exe": b"NEW-EXE", "_internal/python313.dll": b"NEW-DLL"})
        sums = {"crm-windows-x86_64.zip": hashlib.sha256(archive).hexdigest()}
        self._wire(monkeypatch, archive, sums)
        monkeypatch.setattr(update_mod.sys, "platform", "win32")

        install = _bundle(tmp_path / "crm", "OLD")
        result = perform_update(install_dir=install)

        assert result["updated"] is True
        assert (install / "crm.exe").read_bytes() == b"NEW-EXE"
        assert (install / "_internal" / "python313.dll").read_bytes() == b"NEW-DLL"
        # old bundle parked for the next run; no staging dir left behind
        assert len(list(tmp_path.glob("crm.old-*"))) == 1
        assert not list(tmp_path.glob("*.new*"))

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

        monkeypatch.setattr(requests, "get", boom)

        install = tmp_path / "crm"
        install.mkdir()
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

        install = tmp_path / "crm"
        install.mkdir()
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

        install = tmp_path / "crm"
        install.mkdir()
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
        thread = run_background_check(json_mode=False, stderr_isatty=True, env={}, now=1.0)
        assert thread is not None
        thread.join(timeout=2)
        assert read_cache()["latest"] == "v9.9.9"  # type: ignore[index]

    def test_background_check_skipped_when_disabled(self, crm_home: Path) -> None:
        thread = run_background_check(json_mode=True, stderr_isatty=True, env={}, now=1.0)
        assert thread is None
        assert read_cache() is None

    def test_background_check_skipped_when_fresh(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_cache("v1.0.0", now=1000.0)
        called = {"n": 0}
        monkeypatch.setattr(
            update_mod,
            "refresh_cache",
            lambda *a, **k: called.__setitem__("n", called["n"] + 1),
        )
        assert run_background_check(json_mode=False, stderr_isatty=True, env={}, now=1000.0) is None
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
        printed = emit_pending_notice(json_mode=False, stderr_isatty=True, env={}, stream=stream)
        assert printed is True
        assert "3.0.0" in stream.getvalue()

    def test_emit_notice_silent_when_disabled(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io as _io

        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        write_cache("v3.0.0", now=1.0)
        stream = _io.StringIO()
        printed = emit_pending_notice(json_mode=True, stderr_isatty=True, env={}, stream=stream)
        assert printed is False
        assert stream.getvalue() == ""

    def test_emit_stamps_notified_at_for_daily_gate(
        self, crm_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io as _io

        monkeypatch.setattr(update_mod, "current_version", lambda: "2.9.0")
        write_cache("v3.0.0", now=1.0)
        stream = _io.StringIO()
        assert (
            emit_pending_notice(
                json_mode=False, stderr_isatty=True, env={}, stream=stream, now=5000.0
            )
            is True
        )
        assert read_cache()["notified_at"] == 5000.0  # type: ignore[index]


class TestFetchSha256sums:
    """_fetch_sha256sums: GET <base>/<version>/SHA256SUMS, parse and return dict."""

    def test_parses_valid_manifest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import requests

        body = (
            "aabbcc1122334455aabbcc1122334455aabbcc1122334455aabbcc1122334455  crm-linux\n"
            "ddee556677889900ddee556677889900ddee556677889900ddee556677889900  crm-windows.exe\n"
        )
        monkeypatch.setattr(requests, "get", lambda url, timeout=None, **k: _Resp(body))
        from crm.core.update import _fetch_sha256sums

        result = _fetch_sha256sums("https://r2.example/base", "v3.1.4")
        assert (
            result["crm-linux"]
            == "aabbcc1122334455aabbcc1122334455aabbcc1122334455aabbcc1122334455"
        )
        assert (
            result["crm-windows.exe"]
            == "ddee556677889900ddee556677889900ddee556677889900ddee556677889900"
        )

    def test_constructs_correct_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import requests

        seen: dict[str, str] = {}

        def fake_get(url: str, timeout: object = None, **k: object) -> _Resp:
            seen["url"] = url
            return _Resp("aabb  crm-linux\n")

        monkeypatch.setattr(requests, "get", fake_get)
        from crm.core.update import _fetch_sha256sums

        _fetch_sha256sums("https://r2.example/base", "v3.1.4")
        assert seen["url"] == "https://r2.example/base/v3.1.4/SHA256SUMS"

    def test_fetch_failure_raises_update_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import requests

        monkeypatch.setattr(
            requests,
            "get",
            lambda url, timeout=None, **k: (_ for _ in ()).throw(requests.ConnectionError("dead")),
        )
        from crm.core.update import UpdateError, _fetch_sha256sums

        with pytest.raises(UpdateError, match="Failed to fetch checksums"):
            _fetch_sha256sums("https://dead.invalid", "v1.0.0")

    def test_http_error_raises_update_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import requests

        monkeypatch.setattr(requests, "get", lambda url, timeout=None, **k: _Resp("", status=404))
        from crm.core.update import UpdateError, _fetch_sha256sums

        with pytest.raises(UpdateError, match="Failed to fetch checksums"):
            _fetch_sha256sums("https://r2.example/base", "v1.0.0")

    def test_blank_lines_are_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Blank / whitespace-only lines (len(parts) != 2) are silently skipped."""
        import requests

        # blank line and trailing whitespace-only line must not end up in the dict
        body = "\naabbcc  crm-linux\n  \n"
        monkeypatch.setattr(requests, "get", lambda url, timeout=None, **k: _Resp(body))
        from crm.core.update import _fetch_sha256sums

        result = _fetch_sha256sums("https://r2.example/base", "v1.0.0")
        assert result == {"crm-linux": "aabbcc"}
