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
import io
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from crm.utils import safe_xml
from crm.utils.d365_backend import D365Backend, D365Error, as_dict


def _export_translation_response(
    backend: D365Backend,
    solution_name: str,
    *,
    timeout: int | None = None,
) -> dict[str, Any]:
    """POST the ExportTranslation action and return its (dict-narrowed) response.

    The server builds the whole file inside this one request, so the read timeout
    follows `timeout` (else profile.async_timeout). Sole caller of the action, so
    the on-disk and in-memory export paths share one Web API call (issue #942).
    """
    read_timeout = timeout if timeout is not None else backend.profile.async_timeout
    return as_dict(
        backend.post(
            "solutions/Microsoft.Dynamics.CRM.ExportTranslation",
            json_body={"SolutionName": solution_name},
            timeout=read_timeout,
        )
    )


def _decode_translation_file(resp: dict[str, Any]) -> bytes:
    """Base64-decode the ExportTranslationFile payload, or raise D365Error."""
    encoded = resp.get("ExportTranslationFile")
    if not encoded:
        raise D365Error("ExportTranslation returned no ExportTranslationFile payload.")
    try:
        return base64.b64decode(encoded, validate=True)
    except ValueError as exc:  # binascii.Error subclasses ValueError
        raise D365Error(f"ExportTranslationFile is not valid base64: {exc}") from exc


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
    resp = _export_translation_response(backend, solution_name, timeout=timeout)
    if "_dry_run" in resp:
        return {**resp, "action": "ExportTranslation", "solution": solution_name}
    data = _decode_translation_file(resp)
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


def export_translation_bytes(
    backend: D365Backend,
    solution_name: str,
    *,
    timeout: int | None = None,
) -> bytes:
    """Export a solution's translations and return the decoded zip bytes.

    The in-memory counterpart to :func:`export_translation`: reuses the same
    ExportTranslation call and payload decode, but writes nothing to disk — for
    callers (e.g. ``form labels``, #942) that parse the CrmTranslations.xml in
    memory. Raises D365Error when the response carries no payload.
    """
    resp = _export_translation_response(backend, solution_name, timeout=timeout)
    return _decode_translation_file(resp)


# --- CrmTranslations.xml parsing (issue #942) -----------------------------------
#
# ExportTranslation returns a zip holding CrmTranslations.xml — an Excel 2003
# SpreadsheetML workbook. Its "Localized Labels" worksheet is a table whose header
# row is `Entity name | Object ID | Object Column Name | <LCID> | <LCID> …` and
# whose data rows carry one text cell per language column. Form element labels are
# the `displayname` rows (lowercase), keyed by the element's label object id; the
# same worksheet also holds attribute `DisplayName` rows (their object ids never
# collide with form-label ids), so the column-name match is case-insensitive.
#
# SpreadsheetML omits trailing empty cells and uses a 1-based `ss:Index` to skip
# gaps, so cells are read positionally honoring that index — never by raw order.

_SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"
_LOCALIZED_LABELS_SHEET = "Localized Labels"
# 1-based column positions of the fixed leading columns; language columns follow.
_OBJECT_ID_COL = 2
_COLUMN_NAME_COL = 3
_FIRST_LANGUAGE_COL = 4


def _local_name(tag: str) -> str:
    """The local part of a (possibly namespaced) ``{ns}tag`` name."""
    return tag.rsplit("}", 1)[-1]


def _ss_attr(element: ET.Element, name: str) -> str | None:
    """Read a ``ss:``-namespaced attribute by local name, tolerating no namespace."""
    return element.get(f"{{{_SS_NS}}}{name}", element.get(name))


def _row_cells(row: ET.Element) -> dict[int, str]:
    """Map a ``<Row>``'s 1-based column position → cell text, honoring ``ss:Index``.

    A ``<Cell>`` with ``ss:Index`` resets the running column to that position
    (SpreadsheetML's sparse-column encoding); otherwise columns advance by one.
    """
    cells: dict[int, str] = {}
    col = 0
    for cell in row:
        if _local_name(cell.tag) != "Cell":
            continue
        index = _ss_attr(cell, "Index")
        col = int(index) if index and index.isdigit() else col + 1
        data = next((c for c in cell if _local_name(c.tag) == "Data"), None)
        cells[col] = (data.text or "") if data is not None else ""
    return cells


def _localized_labels_rows(zip_bytes: bytes) -> list[ET.Element]:
    """The ``<Row>`` elements of the CrmTranslations.xml 'Localized Labels' sheet.

    Raises D365Error if the zip lacks CrmTranslations.xml or that worksheet.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise D365Error(f"ExportTranslation payload is not a valid zip: {exc}") from exc
    try:
        xml = archive.read("CrmTranslations.xml").decode("utf-8")
    except KeyError as exc:
        raise D365Error("ExportTranslation zip has no CrmTranslations.xml.") from exc
    root = safe_xml.fromstring(xml)
    for sheet in root.iter():
        if (
            _local_name(sheet.tag) == "Worksheet"
            and _ss_attr(sheet, "Name") == _LOCALIZED_LABELS_SHEET
        ):
            return [r for r in sheet.iter() if _local_name(r.tag) == "Row"]
    raise D365Error(
        f"CrmTranslations.xml has no {_LOCALIZED_LABELS_SHEET!r} worksheet; "
        "the export carries no localizable labels."
    )


def parse_localized_labels(zip_bytes: bytes) -> tuple[list[int], dict[str, dict[str, str]]]:
    """Parse a CrmTranslations.xml zip's 'Localized Labels' sheet.

    Returns ``(language_codes, by_object_id)`` where ``language_codes`` are the
    LCID header columns (ints) in sheet order and ``by_object_id`` maps each
    label's object id (lowercased, brace-stripped) to ``{lcid: text}`` over its
    non-empty language cells. The inner LCID keys are **strings** so the map
    serializes cleanly as a JSON object (JSON object keys are always strings).
    Only ``displayname`` rows (case-insensitive) are kept — form element labels
    plus attribute display names; other columns (Description, name) are skipped.
    Raises D365Error if the zip or worksheet is missing.
    """
    rows = _localized_labels_rows(zip_bytes)
    if not rows:
        return [], {}
    header = _row_cells(rows[0])
    languages: list[int] = []
    col = _FIRST_LANGUAGE_COL
    while col in header:
        text = header[col].strip()
        if text.isdigit():
            languages.append(int(text))
        col += 1
    by_object_id: dict[str, dict[str, str]] = {}
    for row in rows[1:]:
        cells = _row_cells(row)
        column_name = cells.get(_COLUMN_NAME_COL, "").strip().lower()
        if column_name != "displayname":
            continue
        object_id = cells.get(_OBJECT_ID_COL, "").strip().strip("{}").lower()
        if not object_id:
            continue
        texts = {
            str(lcid): cells[_FIRST_LANGUAGE_COL + i]
            for i, lcid in enumerate(languages)
            if cells.get(_FIRST_LANGUAGE_COL + i)
        }
        if texts:
            by_object_id.setdefault(object_id, {}).update(texts)
    return languages, by_object_id


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
