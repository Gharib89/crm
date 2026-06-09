# `crm metadata clone-entity --with-charts` Implementation Plan (#153)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `--with-charts` layer to `crm metadata clone-entity` that clones the source entity's public system charts (`savedqueryvisualization`) onto the clone, retargeted to the clone entity. Fold it into `--with-all`. Report a `counts.charts` tally. Pure Web API, no solution import.

**Architecture:** A chart is *read → retarget the stored XML token → recreate against the new entity*, exactly like a form. So the new `crm/core/charts.py` **mirrors `forms.py`**, not `views.py` (views.py rebuilds XML from a parsed projection; charts copy the stored XML verbatim with a token swap). The orchestrator `clone.clone_entity()` grows a `with_charts: bool = False` parameter following the existing `with_forms` loop shape; the command grows a `--with-charts` flag folded into `--with-all`.

**Chart XML contract (confirmed against `docs/guides/crmworx-walkthrough.md` §8 and the issue brief):**
- A `savedqueryvisualization` binds to its host entity via **`primaryentitytypecode`** — the **logical-name string**, *not* an ObjectTypeCode integer. So the "OTC unreadable at apply time" footgun that produces `views_note` does **not** apply to charts. No deferred-second-run note is needed.
- `datadescription` embeds the chart's aggregate FetchXML (`<datadefinition><fetchcollection><fetch><entity name="<src>">…`). It references the source entity name → **must be retargeted** with a whole-token, word-boundary swap (the `\b<src>\b` approach `retarget_formxml` uses, so attribute names like `new_projectid` survive intact).
- `presentationdescription` is the rendering blob (series/axes keyed by data *alias*, e.g. `aggregate_column`) → it carries **no entity reference**. A defensive word-boundary swap is applied anyway; it is expected to be a no-op. Documented in the module docstring.

**Why mirror `forms.py` and not `views.py`:** identical read→retarget-token→recreate shape; the retarget logic is isolated in a pure `retarget_chartxml()` (like `retarget_formxml`) so it is unit-testable without a backend.

