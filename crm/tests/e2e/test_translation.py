# pyright: basic
"""E2E tests for translation export/import commands."""
from __future__ import annotations

import zipfile

from crm.tests.e2e.coverage import covers


@covers("translation export", "translation import")
def test_translation_export_import_roundtrip(cli, ephemeral_solution, tmp_path):
    """Export translations for the ephemeral solution, then import them back.

    ExportTranslation returns a zip even when the solution has no custom labels
    (it always contains CrmTranslations.xml + [Content_Types].xml). Importing
    an unchanged export back is a no-op that still returns ok, giving us a
    fully repeatable round-trip without creating org data.
    """
    zip_path = tmp_path / "translations.zip"

    # Export
    result = cli([
        "--json", "translation", "export",
        "--solution", ephemeral_solution,
        "--output", str(zip_path),
    ])
    import json as _json
    export_data = _json.loads(result.stdout)
    assert export_data["ok"] is True, (
        f"translation export failed for {ephemeral_solution}: {export_data}"
    )
    assert zip_path.exists(), "translation export did not produce an output file"
    assert zip_path.stat().st_size > 0, "translation export produced an empty zip"
    assert zipfile.is_zipfile(zip_path), "translation export output is not a valid zip"

    # Import the exported zip back — round-trip with an unchanged file is a no-op
    result = cli([
        "--json", "translation", "import",
        "--yes",
        str(zip_path),
    ])
    import_data = _json.loads(result.stdout)
    assert import_data["ok"] is True, (
        f"translation import failed for {ephemeral_solution}: {import_data}"
    )
    assert "import_job_id" in import_data["data"], (
        f"import_job_id missing from translation import response: {import_data['data']}"
    )
