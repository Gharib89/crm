# pyright: basic
"""Integration tests for scripts/install.sh.

Drives the real install script as a subprocess against a local HTTP server
that serves a tiny tar.gz + SHA256SUMS, then asserts observable behaviour:
does it install, does it abort, does it touch the install dir. Behaviour only —
nothing here knows how the script verifies, just what it does.
"""

import hashlib
import http.server
import io
import os
import shutil
import socketserver
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
ARCHIVE_NAME = "crm-linux-x86_64.tar.gz"
VERSION = "v9.9.9"

pytestmark = pytest.mark.skipif(
    os.name != "posix"
    or any(shutil.which(t) is None for t in ("sh", "curl", "sha256sum", "tar")),
    reason="install.sh integration test needs a POSIX shell with curl/sha256sum/tar",
)


def _make_archive() -> bytes:
    """A tar.gz whose root holds an executable `crm` stub printing a version."""
    stub = b'#!/bin/sh\ncase "$1" in --version) echo "crm 9.9.9";; *) echo crm;; esac\n'
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("crm")
        info.size = len(stub)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(stub))
    return buf.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Server:
    """Serves a dict of {url_path: bytes} over loopback; 404 for anything else."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        handler = self._make_handler(files)
        self.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @staticmethod
    def _make_handler(files: dict[str, bytes]):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = files.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
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


def _run_install(base_url: str, home: Path, env_extra: dict[str, str]):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["CRM_BASE_URL"] = base_url
    env["CRM_VERSION"] = VERSION
    env.update(env_extra)
    return subprocess.run(
        ["sh", str(INSTALL_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_happy_path_auto_verify_installs(tmp_path: Path):
    """Matching SHA256SUMS -> script installs and the binary runs."""
    archive = _make_archive()
    sums = f"{_sha256(archive)}  {ARCHIVE_NAME}\n".encode()
    files = {
        f"/{VERSION}/{ARCHIVE_NAME}": archive,
        f"/{VERSION}/SHA256SUMS": sums,
    }
    home = tmp_path / "home"
    home.mkdir()

    with _Server(files) as server:
        result = _run_install(server.base_url, home, {})

    assert result.returncode == 0, result.stderr
    installed = home / ".local" / "share" / "crm" / "crm"
    assert installed.exists()
    assert (home / ".local" / "bin" / "crm").exists()


def test_tampered_archive_aborts(tmp_path: Path):
    """SHA256SUMS hash != served archive -> abort non-zero, nothing installed."""
    archive = _make_archive()
    wrong = f"{'0' * 64}  {ARCHIVE_NAME}\n".encode()
    files = {
        f"/{VERSION}/{ARCHIVE_NAME}": archive,
        f"/{VERSION}/SHA256SUMS": wrong,
    }
    home = tmp_path / "home"
    home.mkdir()

    with _Server(files) as server:
        result = _run_install(server.base_url, home, {})

    assert result.returncode != 0
    assert not (home / ".local" / "share" / "crm" / "crm").exists()


def test_missing_sha256sums_aborts(tmp_path: Path):
    """Auto mode, SHA256SUMS 404 -> abort with a clear message, nothing installed."""
    archive = _make_archive()
    files = {f"/{VERSION}/{ARCHIVE_NAME}": archive}  # SHA256SUMS absent -> 404
    home = tmp_path / "home"
    home.mkdir()

    with _Server(files) as server:
        result = _run_install(server.base_url, home, {})

    assert result.returncode != 0
    assert not (home / ".local" / "share" / "crm" / "crm").exists()
    assert "SHA256SUMS" in result.stderr
    assert "CRM_SHA256" in result.stderr


def test_crm_sha256_override_matches_installs(tmp_path: Path):
    """CRM_SHA256 set + correct -> installs without fetching SHA256SUMS."""
    archive = _make_archive()
    files = {f"/{VERSION}/{ARCHIVE_NAME}": archive}  # no SHA256SUMS served
    home = tmp_path / "home"
    home.mkdir()

    with _Server(files) as server:
        result = _run_install(server.base_url, home, {"CRM_SHA256": _sha256(archive)})

    assert result.returncode == 0, result.stderr
    assert (home / ".local" / "share" / "crm" / "crm").exists()


def test_crm_sha256_uppercase_matches_installs(tmp_path: Path):
    """An uppercase CRM_SHA256 must not false-mismatch the lowercase digest."""
    archive = _make_archive()
    files = {f"/{VERSION}/{ARCHIVE_NAME}": archive}
    home = tmp_path / "home"
    home.mkdir()

    with _Server(files) as server:
        result = _run_install(server.base_url, home, {"CRM_SHA256": _sha256(archive).upper()})

    assert result.returncode == 0, result.stderr
    assert (home / ".local" / "share" / "crm" / "crm").exists()


def test_sha256sums_without_archive_entry_aborts(tmp_path: Path):
    """SHA256SUMS served but lists no line for our archive -> explicit abort."""
    archive = _make_archive()
    sums = f"{_sha256(archive)}  some-other-file.tar.gz\n".encode()
    files = {
        f"/{VERSION}/{ARCHIVE_NAME}": archive,
        f"/{VERSION}/SHA256SUMS": sums,
    }
    home = tmp_path / "home"
    home.mkdir()

    with _Server(files) as server:
        result = _run_install(server.base_url, home, {})

    assert result.returncode != 0
    assert not (home / ".local" / "share" / "crm" / "crm").exists()
    assert ARCHIVE_NAME in result.stderr
    assert "CRM_SHA256" in result.stderr


def test_crm_sha256_override_mismatch_aborts(tmp_path: Path):
    """CRM_SHA256 set + wrong -> abort non-zero, nothing installed."""
    archive = _make_archive()
    files = {f"/{VERSION}/{ARCHIVE_NAME}": archive}
    home = tmp_path / "home"
    home.mkdir()

    with _Server(files) as server:
        result = _run_install(server.base_url, home, {"CRM_SHA256": "0" * 64})

    assert result.returncode != 0
    assert not (home / ".local" / "share" / "crm" / "crm").exists()
