"""Convert the code of a migration inventory with registered transforms (ADR 0034).

`convert` reads a migration inventory, the findings file `assess` wrote and the
packages folder `packages` wrote, and optionally a source map. For each component
that carries a finding whose rule names a registered transform
(`inventory_transforms`), it plans the rewrite, passes it through the **emit gate**
and writes, under *out* only, per work package:

- ``<name>.diff``: a unified diff against the extracted text (normalized: no BOM, LF),
- ``<name>.patched<ext>``: the full patched file, its SHA-256 in the ledger,
- ``<name>.repo.diff``: the same patch against the repository path, only when the
  source map links the component to a file by hash (status ``matched``),
- ``ledger.json``: one entry per work package and component key, with a
  **manual-review entry** (file, line, rule, reason) for each site a rule matches but
  the transform cannot prove.

The emit gate writes a patch only if the patched code parses with no more error
nodes than the original and re-running the rule's detection finds exactly the
rewritten sites fewer. A rejected plan changes nothing and becomes a manual-review
entry. No file is written into the inventory or a repository. This module touches no org.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from crm.core.code_parse import error_count, parse
from crm.core.inventory import MANIFEST_NAME, InventoryError, normalize_text, read_inventory
from crm.core.inventory_assess import ContentEvidence, load_rules, read_findings
from crm.core.inventory_source_map import read_source_map
from crm.core.inventory_transforms import TRANSFORMS, Edit, Plan, Transform

#: The format version of the ledger.
SCHEMA_VERSION = 1
LEDGER_NAME = "ledger.json"
#: The package id of a component no work package holds.
UNPACKAGED = "unpackaged"


@dataclass(frozen=True)
class ManualReview:
    """A site a rule matches but the rewrite is not proven for. *line* is None for a whole file."""

    file: str
    line: int | None
    rule: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "rule": self.rule, "reason": self.reason}


@dataclass
class Entry:
    """One ledger row: a component in a work package, patched or left for review."""

    package: str
    kind: str
    key: str
    rule: str
    transform: str
    status: str  # "patched" | "manual-review"
    edits: int = 0
    original_sha256: str | None = None
    patched_sha256: str | None = None
    repo_path: str | None = None
    files: dict[str, str] = field(default_factory=lambda: {})
    manual_review: list[ManualReview] = field(default_factory=lambda: [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "kind": self.kind,
            "key": self.key,
            "rule": self.rule,
            "transform": self.transform,
            "status": self.status,
            "edits": self.edits,
            "original_sha256": self.original_sha256,
            "patched_sha256": self.patched_sha256,
            "repo_path": self.repo_path,
            "files": self.files,
            "manual_review": [m.to_dict() for m in self.manual_review],
        }


@dataclass(frozen=True)
class ConvertResult:
    manifest_sha256: str
    entries: tuple[Entry, ...]

    def ledger(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "inventory": {"manifest_sha256": self.manifest_sha256},
            "entries": [e.to_dict() for e in self.entries],
        }

    def summary(self) -> dict[str, Any]:
        return {
            "patched": sum(e.status == "patched" for e in self.entries),
            "manual_review": sum(len(e.manual_review) for e in self.entries),
            "entries": len(self.entries),
        }


def _hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _members(packages_dir: Path) -> dict[str, list[str]]:
    """Map each ``<kind>:<key>`` member to the ids of the work packages that hold it."""

    def load(path: Path) -> dict[str, Any]:
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InventoryError(f"cannot read {path}: {exc}") from exc
        if type(data) is not dict:
            raise InventoryError(f"{path}: not a JSON object")
        return cast(dict[str, Any], data)

    held: dict[str, list[str]] = {}
    packages = load(packages_dir / "index.json").get("packages")
    if type(packages) is not list:
        raise InventoryError(f"{packages_dir / 'index.json'}: packages is not a list")
    for item in cast(list[Any], packages):
        pid = cast(dict[str, Any], item).get("id") if type(item) is dict else None
        if type(pid) is not str:
            raise InventoryError(f"{packages_dir / 'index.json'}: malformed package {item!r}")
        members = load(packages_dir / f"{pid}.json").get("members")
        if type(members) is not list:
            raise InventoryError(f"{packages_dir / pid}.json: members is not a list")
        for member in cast(list[Any], members):
            held.setdefault(str(member), []).append(pid)
    return held


def _apply(source: bytes, edits: tuple[Edit, ...]) -> bytes | None:
    """Apply non-overlapping *edits*, or None when two overlap or one is out of range."""
    out, at = bytearray(), 0
    for e in edits:
        if e.start < at or e.end < e.start or e.end > len(source):
            return None
        out += source[at : e.start] + e.text
        at = e.end
    return bytes(out + source[at:])


def _gate(
    pattern: Any, transform: Transform, original: bytes, patched: bytes, n: int
) -> str | None:
    """The emit gate: None when the patch may be written, else why it may not."""
    try:
        before_text, after_text = original.decode("utf-8"), patched.decode("utf-8")
    except UnicodeDecodeError:
        return "the text is not UTF-8"
    before = error_count(parse(transform.language, original))
    after = error_count(parse(transform.language, patched))
    if after > before:
        return f"the patched code has {after - before} new syntax error node(s)"
    left = len(pattern.findall(before_text)) - len(pattern.findall(after_text))
    if left != n:
        return f"re-running the rule clears {left} of the {n} rewritten sites"
    return None


def _diff(old: str, new: str, path: str) -> str:
    def lines(text: str) -> list[str]:
        out = text.splitlines(keepends=True)
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n\\ No newline at end of file\n"
        return out

    return "".join(difflib.unified_diff(lines(old), lines(new), f"a/{path}", f"b/{path}"))


def convert(
    inventory_dir: Path,
    findings_path: Path,
    packages_dir: Path,
    out: Path,
    *,
    source_map_path: Path | None = None,
) -> ConvertResult:
    """Plan, gate and write the conversion of every component carrying a transformable finding.

    Raises:
        InventoryError: an input cannot be read or belongs to another inventory, or
            *out* is inside the inventory or not empty.
    """
    inv_root, out_root = inventory_dir.resolve(), out.resolve()
    if out_root == inv_root or inv_root in out_root.parents:
        raise InventoryError(f"--out {out} is inside the inventory; name a folder outside it")
    if out.exists() and any(out.iterdir()):
        raise InventoryError(f"--out {out} is not empty; name a new or empty folder")
    inv = read_inventory(inventory_dir)
    manifest_sha = _hex((inventory_dir / MANIFEST_NAME).read_bytes())
    findings = read_findings(findings_path)
    if findings.manifest_sha256 != manifest_sha:
        raise InventoryError(
            "the findings file was made from another inventory (its manifest_sha256 differs);"
            " re-run `crm inventory assess` on this one"
        )
    matched: dict[tuple[str, str], str] = {}
    if source_map_path is not None:
        source_map = read_source_map(source_map_path)
        if source_map.inventory.get("manifest_sha256") != manifest_sha:
            raise InventoryError(
                "the source map was made from another inventory (its manifest_sha256 differs);"
                " re-run `crm inventory map-source` on this one"
            )
        matched = {
            (c.kind, c.key): c.files[0]
            for c in source_map.components
            if c.status == "matched" and c.files
        }
    held = _members(packages_dir)
    records = {(r.kind, r.key): r for r in inv.records}
    rules = {r.id: r for r in load_rules().rules}

    entries: list[Entry] = []
    seen: set[tuple[str, str, str]] = set()
    for f in findings.findings:
        rule = rules.get(f.rule or "")
        if (
            rule is None
            or rule.transform is None
            or f.cls == "clear"
            or not isinstance(f.evidence, ContentEvidence)
            or (f.kind, f.key, f.evidence.path) in seen
        ):
            continue
        seen.add((f.kind, f.key, f.evidence.path))
        transform = TRANSFORMS.get(rule.transform)
        if transform is None:
            raise InventoryError(
                f"rule {rule.id} names transform {rule.transform!r}, not registered"
            )
        record = records.get((f.kind, f.key))
        path = f.evidence.path
        if (
            record is None
            or record.fields.get("web_resource_type") not in transform.web_resource_types
        ):
            reason = f"transform {rule.transform} does not read this web resource type"
            for pkg in held.get(f"{f.kind}:{f.key}", [UNPACKAGED]):
                entries.append(Entry(pkg, f.kind, f.key, rule.id, rule.transform, "manual-review"))
                entries[-1].manual_review.append(ManualReview(path, None, rule.id, reason))
            continue
        try:
            raw = (inventory_dir / path).read_bytes()
        except OSError:
            raw = b""
        if _hex(raw) != f.evidence.sha256:
            reason = "artifact changed since extraction"
            plan, source = Plan((), ()), b""
        else:
            source = normalize_text(raw)
            plan = transform.plan(parse(transform.language, source))
            reason = ""
        if not plan.edits and not plan.unproven and not reason:
            continue
        patched = _apply(source, plan.edits) if plan.edits else None
        if plan.edits and not reason:
            if patched is None:
                reason = "the planned edits overlap"
            else:
                pattern = cast(Any, rule.detection).pattern
                reason = _gate(pattern, transform, source, patched, len(plan.edits)) or ""
        for pkg in held.get(f"{f.kind}:{f.key}", [UNPACKAGED]):
            entry = Entry(pkg, f.kind, f.key, rule.id, rule.transform, "manual-review")
            entry.manual_review = [
                ManualReview(path, u.line, rule.id, u.reason) for u in plan.unproven
            ]
            if reason:
                msg = f"emit gate rejected the rewrite: {reason}" if plan.edits else reason
                entry.manual_review.insert(0, ManualReview(path, None, rule.id, msg))
            elif patched is not None:
                entry.status, entry.edits = "patched", len(plan.edits)
                entry.original_sha256, entry.patched_sha256 = _hex(source), _hex(patched)
                entry.repo_path = matched.get((f.kind, f.key))
                _write(out, entry, path, source, patched)
            entries.append(entry)
    entries.sort(key=lambda e: (e.package, e.kind, e.key))
    result = ConvertResult(manifest_sha, tuple(entries))
    out.mkdir(parents=True, exist_ok=True)
    (out / LEDGER_NAME).write_text(
        json.dumps(result.ledger(), sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _write(out: Path, entry: Entry, path: str, source: bytes, patched: bytes) -> None:
    """Write the diff, the patched file and, for a matched source, the repo-path diff."""
    folder = out / entry.package
    folder.mkdir(parents=True, exist_ok=True)
    name = Path(path).name
    stem, _, ext = name.partition(".content")
    old, new = source.decode("utf-8"), patched.decode("utf-8")
    files = {
        "diff": (f"{stem}.diff", _diff(old, new, path)),
        "patched": (f"{stem}.patched{ext}", new),
    }
    if entry.repo_path is not None:
        files["repo_diff"] = (f"{stem}.repo.diff", _diff(old, new, entry.repo_path))
    for label, (fname, text) in files.items():
        (folder / fname).write_text(text, encoding="utf-8", newline="")
        entry.files[label] = f"{entry.package}/{fname}"
