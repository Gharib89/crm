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
import shutil
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
    allowlist). IPv6 (AAAA) results are dropped: :func:`nft_ruleset` emits only ``ip``
    (v4) family rules, so an IPv6 address would make ``nft -f`` reject the whole ruleset.
    """
    resolve = resolver or (lambda h: socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP))
    addrs = {str(info[4][0]) for info in resolve(host) if info[0] == socket.AF_INET}
    if not addrs:
        raise ValueError(f"host {host!r} resolved to no IPv4 addresses")
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


def invoking_user_ids() -> tuple[int, int] | None:
    """The ``(uid, gid)`` that invoked us under ``sudo``, or None when not sudo-elevated.

    Both the netns de-privilege (drop the agent back to this uid — see :func:`wrap_agent_cmd`)
    and the results chown key off the same ``SUDO_UID``/``SUDO_GID`` pair the sudo wrapper
    exports, so the parse lives here once.
    """
    sudo_uid, sudo_gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    return (int(sudo_uid), int(sudo_gid)) if sudo_uid and sudo_gid else None


def wrap_agent_cmd(
    agent_cmd: list[str], netns: str, *, drop_to: tuple[int, int] | None = None
) -> list[str]:
    """Prefix ``agent_cmd`` so it runs inside the network namespace ``netns``.

    Creating/entering the netns is a root op, but under ``sudo`` the whole process tree is
    root and ``claude --dangerously-skip-permissions`` refuses to run as root. When
    ``drop_to=(uid, gid)`` is given, insert a ``setpriv`` de-privilege step *after*
    ``ip netns exec`` so the agent drops back to the invoking user — no longer root
    (skip-permissions allowed), yet still trapped in the root-built namespace. ``setpriv``
    (unlike ``runuser``) preserves the env verbatim, so ``HOME``/``PATH``/creds survive.
    ``drop_to=None`` leaves the existing root-runs-the-agent wrap unchanged.
    """
    prefix = ["ip", "netns", "exec", netns]
    if drop_to is not None:
        uid, gid = drop_to
        prefix += ["setpriv", "--reuid", str(uid), "--regid", str(gid), "--init-groups"]
    return [*prefix, *agent_cmd]


@dataclasses.dataclass(frozen=True)
class NetnsSandbox:
    """A provisioned network namespace + its teardown handle."""

    netns: str
    table: str
    veth_host: str
    etc_dir: Path
    #: (uid, gid) the agent drops to inside the netns (from ``SUDO_UID``/``SUDO_GID``), or
    #: None when the harness itself isn't root-via-sudo (the agent then runs as-is).
    drop_to: tuple[int, int] | None = None

    def wrap(self, agent_cmd: list[str]) -> list[str]:
        return wrap_agent_cmd(agent_cmd, self.netns, drop_to=self.drop_to)


def _run(argv: list[str], *, input_text: str | None = None) -> None:
    proc = subprocess.run(
        argv, input=input_text, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise SandboxError(
            f"{' '.join(argv)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )


def _sysctl_get(key: str) -> str | None:
    """Read a sysctl value (``None`` if unavailable), so it can be restored on teardown."""
    proc = subprocess.run(
        ["sysctl", "-n", key], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _require_root_and_tools() -> None:
    if os.geteuid() != 0:
        raise SandboxError(
            "network sandbox needs root (root nftables + netns per ADR 0028) — "
            "re-run the live paired eval under sudo"
        )
    for tool in ("ip", "nft", "sysctl", "setpriv"):
        if shutil.which(tool) is None:
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
    # Under sudo the process tree is root; drop the agent back to the invoking user inside the
    # netns (claude refuses root). None when root wasn't reached via sudo (the agent runs as-is).
    drop_to = invoking_user_ids()
    allow_ips = resolve_allow_ips(host, resolver=resolver)
    netns = f"crmeval-{run_id}"
    table = f"crmeval_{run_id}".replace("-", "_")
    # veth names cap at 15 chars: derive from run_id's random suffix (its unique part), not a
    # truncated timestamp prefix — else two runs in the same second collide and `ip link add`
    # fails. token_hex(2) tails are 4 chars, so vh-/vc- + suffix stays well under the cap.
    sfx = run_id.rsplit("-", 1)[-1][:12]
    veth_h, veth_c = f"vh-{sfx}", f"vc-{sfx}"
    etc_dir = Path("/etc/netns") / netns

    # All effectful setup lives inside the try so the finally tears down a *partial* build
    # too — a failure between mkdir and the last _run must not orphan etc_dir or the netns.
    fwd_prev: str | None = None
    try:
        etc_dir.mkdir(parents=True, exist_ok=True)
        (etc_dir / "hosts").write_text(netns_hosts_file(host, allow_ips), encoding="utf-8")
        (etc_dir / "resolv.conf").write_text("", encoding="utf-8")  # no DNS egress
        fwd_prev = _sysctl_get("net.ipv4.ip_forward")
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
        yield NetnsSandbox(
            netns=netns, table=table, veth_host=veth_h, etc_dir=etc_dir, drop_to=drop_to
        )
    finally:
        # Best-effort teardown: never mask the body's outcome, remove every artifact.
        teardown = [
            ["nft", "delete", "table", "ip", table],
            ["ip", "link", "del", veth_h],
            ["ip", "netns", "del", netns],
        ]
        # Restore the host's prior ip_forward: leaving it on is a global change to the
        # maintainer's machine's network posture that outlives the eval.
        if fwd_prev is not None:
            teardown.append(["sysctl", "-w", f"net.ipv4.ip_forward={fwd_prev}"])
        for argv in teardown:
            with contextlib.suppress(Exception):
                subprocess.run(
                    argv, capture_output=True, text=True, encoding="utf-8", errors="replace"
                )
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
