"""Smoke tests for the Machine B tracer harness.

These run offline — they parse the real task files and dry-run the runner *without
invoking an agent or touching a live org*. They are the harness's own regression
guard and the acceptance gate for issue #570's smoke-test criterion.

Not collected by the default suite (testpaths = crm/tests); run on demand:

    pytest evals/skill
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.skill import analyze, isolation, runner
from evals.skill.taskspec import evaluate_expect, evaluate_feasibility, parse_task_file

TASKS_DIR = Path(__file__).parent / "tasks"


def _task_files() -> list[Path]:
    return sorted(TASKS_DIR.glob("*.md"))


def test_at_least_one_task_exists():
    assert _task_files(), "no task specs found under tasks/"


@pytest.mark.parametrize("task_file", _task_files(), ids=lambda p: p.stem)
def test_task_file_parses(task_file: Path):
    spec = parse_task_file(task_file)
    assert spec.id
    assert spec.prompt.strip()
    # Validate shape per task kind so a malformed task fails here, not at run time:
    # a feasibility task (#891) grades structured output against an evidenced answer key
    # (no org-state query); a predicate `do`-task asserts an `expect` over a fetched
    # payload (non-empty query); a diagnostic task (#572) has no `expect` and is scored
    # by the analysis pass.
    if spec.is_feasibility:
        assert spec.answer_key and spec.evidence and spec.query == [] and spec.expect == {}
    elif spec.is_diagnostic:
        assert spec.expect == {}
    else:
        assert spec.expect and spec.query
    # cleanup steps are well-formed
    for step in spec.cleanup:
        assert step.entity and step.id_field and step.filter


def test_tracer_task_shape():
    spec = parse_task_file(TASKS_DIR / "records-create-verify.md")
    assert spec.id == "records-create-verify"
    assert spec.domain == "records"
    assert spec.target == "cloud"
    assert "EvalTracer570" in spec.prompt
    assert spec.expect["count"] == 1


def test_evaluate_expect_count_pass():
    ok, _ = evaluate_expect([{"firstname": "Tracer"}], {"count": 1})
    assert ok


def test_evaluate_expect_count_fail():
    ok, reason = evaluate_expect([], {"count": 1})
    assert not ok and "count" in reason


def test_evaluate_expect_row_match():
    data = [{"firstname": "Tracer", "lastname": "EvalTracer570"}]
    ok, _ = evaluate_expect(data, {"row": {"firstname": "Tracer"}})
    assert ok


def test_evaluate_expect_row_no_match():
    data = [{"firstname": "Someone"}]
    ok, reason = evaluate_expect(data, {"row": {"firstname": "Tracer"}})
    assert not ok and "row" in reason


def test_evaluate_expect_row_suffix_rejects_unrelated_row():
    # The new suffix matcher must not pass an unrelated row — guards against the
    # "unknown matcher silently passes" trap (an empty expect returns True).
    data = [{"Name": "ag_somethingelse"}]
    ok, reason = evaluate_expect(data, {"row_suffix": {"Name": "maintenancepriority"}})
    assert not ok and "row_suffix" in reason


def test_evaluate_expect_row_suffix_matches_any_publisher_prefix():
    # A correctly-created option set passes regardless of the org's publisher prefix
    # (`ag_`, `new_`, …) — the fix for the hardcoded-`new_` false fail.
    assert evaluate_expect(
        [{"Name": "ag_maintenancepriority"}], {"row_suffix": {"Name": "maintenancepriority"}}
    )[0]
    assert evaluate_expect(
        [{"Name": "new_maintenancepriority"}], {"row_suffix": {"Name": "maintenancepriority"}}
    )[0]


def test_evaluate_expect_row_suffix_requires_all_fields_on_one_row():
    # Like `row`, every field must match on a single row (string compare); an absent
    # key never matches.
    data = [{"Name": "ag_maintenancepriority", "State": "Managed"}]
    assert evaluate_expect(data, {"row_suffix": {"Name": "priority", "State": "Managed"}})[0]
    assert not evaluate_expect(data, {"row_suffix": {"Name": "priority", "State": "Unmanaged"}})[0]


def test_evaluate_expect_row_suffix_absent_key_never_matches():
    # An absent key must never match — including the empty-suffix edge case, where a
    # naive `str(row.get(k)).endswith("")` would spuriously pass on a missing field.
    assert not evaluate_expect([{"Other": "x"}], {"row_suffix": {"Name": ""}})[0]
    assert not evaluate_expect([{"Other": "x"}], {"row_suffix": {"Name": "priority"}})[0]


def test_evaluate_expect_row_suffix_ignores_non_mapping_rows():
    # A non-dict row in the payload must be skipped, not crash the matcher; a later
    # well-formed row can still satisfy it.
    data = ["not a dict", {"Name": "ag_maintenancepriority"}]
    assert evaluate_expect(data, {"row_suffix": {"Name": "maintenancepriority"}})[0]
    assert not evaluate_expect(["not a dict"], {"row_suffix": {"Name": "x"}})[0]


def test_parse_rejects_bad_row_suffix_shape(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nid: x\ndomain: d\ntarget: either\n"
        "end_state:\n  query: [metadata, list-optionsets]\n  expect: {row_suffix: notamap}\n"
        "cleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="row_suffix must be a mapping"):
        parse_task_file(bad)


def test_parse_rejects_non_mapping_frontmatter(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("---\n- just\n- a\n- list\n---\nprompt\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        parse_task_file(bad)


def test_parse_rejects_malformed_cleanup(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nid: x\ndomain: d\ntarget: either\n"
        "end_state:\n  query: [query, odata, contacts]\n  expect: {count: 0}\n"
        "cleanup:\n  - entity: contacts\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cleanup step"):
        parse_task_file(bad)


def test_prompt_preserves_indentation(tmp_path):
    # The body is fed verbatim: surrounding delimiter newlines are dropped but any
    # authored leading indentation in the prompt is preserved.
    f = tmp_path / "indent.md"
    f.write_text(
        "---\nid: x\ndomain: d\ntarget: either\n"
        "end_state:\n  query: [query, odata, contacts]\n  expect: {count: 0}\n"
        "cleanup: []\n---\n\n    indented line\nplain line\n",
        encoding="utf-8",
    )
    spec = parse_task_file(f)
    assert spec.prompt == "    indented line\nplain line"


def test_parse_rejects_bad_expect_shape(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nid: x\ndomain: d\ntarget: either\n"
        'end_state:\n  query: [query, odata, contacts]\n  expect: {count: "1"}\n'
        "cleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="count must be an integer"):
        parse_task_file(bad)


def test_evaluate_expect_non_list_data():
    ok, reason = evaluate_expect({"not": "a list"}, {"count": 1})
    assert not ok and "list" in reason


def test_provision_and_verify_isolation():
    iso = isolation.provision_isolation()
    try:
        checks = isolation.verify_isolation(iso)
        # the skill landed in the fresh HOME, and no repo path leaks through
        assert (iso.skill_dir / "SKILL.md").is_file()
        assert "skill-installed" in checks
        assert "no-pythonpath" in checks
        assert iso.env["HOME"] == str(iso.home)
        assert isolation.repo_root() not in iso.work.resolve().parents
    finally:
        iso.cleanup()
    assert not iso.sandbox.exists()


def test_credentials_passthrough_copies_into_sandbox(monkeypatch, tmp_path):
    # Given a real Claude config dir holding a credentials file, provision_isolation
    # copies ONLY that file into the sandbox HOME so an isolated `claude -p` can
    # authenticate via the subscription — without dragging in CLAUDE.md / memory /
    # settings, which the eval deliberately withholds.
    cfg = tmp_path / "real-claude"
    cfg.mkdir()
    (cfg / ".credentials.json").write_text('{"fake": "token"}', encoding="utf-8")
    (cfg / "CLAUDE.md").write_text("global memory that must NOT leak", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))

    iso = isolation.provision_isolation()
    try:
        creds = iso.home / ".claude" / ".credentials.json"
        assert creds.is_file()
        assert creds.read_text(encoding="utf-8") == '{"fake": "token"}'
        # only the credentials file rode along — the real dir's CLAUDE.md stayed put
        assert not (iso.home / ".claude" / "CLAUDE.md").exists()
        # the agent env must not point back at the real config dir
        assert "CLAUDE_CONFIG_DIR" not in iso.env
        # isolation still holds (no repo, no inherited memory)
        isolation.verify_isolation(iso)
    finally:
        iso.cleanup()


def test_credentials_passthrough_survives_copy_failure(monkeypatch, tmp_path):
    # A failed credential copy (unreadable creds / unwritable HOME) must NOT abort
    # provisioning or leak the sandbox — passthrough is best-effort; the agent falls
    # back to ANTHROPIC_API_KEY. Simulate the failure by making the copy raise OSError.
    cfg = tmp_path / "real-claude"
    cfg.mkdir()
    (cfg / ".credentials.json").write_text('{"fake": "token"}', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))

    def boom(*_a, **_k):
        raise OSError("permission denied")

    monkeypatch.setattr("shutil.copy2", boom)

    iso = isolation.provision_isolation()  # must not raise
    try:
        assert not (iso.home / ".claude" / ".credentials.json").exists()
        isolation.verify_isolation(iso)  # provisioning is still valid
    finally:
        iso.cleanup()
    assert not iso.sandbox.exists()


def test_credentials_passthrough_noop_without_source(monkeypatch, tmp_path):
    # API-key-only setups have no credentials file: passthrough is a clean no-op and
    # isolation is unaffected.
    cfg = tmp_path / "empty-claude"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))

    iso = isolation.provision_isolation()
    try:
        assert not (iso.home / ".claude" / ".credentials.json").exists()
        isolation.verify_isolation(iso)
    finally:
        iso.cleanup()


def test_verify_isolation_rejects_claude_config_dir_leak(monkeypatch, tmp_path):
    # Regression guard for the rejected "point CLAUDE_CONFIG_DIR at the real ~/.claude"
    # approach: that env relocates *everything* (creds AND CLAUDE.md AND memory), so an
    # agent env carrying it would inherit global memory. verify_isolation must catch it.
    # Point provisioning at an empty config dir so it doesn't read the real creds;
    # the guard under test is exercised below by injecting a leak into the agent env.
    cfg = tmp_path / "empty-claude"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    iso = isolation.provision_isolation()
    try:
        leaky = tmp_path / "leaky-config"
        leaky.mkdir()
        (leaky / "CLAUDE.md").write_text("global memory", encoding="utf-8")
        iso.env["CLAUDE_CONFIG_DIR"] = str(leaky)  # simulate the scrub regressing
        with pytest.raises(isolation.IsolationError, match="CLAUDE_CONFIG_DIR"):
            isolation.verify_isolation(iso)
    finally:
        iso.cleanup()


def test_verify_isolation_detects_repo_leak():
    iso = isolation.provision_isolation()
    try:
        # Simulate a leak: a CLAUDE.md reachable from the agent's working dir.
        (iso.work / "CLAUDE.md").write_text("leaked project memory", encoding="utf-8")
        with pytest.raises(isolation.IsolationError, match="repo markers"):
            isolation.verify_isolation(iso)
    finally:
        iso.cleanup()


def test_dry_run_proves_isolation_without_agent():
    result = runner.run_task(TASKS_DIR / "records-create-verify.md", dry_run=True)
    assert result.dry_run is True
    assert result.passed is None  # not scored on a dry run
    assert result.isolation_checks.get("skill-installed")
    assert result.transcript == ""  # no agent was invoked


def test_dry_run_counterfactual_proves_skill_absent_isolation():
    # install_skill=False (the --counterfactual leg) provisions without the skill and
    # verification asserts it is absent — proven offline via the dry path (#588).
    result = runner.run_task(
        TASKS_DIR / "records-create-verify.md", dry_run=True, install_skill=False
    )
    assert result.dry_run is True
    assert result.isolation_checks.get("skill-absent")
    assert "skill-installed" not in result.isolation_checks


def test_dry_counterfactual_leg_needs_no_crm_binary(monkeypatch):
    # The skill-absent dry leg provisions/verifies an empty sandbox and touches no org,
    # so it must not hard-require a crm binary (#588 / Copilot) — but the skill-present
    # leg installs via crm and still does.
    monkeypatch.setattr("shutil.which", lambda _name: None)
    result = runner.run_task(
        TASKS_DIR / "records-create-verify.md", dry_run=True, install_skill=False
    )
    assert result.isolation_checks.get("skill-absent")
    with pytest.raises(runner.RunError, match="crm binary"):
        runner.run_task(TASKS_DIR / "records-create-verify.md", dry_run=True, install_skill=True)


def test_run_requires_agent_cmd_when_not_dry():
    # A real run needs an agent command; absent one, fail clearly before any live call.
    import os

    saved = os.environ.pop("CRM_EVAL_AGENT_CMD", None)
    try:
        with pytest.raises(runner.RunError, match="agent command"):
            runner.run_task(TASKS_DIR / "records-create-verify.md", agent_cmd=None)
    finally:
        if saved is not None:
            os.environ["CRM_EVAL_AGENT_CMD"] = saved


# --- diagnostic tasks + the optional --analyze pass (#572) ------------------------


def test_predicate_task_is_not_diagnostic():
    spec = parse_task_file(TASKS_DIR / "records-create-verify.md")
    assert not spec.is_diagnostic
    assert spec.expect


def test_diagnostic_task_shape():
    spec = parse_task_file(TASKS_DIR / "diagnostic-data-quality.md")
    assert spec.is_diagnostic
    assert spec.expect == {}
    assert spec.query  # still fetches org state to feed the analyzer
    assert "data quality" in spec.prompt.lower()


def test_trial_import_diagnosis_is_diagnostic():
    # Its only real signal is the qualitative --analyze diagnosis; the programmatic
    # predicate false-failed on any org never seeded with the `agtrial8` fixtures, so
    # the task is diagnostic (no `expect`) and the set runner SKIPs it rather than
    # scoring it. It still carries an end_state.query so the analyzer gets org state.
    spec = parse_task_file(TASKS_DIR / "trial-import-diagnosis.md")
    assert spec.is_diagnostic
    assert spec.expect == {}
    assert spec.query  # org state still fetched for the --analyze pass
    assert spec.prompt.strip()


def test_parse_allows_omitted_end_state(tmp_path):
    # A task with no end_state at all is diagnostic with no org-state query.
    f = tmp_path / "diag.md"
    f.write_text(
        "---\nid: x\ndomain: d\ntarget: either\ncleanup: []\n---\ninvestigate something\n",
        encoding="utf-8",
    )
    spec = parse_task_file(f)
    assert spec.is_diagnostic
    assert spec.query == [] and spec.expect == {}


def test_counterfactual_frontmatter_defaults_false_and_parses_true(tmp_path):
    head = (
        "---\nid: x\ndomain: d\ntarget: either\n"
        "end_state:\n  query: [query, odata, contacts]\n  expect: {count: 0}\n"
        "cleanup: []\n"
    )
    plain = tmp_path / "plain.md"
    plain.write_text(head + "---\nprompt\n", encoding="utf-8")
    assert parse_task_file(plain).counterfactual is False

    cf = tmp_path / "cf.md"
    cf.write_text(head + "counterfactual: true\n---\nprompt\n", encoding="utf-8")
    assert parse_task_file(cf).counterfactual is True


def test_parse_query_without_expect_is_diagnostic(tmp_path):
    f = tmp_path / "diag.md"
    f.write_text(
        "---\nid: x\ndomain: d\ntarget: either\n"
        "end_state:\n  query: [query, odata, contacts]\n"
        "cleanup: []\n---\ninvestigate\n",
        encoding="utf-8",
    )
    spec = parse_task_file(f)
    assert spec.is_diagnostic
    assert spec.query == ["query", "odata", "contacts"]


def test_diagnostic_run_refused_without_analyze():
    # A diagnostic task has no programmatic score; running it without --analyze fails
    # fast (before any sandbox/agent/live call), naming the fix.
    with pytest.raises(runner.RunError, match="diagnostic"):
        runner.run_task(TASKS_DIR / "diagnostic-data-quality.md", analyze_pass=False)


def test_diagnostic_dry_run_proves_isolation():
    # A dry run of a diagnostic task still works — it only proves isolation.
    result = runner.run_task(TASKS_DIR / "diagnostic-data-quality.md", dry_run=True)
    assert result.dry_run is True
    assert result.passed is None
    assert result.analysis is None


def test_resolve_analyze_cmd_precedence():
    import os

    assert analyze.resolve_analyze_cmd("my-claude --flag") == ["my-claude", "--flag"]
    saved = os.environ.get("CRM_EVAL_ANALYZE_CMD")
    try:
        os.environ["CRM_EVAL_ANALYZE_CMD"] = "env-claude -p"
        assert analyze.resolve_analyze_cmd(None) == ["env-claude", "-p"]
        os.environ.pop("CRM_EVAL_ANALYZE_CMD")
        assert analyze.resolve_analyze_cmd(None) == analyze.shlex.split(analyze.DEFAULT_ANALYZE_CMD)
    finally:
        if saved is not None:
            os.environ["CRM_EVAL_ANALYZE_CMD"] = saved
        else:
            os.environ.pop("CRM_EVAL_ANALYZE_CMD", None)


def test_build_analysis_prompt_bundles_inputs():
    prompt = analyze.build_analysis_prompt(
        task_prompt="create a contact",
        transcript="[agent exit 0]\ndid the thing",
        org_state=[{"fullname": "Tracer"}],
        verdict={"passed": True, "reason": "all expectations met"},
    )
    assert "create a contact" in prompt
    assert "did the thing" in prompt
    assert "Tracer" in prompt  # org state serialized in
    assert "all expectations met" in prompt


def test_build_analysis_prompt_handles_no_org_state():
    prompt = analyze.build_analysis_prompt(
        task_prompt="diagnose",
        transcript="t",
        org_state=None,
        verdict={"passed": None, "reason": "diagnostic"},
    )
    assert "none captured" in prompt


def test_run_analysis_feeds_prompt_on_stdin():
    # `cat` echoes stdin back, so the captured analysis is the prompt verbatim —
    # proving the prompt is routed on stdin.
    out = analyze.run_analysis("ANALYZE-ME", ["cat"])
    assert "ANALYZE-ME" in out


def test_run_analysis_missing_binary_raises():
    with pytest.raises(analyze.AnalyzeError, match="not found"):
        analyze.run_analysis("x", ["definitely-not-a-real-binary-xyz"])


def test_run_analysis_raises_on_nonzero_exit():
    # A failed analyzer must surface as an error, not a silently-successful read —
    # for a diagnostic task the analysis pass is the only score.
    with pytest.raises(analyze.AnalyzeError, match="exited 3"):
        analyze.run_analysis("x", ["sh", "-c", "exit 3"])


def test_parse_verdict():
    assert analyze.parse_verdict("some reasoning\nVERDICT: PASS") is True
    assert analyze.parse_verdict("VERDICT: fail") is False
    assert analyze.parse_verdict("no verdict at all here") is None
    # the final verdict line wins over an earlier mention
    assert analyze.parse_verdict("VERDICT: FAIL\nactually:\nVERDICT: PASS") is True
    # an inline mention in prose is not a verdict (whole-line anchor)
    assert analyze.parse_verdict("the VERDICT: PASS or FAIL line should be last") is None


# --- feasibility tasks: structured output + answer-key grading (#891) --------------


def test_feasibility_task_shape():
    spec = parse_task_file(TASKS_DIR / "feasibility-bulk-load-verify.md")
    assert spec.is_feasibility
    assert not spec.is_diagnostic
    # graded against an evidenced answer key, not org state
    assert spec.answer_key.get("cli_achievable") is True
    assert "data import" in spec.answer_key["required_commands"]
    assert spec.evidence  # answer-key provenance captured at authoring time
    assert spec.query == [] and spec.expect == {}


def test_feasibility_kind_defaults_to_do():
    # An existing task without a `kind` field stays a do-task (backward compatible).
    spec = parse_task_file(TASKS_DIR / "records-create-verify.md")
    assert spec.kind == "do"
    assert not spec.is_feasibility


def test_evaluate_feasibility_scalar_exact_match():
    # cli_achievable is an exact match — the binary hinges on it.
    data = {"cli_achievable": True, "required_commands": ["data import", "query odata"]}
    ok, _ = evaluate_feasibility(data, {"cli_achievable": True})
    assert ok
    ok, reason = evaluate_feasibility({"cli_achievable": False}, {"cli_achievable": True})
    assert not ok and "cli_achievable" in reason


def test_evaluate_feasibility_bool_is_type_strict():
    # Python's `bool ⊆ int` makes `True == 1`; the pivot field must not accept `1` for
    # `true`, or the "exact match" binary is loose exactly where it matters.
    ok, reason = evaluate_feasibility({"cli_achievable": 1}, {"cli_achievable": True})
    assert not ok and "cli_achievable" in reason


def test_evaluate_feasibility_list_recall_all_present():
    data = {"required_commands": ["crm data import accounts x.jsonl", "crm query odata accounts"]}
    ok, _ = evaluate_feasibility(data, {"required_commands": ["data import", "query odata"]})
    assert ok  # recall: each expected item found as a substring of an emitted item


def test_evaluate_feasibility_list_recall_missing_one_fails():
    # Missing a single required item fails the trial — the ADR-0028 "keep the binary clean" rule.
    data = {"required_commands": ["crm data import accounts x.jsonl"]}
    ok, reason = evaluate_feasibility(data, {"required_commands": ["data import", "query odata"]})
    assert not ok and "query odata" in reason


def test_evaluate_feasibility_rejects_non_object():
    # A schema-invalid answer (not a JSON object) fails, never silently passes.
    ok, reason = evaluate_feasibility(["not", "an", "object"], {"cli_achievable": True})
    assert not ok and "object" in reason
    ok, reason = evaluate_feasibility(None, {"cli_achievable": True})
    assert not ok and "object" in reason


def test_evaluate_feasibility_missing_field_fails():
    # A graded field absent from the output is a schema-invalidity, scored as a fail.
    ok, reason = evaluate_feasibility({"required_commands": []}, {"cli_achievable": True})
    assert not ok and "cli_achievable" in reason


def test_evaluate_feasibility_list_field_wrong_type_fails():
    ok, reason = evaluate_feasibility(
        {"required_commands": "data import"}, {"required_commands": ["data import"]}
    )
    assert not ok and "list" in reason


def test_read_feasibility_answer_roundtrips(tmp_path):
    (tmp_path / runner.FEASIBILITY_ANSWER_FILE).write_text(
        '{"cli_achievable": true, "required_commands": ["data import"]}', encoding="utf-8"
    )
    data = runner._read_feasibility_answer(tmp_path)
    assert data == {"cli_achievable": True, "required_commands": ["data import"]}


def test_read_feasibility_answer_missing_or_invalid_is_none(tmp_path):
    # No answer file, or a non-JSON one, yields None — graded as a schema-invalid fail.
    assert runner._read_feasibility_answer(tmp_path) is None
    (tmp_path / runner.FEASIBILITY_ANSWER_FILE).write_text("not json", encoding="utf-8")
    assert runner._read_feasibility_answer(tmp_path) is None


def test_feasibility_parse_rejects_end_state(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nid: x\ndomain: d\ntarget: either\nkind: feasibility\n"
        "answer_key: {cli_achievable: true}\nevidence: [somewhere]\n"
        "end_state:\n  query: [query, odata, contacts]\n  expect: {count: 0}\n"
        "cleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="end_state"):
        parse_task_file(bad)


def test_feasibility_parse_requires_answer_key(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nid: x\ndomain: d\ntarget: either\nkind: feasibility\n"
        "evidence: [somewhere]\ncleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="answer_key"):
        parse_task_file(bad)


def test_feasibility_parse_requires_evidence(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nid: x\ndomain: d\ntarget: either\nkind: feasibility\n"
        "answer_key: {cli_achievable: true}\ncleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="evidence"):
        parse_task_file(bad)


def test_parse_rejects_unknown_kind(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nid: x\ndomain: d\ntarget: either\nkind: bogus\ncleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="kind"):
        parse_task_file(bad)


def test_feasibility_dry_run_proves_isolation():
    # A dry run of a feasibility task proves isolation without an agent, like any task.
    result = runner.run_task(TASKS_DIR / "feasibility-bulk-load-verify.md", dry_run=True)
    assert result.dry_run is True
    assert result.passed is None
    assert result.isolation_checks.get("skill-installed")


def test_build_analysis_prompt_requests_verdict_line():
    prompt = analyze.build_analysis_prompt(
        task_prompt="t",
        transcript="x",
        org_state=None,
        verdict={"passed": None, "reason": "d"},
    )
    assert "VERDICT: PASS" in prompt and "VERDICT: FAIL" in prompt


# --- Corpus curation metadata: tier + source (#895) ---------------------------
# tier (1/2/3) and source ({type, url}) are authoring/curation metadata — the runner
# never reads them; they record demand-weighting and provenance so the corpus doubles
# as a coverage map (ADR 0028). Optional per task, but validated when present so a
# malformed tag fails the smoke test, not silently sits in the corpus.


def test_parse_accepts_tier_and_source(tmp_path):
    f = tmp_path / "t.md"
    f.write_text(
        "---\nid: x\ndomain: bulk\ntarget: either\ntier: 2\n"
        "source: {type: so, url: 'https://stackoverflow.com/questions/1'}\n"
        "end_state:\n  query: [query, odata, accounts]\n  expect: {count: 0}\n"
        "cleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    spec = parse_task_file(f)
    assert spec.tier == 2
    assert spec.source == {"type": "so", "url": "https://stackoverflow.com/questions/1"}


def test_tier_and_source_optional(tmp_path):
    f = tmp_path / "t.md"
    f.write_text(
        "---\nid: x\ndomain: bulk\ntarget: either\n"
        "end_state:\n  query: [query, odata, accounts]\n  expect: {count: 0}\n"
        "cleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    spec = parse_task_file(f)
    assert spec.tier is None
    assert spec.source == {}


def test_parse_rejects_bad_tier(tmp_path):
    f = tmp_path / "t.md"
    f.write_text(
        "---\nid: x\ndomain: bulk\ntarget: either\ntier: 4\n"
        "end_state:\n  query: [query, odata, accounts]\n  expect: {count: 0}\n"
        "cleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="tier"):
        parse_task_file(f)


def test_parse_rejects_bool_tier(tmp_path):
    # bool ⊆ int, so `tier: true` would otherwise pass as tier 1 (True in (1,2,3)).
    f = tmp_path / "t.md"
    f.write_text(
        "---\nid: x\ndomain: bulk\ntarget: either\ntier: true\n"
        "end_state:\n  query: [query, odata, accounts]\n  expect: {count: 0}\n"
        "cleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="tier"):
        parse_task_file(f)


def test_parse_rejects_bad_source_type(tmp_path):
    f = tmp_path / "t.md"
    f.write_text(
        "---\nid: x\ndomain: bulk\ntarget: either\n"
        "source: {type: blog, url: 'https://example.com'}\n"
        "end_state:\n  query: [query, odata, accounts]\n  expect: {count: 0}\n"
        "cleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="source.type"):
        parse_task_file(f)


def test_parse_rejects_source_without_url(tmp_path):
    f = tmp_path / "t.md"
    f.write_text(
        "---\nid: x\ndomain: bulk\ntarget: either\nsource: {type: firsthand}\n"
        "end_state:\n  query: [query, odata, accounts]\n  expect: {count: 0}\n"
        "cleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="url"):
        parse_task_file(f)


def test_source_url_may_be_null(tmp_path):
    # A firsthand source has no external URL — the key must be present, value null.
    f = tmp_path / "t.md"
    f.write_text(
        "---\nid: x\ndomain: bulk\ntarget: either\n"
        "source: {type: firsthand, url: null}\n"
        "end_state:\n  query: [query, odata, accounts]\n  expect: {count: 0}\n"
        "cleanup: []\n---\nprompt\n",
        encoding="utf-8",
    )
    spec = parse_task_file(f)
    assert spec.source == {"type": "firsthand", "url": None}
