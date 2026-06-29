#!/usr/bin/env python3
"""Run the live e2e suite across all three standing targets, each on its own profile,
then report — and rerun — by skip/fail with full reasons.

    onprem    agent-on-prem    -m 'e2e'                    whole suite; requires_cloud self-skip
    cloud     agent-cloud      -m 'e2e and requires_cloud' cloud-gated; CS-only skip-with-instructions
    cs-trial  agent-cs-trial   -m 'e2e and requires_cloud' same selection, CS-only subset now RUNS

CS-only tests carry no marker — they are requires_cloud tests that runtime-skip unless the
org has CS resources (auditing on, seeded workflow). So the cs-trial leg reuses the cloud
selection; the CS tests are the ones that newly pass there. Union of the legs = full coverage.

Each leg streams live AND writes logs/e2e/<ts>/<leg>.{log,xml}. The junit XML is the source
of truth for the summary (exact outcome + reason + param-aware node id); the .log is the
human transcript (and a fallback parse for runs written before XMLs existed).

Usage:
    python scripts/e2e_all.py [run] [pytest args...]   full 3-leg run (default)
    python scripts/e2e_all.py rerun <logdir> [args...]  rerun ONLY skipped+failed from a prior
                                                         run, each on its original leg's profile
    python scripts/e2e_all.py summary <logdir>          (re)print a run's summary
    python scripts/e2e_all.py selftest                  offline self-check of the parser

Goal-oriented: run -> read SKIPPED reasons -> fix env/seed the org -> `rerun <dir>` -> watch
skips shrink toward zero (every test actually executed).
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
CRM_HOME = Path(os.environ.get("CRM_HOME") or (Path.home() / ".crm"))
CYAN, DIM, YEL, RST = "\033[1;36m", "\033[2m", "\033[33m", "\033[0m"


@dataclass(frozen=True)
class Leg:
    name: str
    profile: str
    marker: str


LEGS: List[Leg] = [
    Leg("onprem", "agent-on-prem", "e2e"),
    Leg("cloud", "agent-cloud", "e2e and requires_cloud"),
    Leg("cs-trial", "agent-cs-trial", "e2e and requires_cloud"),
]

Row = Tuple[str, str, str, str]  # (leg, nodeid, outcome, reason)

# A pytest -v result line: "crm/tests/e2e/test_x.py::test_y[p] SKIPPED [ 3%]".
_VLINE = re.compile(r"^(crm/\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")
_VMAP = {"PASSED": "passed", "FAILED": "failed", "ERROR": "error",
         "SKIPPED": "skipped", "XFAIL": "xfail", "XPASS": "passed"}


# ── environment ──────────────────────────────────────────────────────────────
def _pytest_cmd() -> List[str]:
    for p in (REPO / ".venv/bin/pytest", REPO / ".venv/Scripts/pytest.exe"):
        if p.exists():
            return [str(p)]
    return [sys.executable, "-m", "pytest"]


def _profile_host(profile: str) -> Optional[str]:
    """Target host from the profile JSON, or None if the profile file is absent.

    The host opens the *.dynamics.com prod-host guard for the exact org (the cs-trial
    host changes each provisioning); harmless no-op for an on-prem host. Read-only."""
    pf = CRM_HOME / "profiles" / f"{profile}.json"
    if not pf.is_file():
        return None
    url: str = json.loads(pf.read_text(encoding="utf-8")).get("url", "")
    # Same host extraction as crm/tests/e2e/conftest.py::_assert_not_production.
    return url.split("//", 1)[-1].split("/", 1)[0].lower()


def _leg_env(profile: str, host: str) -> Dict[str, str]:
    env = dict(os.environ)
    # dotnet + its global tools (pac) install under ~/.dotnet, on PATH only via the
    # interactive shell rc. A non-reloaded/non-interactive shell won't have it, so the
    # plugin-lifecycle (dotnet) and solution pack/extract (pac) e2e skip as "not found".
    # Add it to the child PATH so the runner is self-sufficient regardless of caller PATH.
    dotnet = Path(env.get("DOTNET_ROOT") or (Path.home() / ".dotnet"))
    if dotnet.is_dir():
        env["DOTNET_ROOT"] = str(dotnet)
        env["PATH"] = os.pathsep.join([str(dotnet), str(dotnet / "tools"), env.get("PATH", "")])
    env.update(D365_E2E="1", D365_E2E_PROFILE=profile,
               D365_E2E_ALLOW_HOST=host, PYTHONUNBUFFERED="1")
    return env


# ── running ──────────────────────────────────────────────────────────────────
def _stream(cmd: List[str], env: Dict[str, str], log: Path) -> int:
    """Run cmd from the repo root, streaming combined output to console AND log file.
    Popen avoids the shell pipe/tee SIGPIPE/MULTIOS exit-code corruption documented for
    this repo's zsh; the child's PYTHONUNBUFFERED + bufsize=1 keep progress live."""
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(cmd, cwd=str(REPO), env=env, text=True, bufsize=1,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            lf.write(line)
        return proc.wait()


def _run_leg(leg: Leg, logdir: Path, extra: List[str],
             marker: Optional[str] = None, nodes: Optional[List[str]] = None) -> int:
    """Run one leg: pass marker= for a full run or nodes= for a rerun (exactly one).
    Returns the pytest exit code (0=ok, 5=nothing collected — both tolerated by callers)."""
    log, xml = logdir / f"{leg.name}.log", logdir / f"{leg.name}.xml"
    print(f"\n{CYAN}═══ {leg.name}  (profile: {leg.profile}) ═══{RST}")

    host = _profile_host(leg.profile)
    if host is None:
        msg = f"SKIPPED LEG — profile {leg.profile!r} not found ({CRM_HOME}/profiles)"
        print(f"{YEL}{msg}{RST}")
        log.write_text(msg + "\n", encoding="utf-8")
        return 0

    if marker is not None:
        sel = ["-m", marker]
    else:
        node_list = list(nodes or [])
        if not node_list:
            print(f"{DIM}no tests to rerun on {leg.name}{RST}")
            log.write_text("no tests to rerun\n", encoding="utf-8")
            return 0
        print(f"{DIM}rerunning {len(node_list)} test(s) on {leg.name}{RST}")
        # The repo pins `addopts = -m 'not e2e'`; an explicit `-m e2e` overrides it so the
        # named e2e nodes aren't deselected (every rerun target lives under crm/tests/e2e).
        sel = ["-m", "e2e", *node_list]

    cmd = [*_pytest_cmd(), *sel, "-v", "-ra", f"--junit-xml={xml}", *extra]
    return _stream(cmd, _leg_env(leg.profile, host), log)


# ── parsing ────────────────────────────────────────────────────────────────--
def _rows(logdir: Path) -> List[Row]:
    """(leg, nodeid, outcome, reason) per testcase. Prefer junit XML (exact reasons);
    fall back to the .log -v lines (nodeid+outcome only) for pre-XML runs."""
    rows: List[Row] = []
    xmls = sorted(glob.glob(str(logdir / "*.xml")))
    if xmls:
        for xml in xmls:
            leg = Path(xml).stem
            try:
                root = ET.parse(xml).getroot()
            except ET.ParseError as exc:
                # A run interrupted mid-write leaves a truncated XML; warn and skip it
                # rather than crash the whole summary/rerun.
                print(f"{YEL}warning: skipping unreadable {xml}: {exc}{RST}", file=sys.stderr)
                continue
            for tc in root.iter("testcase"):
                nodeid = tc.get("classname", "").replace(".", "/") + ".py::" + tc.get("name", "")
                child = next(iter(tc), None)
                if child is None:
                    outcome, reason = "passed", ""
                elif child.tag == "skipped":
                    outcome = "xfail" if child.get("type") == "pytest.xfail" else "skipped"
                    reason = child.get("message", "")
                elif child.tag == "error":
                    outcome, reason = "error", child.get("message", "")
                else:  # failure
                    outcome, reason = "failed", child.get("message", "")
                rows.append((leg, nodeid, outcome, " ".join((reason or "").split())))
        return rows
    for log in sorted(glob.glob(str(logdir / "*.log"))):
        leg = Path(log).stem
        for line in Path(log).read_text(encoding="utf-8", errors="replace").splitlines():
            m = _VLINE.match(line)
            if m:
                rows.append((leg, m.group(1), _VMAP[m.group(2)], f"(reason in {leg}.log)"))
    return rows


def _coverage(rows: List[Row]) -> Tuple[Dict[str, List[Row]], List[str], List[str]]:
    """Group rows by nodeid for union coverage. A test is a GAP iff every leg that
    collected it skipped it (it ran nowhere); otherwise it is COVERED — executed
    (passed/failed/error/xfail) on at least one leg. Returns (by_node, covered, gaps)
    with the node lists sorted for deterministic, diffable output."""
    by_node: Dict[str, List[Row]] = {}
    for row in rows:
        by_node.setdefault(row[1], []).append(row)
    gaps = sorted(n for n, inst in by_node.items()
                  if all(o == "skipped" for _l, _n, o, _r in inst))
    gapset = set(gaps)
    covered = sorted(n for n in by_node if n not in gapset)
    return by_node, covered, gaps


def _summary_text(logdir: Path) -> str:
    rows = _rows(logdir)
    if not rows:
        return f"no leg results found in {logdir} — nothing to summarize.\n"

    legs: List[str] = []
    by_leg: Dict[str, Dict[str, int]] = {}
    tot = {k: 0 for k in ("passed", "skipped", "failed", "error", "xfail")}
    for leg, _node, outcome, _r in rows:
        if leg not in by_leg:
            by_leg[leg] = {k: 0 for k in tot}
            legs.append(leg)
        by_leg[leg][outcome] += 1
        tot[outcome] += 1

    by_node, covered, gaps = _coverage(rows)
    gapset = set(gaps)
    failing = sorted({n for _l, n, o, _r in rows if o in ("failed", "error")})
    covered_skips = sum(1 for _l, n, o, _r in rows if o == "skipped" and n not in gapset)

    out = [f"e2e-all summary: {logdir.name}", ""]
    out.append(f"Instances:  total {len(rows)}, PASSED {tot['passed']}, SKIPPED {tot['skipped']}, "
               f"FAILED {tot['failed'] + tot['error']}    (xfailed {tot['xfail']})")
    out.append(f"Coverage:   {len(by_node)} tests — COVERED {len(covered)}, "
               f"GAP {len(gaps)}, FAILING {len(failing)}")

    out += ["", "Per leg:"]
    for leg in legs:
        c = by_leg[leg]
        parts = [f"{c['passed']} passed", f"{c['skipped']} skipped",
                 f"{c['failed'] + c['error']} failed"]
        if c["xfail"]:
            parts.append(f"{c['xfail']} xfailed")
        out.append(f"  {leg:<10} {', '.join(parts)}")

    # GAPS — skipped on every leg, covered nowhere: the actionable chase-list. Show each
    # leg's skip reason (deduped) so the remedy (install/seed/provision-trial) is visible.
    out += ["", f"GAPS ({len(gaps)}) — skipped on every leg, covered nowhere:"]
    if not gaps:
        out.append("  (none)")
    for node in gaps:
        out.append(f"  {node}")
        seen: Set[Tuple[str, str]] = set()
        for leg, _n, o, r in sorted(by_node[node]):
            if o == "skipped" and (leg, r) not in seen:
                seen.add((leg, r))
                out.append(f"      [{leg}] {r or '(no reason recorded)'}")

    # FAILING — a test that ran and broke on some leg: a bug to fix, reported regardless
    # of coverage (a failure is still execution).
    out += ["", f"FAILING ({len(failing)}):"]
    if not failing:
        out.append("  (none)")
    for node in failing:
        for leg, _n, o, r in sorted(by_node[node]):
            if o in ("failed", "error"):
                out += [f"  [{leg}] {node}  ({o})", f"      {r or '(see leg log for traceback)'}"]

    out += ["", f"skipped-but-covered-elsewhere: {covered_skips}  "
               f"(not listed — covered on another leg, no action)"]
    return "\n".join(out) + "\n"


def _summarize(logdir: Path) -> None:
    text = _summary_text(logdir)
    (logdir / "summary.txt").write_text(text, encoding="utf-8")
    sys.stdout.write(text)


def _rerun_nodes(logdir: Path, leg_name: str) -> List[str]:
    """Per leg: failures/errors + skipped-here-AND-a-gap. Excludes skips that are
    covered on another leg (rerunning those on this profile would just re-skip)."""
    rows = _rows(logdir)
    _by, _cov, gaps = _coverage(rows)
    gapset = set(gaps)
    nodes = {n for leg, n, o, _r in rows if leg == leg_name
             and (o in ("failed", "error") or (o == "skipped" and n in gapset))}
    return sorted(nodes)


# ── commands ───────────────────────────────────────────────────────────────--
def _new_logdir(suffix: str = "") -> Path:
    d = REPO / "logs" / "e2e" / (datetime.now().strftime("%Y%m%d-%H%M%S") + suffix)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_run(extra: List[str]) -> int:
    logdir = _new_logdir()
    overall = 0
    for leg in LEGS:
        rc = _run_leg(leg, logdir, extra, marker=leg.marker)
        if rc not in (0, 5):
            overall = 1
    print(f"\n{CYAN}═══ SUMMARY ═══{RST}")
    _summarize(logdir)
    print(f"Logs: {logdir}/   (rerun skips/fails: python scripts/e2e_all.py rerun {logdir})")
    return overall


def cmd_rerun(src: Path, extra: List[str]) -> int:
    if not src.is_dir():
        print(f"usage: python scripts/e2e_all.py rerun <prior-run-logdir>", file=sys.stderr)
        return 2
    logdir = _new_logdir("-rerun")
    print(f"Rerunning failures + genuine gaps from {src} (covered-elsewhere skips excluded)")
    overall, ran = 0, False
    for leg in LEGS:
        nodes = _rerun_nodes(src, leg.name)
        if not nodes:
            continue
        ran = True
        rc = _run_leg(leg, logdir, extra, nodes=nodes)
        if rc not in (0, 5):
            overall = 1
    if not ran:
        print(f"Nothing to rerun in {src} — no failures, and every skip is covered on another leg.")
        return 0
    print(f"\n{CYAN}═══ RERUN SUMMARY ═══{RST}")
    _summarize(logdir)
    print(f"Logs: {logdir}/")
    return overall


def cmd_selftest() -> int:
    """Offline check of the fragile bits: nodeid reconstruction, xfail-vs-skip, union
    coverage (gap = skipped everywhere; covered-elsewhere skip is NOT a gap), and the
    gap-aware rerun set. Two legs so the cross-leg coverage logic is actually exercised."""
    import tempfile
    onprem = """<testsuites><testsuite>
      <testcase classname="crm.tests.e2e.test_x" name="test_ok"/>
      <testcase classname="crm.tests.e2e.test_x" name="test_gap">
        <skipped type="pytest.skip" message="seed a workflow"/></testcase>
      <testcase classname="crm.tests.e2e.test_x" name="test_routing">
        <skipped type="pytest.skip" message="requires a cloud/OAuth target"/></testcase>
      <testcase classname="crm.tests.e2e.test_x" name="test_xf">
        <skipped type="pytest.xfail" message="known broken"/></testcase>
      <testcase classname="crm.tests.e2e.test_x" name="test_bad">
        <failure message="assert 1 == 2"/></testcase>
    </testsuite></testsuites>"""
    # cs-trial covers test_routing (it passes there) — so it must NOT count as a gap.
    cstrial = """<testsuites><testsuite>
      <testcase classname="crm.tests.e2e.test_x" name="test_routing"/>
    </testsuite></testsuites>"""
    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        (dd / "onprem.xml").write_text(onprem, encoding="utf-8")
        (dd / "cs-trial.xml").write_text(cstrial, encoding="utf-8")
        rows = _rows(dd)
        assert ("onprem", "crm/tests/e2e/test_x.py::test_gap", "skipped", "seed a workflow") in rows

        _by, covered, gaps = _coverage(rows)
        # test_gap skipped everywhere => gap. test_routing skipped on onprem but ran on
        # cs-trial => covered, NOT a gap. test_bad failed = ran = covered.
        assert gaps == ["crm/tests/e2e/test_x.py::test_gap"], gaps
        assert "crm/tests/e2e/test_x.py::test_routing" in covered

        # rerun(onprem) = failures + skipped-gaps; excludes the covered routing skip and xfail.
        assert _rerun_nodes(dd, "onprem") == ["crm/tests/e2e/test_x.py::test_bad",
                                              "crm/tests/e2e/test_x.py::test_gap"], \
            _rerun_nodes(dd, "onprem")
        # rerun(cs-trial) = nothing (test_routing passed there).
        assert _rerun_nodes(dd, "cs-trial") == [], _rerun_nodes(dd, "cs-trial")

        text = _summary_text(dd)
        assert "GAP 1" in text and "FAILING 1" in text and "skipped-but-covered-elsewhere: 1" in text, text
    print("selftest OK")
    return 0


def main(argv: List[str]) -> int:
    # Hand-rolled parse: argparse's REMAINDER drops leading-dash passthrough args
    # (e.g. `run -k foo`), so we split the command off the front and forward the rest
    # to pytest verbatim. First token that isn't a known command => an implicit `run`.
    args = list(argv)
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0] if args and args[0] in ("run", "rerun", "summary", "selftest") else "run"
    rest = args[1:] if (args and args[0] == cmd) else args  # drop the command token if explicit

    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "summary":
        if not rest:
            print("usage: python scripts/e2e_all.py summary <logdir>", file=sys.stderr)
            return 2
        _summarize(Path(rest[0]))
        return 0
    if cmd == "rerun":
        if not rest:
            print("usage: python scripts/e2e_all.py rerun <logdir> [pytest args...]", file=sys.stderr)
            return 2
        return cmd_rerun(Path(rest[0]), rest[1:])
    return cmd_run(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
