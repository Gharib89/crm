"""``is_cloud``: the one place that answers "is this a cloud profile"."""

# pyright: basic
from __future__ import annotations

import pytest

from crm.utils.d365_backend import AUTH_SCHEMES, ConnectionProfile, is_cloud


@pytest.mark.parametrize("scheme", AUTH_SCHEMES)
def test_is_cloud_per_scheme(scheme: str) -> None:
    profile = ConnectionProfile(
        name="p", url="https://x", domain="", username="", auth_scheme=scheme
    )
    assert is_cloud(profile) is (scheme == "oauth")
