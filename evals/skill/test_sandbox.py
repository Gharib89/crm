"""Offline tests for the sandbox network block (issue #906, ADR 0028).

The ADR requires outbound network blocked at the **sandbox level** — not tool-deep —
so the agent's ``Bash`` can reach the live org but nothing else, identically on both
legs. The mechanism is Claude Code's built-in Bash sandbox, declared as a user-scope
``settings.json`` block; the effectful part (a real ``claude -p`` under bubblewrap) needs
a Claude login + a live org and is exercised only on the maintainer's live run. Here we
pin the pure builder that emits that settings block.

    pytest evals/skill/test_sandbox.py
"""

from __future__ import annotations

from evals.skill.sandbox import parse_enforcement, sandbox_settings


def test_sandbox_settings_allowlists_only_the_org_host():
    """The one lever that varies by run: the agent's Bash may egress to the org host alone."""
    settings = sandbox_settings("contoso.crm.dynamics.com")
    assert settings["sandbox"]["network"]["allowedDomains"] == ["contoso.crm.dynamics.com"]


def test_sandbox_settings_is_fail_closed_and_unbypassable():
    """Enabled and fail-closed: a missing bubblewrap/socat/userns aborts the run.

    That replaces the old root check, so a run never silently goes unsandboxed. With
    unsandboxed commands disallowed, the per-command ``dangerouslyDisableSandbox`` escape
    hatch is ignored and settings writes are denied, so the agent-under-test cannot
    self-bypass. ``strictAllowlist`` is what makes ``allowedDomains`` deny-by-default:
    since Claude Code 2.1.219 an unlisted host otherwise *prompts*, and the headless
    agent's ``--dangerously-skip-permissions`` auto-allows it (a silent leak). The closed
    escape hatch then guarantees no unsandboxed retry. ``allowManagedDomainsOnly`` is a
    managed-settings-only lock and a no-op in this user-scope block, so it is not set.
    """
    settings = sandbox_settings("server.contoso.local")["sandbox"]
    assert settings["enabled"] is True
    assert settings["failIfUnavailable"] is True
    assert settings["allowUnsandboxedCommands"] is False
    assert settings["network"]["strictAllowlist"] is True
    assert "allowManagedDomainsOnly" not in settings


def test_sandbox_settings_takes_a_hostname_verbatim():
    """Both eval targets resolve as hostnames (cloud .com, on-prem .local).

    So the allowlist is the host string as-is: no scheme, no port, no bare-IP special-casing.
    """
    for host in ("contoso.crm.dynamics.com", "server.contoso.local"):
        assert sandbox_settings(host)["sandbox"]["network"]["allowedDomains"] == [host]


def test_sandbox_settings_is_pure():
    """Same input gives equal, independent objects: a builder with no shared mutable state."""
    a = sandbox_settings("org.example.com")
    b = sandbox_settings("org.example.com")
    assert a == b
    a["sandbox"]["network"]["allowedDomains"].append("evil.example.com")
    assert b["sandbox"]["network"]["allowedDomains"] == ["org.example.com"]


def test_parse_enforcement_clean_green():
    """Org returned an HTTP status and the non-org host was blocked: the only runnable state.

    ``failIfUnavailable`` only proves the binaries exist; ``parse_enforcement`` is the
    runtime gate, so only (org reachable AND non-org blocked) may run the pair and every
    other shape must abort.
    """
    out = "VERDICT org=302 web=SEALED"
    assert parse_enforcement(out) == (True, True)


def test_parse_enforcement_dead_proxy_fails_org():
    """A dead proxy never connects the org (org=000): not runnable even though web is "blocked"."""
    out = "curl: (7) Failed to connect to localhost port 3128\nVERDICT org=000 web=SEALED"
    assert parse_enforcement(out) == (False, True)


def test_parse_enforcement_leaky_proxy_fails_web():
    """A proxy that is up but not enforcing lets a non-org host egress (web=OPEN).

    Not runnable: it would inflate lift.
    """
    out = "VERDICT org=302 web=OPEN"
    assert parse_enforcement(out) == (True, False)


def test_parse_enforcement_no_verdict_fails_closed():
    """A stalled/derailed agent that never prints the verdict line must not read as runnable."""
    out = "I was unable to run the command because ..."
    assert parse_enforcement(out) == (False, False)


def test_parse_enforcement_ignores_echoed_command_text():
    """Agents quote the command verbatim in their reply.

    The command text contains only the run-time pieces (``W=OPEN``, ``web=$W``), never an
    assembled verdict, so a quoted command plus the real output line must parse from the
    output line alone.
    """
    out = (
        "I ran: ORG=$(curl -sS -o /dev/null -w '%{http_code}' https://org.example.com); "
        "curl -sS --max-time 5 -o /dev/null https://example.com && W=OPEN || W=SEALED; "
        'echo "VERDICT org=$ORG web=$W"\n'
        "It printed:\nVERDICT org=302 web=SEALED"
    )
    assert parse_enforcement(out) == (True, True)


def test_parse_enforcement_takes_the_last_verdict():
    """If the transcript repeats the line (echoed output + a final summary), the last one wins."""
    out = "VERDICT org=302 web=OPEN\n...retrying...\nVERDICT org=302 web=SEALED"
    assert parse_enforcement(out) == (True, True)


def test_sandbox_settings_widens_to_auth_hosts_only_when_asked():
    """The cloud target's OAuth flow needs AAD.

    A strict allowlist without it kills every crm call ("OAuth setup failed ...
    login.microsoftonline.com ... Max retries exceeded"). The widening is explicit and
    additive: the default stays org-host-only (on-prem).
    """
    from evals.skill.sandbox import AAD_LOGIN_HOST

    cloud = sandbox_settings("contoso.crm.dynamics.com", auth_hosts=(AAD_LOGIN_HOST,))
    assert cloud["sandbox"]["network"]["allowedDomains"] == [
        "contoso.crm.dynamics.com",
        "login.microsoftonline.com",
    ]
    onprem = sandbox_settings("server.contoso.local")
    assert onprem["sandbox"]["network"]["allowedDomains"] == ["server.contoso.local"]
