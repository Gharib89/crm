"""Judge recorded verification results against the work packages' requirements (ADR 0032).

A **verification results file** records, for each verification requirement a
work package states (``crm inventory packages``), the outcome of a check someone
ran: ``pass``, ``fail``, ``blocked`` (the check could not run, an environment
down, say) or ``not_tested``, with its evidence. Its format is
``crm/schemas/verification-results-v1.schema.json``; the reader checks structure
without it, so no runtime code depends on ``jsonschema``. This module runs no
check and touches no org: it only judges what was recorded, and never calls a
package complete without a passing, evidenced result for every requirement.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from crm.core.inventory import (
    _SHA256,  # pyright: ignore[reportPrivateUsage]  # one hex-digest rule for every inventory file
    InventoryError,
)
from crm.core.inventory_packages import read_packages

#: The format version of the results file and of the report.
SCHEMA_VERSION = 1
OUTCOMES = ("pass", "fail", "blocked", "not_tested")
#: Package verdicts in precedence order: the first that applies wins.
PACKAGE_VERDICTS = ("failed", "blocked", "not_tested", "complete")


@dataclass(frozen=True)
class Evidence:
    """An artifact a result points to: a path relative to the results file's folder."""

    path: str
    sha256: str


@dataclass(frozen=True)
class Deployed:
    """The deployed artifact a check exercised: its component id and content hash."""

    component: str
    sha256: str


@dataclass(frozen=True)
class Environment:
    """Where a check ran: the crm profile name and the org's identity."""

    profile: str
    org: str


@dataclass(frozen=True)
class VerificationResult:
    """One requirement's recorded outcome with its evidence; a pass without evidence is not a pass.

    A *negative_control* result records a deliberately introduced regression, so
    its expected outcome is ``fail``.
    """

    package: str
    requirement: str
    outcome: str
    evidence: tuple[Evidence, ...]
    expected: str | None
    actual: str | None
    deployed: tuple[Deployed, ...]
    errors: tuple[str, ...]
    environment: Environment | None
    open_decisions: tuple[str, ...]
    negative_control: bool


@dataclass(frozen=True)
class Results:
    """A results file: the packages output it was recorded against, and its results."""

    packages_sha256: str
    results: tuple[VerificationResult, ...]


