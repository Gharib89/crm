# Install

The canonical install instructions live in the project
[README](https://github.com/Gharib89/crm#install) and are summarised here.

## Install script (no Python required)

=== "Windows (PowerShell)"

    ```powershell
    irm https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.ps1 | iex
    ```

=== "Linux"

    ```bash
    curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | sh
    ```

Pin a version by setting `CRM_VERSION` (e.g. `v1.0.0`). The binary is unsigned;
Windows SmartScreen may warn on first run. On managed machines it may be blocked
outright by endpoint security (e.g. Microsoft Defender ASR or AppLocker) — use
[uv tool install](#uv-tool-install-isolated) in that case.

### Integrity verification

Both scripts verify the downloaded archive's SHA-256 against the `SHA256SUMS`
published alongside it before extracting; a mismatch or a missing `SHA256SUMS`
aborts the install. To pin a hash you obtained out-of-band, set `CRM_SHA256`
(this also covers releases published before checksums existed):

```bash
curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | CRM_SHA256=<hash> sh
```

```powershell
$env:CRM_SHA256='<hash>'; irm https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.ps1 | iex
```

The checksum is fetched from the same R2 bucket as the archive, so this guards
against download corruption and single-object tampering, not a full bucket
compromise. Use `CRM_SHA256` with a hash from a trusted channel for stronger
guarantees.

## uv tool install (isolated)

Installs `crm` into an isolated environment that runs through your trusted
`python` interpreter instead of a standalone binary, so there is no new unsigned
executable for endpoint security to flag. Recommended when the install script's
prebuilt binary is blocked on a managed machine.

First install [uv](https://docs.astral.sh/uv/getting-started/installation/) if
you don't already have it (`winget install --id=astral-sh.uv -e` on Windows, or
`curl -LsSf https://astral.sh/uv/install.sh | sh` on Linux/macOS). Then:

```bash
uv tool install git+https://github.com/Gharib89/crm
crm --version
```

`crm` is not published to PyPI, so install from the Git source (above) or a
wheel. If even the launcher uv places on your PATH is blocked, run the CLI as a
module — no executable is created at all:

```bash
uv run --from git+https://github.com/Gharib89/crm crm --version
```

## From source

```bash
pip install -e .
crm --version
```

Requires Python ≥ 3.9. See the README for the full per-platform walkthrough.

## Verify

```bash
crm --version
```

Then create a connection with [Add a profile](add-profile.md), or jump to the
[Quickstart](quickstart.md).
