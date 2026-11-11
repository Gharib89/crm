"""Check that a repository carries the patches of a convert ledger: read only (ADR 0034).

`convert` records, for each patch of a repository file, the SHA-256 of the original and of
the patched text, both over normalized text (`normalize_text`: no BOM, LF). This module
hashes the file the entry names in the repository the same way and reports which side it
equals. It writes nothing and touches no org.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from crm.core.inventory import InventoryError, normalize_text
from crm.core.inventory_convert import LEDGER_NAME, SCHEMA_VERSION

#: What a checkable entry reports; a ledger entry with no repository file is `not_checkable`.
STATUSES = ("applied", "not_applied", "diverged", "missing", "not_checkable")


@dataclass(frozen=True)
class Row:
    package: str
    kind: str
    key: str
    rule: str
    status: str
    path: str | None
    reason: str | None = None


@dataclass(frozen=True)
class AppliedReport:
    rows: tuple[Row, ...]

    @property
    def ok(self) -> bool:
        """Every checkable entry is applied; not-checkable entries do not count."""
        return all(r.status in ("applied", "not_checkable") for r in self.rows)

    def summary(self) -> dict[str, Any]:
        counts = Counter(r.status for r in self.rows)
        return {
            "all_applied": self.ok,
            "counts": {s: counts[s] for s in STATUSES},
            "rows": [asdict(r) for r in self.rows],
        }


def _read_ledger(out: Path) -> list[dict[str, Any]]:
    path = out / LEDGER_NAME
    try:
        ledger: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # ValueError: malformed UTF-8 or JSON
        raise InventoryError(f"cannot read the conversion ledger {path}: {exc}") from exc
    body = cast(dict[str, Any], ledger) if isinstance(ledger, dict) else {}
    version = body.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise InventoryError(
            f"{path}: schema_version {version!r} is not supported;"
            f" this crm reads ledger schema_version {SCHEMA_VERSION}"
        )
    entries = body.get("entries")
    if not isinstance(entries, list):
        raise InventoryError(f"{path}: entries must be a list")
    return cast(list[dict[str, Any]], entries)


def check_applied(out: Path, repo: Path) -> AppliedReport:
    """Compare each patched ledger entry with its repository file.

    Raises:
        InventoryError: the ledger is missing, unreadable or of another schema version.
    """
    rows: list[Row] = []
    for e in _read_ledger(out):
        try:
            who = (e["package"], e["kind"], e["key"], e["rule"])
        except KeyError as exc:
            raise InventoryError(f"{out / LEDGER_NAME}: an entry lacks {exc}") from exc
        rel, original, patched = (
            e.get("repo_path"),
            e.get("original_sha256"),
            e.get("patched_sha256"),
        )
        if e.get("status") != "patched":
            rows.append(Row(*who, "not_checkable", rel, "a manual-review entry: no patch to check"))
            continue
        if rel is None:
            rows.append(
                Row(
                    *who,
                    "not_checkable",
                    None,
                    "no repository file: the patch is for an inventory artifact only",
                )
            )
            continue
        path = (repo / rel).resolve()
        try:
            if not path.is_relative_to(repo.resolve()):
                raise OSError("the path leaves the repository")
            digest = hashlib.sha256(normalize_text(path.read_bytes())).hexdigest()
        except OSError as exc:
            reason = f"the file cannot be read: {exc.strerror or exc}"
            rows.append(Row(*who, "missing", rel, reason))
            continue
        status = (
            "applied" if digest == patched else "not_applied" if digest == original else "diverged"
        )
        rows.append(Row(*who, status, rel))
    return AppliedReport(tuple(rows))
