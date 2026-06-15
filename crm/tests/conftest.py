"""Shared scaffolding for the unit suite (``crm/tests/``).

This is the parent conftest. ``crm/tests/e2e/conftest.py`` sits below it and
its session-scoped ``backend`` / ``live_profile`` fixtures intentionally
override the function-scoped ones here within ``e2e/``.

Two sanctioned ways to fake the backend, each at a real seam:

1. Real ``D365Backend`` + ``requests_mock`` at the wire — use the ``backend`` /
   ``dry_backend`` fixtures and mock HTTP. Exercises the transport layer.
2. ``FakeBackend`` injected at ``CLIContext.backend`` — use ``make_fake_backend``
   (or ``fake_backend``) plus ``inject_backend``. Bypasses transport entirely;
   for command-layer tests that only care about the parsed response.

Canonical literals (the one source of truth — do not re-invent per file):
``testp`` / ``https://crm.contoso.local/contoso`` / ``CONTOSO`` / ``alice`` /
``v9.2``, password ``pw``.
"""

from __future__ import annotations

import os
import time as _time
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from crm.utils.d365_backend import ConnectionProfile, D365Backend

# Legacy credential env vars (v2.0.0 removed env-derived credentials). No code
# path reads these any more, but a developer's stray export must never sway a
# test, so isolated_home scrubs them for the duration of each test.
_LEGACY_CRED_ENV = (
    "D365_URL", "CRM_BASE_URL", "CRM_URL",
    "D365_USERNAME", "CRM_USERNAME", "CRM_USER",
    "D365_PASSWORD", "CRM_PASSWORD", "CRM_PASS",
    "D365_DOMAIN", "CRM_DOMAIN",
    "D365_AUTH", "CRM_AUTH",
    "D365_TENANT_ID", "CRM_TENANT_ID",
    "D365_CLIENT_ID", "CRM_CLIENT_ID",
    "D365_CLIENT_SECRET", "CRM_CLIENT_SECRET",
)


# --------------------------------------------------------------------------- #
# Connection profile + real backend
# --------------------------------------------------------------------------- #
@pytest.fixture
def profile() -> ConnectionProfile:
    """The canonical test profile. Override in a module only to vary a field
    (e.g. ``api_version="v9.1"``); ``backend``/``dry_backend`` resolve the
    nearest ``profile``, so a local override flows through automatically."""
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile: ConnectionProfile) -> D365Backend:
    """A real ``D365Backend`` (no network at construction). Pair with
    ``requests_mock`` to exercise the transport layer."""
    return D365Backend(profile, password="pw", dry_run=False)


@pytest.fixture
def dry_backend(profile: ConnectionProfile) -> D365Backend:
    """A real ``D365Backend`` in ``dry_run`` mode."""
    return D365Backend(profile, password="pw", dry_run=True)


# --------------------------------------------------------------------------- #
# Environment isolation
# --------------------------------------------------------------------------- #
@pytest.fixture
def isolated_home(tmp_path: Path) -> Iterator[Path]:
    """Isolate ``CRM_HOME`` to a tmpdir and disable ``.env`` autoload, fully
    snapshotting/restoring ``os.environ`` around the test.

    Manual snapshot/restore (not ``monkeypatch.setenv``) because
    ``python-dotenv``'s ``load_dotenv`` mutates ``os.environ`` via
    ``__setitem__`` outside monkeypatch's tracking, so monkeypatch alone
    cannot undo it (cf. #56). Yields ``tmp_path`` for callers that need it.

    Opt in per module with
    ``pytestmark = pytest.mark.usefixtures("isolated_home")``."""
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(tmp_path / ".crm")
    os.environ["CRM_DOTENV"] = str(tmp_path / "noop.env")
    for key in _LEGACY_CRED_ENV:
        os.environ.pop(key, None)
    try:
        yield tmp_path
    finally:
        os.environ.clear()
        os.environ.update(saved)


