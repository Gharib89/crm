"""`crm inventory extract` progress: the core's events and the command's stderr lines, offline."""

from __future__ import annotations

import json
from itertools import count
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.commands.inventory import ProgressLines
from crm.core import inventory_extract
from crm.core.inventory_extract import Progress
from crm.core.webapi_inventory import WebApiSource
from crm.tests.test_webapi_inventory import (
    _ENTITIES,  # pyright: ignore[reportPrivateUsage]
    _TRANSPORT_FAILURE_PREFIX,  # pyright: ignore[reportPrivateUsage]
    D365Error,
    FakeBackend,
    crm_home,  # noqa: F401  # pyright: ignore[reportUnusedImport]  (fixture)
)

_STEP = 10.0  # seconds the fake clock advances per reading


def _clock():
    ticks = count()
    return lambda: next(ticks) * _STEP


def _run(backend, out: Path, **kw) -> list[Progress]:
    events: list[Progress] = []
    inventory_extract.extract(
        WebApiSource(backend),
        out,
        cli_version="9.9.9",
        now=lambda: "2026-11-04T10:00:00Z",
        progress=events.append,
        clock=_clock(),
        **kw,
    )
    return events


def _stage(events: list[Progress], name: str) -> list[Progress]:
    return [e for e in events if e.stage == name]


def test_each_stage_starts_and_ends_with_its_total(tmp_path):
    events = _run(FakeBackend(), tmp_path / "inv")
    stages = list(dict.fromkeys(e.stage for e in events))
    assert stages == ["source", "records", "entity_detail", "ribbons", "build"]
    for name, total in (
        ("source", 3),
        ("records", len(inventory_extract._V9_READS)),  # pyright: ignore[reportPrivateUsage]
        ("entity_detail", len(_ENTITIES)),
    ):
        got = _stage(events, name)
        assert (got[0].done, got[0].total) == (0, total)
        assert (got[-1].done, got[-1].total) == (total, total)
    build = _stage(events, "build")
    assert [(e.done, e.total) for e in build] == [(0, None)]


def test_the_records_stage_reports_reads_with_kind_and_pages(tmp_path):
    org = {"webresourceset": [[], [], []]}  # three pages of one kind
    events = _stage(_run(FakeBackend(org=org), tmp_path / "inv"), "records")
    pages = [(e.kind, e.pages) for e in events if e.pages and e.kind == "web_resource"]
    assert pages == [("web_resource", 1), ("web_resource", 2), ("web_resource", 3)]
    assert all(e.eta is None for e in events)  # a read's cost varies too much to estimate


def test_the_eta_waits_for_read_units_and_is_an_estimate_of_the_rest(tmp_path):
    events = _stage(_run(FakeBackend(), tmp_path / "inv"), "entity_detail")
    # Each read takes one _STEP on the fake clock, so the average is _STEP.
    reads = [e for e in events if e.done > 0]
    threshold = inventory_extract._ETA_MIN_READS  # pyright: ignore[reportPrivateUsage]
    assert all(e.eta is None for e in reads[: threshold - 1])
    for e in reads[threshold - 1 :]:
        assert e.total is not None and e.eta == _STEP * (e.total - e.done)
    assert events[0].eta is None and all(e.eta is None for e in _stage(events, "source"))


def test_reused_units_count_as_done_and_do_not_skew_the_eta(tmp_path):
    out = tmp_path / "inv"
    reset = D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: connection reset")
    with pytest.raises(D365Error):
        _run(FakeBackend(fail={("webresourceset", 1): reset}), out)
    events = _run(FakeBackend(), out)
    source = _stage(events, "source")
    assert [(e.done, e.reused) for e in source] == [(0, 0), (1, 1), (2, 2), (3, 3)]
    reads = _stage(events, "records")
    assert reads[-1].done == reads[-1].total and reads[-1].reused > 0
    # Entity detail was never reached on the first run, so every unit is read now.
    assert _stage(events, "entity_detail")[-1].reused == 0


def test_no_callback_changes_nothing(tmp_path):
    with_cb = tmp_path / "a"
    without = tmp_path / "b"
    _run(FakeBackend(), with_cb)
    inventory_extract.extract(
        WebApiSource(FakeBackend()),
        without,
        cli_version="9.9.9",
        now=lambda: "2026-11-04T10:00:00Z",
    )
    assert (with_cb / "manifest.json").read_bytes() == (without / "manifest.json").read_bytes()


def _lines(events: list[Progress], clock) -> list[str]:
    out: list[str] = []
    printer = ProgressLines(lambda line: out.append(line.rstrip("\n")), clock)
    for e in events:
        printer(e)
    return out


