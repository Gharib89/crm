"""Generic, cross-domain Click option decorators for crm.commands.*."""
# pyright: basic
from __future__ import annotations
import click


def _output_option(required: bool = False, help: str | None = None):
    """Stack the standard `--output / -o` option on a command."""
    def decorator(f):
        return click.option(
            "--output", "-o",
            required=required,
            type=click.Path(dir_okay=False),
            help=help,
        )(f)
    return decorator
