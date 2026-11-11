"""Read a Power Apps Solution Checker report a person already produced (ADR 0004, ADR 0030).

`pac solution check` writes a zip holding SARIF v2 JSON; this module reads that zip or the bare
SARIF file and nothing else. It never runs `pac` and never calls the checker service. The layout
is the one captured in #1197: results under ``runs[].results``, the rule catalog under
``runs[].tool.driver.rules``, the file as the web resource's unique name with a leading slash
under the first location's artifact location, lines under its region and the severity under the
result's ``properties`` (the SARIF ``level`` is absent on Medium results, so it decides nothing).
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from crm.core.inventory import InventoryError

OVERRIDES_PATH = Path(__file__).resolve().parent.parent / "rules" / "checker-overrides.json"
OVERRIDES_SCHEMA_VERSION = 1
_CLASSES = ("blocker", "deprecated", "optional")
#: The class a checker severity maps to, before the override table; keys are lowercase.
SEVERITY_CLASS = {
    "critical": "blocker",
    "high": "blocker",
    "medium": "deprecated",
    "low": "optional",
    "informational": "optional",
}


@dataclass(frozen=True)
class CheckerResult:
    """One result: the checker *rule* id as written, *severity* text, the file *uri* and place."""

    rule: str
    severity: str | None
    uri: str
    line: int | None
    column: int
    end_line: int | None
    message: str


@dataclass(frozen=True)
class CheckerRule:
    """A catalog entry: what a finding's ``rules_hit`` row says about the checker rule."""

    title: str
    fix: str
    source: str | None


@dataclass(frozen=True)
class CheckerReport:
    """A report read back: the SHA-256 of the file as given, its results and rule catalog."""

    sha256: str
    results: tuple[CheckerResult, ...]
    rules: dict[str, CheckerRule]


def _get(obj: Any, *names: str) -> Any:
    for name in names:
        obj = cast(dict[str, Any], obj).get(name) if isinstance(obj, dict) else None
    return obj


def _list(value: Any) -> list[Any]:
    return cast(list[Any], value) if type(value) is list else []


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    return value if type(value) is int and value >= 1 else None


def _sarif(raw: bytes, where: str) -> tuple[list[CheckerResult], dict[str, CheckerRule]]:
    try:
        doc: Any = json.loads(raw.decode("utf-8-sig"))
    except ValueError as exc:  # ValueError: malformed UTF-8 or JSON
        raise InventoryError(f"{where} is not SARIF JSON: {exc}") from exc
    runs = _get(doc, "runs")
    if type(runs) is not list:
        raise InventoryError(f"{where} is not a SARIF file: it has no runs list")
    results: list[CheckerResult] = []
    catalog: dict[str, CheckerRule] = {}
    for run in _list(runs):
        for entry in _list(_get(run, "tool", "driver", "rules")):
            rule_id = _text(_get(entry, "id"))
            if rule_id is None:
                continue
            title = _text(_get(entry, "shortDescription", "text")) or _text(_get(entry, "name"))
            fix = _text(_get(entry, "help", "text")) or _text(
                _get(entry, "fullDescription", "text")
            )
            catalog[rule_id] = CheckerRule(
                title or rule_id, fix or title or rule_id, _text(_get(entry, "helpUri"))
            )
        for item in _list(_get(run, "results")):
            rule = _text(_get(item, "ruleId"))
            if rule is None:
                raise InventoryError(f"{where} holds a result with no ruleId")
            places = _list(_get(item, "locations"))
            place = places[0] if places else None
            region = _get(place, "physicalLocation", "region")
            end = _int(_get(region, "endLine"))
            results.append(
                CheckerResult(
                    rule,
                    _text(_get(item, "properties", "severity")),
                    _text(_get(place, "physicalLocation", "artifactLocation", "uri")) or "",
                    _int(_get(region, "startLine")),
                    _int(_get(region, "startColumn")) or 1,
                    end,
                    _text(_get(item, "message", "text")) or "",
                )
            )
    return results, catalog


def read_report(path: Path) -> CheckerReport:
    """Read the zip `pac solution check` wrote, or the SARIF file inside it.

    Raises:
        InventoryError: the file is unreadable, a zip with no ``.sarif`` file, or not SARIF.
    """
    where = f"checker report {path}"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InventoryError(f"cannot read {where}: {exc}") from exc
    results: list[CheckerResult] = []
    catalog: dict[str, CheckerRule] = {}
    if zipfile.is_zipfile(io.BytesIO(raw)):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = sorted(n for n in archive.namelist() if n.lower().endswith(".sarif"))
                if not names:
                    raise InventoryError(f"{where} is a zip with no .sarif file")
                for name in names:
                    found, rules = _sarif(archive.read(name), f"{where}: {name}")
                    results += found
                    catalog |= rules
        except (
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            OSError,
            RuntimeError,
            NotImplementedError,
        ) as exc:
            raise InventoryError(f"cannot read {where}: {exc}") from exc
    else:
        results, catalog = _sarif(raw, where)
    return CheckerReport(hashlib.sha256(raw).hexdigest(), tuple(results), catalog)


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, str]:
    """The class each named checker rule gets whatever its severity, from the table at *path*.

    Raises:
        InventoryError: the table is unreadable, of another version or malformed.
    """
    where = f"checker overrides {path}"
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:  # ValueError: malformed UTF-8 or JSON
        raise InventoryError(f"cannot read {where}: {exc}") from exc
    if _get(data, "schema_version") != OVERRIDES_SCHEMA_VERSION:
        raise InventoryError(
            f"{where}: schema_version is not supported; this crm reads {OVERRIDES_SCHEMA_VERSION}"
        )
    table: dict[str, str] = {}
    for item in _list(_get(data, "overrides")):
        rule, cls = _text(_get(item, "rule")), _get(item, "class")
        if rule is None or cls not in _CLASSES:
            raise InventoryError(f"{where}: each override needs a rule and a class in {_CLASSES}")
        table[rule] = cast(str, cls)
    return table


def result_class(result: CheckerResult, overrides: dict[str, str]) -> str | None:
    """The finding class of *result*: its rule's override, else its severity's; None if unknown."""
    if result.rule in overrides:
        return overrides[result.rule]
    return SEVERITY_CLASS.get((result.severity or "").lower())
