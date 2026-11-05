"""Generic, cross-domain Click option decorators for crm.commands.*."""

# pyright: basic
from __future__ import annotations

import functools

import click


def _output_option(required: bool = False, help: str | None = None):
    """Stack the standard `--output / -o` option on a command."""

    def decorator(f):
        return click.option(
            "--output",
            "-o",
            required=required,
            type=click.Path(dir_okay=False),
            help=help,
        )(f)

    return decorator


def in_use_options(f):
    """Stack the shared `--in-use` / `--exclude-unknown` pair on a listing command (spec #1091).

    The command receives `in_use` and `exclude_unknown`; `--exclude-unknown`
    alone is a usage error, since it only tightens `--in-use`.
    """

    @functools.wraps(f)
    def checked(*args, in_use, exclude_unknown, **kwargs):
        if exclude_unknown and not in_use:
            raise click.UsageError("--exclude-unknown needs --in-use.")
        return f(*args, in_use=in_use, exclude_unknown=exclude_unknown, **kwargs)

    return click.option(
        "--in-use",
        is_flag=True,
        help="Add a `usage` object to each row and drop the rows proven unused (unknown stay).",
    )(
        click.option(
            "--exclude-unknown",
            is_flag=True,
            help="With --in-use, also drop rows whose usage is unknown: proven-used only.",
        )(checked)
    )
