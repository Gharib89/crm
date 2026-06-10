"""Solution translation export/import via ExportTranslation / ImportTranslation.

ExportTranslation is bound to the ``solutions`` entity collection and returns
the compressed translations file (CrmTranslations.xml + [Content_Types].xml)
base64-encoded in the action response; ImportTranslation is unbound and takes
the same compressed file back, base64-encoded, plus a client-supplied
ImportJobId. Both run synchronously — there is no async variant with a
download step like ExportSolutionAsync.
"""

from __future__ import annotations

import base64
import uuid
import zipfile
from pathlib import Path
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict


def export_translation(
    backend: D365Backend,
    solution_name: str,
    output_path: str | Path,
    *,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Export all translations for a solution to a zip on disk.

    The server builds the whole file inside this one request, so the read
    timeout follows `timeout` (else profile.async_timeout). Returns a dict
    with the on-disk path, byte count, solution name, and duration in ms.
    Raises D365Error when the response carries no payload.
    """
    import time as _time

    started = _time.monotonic()
    read_timeout = timeout if timeout is not None else backend.profile.async_timeout
    resp = as_dict(backend.post(
        "solutions/Microsoft.Dynamics.CRM.ExportTranslation",
        json_body={"SolutionName": solution_name},
        timeout=read_timeout,
    ))
    if "_dry_run" in resp:
        return {**resp, "action": "ExportTranslation", "solution": solution_name}
    encoded = resp.get("ExportTranslationFile")
    if not encoded:
        raise D365Error("ExportTranslation returned no ExportTranslationFile payload.")
    data = base64.b64decode(encoded)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {
        "output": str(out),
        "bytes": len(data),
        "solution": solution_name,
        "action": "ExportTranslation",
        "duration_ms": int((_time.monotonic() - started) * 1000),
    }


def import_translation(
    backend: D365Backend,
    zip_path: str | Path,
    *,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Import a translations zip via the synchronous ImportTranslation action.

    The file must be the compressed translations package (the edited
    CrmTranslations.xml zipped back up), not the bare XML — validated locally
    before any HTTP call. The whole import runs inside one request, so the
    read timeout follows `timeout` (else profile.async_timeout). Returns a
    dict carrying the client-supplied import_job_id; per-component results are
    retrievable from the importjobs row / RetrieveFormattedImportJobResults.
    """
    import time as _time

    p = Path(zip_path)
    if not p.is_file():
        raise D365Error(f"Translation file not found: {zip_path}")
    if not zipfile.is_zipfile(p):
        raise D365Error(
            f"{zip_path} is not a zip archive. ImportTranslation takes the "
            "compressed translations package (zip the edited CrmTranslations.xml "
            "+ [Content_Types].xml back up), not the bare XML."
        )
    encoded = base64.b64encode(p.read_bytes()).decode("ascii")
    import_job_id = str(uuid.uuid4())
    body: dict[str, Any] = {
        "TranslationFile": encoded,
        "ImportJobId": import_job_id,
    }

    started = _time.monotonic()
    read_timeout = timeout if timeout is not None else backend.profile.async_timeout
    resp = backend.post("ImportTranslation", json_body=body, timeout=read_timeout)
    if isinstance(resp, dict) and "_dry_run" in resp:
        return {**resp, "action": "ImportTranslation", "import_job_id": import_job_id}
    return {
        "import_job_id": import_job_id,
        "status": "succeeded",
        "action": "ImportTranslation",
        "duration_ms": int((_time.monotonic() - started) * 1000),
    }
