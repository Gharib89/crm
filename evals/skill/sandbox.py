"""Sandbox network block — outbound-web off, org-only, at the OS level (ADR 0028, #890).

The behavioral eval's guardrail is ``--allowedTools Bash,Read,Grep,Glob,Skill`` with the
web tools denied — but denying ``WebSearch``/``WebFetch`` does **not** stop ``curl`` or
Python HTTP from the allowed ``Bash``. ADR 0028 therefore requires the block at the
**sandbox level** so it is real rather than tool-deep, applied *identically* on both legs
of a pair (otherwise the block itself would confound the with-skill vs bare comparison).

Mechanism (the maintainer chose root ``nftables`` + a network namespace):

- a dedicated **network namespace** holds the agent; a veth pair links it to the host,
  which NATs (masquerades) the namespace's traffic;
- an **nftables forward allowlist** with a default-drop policy permits egress *only* to
  the org's resolved IP(s) on 443 — every other destination is dropped, so ``curl
  https://any-other-host`` fails at the kernel, not at the tool layer;
- the org host is pinned host→IP in a **static ``/etc/netns/<ns>/hosts``** file (which
  ``ip netns exec`` bind-mounts over ``/etc/hosts``), with an empty ``resolv.conf``, so
  the agent needs **no DNS egress at all** — closing the udp/53 hole a DNS allow-rule
  would otherwise leave open as a web-exfil path.

Only the pure builders (IP resolution, the ruleset text, the hosts file, the ``ip netns
exec`` wrapper) are unit-tested offline; :func:`network_sandbox` needs root + a live org
and runs on the maintainer's hand-back run.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

#: A getaddrinfo-shaped resolver, injected in tests.
Resolver = Callable[[str], list[tuple[Any, ...]]]

#: Fixed /30 for the veth pair: host end .1, namespace end .2. A private, link-local-ish
#: block unlikely to collide with a corp/on-prem subnet the org actually lives on.
_HOST_IP = "10.200.0.1"
_CHILD_IP = "10.200.0.2"
_PREFIX = "30"


class SandboxError(RuntimeError):
    """Raised when the network sandbox cannot be provisioned (not root, tool missing)."""


def resolve_allow_ips(host: str, *, resolver: Resolver | None = None) -> list[str]:
    """Resolve ``host`` to a sorted, de-duplicated list of IP-address strings.

    Resolved once, in the harness (which has full network), so the sandboxed agent never
    needs DNS. ``resolver`` defaults to :func:`socket.getaddrinfo` and is injectable for
    tests. Raises :class:`ValueError` when the host resolves to nothing (a mis-seeded
    target must fail loudly, not silently produce an empty — i.e. block-everything —
    allowlist).
    """
    resolve = resolver or (lambda h: socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP))
    addrs = {str(info[4][0]) for info in resolve(host)}
    if not addrs:
        raise ValueError(f"host {host!r} resolved to no addresses")
    return sorted(addrs)


def nft_ruleset(*, table: str, child_ip: str, allow_ips: list[str]) -> str:
    """The nftables ruleset: NAT the namespace out, forward-allow only ``allow_ips``:443.

    A default-drop forward chain means the *absence* of an allow rule is a block, so any
    destination not in ``allow_ips`` is dropped at the kernel. No udp/53 rule exists — DNS
    is served by the static hosts file, not a firewall hole.
    """
    allow_rules = "\n".join(
        f"    add rule ip {table} forward ip saddr {child_ip} ip daddr {ip} tcp dport 443 accept"
        for ip in allow_ips
    )
    return f"""\