def _obj(value: object, where: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise InventoryError(f"results file {where} is not an object")
    return cast(dict[str, Any], value)


def _list(value: object, where: str) -> list[Any]:
    if type(value) is not list:
        raise InventoryError(f"results file {where} is not a list")
    return cast(list[Any], value)


def _str(obj: dict[str, Any], name: str, where: str, *, optional: bool = False) -> Any:
    value = obj.get(name)
    if (value is None and optional) or isinstance(value, str):
        return value
    raise InventoryError(f"results file {where}.{name} is not a string")


def _strings(obj: dict[str, Any], name: str, where: str) -> tuple[str, ...]:
    items = _list(obj.get(name, []), f"{where}.{name}")
    if not all(isinstance(i, str) for i in items):
        raise InventoryError(f"results file {where}.{name} holds a non-string")
    return tuple(cast(list[str], items))


def _sha(obj: dict[str, Any], where: str) -> str:
    value = _str(obj, "sha256", where)
    if not _SHA256.fullmatch(value):
        raise InventoryError(f"results file {where}.sha256 is not 64 lowercase hex digits")
    return value


def _evidence(value: object, where: str) -> Evidence:
    obj = _obj(value, where)
    path = _str(obj, "path", where)
    parts = PurePosixPath(path).parts
    unsafe = any(c in path for c in "\\:\0") or path.startswith("/") or ".." in parts
    if not parts or unsafe:
        raise InventoryError(
            f"results file {where}.path {path!r} must be a relative path inside the"
            " results file's folder"
        )
    return Evidence(path, _sha(obj, where))


def _deployed(value: object, where: str) -> Deployed:
    obj = _obj(value, where)
    return Deployed(_str(obj, "component", where), _sha(obj, where))


def _result(value: object, where: str) -> VerificationResult:
    obj = _obj(value, where)
    outcome = _str(obj, "outcome", where)
    if outcome not in OUTCOMES:
        raise InventoryError(f"results file {where}.outcome {outcome!r} is not one of {OUTCOMES}")
    env = obj.get("environment")
    environment = None
    if env is not None:
        at = f"{where}.environment"
        e = _obj(env, at)
        environment = Environment(_str(e, "profile", at), _str(e, "org", at))
    control = obj.get("negative_control", False)
    if type(control) is not bool:
        raise InventoryError(f"results file {where}.negative_control is not a boolean")
    return VerificationResult(
        package=_str(obj, "package", where),
        requirement=_str(obj, "requirement", where),
        outcome=outcome,
        evidence=tuple(
            _evidence(v, f"{where}.evidence[{i}]")
            for i, v in enumerate(_list(obj.get("evidence"), f"{where}.evidence"))
        ),
        expected=_str(obj, "expected", where, optional=True),
        actual=_str(obj, "actual", where, optional=True),
        deployed=tuple(
            _deployed(v, f"{where}.deployed[{i}]")
            for i, v in enumerate(_list(obj.get("deployed", []), f"{where}.deployed"))
        ),
        errors=_strings(obj, "errors", where),
        environment=environment,
        open_decisions=_strings(obj, "open_decisions", where),
        negative_control=control,
    )


def parse_results(data: object) -> Results:
    """Build `Results` from a decoded results file.

    The version rule runs first: a ``schema_version`` other than this module's
    is refused. A field this version does not define is ignored.

    Raises:
        InventoryError: the file breaks the results format; the message names where.
    """
    obj = _obj(data, "file")
    version = obj.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise InventoryError(
            f"results file schema_version {version!r} is not supported; "
            f"this crm reads schema_version {SCHEMA_VERSION}"
        )
    sha = _str(obj, "packages_sha256", "file")
    if not _SHA256.fullmatch(sha):
        raise InventoryError("results file packages_sha256 is not 64 lowercase hex digits")
    items = _list(obj.get("results"), "results")
    return Results(sha, tuple(_result(v, f"results[{i}]") for i, v in enumerate(items)))


def read_results(path: Path) -> Results:
    """Read and check a verification results file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:  # ValueError: malformed UTF-8 or JSON
        raise InventoryError(f"cannot read {path}: {exc}") from exc
    return parse_results(data)


# ── Verdicts ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RequirementVerdict:
    """One requirement's status: ``pass``, ``fail``, ``blocked`` or ``not_tested``.

    *reason* says why a requirement is ``not_tested``: ``no_result``, ``recorded``
    (its result says so), ``no_evidence``, ``evidence_unverified`` (an evidence
    file is missing or its hash differs) or ``control_escaped``. *controls* holds
    each negative control's status: ``held``, ``escaped`` or ``not_run``.
    """

    id: str
    kind: str
    member: str
    status: str
    reason: str | None
    controls: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "member": self.member,
            "status": self.status,
            "reason": self.reason,
            "controls": list(self.controls),
        }


@dataclass(frozen=True)
class PackageVerdict:
    """One package's verdict over its requirements, and the manual decisions still open."""

    id: str
    verdict: str
    requirements: tuple[RequirementVerdict, ...]
    open_decisions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "verdict": self.verdict,
            "requirements": [r.to_dict() for r in self.requirements],
            "open_decisions": list(self.open_decisions),
        }


@dataclass(frozen=True)
class UnknownResult:
    """A result naming a package or requirement the packages output does not hold."""

    package: str
    requirement: str
    reason: str  # unknown_package | unknown_requirement

    def to_dict(self) -> dict[str, Any]:
        return {"package": self.package, "requirement": self.requirement, "reason": self.reason}


@dataclass(frozen=True)
class VerificationReport:
    """The verdict of every package of one packages output."""

    packages_sha256: str
    packages: tuple[PackageVerdict, ...]
    unknown_results: tuple[UnknownResult, ...]

    def summary(self) -> dict[str, Any]:
        verdicts = [p.verdict for p in self.packages]
        return {
            "schema_version": SCHEMA_VERSION,
            "packages_sha256": self.packages_sha256,
            "counts": {v: verdicts.count(v) for v in PACKAGE_VERDICTS},
            "packages": [{"id": p.id, "verdict": p.verdict} for p in self.packages],
            "escaped": [
                {"package": p.id, "requirement": r.id}
                for p in self.packages
                for r in p.requirements
                if "escaped" in r.controls
            ],
            "unknown_results": [u.to_dict() for u in self.unknown_results],
        }

    def report(self) -> dict[str, Any]:
        """The summary with each package's requirements and open decisions."""
        return {**self.summary(), "packages": [p.to_dict() for p in self.packages]}


def dump_report(report: VerificationReport) -> str:
    """The full report as the bytes ``--out`` writes: the same inputs give the same bytes."""
    return json.dumps(report.report(), sort_keys=True, ensure_ascii=False, indent=2) + "\n"