**Scope (from #153, verbatim):**
- Public **system** charts only (`savedqueryvisualization`). Personal `userqueryvisualization` is out of scope.
- No standalone `crm chart` command group — this issue only wires charts into `clone-entity`.
- Cross-entity charts whose fetch references a *different* entity than the source: clone as-is; only the source entity's own token is retargeted (the word-boundary swap naturally leaves other entity names alone).
- Ribbon, N:N, parent-side relationships: already documented as not cloned; unchanged.

**Tech Stack:** Python 3, Click, Dataverse Web API (OData v4), `requests_mock` + `pytest`, pyright **strict** on `crm/core/charts.py`.

---

## File Structure

| File | Responsibility | pyright |
|---|---|---|
| `crm/core/charts.py` (**new**) | `read_entity_charts()`, `retarget_chartxml()` (pure), `clone_chart_to_entity()`. Mirrors `forms.py`. | strict |
| `crm/core/clone.py` (**modify**) | Add `with_charts: bool=False` param + the charts loop; init `counts.charts`. | strict |
| `crm/commands/metadata.py` (**modify**) | Add `--with-charts` flag; fold into `--with-all`; thread to `clone_entity`. | basic |
| `crm/tests/test_charts.py` (**new**) | Unit tests for `charts.py` (pure retarget + `requests_mock` read/clone). Mirror `test_forms.py`. | — |
| `crm/tests/test_clone.py` (**modify**) | Add a `TestCloneEntityCharts` class + extend the command tests for `--with-charts` / `--with-all`. | — |
| `README.md`, `docs/how-to/metadata.md`, `crm/skills/SKILL.md` (**modify**) | Docs shipped in the same change. | — |

**Reused as-is (do not modify):** `metadata.maybe_publish`, `solution.publish_all`, `D365Backend.get/post`, `as_dict`.

### Key signatures the executor will call (verified against HEAD)

```python
# crm/core/forms.py — THE MIRROR TEMPLATE
def retarget_formxml(formxml, *, src_entity, dst_entity) -> str   # re.sub(rf"\b{escape(src)}\b", dst, xml)
def read_entity_forms(backend, entity_logical_name, *, form_types=(2,)) -> list[dict]
def clone_form_to_entity(backend, form, new_entity, *, publish=False, solution=None) -> dict

# crm/core/metadata.py
def maybe_publish(backend, info, publish) -> dict   # no-op if not publish or info["_dry_run"]

# crm/utils/d365_backend.py
class D365Backend: dry_run: bool; def get/post(path, params=, json_body=, extra_headers=) -> Any
def as_dict(value) -> dict[str, Any]
# backend.post on 204 returns {"_entity_id_url": <OData-EntityId url>}; in dry_run returns {"_dry_run": True}
```

---

## Task 1: `crm/core/charts.py` — read + pure retarget + clone-one

Mirror `forms.py` field-for-field. The savedqueryvisualization set is `savedqueryvisualizations`; the id is `savedqueryvisualizationid`; entity binding is `primaryentitytypecode` (logical-name string).

**Files:** Create `crm/core/charts.py`; create `crm/tests/test_charts.py`.

- [ ] **Step 1: Write failing tests** (`crm/tests/test_charts.py`), mirroring `test_forms.py`:
  - `TestReadEntityCharts`:
    - reads charts → projection has `savedqueryvisualizationid, name, primaryentitytypecode, datadescription, presentationdescription, description, isdefault`.
    - filters by `primaryentitytypecode eq '<entity>'` in the request URL.
    - escapes a single-quote in the entity name (`it's_table` → `it%27%27s_table`).
  - `TestRetargetChartxml`:
    - rewrites whole-word entity refs: `<entity name="new_project">` → `<entity name="cwx_ticketclone">`.
    - protects attribute names: `name="new_projectid"` and `name="new_project_code"` left intact when swapping `new_project`→`cwx_ticketclone`.
    - no-op when entity absent (`""`/`None` returns input unchanged).
  - `TestCloneChartToEntity`:
    - POSTs a retargeted chart: body `primaryentitytypecode == "cwx_ticketclone"`, `datadescription` references the clone, `name` preserved; parses `savedqueryvisualizationid` from the `OData-EntityId` header; `created is True`.
    - adds `MSCRM.SolutionUniqueName` header when `solution=` given.
    - presentationdescription retarget is a no-op when it has no entity ref (assert it round-trips unchanged through `clone_chart_to_entity`).

- [ ] **Step 2: Implement `charts.py`** to pass. Required surface:

```python
"""Read and clone savedqueryvisualization (system chart) records.

Mirrors forms.py (read -> retarget stored XML token -> recreate against a new
primaryentitytypecode). A chart's datadescription embeds an aggregate FetchXML
that names the host entity; presentationdescription is alias-keyed rendering XML
with no entity ref (the defensive swap there is a documented no-op). Retarget
logic is isolated so it is testable without the orchestrator.
"""
CHART_TYPE_PUBLIC_SELECT = "savedqueryvisualizationid,name,primaryentitytypecode,datadescription,presentationdescription,description,isdefault"

def retarget_chartxml(xml: str, *, src_entity: str, dst_entity: str) -> str:
    # identical body to retarget_formxml: word-boundary whole-token swap, no-op on falsy.

def read_entity_charts(backend, entity_logical_name) -> list[dict[str, Any]]:
    # GET savedqueryvisualizations $filter=primaryentitytypecode eq '<lit>'
    # project to the keys above; datadescription/presentationdescription default to "".

def clone_chart_to_entity(backend, chart, new_entity, *, publish=False, solution=None) -> dict[str, Any]:
    # src = chart["primaryentitytypecode"]; raise D365Error if missing.
    # body = {name, primaryentitytypecode: new_entity,
    #         datadescription: retarget(...), presentationdescription: retarget(...)}
    # include description only if not None.
    # MSCRM.SolutionUniqueName header when solution.
    # honor _dry_run passthrough; parse savedqueryvisualizationid from _entity_id_url;
    #   on miss set chart_lookup_error; maybe_publish(backend, out, publish).
```

- [ ] **Step 3:** `pyright --pythonpath .venv\Scripts\python.exe crm/core/charts.py` clean; `pytest crm/tests/test_charts.py` green.

**Verify:** `pytest crm/tests/test_charts.py -q` all pass; pyright strict clean on `charts.py`.

---

## Task 2: Wire `with_charts` into the `clone_entity` orchestrator

Mirror the `with_forms` block in `crm/core/clone.py`.

**Files:** Modify `crm/core/clone.py`; modify `crm/tests/test_clone.py`.

- [ ] **Step 1: Write failing tests** — add `TestCloneEntityCharts` to `test_clone.py`, mirroring `TestCloneEntityForms`:
  - `--with-charts` clones each chart, tallies `counts.charts`, targets the clone logical name + threads `solution`, and publishes once after the loop (when `publish` and not dry-run).
  - without `--with-charts`, `read_entity_charts` is **not** called and `counts.charts == 0`.
  - a failed skeleton (`apply ok=False`) skips charts entirely.
  - Also update `_applied`/skeleton assertions: `counts` now has a `charts` key (skeleton test asserts `counts.charts == 0`).

- [ ] **Step 2: Implement.** In `clone.py`:
  - `from crm.core.charts import clone_chart_to_entity, read_entity_charts`.
  - add `with_charts: bool = False` to the signature (after `with_workflows`).
  - add `"charts": 0` to the initial `counts` dict.
  - after the `with_forms` block (publish style identical), add:
    ```python
    if with_charts:
        charts_done = 0
        for chart in read_entity_charts(backend, source):
            clone_chart_to_entity(backend, chart, clone_logical, solution=solution)
            charts_done += 1
        out["counts"]["charts"] = charts_done
        if charts_done and publish and (not backend or not backend.dry_run):
            publish_all(backend)
    ```
  - update the module docstring's opening line to mention charts alongside forms/workflows.

- [ ] **Step 3:** `pytest crm/tests/test_clone.py -q` green; pyright strict clean on `clone.py`.

**Verify:** orchestrator tests pass; existing forms/workflows/skeleton tests still pass (the new `charts` count key does not break them).

---

## Task 3: `--with-charts` command flag

**Files:** Modify `crm/commands/metadata.py`; extend the command tests in `crm/tests/test_clone.py`.

- [ ] **Step 1: Write failing command tests** in `TestCloneCommand`:
  - `--with-charts` threads `with_charts=True` to `clone_entity`.
  - `--with-all` sets `with_charts` True alongside forms/views/workflows (extend the existing `test_with_all_overrides_individual_flags`).

- [ ] **Step 2: Implement** in `metadata_clone_entity`:
  - add `@click.option("--with-charts", is_flag=True, default=False, help="Clone the source's public system charts onto the clone.")`.
  - add `with_charts` to the function params.
  - in the `if with_all:` line, set `with_charts` True too: `with_forms = with_views = with_workflows = with_charts = True`.
  - update the `--with-all` flag help to mention charts.
  - pass `with_charts=with_charts` to `clone_mod.clone_entity(...)`.

- [ ] **Step 3:** `pytest crm/tests/test_clone.py::TestCloneCommand -q` green.

**Verify:** command tests pass; `crm metadata clone-entity --help` lists `--with-charts`.

---

## Task 4: Docs (shipped in the same change)

**Files:** `README.md`, `docs/how-to/metadata.md`, `crm/skills/SKILL.md`.

- [ ] **README.md** (~line 346): add `--with-charts` to the clone-entity flag list and the `--with-all` enumeration.
- [ ] **docs/how-to/metadata.md** (Clone an entity section, ~199–239):
  - mention `--with-charts` clones public system charts (retargeted via `primaryentitytypecode` + datadescription FetchXML token swap).
  - **remove** the "Charts — deferred to a follow-up" bullet from the *Not cloned* list (it is now cloned).
- [ ] **crm/skills/SKILL.md** (~338–368):
  - add `--with-charts` to the opt-in flags example + the "Key flags" sentence; update `--with-all` to say it enables all four layers.
  - change the "**Charts** — deferred follow-up." bullet under *Not cloned* into a one-line note that `--with-charts` clones public system charts (or remove the bullet from *Not cloned* and document the flag above).
- [ ] `cli.md` is auto-generated by mkdocs-click — do **not** hand-edit; the `--with-charts` help string is picked up from the Click option.

- [ ] **Verify:** `mkdocs build --strict` passes (no stale refs / broken links).

---

## Task 5: Full-suite gate + Conventional Commit

- [ ] `pytest -q` (whole suite, minus live-E2E) green.
- [ ] `pyright --pythonpath .venv\Scripts\python.exe` clean on `crm/core/charts.py` and `crm/core/clone.py`.
- [ ] `mkdocs build --strict` passes.
- [ ] Commit subject is a single `feat:` line so PSR cuts the release + CHANGELOG (do **not** hand-edit CHANGELOG). PR body includes `Closes #153`.

**Acceptance criteria (from #153) — all must hold:**
- [ ] `clone-entity SRC NEW --with-charts` creates a `savedqueryvisualization` per source chart, `primaryentitytypecode` = clone, `datadescription` retargeted.
- [ ] `--with-all` enables charts in addition to forms/views/workflows.
- [ ] Result envelope includes `counts.charts`, count correct.
- [ ] Dry-run returns a preview, creates nothing (forms-style `_dry_run` passthrough).
- [ ] `--solution` routes the create through `MSCRM.SolutionUniqueName`.
- [ ] Retarget unit test proves a whole-token swap leaving entity-prefixed attribute names intact.
- [ ] `read_entity_charts` / `clone_chart_to_entity` unit tests mirror the forms test patterns (mocked backend).
- [ ] Docs ship in the same change; `mkdocs build --strict` passes.
- [ ] `feat:` commit subject.
