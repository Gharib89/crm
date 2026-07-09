"""Offline guard for the shared-fixture e2e artifact marker (#769).

The live e2e fixtures stamp every SHARED-fixture artifact on the long-lived cloud
org (#760) with a single marker so leaks are greppable/safe to sweep. That marker
doubles as a Dataverse customization PREFIX, whose rules are the strictest name
constraint in play (2-8 alnum, start with a letter, MaxLength 8). This test pins
the marker to those rules WITHOUT a live org, so a bad marker fails in plain CI
instead of only surfacing mid-suite against the org.

Lives OUTSIDE crm/tests/e2e/ so it is not auto-marked `e2e` and runs offline.
"""
from __future__ import annotations

import pytest

from crm.core.solution import validate_customization_prefix
from crm.tests.e2e.conftest import E2E_MARKER, _marker_prefix


def test_marker_is_a_db_legal_customization_prefix():
    # The real backend validator is the source of truth for "DB-legal" — if the
    # marker cannot itself be a customization prefix, ephemeral_solution breaks.
    validate_customization_prefix(E2E_MARKER)


@pytest.mark.parametrize("suffix", ["", "a", "abcd", "0123abcd", "deadbeefcafe"])
def test_marker_prefix_stays_db_legal_for_any_suffix(suffix):
    prefix = _marker_prefix(suffix)
    assert prefix.startswith(E2E_MARKER), prefix
    assert len(prefix) <= 8, prefix
    validate_customization_prefix(prefix)  # 2-8 alnum, letter-start, not 'mscrm'


def test_marker_prefix_varies_with_the_run_suffix():
    # The prefix must track the random per-run suffix, not be a fixed literal, or
    # concurrent runs on the shared org clash on the environment-unique publisher
    # prefix. (Only the surviving head of the suffix matters — see _marker_prefix.)
    assert _marker_prefix("aaaabbbb") != _marker_prefix("ccccdddd")