def test_every_stage_start_and_last_line_print_and_the_rest_are_rate_limited():
    events = [
        Progress("entity_detail", 0, 4),
        Progress("entity_detail", 1, 4),
        Progress("entity_detail", 2, 4),
        Progress("entity_detail", 3, 4),
        Progress("entity_detail", 4, 4),
    ]
    ticks = iter([0.0, 0.2, 0.4, 0.6, 0.8])  # all inside one second
    lines = _lines(events, lambda: next(ticks))
    assert lines == [
        "[crm] extract stage=entity_detail total=4",
        "[crm] extract stage=entity_detail done=4/4",
    ]


def test_a_line_prints_again_once_a_second_has_passed():
    events = [Progress("ribbons", 0, 9), Progress("ribbons", 1, 9), Progress("ribbons", 2, 9)]
    ticks = iter([0.0, 0.5, 1.2])
    assert _lines(events, lambda: next(ticks))[-1] == "[crm] extract stage=ribbons done=2/9"


def test_a_line_names_the_kind_pages_reused_and_a_labelled_eta():
    events = [
        Progress("records", 3, 12, kind="web_resource", pages=4),
        Progress("entity_detail", 7, 300, reused=5, eta=185.0),
        Progress("entity_detail", 8, 300, eta=29.0),
        Progress("entity_detail", 9, 300, eta=4000.0),
    ]
    ticks = iter([10.0, 20.0, 30.0, 40.0])
    assert _lines(events, lambda: next(ticks)) == [
        "[crm] extract stage=records done=3/12 kind=web_resource pages=4",
        "[crm] extract stage=entity_detail done=7/300 reused=5 eta=~3m (estimate)",
        "[crm] extract stage=entity_detail done=8/300 eta=~29s (estimate)",
        "[crm] extract stage=entity_detail done=9/300 eta=~1h07m (estimate)",
    ]


def test_a_stage_without_a_total_prints_its_name_only():
    assert _lines([Progress("build", 0, None)], lambda: 0.0) == ["[crm] extract stage=build"]


@pytest.mark.usefixtures("crm_home")
@pytest.mark.parametrize("flags", [[], ["--json"]])
def test_quiet_leaves_stdout_unchanged_and_stderr_without_progress(tmp_path, monkeypatch, flags):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: FakeBackend())

    def run(out: str, *extra: str):
        argv = [*flags, "--profile", "v9", "inventory", "extract", "--out", out, *extra]
        return CliRunner().invoke(cli, argv)

    loud = run(str(tmp_path / "a"))
    quiet = run(str(tmp_path / "b"), "--quiet")
    assert loud.exit_code == quiet.exit_code == 0, loud.output
    assert loud.stdout.replace(str(tmp_path / "a"), "") == quiet.stdout.replace(
        str(tmp_path / "b"), ""
    )
    assert "[crm] extract" not in loud.stdout
    assert "[crm] extract stage=source total=3" in loud.stderr
    assert "[crm] extract stage=build" in loud.stderr
    assert "[crm] extract" not in quiet.stderr
    if flags:
        assert json.loads(loud.stdout)["ok"] is True


@pytest.mark.usefixtures("crm_home")
def test_the_short_flag_is_quiet_too(tmp_path, monkeypatch):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: FakeBackend())
    argv = ["--profile", "v9", "inventory", "extract", "--out", str(tmp_path / "i"), "-q"]
    result = CliRunner().invoke(cli, argv)
    assert result.exit_code == 0, result.output
    assert "[crm] extract" not in result.stderr


def test_a_first_read_page_event_is_not_a_stage_start_and_is_rate_limited():
    events = [
        Progress("records", 0, 12),
        Progress("records", 0, 12, kind="solution", pages=1),
        Progress("records", 0, 12, kind="solution", pages=2),
    ]
    ticks = iter([0.0, 0.2, 1.5])
    assert _lines(events, lambda: next(ticks)) == [
        "[crm] extract stage=records total=12",
        "[crm] extract stage=records done=0/12 kind=solution pages=2",
    ]


def test_the_eta_averages_read_units_only_when_reused_units_come_first(tmp_path):
    cp, _, _ = inventory_extract.Checkpoint.open(tmp_path / "inv", "https://x", "t")
    for n in range(6):
        cp.save(f"entity/e{n}", {"n": n})
    events: list[Progress] = []
    run = inventory_extract._Run(  # pyright: ignore[reportPrivateUsage]
        None,
        cp,
        events.append,
        _clock(),  # pyright: ignore[reportArgumentType]
    )
    run.begin("entity_detail", 16, estimate=True)
    for n in range(16):
        run.unit((), f"entity/e{n}", lambda: {"ok": True})
    # events[0] is the stage start, [1:7] the reused units, [7:] the reads.
    assert [e.eta for e in events[:11]] == [None] * 11  # reused units never feed it
    assert events[11].done == 11 and events[11].eta == _STEP * (16 - 11)  # the fifth read
    assert events[7].reused == 6
