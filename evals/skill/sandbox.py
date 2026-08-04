"""Sandbox network block — the agent's Bash reaches the org host and nothing else (ADR 0028).

The behavioral eval's guardrail is ``--allowedTools Bash,Read,Grep,Glob,Skill`` with the
web tools denied — but denying ``WebSearch``/``WebFetch`` does **not** stop ``curl`` or
Python HTTP from the allowed ``Bash``. ADR 0028 therefore requires the block at the
**sandbox level** so it is real rather than tool-deep, applied *identically* on both legs
of a pair (otherwise the block itself would confound the with-skill vs bare comparison).

Mechanism — **Claude Code's built-in Bash sandbox** (bubblewrap + socat on Linux/WSL2):
each Bash command the agent runs is confined by an out-of-sandbox proxy to the domains in
``network.allowedDomains`` (the org host only), while the *main* ``claude`` process keeps
normal network — so the model driver reaches ``api.anthropic.com`` and the agent's shell
reaches only the org. This dissolves the conflict the earlier root network-namespace
mechanism hit (#906): caging the whole ``claude`` process also blocked its own model API,
so both legs died at ``ENOTIMP`` and produced a false ``0% vs 0%`` null. The built-in
sandbox needs **no root** and catches every Bash child process, not just direct calls.

The block is declared as user-scope ``settings.json`` written into each leg's fresh config
dir (the fresh ``HOME``'s ``.claude/``, alongside the passed-through credentials — see
:mod:`evals.skill.isolation`, which also writes the **workspace trust record** the sandbox
needs to fully initialize); :func:`sandbox_settings` is the pure builder, unit-tested
offline. ``failIfUnavailable`` makes ``claude`` abort when bubblewrap/socat or unprivileged
user namespaces are missing. ``allowUnsandboxedCommands: false`` means the agent-under-test
cannot self-bypass: the per-command ``dangerouslyDisableSandbox`` escape hatch is ignored
and writes to ``settings.json`` are denied at every scope. Egress is pinned to exactly the
declared ``allowedDomains`` (the org host) by ``strictAllowlist`` — since Claude Code
2.1.219 ``allowedDomains`` alone only *pre-approves* (an unlisted host prompts, which
``--dangerously-skip-permissions`` turns into an allow); ``strictAllowlist`` restores the
hard deny, and with the escape hatch closed the allowlist is never widened dynamically.

**``failIfUnavailable`` is necessary but not sufficient** — it only checks that the sandbox
*binaries* exist, not that the network proxy actually *started* and enforces. A host where
the proxy is dead (every request fails, org included → a false ``0% vs 0%`` null) or up but
not enforcing (a denied host still egresses → inflated lift) passes ``failIfUnavailable``
yet silently breaks isolation — observed on WSL2, where proxy startup is unreliable. So the
real fail-closed gate is a runtime probe: :func:`probe_enforcement` drives one sandboxed
``claude -p`` and reports whether the org is reachable *and* a non-org host is blocked; the
paired front door refuses to run the pair unless both hold.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any

#: The AAD endpoint the cloud target's OAuth client-credentials flow must reach (openid
#: discovery + token). On-prem/NTLM authenticates against the org host itself and needs
#: no extra egress.
AAD_LOGIN_HOST = "login.microsoftonline.com"


def sandbox_settings(host: str, *, auth_hosts: tuple[str, ...] = ()) -> dict[str, Any]:
    """The Claude Code ``settings.json`` block confining the agent's Bash to ``host``.

    A pure builder (the deleted ``nft_ruleset`` / ``netns_hosts_file`` / ``resolve_allow_ips``
    are gone with the root netns mechanism). Written identically into both legs' config dirs
    — only the ``crm skill install`` differs between legs — so the block never confounds the
    with-skill vs bare comparison. ``host`` is both eval targets' hostname (cloud ``.com``,
    on-prem ``.local``), taken verbatim: the built-in sandbox matches on hostname, so no
    scheme/port/bare-IP special-casing is needed. ``auth_hosts`` widens the allowlist to the
    target's *auth* endpoints only — the cloud target's OAuth flow dies inside a strict
    allowlist without :data:`AAD_LOGIN_HOST` (the leaky pre-2.1.219 sandbox masked this).
    """
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            # strictAllowlist (Claude Code ≥ 2.1.219): without it, allowedDomains only
            # pre-approves — an unlisted host *prompts*, and the headless agent's
            # --dangerously-skip-permissions turns that prompt into an allow (a silent
            # leak the enforcement preflight caught). With it, unlisted hosts are denied.
            "network": {"allowedDomains": [host, *auth_hosts], "strictAllowlist": True},
        }
    }


#: The probe prompt: the org host must return an HTTP status; a non-org host must be blocked.
#: One command, one output line. The verdict markers (``VERDICT org=<code> web=OPEN|SEALED``)
#: are assembled at *run time* from shell variables, so they can never appear literally in
#: the command text — an agent quoting the command verbatim in its reply (they do) must not
#: be able to fake or mask a marker (the old ``WEB_OK``/``WEB_BLOCKED`` sentinels appeared
#: in the echoed command line and false-failed the parse).
_PROBE_PROMPT = (
    "Use the Bash tool to run exactly this one command, then reply with only the "
    "single line it prints, no commentary:\n"
    "ORG=$(curl -sS -o /dev/null -w '%{{http_code}}' https://{host}); "
    "curl -sS --max-time 5 -o /dev/null https://example.com && W=OPEN || W=SEALED; "
    'echo "VERDICT org=$ORG web=$W"'
)


def parse_enforcement(out: str) -> tuple[bool, bool]:
    """``(org_reachable, web_blocked)`` parsed from a :data:`_PROBE_PROMPT` transcript.

    Matches the run-time-assembled ``VERDICT org=<code> web=OPEN|SEALED`` line (the last
    one, if the transcript repeats it). ``org_reachable``: the org ``curl`` printed an HTTP
    status that is not ``000`` (``000`` is curl's "never connected" — e.g. the sandbox proxy
    is dead). ``web_blocked``: the non-org ``curl`` failed, so ``web=SEALED``. No verdict
    line at all parses ``(False, False)`` — fail closed. Pure so the verdict is unit-tested
    without a live agent.
    """
    matches = re.findall(r"VERDICT org=(\d{3}) web=(OPEN|SEALED)", out)
    if not matches:
        return False, False
    code, web = matches[-1]
    return code != "000", web == "SEALED"


def probe_enforcement(
    host: str, *, model: str = "sonnet", timeout: int = 180
) -> tuple[bool, bool, str]:  # pragma: no cover - live, drives a real claude -p
    """Drive one sandboxed ``claude -p`` and report ``(org_reachable, web_blocked, transcript)``.

    The runtime fail-closed gate (see the module docstring): ``failIfUnavailable`` proves the
    sandbox *binaries* exist but not that the proxy *enforces*, so before trusting a run we
    make the agent ``curl`` the org host (must connect) and a non-org host (must be blocked).
    Reuses the real harness isolation (fresh HOME, scrubbed env, passed-through creds, and the
    workspace trust record) via ``provision_isolation(install_skill=False)`` — no crm binary
    needed — so the probe exercises the exact path the paired eval takes. A stalled agent
    times out and reports ``(False, False, …)`` so a hang fails closed.
    """
    from evals.skill import isolation

    iso = isolation.provision_isolation(install_skill=False)
    try:
        cfg = iso.home / ".claude"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "settings.json").write_text(json.dumps(sandbox_settings(host)), encoding="utf-8")
        try:
            proc = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--dangerously-skip-permissions",
                    "--allowedTools",
                    "Bash",
                    "--model",
                    model,
                    _PROBE_PROMPT.format(host=host),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=iso.env,
                cwd=str(iso.work),  # the trusted workspace, matching the paired runner
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, False, f"claude -p timed out after {timeout}s (stalled)"
        out = proc.stdout + proc.stderr
        return (*parse_enforcement(out), out)
    finally:
        iso.cleanup()


def _selfcheck(host: str) -> int:  # pragma: no cover - live smoke helper, not offline-tested
    """``python -m evals.skill.sandbox <host>``: prove the built-in sandbox lets the org
    through and blocks the web — a manual live check driving :func:`probe_enforcement`.
    """
    org_reachable, web_blocked, out = probe_enforcement(host)
    print(out)
    print(f"\n=== sandbox selfcheck for {host} ===")
    print(f"  org reachable: {org_reachable}")
    print(f"  web blocked:   {web_blocked}")
    return 0 if org_reachable and web_blocked else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_selfcheck(sys.argv[1]))
