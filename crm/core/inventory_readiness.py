"""The readiness checklist: an `assess --target online` findings file read as one row per gate.

Offline, and a roll-up only: every verdict comes from the findings file and the rules that
made it, nothing is re-judged. A checklist, not the OP2OL service, Lifecycle Services or the
upgrade hops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crm.core.inventory import InventoryError
from crm.core.inventory_assess import (
    NO_RULE,
    Finding,
    FindingsFile,
    HostPattern,
    PluginBinary,
    PluginProject,
    PluginSource,
    Rule,
    Rules,
    load_rules,
    read_findings,
)

ONLINE_BASIS = "Dataverse online requirement"
WHITEPAPER = "OP2OL whitepaper (https://aka.ms/op2olwhitepaper)"
REBUILD_BASIS = "Dataverse plug-in build target (deprecated, not a blocker)"
MIN_SOURCE_VERSION = (9, 0)
_DOTTED = re.compile(r"[0-9]+(\.[0-9]+)*")
_REASONS_SHOWN = 3


@dataclass(frozen=True)
class GateInfo:
    """What a rule's gate says beyond its findings.

    *basis* is where the gate comes from; *warn* turns a blocker into a ``warn`` and is the
    note shown with it, and makes a gate of a rule of another class too; *gap* names the
    unattached extraction gap (by its description) that leaves the gate ``unknown``.
    """

    basis: str = ONLINE_BASIS
    warn: str | None = None
    gap: str | None = None


# Rule id -> gate details; a rule absent here is an online blocker that is not a documented
# OP2OL gate (basis ONLINE_BASIS). OP2OL gates are in the whitepaper; the Learn overview
# lists almost none of them.
OP2OL_GATES: dict[str, GateInfo] = {
    "step-on-systemuser": GateInfo(basis=WHITEPAPER),
    "assembly-isolation-none": GateInfo(
        basis=WHITEPAPER,
        warn="The migration converts non-sandboxed plug-ins to sandbox mode;"
        " the code may still fail in the sandbox.",
    ),
    "report-sql-datasource": GateInfo(gap="shared data sources of SSRS reports"),
    "plugin-binary-sdk-net462": GateInfo(
        basis=REBUILD_BASIS,
        warn="Read from the registered binary, not the source: the assembly needs a rebuild at"
        " .NET Framework 4.6.2 or later against the current SDK.",
    ),
}

# Gates the API cannot see, so no status: the whitepaper's list, in its words where known.
MANUAL_GATES: tuple[str, ...] = (
    "Database collation",
    "Database size",
    "Transparent Data Encryption (TDE)",
    "Database integrity (DBCC CHECKDB)",
    "Target tenant users and licences",
    "The rest of the whitepaper's pre-migration list",
)

STATUSES = ("fail", "warn", "unknown", "pass")


@dataclass(frozen=True)
class Row:
    """One gate: its *status*, the *components* behind it, the *rules* that decided it."""

    gate: str
    status: str
    components: int
    rules: tuple[str, ...]
    basis: str
    note: str

    @property
    def display_status(self) -> str:
        return (
            "pass (0 components)" if self.status == "pass" and not self.components else self.status
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status,
            "components": self.components,
            "rules": list(self.rules),
            "basis": self.basis,
            "note": self.note,
        }


@dataclass(frozen=True)
class Readiness:
    source_version: str
    target: str
    rows: tuple[Row, ...]
    manual: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_version": self.source_version,
            "target": self.target,
            "rows": [r.to_dict() for r in self.rows],
            "manual": list(self.manual),
        }


def _version_row(text: str) -> Row:
    if not _DOTTED.fullmatch(text):
        return Row(
            "source-version",
            "unknown",
            0,
            (),
            WHITEPAPER,
            f"source version {text!r} is not a dotted version",
        )
    parts = tuple(int(p) for p in text.split("."))
    # Pad first: a bare "9" is 9.0, though (9,) sorts below (9, 0).
    ok = (parts + (0,) * len(MIN_SOURCE_VERSION))[: len(MIN_SOURCE_VERSION)] >= MIN_SOURCE_VERSION
    need = ".".join(map(str, MIN_SOURCE_VERSION))
    note = f"source version {text}" + ("" if ok else f"; the migration needs {need} or later")
    return Row("source-version", "pass" if ok else "fail", 0, (), WHITEPAPER, note)


def _rule_row(rule: Rule, findings: list[Finding], found: FindingsFile) -> Row:
    info = OP2OL_GATES.get(rule.id, GateInfo())
    of_kind = [f for f in findings if f.kind == rule.kind]
    total = {f.key for f in of_kind}
    hits = {f.key for f in of_kind if f.rule == rule.id and f.cls == rule.cls}
    unknown = [f for f in of_kind if f.cls == "unknown" and f.rule in (rule.id, None)]
    reasons = {f.reason or "unknown" for f in unknown}
    for gap in found.unattached_gaps:
        if gap.get("kind") == rule.kind or (info.gap and info.gap in str(gap.get("description"))):
            reasons.add(f"extraction gap: {gap.get('reason')}")
    if rule.kind in found.incomplete:
        reasons.add(f"kind coverage {found.incomplete[rule.kind]}")
    if rule.kind in found.uncovered_kinds:
        # Its records carry no finding: the rule ran on none of them, so the gate cannot pass.
        reasons.add(f"{NO_RULE}: {found.uncovered_kinds[rule.kind]} {rule.kind} record(s)")
    d = rule.detection
    # The findings file does not say which assemblies the source map matched, and assess reads no
    # binary of those, so this row counts only what it read and names where the rest are judged.
    mapped = (
        "; an assembly mapped to one project is judged on the csproj-sdk-net462 row"
        if isinstance(d, PluginBinary)
        else ""
    )
    if not hits and rule.id not in found.sources:
        # A rule that needs an input the findings file does not record cannot be passed.
        if isinstance(d, HostPattern) and not found.source_hosts:
            reasons.add("not run: assess was given no --source-host")
        elif isinstance(d, PluginProject | PluginSource):
            reasons.add("not run unless assess was given --source-map and --repo")
    ids = (rule.id,)
    if hits:
        status = "warn" if info.warn else "fail"
        passed = len(total - hits - {f.key for f in unknown})
        count = "" if mapped else f" ({passed} of {len(total)} pass this gate)"
        note = f"{info.warn or rule.title}{count}{mapped}"
        return Row(rule.id, status, len(hits), ids, info.basis, note)
    if reasons:
        shown = sorted(reasons)[:_REASONS_SHOWN]
        more = len(reasons) - len(shown)
        note = "; ".join(shown) + (f"; and {more} more" if more else "") + mapped
        return Row(rule.id, "unknown", len({f.key for f in unknown}), ids, info.basis, note)
    # What passed, never the rule's title: that names the condition a failing row reports.
    note = (
        f"all {len(total)} {rule.kind} record(s) pass this gate"
        if total
        else f"no {rule.kind} records to judge"
    )
    return Row(rule.id, "pass", len(total), ids, info.basis, note + mapped)


def readiness(findings_file: Path, rules: Rules | None = None) -> Readiness:
    """The checklist for *findings_file*, judged by *rules* (default: the packaged ones).

    Raises:
        InventoryError: the file is unreadable, was written for another target than
            ``online``, dropped findings by usage, or was made with other rules than *rules*.
    """
    found = read_findings(findings_file)
    if found.target != "online":
        raise InventoryError(
            f"findings file {findings_file} was written for --target {found.target}: run"
            " `crm inventory assess INVENTORY --target online --out FILE`, then readiness on FILE"
        )
    if found.usage_filtered:
        raise InventoryError(
            f"findings file {findings_file} dropped {found.usage_filtered} finding(s)"
            " (usage_filtered): a dropped blocker or unknown would read pass. Re-run"
            " `crm inventory assess INVENTORY --target online --out FILE` without"
            " --in-use or --exclude-unknown"
        )
    rules = rules or load_rules()
    if found.rules_sha256 and found.rules_sha256 != rules.sha256:
        raise InventoryError(
            f"findings file {findings_file} was made with other rules than this crm ships:"
            " re-run `crm inventory assess INVENTORY --target online --out FILE`"
        )
    findings = list(found.findings)
    gates = [
        r
        for r in rules.rules
        if "online" in r.targets and (r.cls == "blocker" or OP2OL_GATES.get(r.id, GateInfo()).warn)
    ]
    rows = [_version_row(found.version), *(_rule_row(r, findings, found) for r in gates)]
    rows[1:] = sorted(rows[1:], key=lambda r: (STATUSES.index(r.status), r.gate))
    manual = tuple({"gate": g, "basis": WHITEPAPER} for g in MANUAL_GATES)
    return Readiness(found.version, found.target, tuple(rows), manual)
