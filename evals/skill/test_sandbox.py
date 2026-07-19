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

from evals.skill.sandbox import sandbox_settings


def test_sandbox_settings_allowlists_only_the_org_host():
    settings = sandbox_settings("agent-cloud.crm.dynamics.com")
    # The one lever that varies by run: the agent's Bash may egress to the org host alone.
    assert settings["sandbox"]["network"]["allowedDomains"] == ["agent-cloud.crm.dynamics.com"]


def test_sandbox_settings_is_fail_closed_and_unbypassable():
    settings = sandbox_settings("server.contoso.local")["sandbox"]
    # enabled + fail-closed: a missing bubblewrap/socat/userns aborts the run rather than
    # silently running unsandboxed (the gate that replaces the old root check).
    assert settings["enabled"] is True
    assert settings["failIfUnavailable"] is True
    # the agent-under-test cannot self-bypass: with unsandboxed commands disallowed, the
    # per-command dangerouslyDisableSandbox escape hatch is ignored and settings writes denied.
    assert settings["allowUnsandboxedCommands"] is False
    # no domain may be reached beyond the declared allowlist (no dynamic widening).
    assert settings["allowManagedDomainsOnly"] is True


def test_sandbox_settings_takes_a_hostname_verbatim():
    # Both eval targets resolve as hostnames (cloud .com, on-prem .local), so the allowlist
    # is the host string as-is — no scheme, no port, no bare-IP special-casing.
    for host in ("agent-cloud.crm.dynamics.com", "server.contoso.local"):
        assert sandbox_settings(host)["sandbox"]["network"]["allowedDomains"] == [host]


def test_sandbox_settings_is_pure():
    # Same input → equal, independent objects (a builder, no shared mutable state).
    a = sandbox_settings("org.example.com")
    b = sandbox_settings("org.example.com")
    assert a == b
    a["sandbox"]["network"]["allowedDomains"].append("evil.example.com")
    assert b["sandbox"]["network"]["allowedDomains"] == ["org.example.com"]