add table ip {table}
add chain ip {table} postrouting {{ type nat hook postrouting priority 100 ; }}
add rule ip {table} postrouting ip saddr {child_ip} masquerade
add chain ip {table} forward {{ type filter hook forward priority 0 ; policy drop ; }}
add rule ip {table} forward ct state established,related accept
{allow_rules}
"""


def netns_hosts_file(host: str, allow_ips: list[str]) -> str:
    """The static ``/etc/hosts`` for the namespace: loopback plus host→IP for every org IP.

    ``ip netns exec`` bind-mounts ``/etc/netns/<ns>/hosts`` over ``/etc/hosts``, so pinning
    the org host here lets the agent resolve it with **no DNS egress**.
    """
    lines = ["127.0.0.1 localhost", "::1 localhost"]
    lines += [f"{ip} {host}" for ip in allow_ips]
    return "\n".join(lines) + "\n"


def wrap_agent_cmd(agent_cmd: list[str], netns: str) -> list[str]:
    """Prefix ``agent_cmd`` so it runs inside the network namespace ``netns``."""
    return ["ip", "netns", "exec", netns, *agent_cmd]


@dataclasses.dataclass(frozen=True)
class NetnsSandbox:
    """A provisioned network namespace + its teardown handle."""

    netns: str
    table: str
    veth_host: str
    etc_dir: Path

    def wrap(self, agent_cmd: list[str]) -> list[str]:
        return wrap_agent_cmd(agent_cmd, self.netns)


def _run(argv: list[str], *, input_text: str | None = None) -> None:
    proc = subprocess.run(
        argv, input=input_text, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise SandboxError(
            f"{' '.join(argv)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )


def _require_root_and_tools() -> None:
    if os.geteuid() != 0:
        raise SandboxError(
            "network sandbox needs root (root nftables + netns per ADR 0028) — "
            "re-run the live paired eval under sudo"
        )
    for tool in ("ip", "nft"):
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            raise SandboxError(f"required tool {tool!r} not on PATH")


@contextlib.contextmanager
def network_sandbox(
    host: str, *, run_id: str, resolver: Resolver | None = None
) -> Iterator[NetnsSandbox]:
    """Provision an org-only network namespace for ``host``; yield it; tear it all down.

    Effectful and root-only (see module docstring): creates the netns + veth + NAT +
    nftables allowlist and the static hosts file, then removes every artifact on exit even
    if the body raises. Exercised on the maintainer's live run, not the offline suite.
    """
    _require_root_and_tools()
    allow_ips = resolve_allow_ips(host, resolver=resolver)
    netns = f"crmeval-{run_id}"
    table = f"crmeval_{run_id}".replace("-", "_")
    veth_h, veth_c = f"vh-{run_id}"[:15], f"vc-{run_id}"[:15]
    etc_dir = Path("/etc/netns") / netns

    etc_dir.mkdir(parents=True, exist_ok=True)
    (etc_dir / "hosts").write_text(netns_hosts_file(host, allow_ips), encoding="utf-8")
    (etc_dir / "resolv.conf").write_text("", encoding="utf-8")  # no DNS egress
    try:
        _run(["ip", "netns", "add", netns])
        _run(["ip", "link", "add", veth_h, "type", "veth", "peer", "name", veth_c])
        _run(["ip", "link", "set", veth_c, "netns", netns])
        _run(["ip", "addr", "add", f"{_HOST_IP}/{_PREFIX}", "dev", veth_h])
        _run(["ip", "link", "set", veth_h, "up"])
        _run(
            [
                "ip",
                "netns",
                "exec",
                netns,
                "ip",
                "addr",
                "add",
                f"{_CHILD_IP}/{_PREFIX}",
                "dev",
                veth_c,
            ]
        )
        _run(["ip", "netns", "exec", netns, "ip", "link", "set", veth_c, "up"])
        _run(["ip", "netns", "exec", netns, "ip", "link", "set", "lo", "up"])
        _run(["ip", "netns", "exec", netns, "ip", "route", "add", "default", "via", _HOST_IP])
        _run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
        _run(
            ["nft", "-f", "-"],
            input_text=nft_ruleset(table=table, child_ip=_CHILD_IP, allow_ips=allow_ips),
        )
        yield NetnsSandbox(netns=netns, table=table, veth_host=veth_h, etc_dir=etc_dir)
    finally:
        # Best-effort teardown: never mask the body's outcome, remove every artifact.
        for argv in (
            ["nft", "delete", "table", "ip", table],
            ["ip", "link", "del", veth_h],
            ["ip", "netns", "del", netns],
        ):
            with contextlib.suppress(Exception):
                subprocess.run(argv, capture_output=True)
        with contextlib.suppress(Exception):
            for f in ("hosts", "resolv.conf"):
                (etc_dir / f).unlink(missing_ok=True)
            etc_dir.rmdir()


def _selfcheck(host: str) -> int:  # pragma: no cover - live smoke helper, not offline-tested
    """`python -m evals.skill.sandbox <host>`: prove the block lets the org through and
    nothing else — a manual live check on the maintainer's box (needs root).
    """
    with network_sandbox(host, run_id="selfcheck") as sb:
        org = subprocess.run(
            sb.wrap(["bash", "-lc", f"curl -sS -o /dev/null -w '%{{http_code}}' https://{host}"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        web = subprocess.run(
            sb.wrap(["bash", "-lc", "curl -sS --max-time 5 https://example.com"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        print(f"org {host}: http={org.stdout!r} rc={org.returncode}")
        print(f"web example.com: rc={web.returncode} (non-zero = correctly blocked)")
        return 0 if org.returncode == 0 and web.returncode != 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_selfcheck(sys.argv[1]))
