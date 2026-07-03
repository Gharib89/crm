"""Curated, runnable `crm` example gallery (#658).

The single source of truth for `crm examples`: a hand-picked set of real
invocations per high-traffic group, each a command string plus a one-line
description. Quality over quantity — this is a teaching surface, not an
exhaustive catalogue (that is `crm describe`).

Every command here is verified against the live Click tree by an offline
anti-drift test (``crm/tests/test_examples.py``): if an example ever references
a removed command or flag, CI fails. That gate is the whole point of keeping the
gallery *in code* rather than as hand-written prose that silently rots.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Example:
    """One curated invocation.

    `workflow` optionally tags an example as one step of a short, ordered
    sequence (e.g. the solution source-control round-trip) so related examples
    read as a group in the human picker. It is display-only — the JSON contract
    (ADR 0008) exposes just ``group``/``command``/``description``.
    """

    command: str
    description: str
    workflow: str | None = None


# Per-group curated examples. Keyed by top-level group name; the key is the
# `crm examples <group>` argument. Start with the high-traffic groups and keep
# each list short — every entry must earn its place and stay anti-drift-valid.
# Placeholder tokens are UPPER_CASE (e.g. ACCOUNT_ID) so a user replaces them
# without tripping over shell metacharacters.
EXAMPLES: dict[str, list[Example]] = {
    "entity": [
        Example(
            "crm entity get accounts ACCOUNT_ID --select name,telephone1",
            "Read chosen columns from a single record.",
        ),
        Example(
            "crm entity get accounts ACCOUNT_ID --expand primarycontactid($select=fullname)",
            "Expand a related record inline.",
        ),
        Example(
            'crm entity create accounts --data \'{"name": "Contoso"}\'',
            "Create a record from inline JSON.",
        ),
        Example(
            'crm entity update accounts ACCOUNT_ID --data \'{"telephone1": "555-0100"}\'',
            "Update fields on an existing record.",
        ),
        Example(
            "crm entity delete accounts ACCOUNT_ID --yes",
            "Delete a record, skipping the confirmation prompt.",
        ),
    ],
    "query": [
        Example(
            "crm query odata accounts --select name,revenue --top 5",
            "First N rows with selected columns.",
        ),
        Example(
            'crm query odata accounts --filter "revenue gt 100000" --orderby "revenue desc"',
            "Filter and sort a result set.",
        ),
        Example(
            "crm query odata contacts --all --max-records 5000",
            "Page through every matching row up to a cap.",
        ),
        Example(
            "crm query count accounts",
            "Count the rows in a table.",
        ),
        Example(
            "crm query fetchxml accounts --file query.xml",
            "Run a FetchXML query stored in a file.",
        ),
    ],
    "solution": [
        Example(
            "crm solution list --managed",
            "List installed solutions.",
        ),
        Example(
            "crm solution create --name dev --display \"Dev\" --publisher acme",
            "Create a new unmanaged solution.",
        ),
        Example(
            "crm solution export MySolution --output ./MySolution.zip",
            "Export a solution to a zip.",
            workflow="source-control round-trip",
        ),
        Example(
            "crm solution extract --zipfile ./MySolution.zip --folder ./src/MySolution",
            "Unpack a solution zip into source folders.",
            workflow="source-control round-trip",
        ),
        Example(
            "crm solution pack --folder ./src/MySolution --zipfile ./MySolution.zip",
            "Repack source folders back into a zip.",
            workflow="source-control round-trip",
        ),
        Example(
            "crm solution import ./MySolution.zip --publish",
            "Import a solution zip and publish customizations.",
            workflow="source-control round-trip",
        ),
    ],
    "profile": [
        Example(
            "crm profile list",
            "Show saved profiles and which one is active.",
        ),
        Example(
            "crm profile add --url https://contoso.crm.dynamics.com --name PROFILE_NAME",
            "Add a profile (auth scheme inferred from the URL).",
        ),
        Example(
            "crm profile use PROFILE_NAME",
            "Switch the active profile.",
        ),
    ],
    "metadata": [
        Example(
            "crm metadata entities --custom-only",
            "List custom tables in the org.",
        ),
        Example(
            "crm metadata entity account",
            "Describe a table's definition.",
        ),
        Example(
            "crm metadata attributes account",
            "List a table's columns.",
        ),
        Example(
            "crm metadata add-attribute account --kind string --schema-name new_nickname --display \"Nickname\"",
            "Add a column to a table.",
        ),
        Example(
            "crm metadata create-optionset --name new_color --display \"Color\" --option 1=Red --option 2=Blue",
            "Create a global choice (option set).",
        ),
    ],
}


def groups() -> list[str]:
    """Group names that have curated examples, sorted for stable display."""
    return sorted(EXAMPLES)


def examples_for(group: str) -> list[Example]:
    """Curated examples for one group (empty list if the group is unknown)."""
    return EXAMPLES.get(group, [])


def listing(group: str | None) -> list[tuple[str, Example]]:
    """Flat ``(group, example)`` pairs — the whole gallery, or one group's slice.

    Group order follows :func:`groups`; within a group, registry order is
    preserved so any workflow sequence stays in step order.
    """
    selected = [group] if group is not None else groups()
    return [(g, ex) for g in selected for ex in examples_for(g)]