# --------------------------------------------------------------------------- #
# Retry / backoff
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise ``time.sleep`` so retry/async-backoff tests don't actually
    wait. Patches the stdlib ``time`` module (the backend sleeps via it)."""

    def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(_time, "sleep", _noop)


# --------------------------------------------------------------------------- #
# FakeBackend — duck-typed stand-in injected at CLIContext.backend
# --------------------------------------------------------------------------- #
# Standard entity definitions served by ``get_collection("EntityDefinitions")``
# when a test does not override it — lets commands that resolve a name through
# ``entity_names.load_name_map`` (e.g. ``query count``) work out of the box.
_DEFAULT_ENTITY_DEFINITIONS: list[dict[str, str]] = [
    {"LogicalName": "account", "EntitySetName": "accounts",
     "PrimaryIdAttribute": "accountid", "PrimaryNameAttribute": "name"},
    {"LogicalName": "contact", "EntitySetName": "contacts",
     "PrimaryIdAttribute": "contactid", "PrimaryNameAttribute": "fullname"},
]


class FakeBackend:
    """In-memory stand-in for ``D365Backend``, injected at
    ``CLIContext.backend`` for command-layer tests.

    Configure per-verb behaviour and assert on the recorded calls:

    - ``responses``: ``{verb: value_or_callable}`` — a callable is invoked with
      the request path and its return value is used (default: ``{"value": []}``
      for ``get``, the standard entity definitions for
      ``get_collection("EntityDefinitions")``, ``None`` for write verbs).
    - ``errors``: ``{verb: exception}`` — raised when that verb is called.
    - ``forbid``: verbs that must never be called (raise ``AssertionError``).

    Carries a real ``profile`` so the read-through metadata cache (used by the
    name-resolution seam) can key on it. Recorded state: ``calls`` (list of
    ``(verb, path, kwargs)``), and the convenience accessors ``called``,
    ``last_path``, ``count(verb=None)``.
    """

    def __init__(
        self,
        *,
        dry_run: bool = False,
        responses: dict[str, Any] | None = None,
        errors: dict[str, BaseException] | None = None,
        forbid: tuple[str, ...] = (),
        profile: ConnectionProfile | None = None,
    ) -> None:
        self.dry_run = dry_run
        self._responses: dict[str, Any] = dict(responses or {})
        self._errors: dict[str, BaseException] = dict(errors or {})
        self._forbid: set[str] = set(forbid)
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.profile: ConnectionProfile = profile or ConnectionProfile(
            name="testp",
            url="https://crm.contoso.local/contoso",
            domain="CONTOSO",
            username="alice",
            api_version="v9.2",
            verify_ssl=False,
        )

    @property
    def called(self) -> bool:
        return bool(self.calls)

    @property
    def last_path(self) -> Any:
        return self.calls[-1][1] if self.calls else None

    def count(self, verb: str | None = None) -> int:
        if verb is None:
            return len(self.calls)
        return sum(1 for v, _p, _k in self.calls if v == verb)

    @staticmethod
    def _default(verb: str) -> Any:
        # Fresh object per call: the GET default is a mutable dict, so a caller
        # that mutates the result must not be able to leak state across calls
        # or tests.
        return {"value": []} if verb == "get" else None

    def _dispatch(self, verb: str, path: Any, kwargs: dict[str, Any]) -> Any:
        if verb in self._forbid:
            raise AssertionError(
                f"FakeBackend.{verb} should not be called (path={path!r})"
            )
        self.calls.append((verb, path, kwargs))
        if verb in self._errors:
            raise self._errors[verb]
        if verb in self._responses:
            resp = self._responses[verb]
            return resp(path) if callable(resp) else resp
        return self._default(verb)

    def get(
        self, path: Any = None, *_args: Any, params: Any = None, **kwargs: Any
    ) -> Any:
        return self._dispatch("get", path, {"params": params, **kwargs})

    def get_collection(
        self, path: Any = None, *_args: Any, params: Any = None, **kwargs: Any
    ) -> Any:
        result = self._dispatch("get_collection", path, {"params": params, **kwargs})
        # No override configured (_default → None for non-get verbs): serve the
        # standard entity definitions for the name-resolution seam, [] otherwise.
        if result is None:
            return list(_DEFAULT_ENTITY_DEFINITIONS) if path == "EntityDefinitions" else []
        return result

    def post(self, path: Any = None, *_args: Any, **kwargs: Any) -> Any:
        return self._dispatch("post", path, kwargs)

    def patch(self, path: Any = None, *_args: Any, **kwargs: Any) -> Any:
        return self._dispatch("patch", path, kwargs)

    def put(self, path: Any = None, *_args: Any, **kwargs: Any) -> Any:
        return self._dispatch("put", path, kwargs)

    def delete(self, path: Any = None, *_args: Any, **kwargs: Any) -> Any:
        return self._dispatch("delete", path, kwargs)

    def url_for(self, path: str) -> str:
        import urllib.parse
        return urllib.parse.urljoin(self.profile.api_base, path.lstrip("/"))


@pytest.fixture
def make_fake_backend() -> Callable[..., FakeBackend]:
    """Factory for a configured ``FakeBackend`` (see the class docstring)."""

    def _make(**kwargs: Any) -> FakeBackend:
        return FakeBackend(**kwargs)

    return _make


@pytest.fixture
def fake_backend() -> FakeBackend:
    """A default ``FakeBackend`` (``get`` returns ``{"value": []}``)."""
    return FakeBackend()


@pytest.fixture
def inject_backend(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], Any]:
    """Inject a backend at the ``CLIContext.backend`` seam and return it.

    ``b = inject_backend(make_fake_backend(responses={"get": ...}))`` — every
    command run in the test then sees ``b`` instead of a live backend."""
    from crm.cli import CLIContext

    def _inject(backend: Any) -> Any:
        def _method(_self: CLIContext) -> Any:
            return backend

        monkeypatch.setattr(CLIContext, "backend", _method)
        return backend

    return _inject
