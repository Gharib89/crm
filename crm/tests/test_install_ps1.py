# pyright: basic
"""Integration tests for scripts/install.ps1.

Drives the real install script as a subprocess against a local HTTP server
that serves a tiny zip + SHA256SUMS, then asserts observable behaviour, as
test_install_sh.py does for install.sh. Windows runs it under Windows
PowerShell 5.1 (the version the one-liner meets in the field); POSIX runs it
under pwsh, where the user-PATH step is a no-op, so PATH is asserted on
Windows only.
"""

import hashlib
import http.server
import io
import os
import shutil
import socketserver
import stat
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"
ARCHIVE_NAME = "crm-windows-x86_64.zip"
VERSION = "v9.9.9"
WINDOWS = os.name == "nt"
SHELL = shutil.which("powershell" if WINDOWS else "pwsh")

pytestmark = pytest.mark.skipif(
    SHELL is None, reason="install.ps1 integration test needs powershell (Windows) or pwsh (POSIX)"
)


def _make_archive() -> bytes:
    """A zip whose root holds a `crm.exe` the script can run with --version.

    Windows needs a real PE image, so it ships a copy of the inbox curl.exe,
    which answers --version on stdout and exits 0 (a stub writing stderr would
    trip Windows PowerShell 5.1: captured native stderr becomes a
    NativeCommandError, fatal under the script's ErrorActionPreference Stop).
    POSIX ships an executable shell stub.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if WINDOWS:
            zf.write(Path(os.environ["SystemRoot"]) / "System32" / "curl.exe", "crm.exe")
        else:
            info = zipfile.ZipInfo("crm.exe")
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            zf.writestr(info, '#!/bin/sh\ncase "$1" in --version) echo "crm 9.9.9";; esac\n')
    return buf.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Server:
    """Serves a dict of {url_path: bytes} over loopback; 404 for anything else.
    Text/plain, as R2 serves SHA256SUMS: pwsh 7 hands back `.Content` of an
    untyped response as bytes.

    ``on_get`` runs before each response with the request path, so a test can
    act at a known point in the script's run (the SHA256SUMS fetch comes after
    the zip download has finished and closed the file).
    """

    def __init__(self, files: dict[str, bytes], on_get=None):
        handler = self._make_handler(files, on_get or (lambda _path: None))
        self.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @staticmethod
    def _make_handler(files: dict[str, bytes], on_get):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                on_get(self.path)
                body = files.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):  # silence
                pass

        return H

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[0], self.httpd.server_address[1]
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def user_path():
    """Snapshot HKCU\\Environment Path and restore it, so a Windows run leaves
    the developer's (or runner's) real user PATH as it found it. Yields a
    reader for the current value; POSIX has no user PATH to read.
    """
    if sys.platform != "win32":  # sys.platform, not WINDOWS: pyright narrows on it
        yield lambda: None
        return
    import winreg

    def read():
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            try:
                return winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return None

    saved = read()
    yield lambda: (read() or (None,))[0]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        if saved is None:
            try:
                winreg.DeleteValue(key, "Path")
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, "Path", 0, saved[1], saved[0])


def _run_install(base_url: str, temp: Path, local_app_data: Path):
    env = dict(os.environ)
    env["TEMP"] = str(temp)
    env["LOCALAPPDATA"] = str(local_app_data)
    env["CRM_INSTALL_BASE_URL"] = base_url
    env["CRM_VERSION"] = VERSION
    env.pop("CRM_SHA256", None)
    env["NO_COLOR"] = "1"  # plain pwsh error text in assertion messages
    if WINDOWS:
        # A pwsh 7 parent (CI's default Windows shell) exports its own module
        # path; 5.1 would then fail to load Get-FileHash. Unset, 5.1 rebuilds
        # its default, as a fresh Windows PowerShell console has.
        env.pop("PSModulePath", None)
    return subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
        + ["-File", str(INSTALL_PS1)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _output(result) -> str:
    """Return stdout + stderr with all whitespace removed: PowerShell hard-wraps error
    text at the console width, which can split a word across lines.
    """
    return "".join((result.stdout + result.stderr).split())


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    """A fresh $env:TEMP and $env:LOCALAPPDATA for one install run."""
    temp, local_app_data = tmp_path / "temp", tmp_path / "local_app_data"
    temp.mkdir()
    local_app_data.mkdir()
    return temp, local_app_data


def _served(archive: bytes) -> dict[str, bytes]:
    return {
        f"/{VERSION}/{ARCHIVE_NAME}": archive,
        f"/{VERSION}/SHA256SUMS": f"{_sha256(archive)}  {ARCHIVE_NAME}\n".encode(),
    }


def test_temp_zip_delete_failure_warns_and_still_adds_path(tmp_path: Path, user_path):
    """A temp zip that cannot be deleted is a warning, never an abort: the PATH
    step and the version print still run (#979).
    """
    if not WINDOWS and os.geteuid() == 0:
        pytest.skip("root bypasses directory write permission, so the delete cannot be blocked")
    temp, local_app_data = _dirs(tmp_path)
    held = []

    def block_delete(path: str):
        if not path.endswith("/SHA256SUMS"):
            return
        if WINDOWS:  # an open handle without FILE_SHARE_DELETE blocks deletion
            held.extend(open(z, "rb") for z in temp.glob("crm-*.zip"))
        else:  # unlinking needs write permission on the directory
            temp.chmod(0o555)

    try:
        with _Server(_served(_make_archive()), on_get=block_delete) as server:
            result = _run_install(server.base_url, temp, local_app_data)
    finally:
        for f in held:
            f.close()
        temp.chmod(0o755)

    install_dir = local_app_data / "Programs" / "crm"
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Couldnotdeletetempfile" in _output(result)
    assert "Installed:" in result.stdout
    assert (install_dir / "crm.exe").exists()
    assert list(temp.glob("crm-*.zip")), "the undeletable temp zip should be left in place"
    if WINDOWS:
        assert str(install_dir) in (user_path() or "").split(";")


def _short_path(path: Path) -> str:
    """The 8.3 short form of an existing path (GetShortPathNameW)."""
    assert sys.platform == "win32"
    import ctypes

    buf = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf)):
        raise ctypes.WinError()
    return buf.value


@pytest.mark.skipif(not WINDOWS, reason="8.3 short names are a Windows filesystem feature")
def test_short_form_temp_installs_and_adds_path(tmp_path: Path, user_path):
    """$env:TEMP in 8.3 short form, as a dotted username produces
    (C:\\Users\\NANCY~1.EMA): the install completes, the temp zip is removed and
    the install dir lands on the user PATH (#979).
    """
    long_temp = tmp_path / "Nancy.Emad"
    long_temp.mkdir()
    temp = _short_path(long_temp)
    if "~" not in Path(temp).name:
        pytest.skip("8.3 short-name generation is disabled on this volume")
    local_app_data = tmp_path / "local_app_data"
    local_app_data.mkdir()

    with _Server(_served(_make_archive())) as server:
        result = _run_install(server.base_url, Path(temp), local_app_data)

    install_dir = local_app_data / "Programs" / "crm"
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Couldnotdeletetempfile" not in _output(result)
    assert (install_dir / "crm.exe").exists()
    assert not list(long_temp.glob("crm-*.zip"))
    assert str(install_dir) in (user_path() or "").split(";")


def test_checksum_mismatch_aborts_without_installing(tmp_path: Path, user_path):
    """SHA256SUMS hash != served archive -> throw, nothing installed, PATH untouched."""
    archive = _make_archive()
    files = {**_served(archive), f"/{VERSION}/SHA256SUMS": f"{'0' * 64}  {ARCHIVE_NAME}\n".encode()}
    temp, local_app_data = _dirs(tmp_path)
    before = user_path()

    with _Server(files) as server:
        result = _run_install(server.base_url, temp, local_app_data)

    assert result.returncode != 0
    assert "Checksummismatch" in _output(result)
    assert not (local_app_data / "Programs" / "crm").exists()
    assert not list(temp.glob("crm-*.zip"))
    assert user_path() == before


def test_missing_sha256sums_aborts_without_installing(tmp_path: Path, user_path):
    """SHA256SUMS 404 -> throw naming CRM_SHA256, nothing installed, PATH untouched."""
    files = {f"/{VERSION}/{ARCHIVE_NAME}": _make_archive()}
    temp, local_app_data = _dirs(tmp_path)
    before = user_path()

    with _Server(files) as server:
        result = _run_install(server.base_url, temp, local_app_data)

    out = _output(result)
    assert result.returncode != 0
    assert "CouldnotfetchSHA256SUMS" in out
    assert "CRM_SHA256" in out
    assert not (local_app_data / "Programs" / "crm").exists()
    assert not list(temp.glob("crm-*.zip"))
    assert user_path() == before