_RANK = {"fail": 0, "blocked": 1, "not_tested": 2, "pass": 3}
#: A package's verdict from its worst requirement status; no requirement is not_tested.
_VERDICT = {"fail": "failed", "blocked": "blocked", "not_tested": "not_tested", "pass": "complete"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidenced(result: VerificationResult, base: Path) -> bool:
    """Every evidence file exists inside *base*, a symlink's target included, with its hash."""
    root = base.resolve()
    for e in result.evidence:
        path = (base / e.path).resolve()
        try:
            if not path.is_relative_to(root) or _digest(path) != e.sha256:
                return False
        except OSError:
            return False
    return True


def _aliases(out: Path, path: Path) -> bool:
    """*out* names *path*'s file: the same resolved path, or a hard link to it."""
    if out.resolve() == path.resolve():
        return True
    try:
        return out.samefile(path)
    except OSError:
        return False


def _status(results: list[VerificationResult], base: Path) -> tuple[str, str | None]:
    """The worst status among a requirement's results, with why it is not tested."""
    if not results:
        return "not_tested", "no_result"
    statuses: list[tuple[str, str | None]] = []
    for r in results:
        if r.outcome != "pass":
            statuses.append((r.outcome, "recorded" if r.outcome == "not_tested" else None))
        elif not r.evidence:
            statuses.append(("not_tested", "no_evidence"))
        elif not _evidenced(r, base):
            statuses.append(("not_tested", "evidence_unverified"))
        else:
            statuses.append(("pass", None))
    return min(statuses, key=lambda s: (_RANK[s[0]], s[1] or ""))


def verify(packages_dir: Path, results_file: Path, out: Path | None = None) -> VerificationReport:
    """Give each package of *packages_dir* a verdict from the results in *results_file*.

    A requirement's status is the worst of its results (fail, blocked,
    not_tested, pass); a ``pass`` counts only with evidence whose every file,
    relative to the results file's folder, exists with its recorded hash. A
    package is ``failed`` if any requirement failed, else ``blocked`` if any is
    blocked, else ``not_tested`` if any is not tested or none is known, else
    ``complete``. A negative control recording ``pass`` escaped: its requirement
    is not tested. A result naming a package or requirement the packages output
    lacks is listed, never dropped. *out*, the file the caller will write the
    report to, is refused when it is one of the run's inputs.

    Raises:
        InventoryError: an input is unreadable, the results were recorded
            against another packages output, or *out* would overwrite an input.
    """
    packages = read_packages(packages_dir)
    recorded = read_results(results_file)
    if recorded.packages_sha256 != packages.sha256:
        raise InventoryError(
            "the results file was recorded against another packages output"
            " (its packages_sha256 differs); record the results against this one"
        )
    if out is not None:
        base = results_file.parent
        inputs = [results_file, *packages_dir.iterdir()]
        inputs += [base / e.path for r in recorded.results for e in r.evidence]
        inside = out.resolve().is_relative_to(packages_dir.resolve())
        if inside or any(_aliases(out, path) for path in inputs):
            raise InventoryError(
                f"--out {out} would overwrite an input of this run (the results file,"
                " an evidence file or the packages output): pass another path"
            )
    checks: dict[tuple[str, str], list[VerificationResult]] = {}
    controls: dict[tuple[str, str], list[VerificationResult]] = {}
    unknown: list[UnknownResult] = []
    for r in recorded.results:
        reqs = packages.requirements.get(r.package)
        if reqs is None or r.requirement not in {q.id for q in reqs}:
            reason = "unknown_package" if reqs is None else "unknown_requirement"
            unknown.append(UnknownResult(r.package, r.requirement, reason))
            continue
        (controls if r.negative_control else checks).setdefault(
            (r.package, r.requirement), []
        ).append(r)
    verdicts: list[PackageVerdict] = []
    for pid, reqs in sorted(packages.requirements.items()):
        rows: list[RequirementVerdict] = []
        decisions: set[str] = set()
        for q in reqs:
            key = (pid, q.id)
            status, reason = _status(checks.get(key, []), results_file.parent)
            held = sorted(
                "held" if c.outcome == "fail" else "escaped" if c.outcome == "pass" else "not_run"
                for c in controls.get(key, [])
            )
            if status == "pass" and "escaped" in held:
                status, reason = "not_tested", "control_escaped"
            rows.append(RequirementVerdict(q.id, q.kind, q.member, status, reason, tuple(held)))
            for r in checks.get(key, []) + controls.get(key, []):
                decisions.update(r.open_decisions)
        worst = min((r.status for r in rows), key=_RANK.__getitem__, default="not_tested")
        verdict = _VERDICT[worst]
        verdicts.append(PackageVerdict(pid, verdict, tuple(rows), tuple(sorted(decisions))))
    order = sorted(unknown, key=lambda u: (u.package, u.requirement, u.reason))
    return VerificationReport(packages.sha256, tuple(verdicts), tuple(order))
