# Install

The canonical install instructions live in the project
[README](https://github.com/Gharib89/crm#install) and are summarised here.

## Install script (no Python required)

**Windows (PowerShell):**

```powershell
irm https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.ps1 | iex
```

**Linux:**

```bash
curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | sh
```

Pin a version by setting `CRM_VERSION` (e.g. `v0.6.0`). The binary is unsigned;
Windows SmartScreen may warn on first run.

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

## From source

```bash
pip install -e .
crm --version
```

Requires Python ≥ 3.9. See the README for the full per-platform walkthrough.
