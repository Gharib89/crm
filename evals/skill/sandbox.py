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
:mod:`evals.skill.isolation`); :func:`sandbox_settings` is the pure builder, unit-tested
offline. ``failIfUnavailable`` makes ``claude`` abort (rather than silently run
unsandboxed) when bubblewrap/socat or unprivileged user namespaces are missing — the
fail-closed gate that replaces the old root check. ``allowUnsandboxedCommands: false``
means the agent-under-test cannot self-bypass: the per-command ``dangerouslyDisableSandbox``
escape hatch is ignored and writes to ``settings.json`` are denied at every scope.
``allowManagedDomainsOnly: true`` pins egress to exactly the declared ``allowedDomains`` —
no domain may be reached beyond the org host, and the allowlist is never widened dynamically.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def sandbox_settings(host: str) -> dict[str, Any]:
    """The Claude Code ``settings.json`` block confining the agent's Bash to ``host``.

    A pure builder (the deleted ``nft_ruleset`` / ``netns_hosts_file`` / ``resolve_allow_ips``
    are gone with the root netns mechanism). Written identically into both legs' config dirs
    — only the ``crm skill install`` differs between legs — so the block never confounds the
    with-skill vs bare comparison. ``host`` is both eval targets' hostname (cloud ``.com``,
    on-prem ``.local``), taken verbatim: the built-in sandbox matches on hostname, so no
    scheme/port/bare-IP special-casing is needed.
    """
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "allowManagedDomainsOnly": True,
            "network": {"allowedDomains": [host]},
        }
    }


def _selfcheck(host: str) -> int:  # pragma: no cover - live smoke helper, not offline-tested
    """``python -m evals.skill.sandbox <host>``: prove the built-in sandbox lets the org
    through and blocks the web — a manual live check driving a real ``claude -p`` (needs a
    Claude login + bubblewrap/socat installed).

    Writes :func:`sandbox_settings` into a throwaway config dir, passes the Claude
    credentials through (so the headless agent authenticates), then asks the agent to
    ``curl`` the org host (expect an HTTP status) and a non-org host (expect a block),
    reporting the org-through / web-blocked verdict parsed from the transcript.
    """
    from evals.skill import isolation

    home = Path(tempfile.mkdtemp(prefix="crm-eval-sandbox-check-"))
    try:
        cfg = home / ".claude"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "settings.json").write_text(json.dumps(sandbox_settings(host)), encoding="utf-8")
        isolation.passthrough_claude_auth(home)
        # Fresh HOME (so claude reads the throwaway config dir), no CLAUDE_CONFIG_DIR override.
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        env["HOME"] = str(home)
        prompt = (
            "Use the Bash tool to run these two commands and report each command's exit "
            "code and full output verbatim:\n"
            f"1. curl -sS -o /dev/null -w 'ORG_HTTP=%{{http_code}}' https://{host}\n"
            "2. curl -sS --max-time 5 https://example.com && echo WEB_OK || echo WEB_BLOCKED"
        )
        try:
            proc = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--dangerously-skip-permissions",
                    "--allowedTools",
                    "Bash",
                    "--model",
                    "sonnet",
                    prompt,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            # A login/API/model stall must not hang the smoke forever — a stalled check fails.
            print(f"selfcheck for {host}: claude -p timed out (stalled)")
            return 1
        out = proc.stdout + proc.stderr
        print(out)
        org_ok = "ORG_HTTP=" in out and "ORG_HTTP=000" not in out
        web_blocked = "WEB_BLOCKED" in out and "WEB_OK" not in out
        print(f"\n=== sandbox selfcheck for {host} ===")
        print(f"  org reachable: {org_ok}")
        print(f"  web blocked:   {web_blocked}")
        return 0 if org_ok and web_blocked else 1
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_selfcheck(sys.argv[1]))
