"""Offline tests for the sandbox network block (issue #890, ADR 0028).

The ADR requires outbound network blocked at the **sandbox level** — not tool-deep —
so the agent's ``Bash`` can reach the live org but nothing else, identically on both
legs. The effectful setup (a network namespace + nftables egress allowlist) needs root
and a live org, so it is exercised only on the maintainer's live run; here we pin the
pure pieces: host→IP resolution, the nftables ruleset text, the static hosts file that
removes any need for DNS egress, and the ``ip netns exec`` command wrapper.

    pytest evals/skill/test_sandbox.py
"""

from __future__ import annotations

from evals.skill.sandbox import (
    netns_hosts_file,
    nft_ruleset,
    resolve_allow_ips,
    wrap_agent_cmd,
)


def test_resolve_allow_ips_dedups_and_sorts():
    def fake_resolver(host: str):
        # getaddrinfo-shaped: (family, type, proto, canonname, sockaddr)
        return [
            (2, 1, 6, "", ("203.0.113.9", 443)),
            (2, 1, 6, "", ("203.0.113.1", 443)),
            (2, 1, 6, "", ("203.0.113.9", 443)),  # duplicate
        ]

    assert resolve_allow_ips("org.example.com", resolver=fake_resolver) == [
        "203.0.113.1",
        "203.0.113.9",
    ]


def test_resolve_allow_ips_raises_on_empty():
    import pytest

    with pytest.raises(ValueError, match="resolved to no addresses"):
        resolve_allow_ips("org.example.com", resolver=lambda host: [])


def test_nft_ruleset_is_default_drop_with_org_allowlist():
    rs = nft_ruleset(
        table="crmeval", child_ip="10.200.0.2", allow_ips=["203.0.113.1", "203.0.113.9"]
    )
    # a default-drop forward chain is the whole point — absence of an allow rule = blocked.
    assert "type filter hook forward" in rs
    assert "policy drop" in rs
    # each org IP is explicitly allowed on 443; nothing else can egress.
    assert "ip daddr 203.0.113.1 tcp dport 443 accept" in rs
    assert "ip daddr 203.0.113.9 tcp dport 443 accept" in rs
    # return traffic for an allowed, established flow comes back.
    assert "ct state established,related accept" in rs
    # the child subnet is masqueraded so the allowed flow actually routes out.
    assert "type nat hook postrouting" in rs
    assert "masquerade" in rs


def test_nft_ruleset_has_no_dns_hole():
    # DNS is removed by the static hosts file, not punched through the firewall — so no
    # udp/53 rule should exist (a DNS hole is a web-exfil path the eval must not leave).
    rs = nft_ruleset(table="crmeval", child_ip="10.200.0.2", allow_ips=["203.0.113.1"])
    assert "53" not in rs
    assert "udp" not in rs


def test_netns_hosts_file_pins_every_ip():
    content = netns_hosts_file("org.example.com", ["203.0.113.1", "203.0.113.9"])
    assert "203.0.113.1 org.example.com" in content
    assert "203.0.113.9 org.example.com" in content
    # loopback stays defined so localhost tooling still resolves inside the namespace.
    assert "127.0.0.1 localhost" in content


def test_wrap_agent_cmd_prefixes_ip_netns_exec():
    assert wrap_agent_cmd(["claude", "-p", "--model", "sonnet"], "crmeval-run1") == [
        "ip",
        "netns",
        "exec",
        "crmeval-run1",
        "claude",
        "-p",
        "--model",
        "sonnet",
    ]
