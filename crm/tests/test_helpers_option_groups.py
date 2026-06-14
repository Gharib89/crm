"""Behavior of the shared option-group helpers (#294).

Covers the three seams finished in #294: the `value:label` parser
`_parse_value_labels` (with its int-guard hardening) and the paired option
decorators `_publish_option` / `_destructive_option`.
"""
# pyright: basic
from __future__ import annotations

import click
import pytest

from crm.commands._helpers import (
    _parse_value_labels,
    _publish_option,
    _destructive_option,
)


class TestParseValueLabels:
    def test_basic_pairs(self):
        assert _parse_value_labels(("1:Active", "2:Inactive"), flag="--option") == [
            (1, "Active"),
            (2, "Inactive"),
        ]

    def test_value_omitted_yields_none(self):
        assert _parse_value_labels((":Active",), flag="--option") == [(None, "Active")]

    def test_whitespace_is_trimmed(self):
        assert _parse_value_labels((" 1 : Active ",), flag="--option") == [(1, "Active")]

    def test_label_may_contain_colon(self):
        # Split on the FIRST ':' only — the label keeps later colons.
        assert _parse_value_labels(("1:a:b",), flag="--option") == [(1, "a:b")]

    def test_missing_colon_is_usage_error(self):
        with pytest.raises(click.UsageError):
            _parse_value_labels(("Active",), flag="--option")

    def test_non_int_value_is_usage_error(self):
        # The #294 hardening: previously a bare int(v) raised an unhandled
        # ValueError; now it is a clean exit-2 UsageError.
        with pytest.raises(click.UsageError):
            _parse_value_labels(("abc:foo",), flag="--option")

    def test_empty_input_yields_empty_list(self):
        assert _parse_value_labels((), flag="--option") == []

    def test_required_value_ok(self):
        assert _parse_value_labels(
            ("1:Renamed",), flag="--update-option", require_value=True
        ) == [(1, "Renamed")]

    def test_required_value_rejects_empty(self):
        with pytest.raises(click.UsageError):
            _parse_value_labels(
                (":Renamed",), flag="--update-option", require_value=True
            )

    def test_required_value_rejects_non_int(self):
        with pytest.raises(click.UsageError):
            _parse_value_labels(
                ("abc:Renamed",), flag="--update-option", require_value=True
            )


def _option_named(cmd: click.Command, name: str) -> click.Option:
    (opt,) = [
        p for p in cmd.params if isinstance(p, click.Option) and p.name == name
    ]
    return opt


class TestPublishOption:
    def test_stacks_publish_flag(self):
        @click.command()
        @_publish_option
        def cmd(publish):  # pragma: no cover - never invoked
            pass

        opt = _option_named(cmd, "publish")
        assert opt.default is True
        assert "--publish" in opt.opts and "--no-publish" in opt.secondary_opts
        assert opt.help == "Run PublishAllXml after the change. Default: publish."


class TestDestructiveOption:
    def test_stacks_yes_flag(self):
        @click.command()
        @_destructive_option
        def cmd(yes):  # pragma: no cover - never invoked
            pass

        opt = _option_named(cmd, "yes")
        assert opt.is_flag is True
        assert opt.default is False
        assert "--yes" in opt.opts
        assert opt.help == "Skip interactive confirmation."
