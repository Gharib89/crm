# Install

The canonical install instructions live in the project
[README](https://github.com/Gharib89/crm#install) and are summarised here.

## Install script (no Python required)

**Windows (PowerShell):**

```powershell
irm https://pub-REPLACE_ME.r2.dev/install.ps1 | iex
```

**Linux:**

```bash
curl -fsSL https://pub-REPLACE_ME.r2.dev/install.sh | sh
```

Pin a version by setting `CRM_VERSION` (e.g. `v0.6.0`). The binary is unsigned;
Windows SmartScreen may warn on first run.

## From source

```bash
pip install -e .
crm --version
```

Requires Python ≥ 3.9. See the README for the full per-platform walkthrough.
