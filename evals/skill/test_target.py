"""Offline tests for target reachability classification (issue #573).

The live ``probe_reachable`` needs a real org, so it is exercised by an opportunistic
live run, not here; this pins the pure classifier that decides whether a failed probe
means "host never answered" (skip the target) or "host answered with an error" (a
reachable target whose failures should surface). Mirrors the e2e conftest's
``_is_unreachable`` so the harness skips a downed VPN the same way the e2e suite does.

    pytest evals/skill
"""
from __future__ import annotations

from crm.utils.d365_backend import D365Error, _TRANSPORT_FAILURE_PREFIX
from evals.skill.target import _is_unreachable


def test_transport_failure_is_unreachable():
    # A connect/timeout/TLS failure is wrapped status-less with the transport prefix —
    # that is the only case that means "VPN down / host not responding".
    exc = D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: Connection refused")
    assert _is_unreachable(exc) is True


def test_http_error_is_reachable():
    # Any HTTP response (incl 401/403) sets a status, so the host answered: reachable.
    assert _is_unreachable(D365Error("Unauthorized", status=401)) is False
    assert _is_unreachable(D365Error("Forbidden", status=403)) is False


def test_statusless_validation_error_is_reachable():
    # A status-less client-side validation error is not a transport failure, so it
    # must not be mistaken for an unreachable host.
    assert _is_unreachable(D365Error("bad filter syntax")) is False
