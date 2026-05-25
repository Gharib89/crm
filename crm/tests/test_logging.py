"""Unit tests for Spec E logging setup."""
# pyright: basic
from __future__ import annotations

import json
import logging

import pytest

from crm.core.logging_setup import CrmLogHandler, setup_logging


@pytest.fixture(autouse=True)
def _reset_crm_logger():
    """Strip handlers off the 'crm' logger between tests."""
    logger = logging.getLogger("crm")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    logger.handlers.clear()
    yield
    logger.handlers.clear()
    for h in saved_handlers:
        logger.addHandler(h)
    logger.setLevel(saved_level)


class TestTextFormat:
    def test_request_log_text_format(self, capsys):
        setup_logging(level="debug", fmt="text")
        logging.getLogger("crm.http").debug(
            "request", extra={"event": "request", "method": "GET",
                              "url": "https://crm/api/data/v9.2/accounts"}
        )
        err = capsys.readouterr().err
        assert "[DEBUG]" in err
        assert "GET" in err
        assert "https://crm/api/data/v9.2/accounts" in err

    def test_response_log_text_format_includes_ms(self, capsys):
        setup_logging(level="debug", fmt="text")
        logging.getLogger("crm.http").debug(
            "response", extra={"event": "response", "status": 200, "ms": 142}
        )
        err = capsys.readouterr().err
        assert "200" in err
        assert "142" in err


class TestJsonLineFormat:
    def test_request_log_emits_single_json_line(self, capsys):
        setup_logging(level="debug", fmt="json-line")
        logging.getLogger("crm.http").debug(
            "request", extra={"event": "request", "method": "GET",
                              "url": "https://crm/api/data/v9.2/accounts"}
        )
        err = capsys.readouterr().err.strip()
        assert err.count("\n") == 0
        payload = json.loads(err)
        assert payload == {
            "level": "debug",
            "event": "request",
            "method": "GET",
            "url": "https://crm/api/data/v9.2/accounts",
        }

    def test_response_log_includes_status_and_ms(self, capsys):
        setup_logging(level="debug", fmt="json-line")
        logging.getLogger("crm.http").debug(
            "response", extra={"event": "response", "status": 200, "ms": 142}
        )
        payload = json.loads(capsys.readouterr().err.strip())
        assert payload["status"] == 200
        assert payload["ms"] == 142


class TestSetupIdempotency:
    def test_repeated_setup_does_not_stack_handlers(self):
        setup_logging(level="debug", fmt="text")
        setup_logging(level="warning", fmt="json-line")
        handlers = [h for h in logging.getLogger("crm").handlers
                    if isinstance(h, CrmLogHandler)]
        assert len(handlers) == 1
        assert handlers[0].fmt == "json-line"

    def test_level_filters_below_threshold(self, capsys):
        setup_logging(level="warning", fmt="text")
        logging.getLogger("crm.http").debug("request",
            extra={"event": "request", "method": "GET", "url": "..."})
        assert capsys.readouterr().err == ""


class TestBackendIntegration:
    def test_backend_request_emits_request_response_logs(self, capsys):
        import requests_mock
        from crm.utils.d365_backend import ConnectionProfile, D365Backend

        setup_logging(level="debug", fmt="text")
        profile = ConnectionProfile(
            name="t", url="https://crm.contoso.local/contoso",
            domain="CONTOSO", username="alice", verify_ssl=False,
        )
        backend = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.get(
                "https://crm.contoso.local/contoso/api/data/v9.2/WhoAmI",
                json={"UserId": "00000000-0000-0000-0000-000000000000"},
            )
            backend.get("WhoAmI")

        err = capsys.readouterr().err
        assert "request" in err
        assert "GET" in err
        assert "response" in err
        assert "200" in err
