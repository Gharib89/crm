# Safe customization-XML editors — research design

**Date:** 2026-06-21
**Status:** Draft (pending user review before agents spin)
**Type:** Research effort (no implementation in this effort)

## Goal

Determine, exhaustively and with evidence, **which Dynamics 365 customization
components stored as XML the `crm` CLI can safely add targeted editors for**,
and **how to edit that XML safely** (mutation mechanics + validation). Produce a
per-candidate feasibility dossier, a recommended editing-mechanics design, and a
review-gated build backlog. Implementation happens later, issue by issue.

This narrows and *re-opens* the XML-surgery question that the June 2026
developer-scenarios research
(`docs/research/2026-06-crm-dev-scenarios-report.md`) closed too narrowly — that
report treated the XML-surgery family as **only** SCN-021 (BPF authoring) and
recommended no reversal. The truth from its own matrix is broader: the CLI
**already** does targeted XML editing (FormXml fields, RibbonDiffXml buttons,
SiteMap, layoutxml/fetchxml, chart XML), and a cluster of `cli-gap`s (forms
deep-edit SCN-010, JS handler wiring SCN-012, charts/dashboards SCN-016,
component role-gating SCN-049) are blocked *only* because they need
customization-XML edits an agent would corrupt unguided. Those are the prize.

## Background — what's already true

- **XML editing is not banned.** The tool ships targeted editors today:
  `form` field add/remove/move (FormXml tree edits + regex GUID-regen on clone),
  `ribbon add-button`/`remove` (RibbonDiffXml inside the solution zip),
  `view create` (builds layoutxml + fetchxml), `app build-sitemap`/`set-sitemap`
  (SiteMapXml), `chart create` (datadescription/presentationdescription).
- **Validation today = well-formedness only. Zero XSD.** `ET.fromstring`
  catches `ParseError`; nothing validates against a schema.
- **What's refused** (narrow): ribbon clone (no write path), BPF/workflow XAML
  clone (StageId regen), form-clone non-target-GUID bail, BPF `clientdata`
  authoring (#37), solution clone via GUID-regen (#166).
- **Prior decision (softened 2026-06-10):** the XML-surgery family is
  *challengeable with evidence* — acceptable if built with a validation layer
  and the relevant XML schema. See `CONTEXT.md` glossary and
  `project-out-of-scope-xml-surgery-family` memory.

## Doc-grounded facts (MS Learn, fetched 2026-06-21)

1. **MS publishes the full customization XSD set**, downloadable
   ([Schemas.zip](https://download.microsoft.com/download/B/9/7/B97655A4-4E46-4E51-BA0A-C669106D563F/Schemas.zip))
   and installed on-prem at
   `[Install]\Program Files\Microsoft Dynamics CRM\Server\ApplicationFiles`:
   `CustomizationsSolution.xsd`, `FormXml.xsd`, `RibbonCore.xsd`,
   `RibbonTypes.xsd`, `RibbonWSS.xsd`, `SiteMap.xsd`, `SiteMapType.xsd`,
   `fetch.xsd`, `VisualizationDataDescription.xsd`, `isv.config.xsd`.
   **This XSD set is the ground-truth boundary of "documented customization
   XML" — Phase 0's spine.**
2. **MS draws its own supported/unsupported edit line**
   ([When to edit the customizations file](https://learn.microsoft.com/power-apps/developer/model-driven-apps/when-edit-customization-file)):
   editing the customizations.xml file to *define* Entities, Attributes,
   Relationships, Option Sets, Web Resources, Processes, Plugin Assemblies, SDK
   steps, Service Endpoints, Reports, Connection Roles, Templates, Security
   Roles, Field Security Profiles is **unsupported** (use the metadata APIs).
   So **"has a published XSD" ≠ "editing it is a supported operation"** — these
   are two independent axes the research must score separately.
3. **No BPF / process-XAML / business-rule schema exists in the published
   set** — confirms Track 2 is genuinely undocumented serialization. Business
   rules are explicitly designer-only (the Process Trigger entity is the only
   API lever for *initiation*, not authoring).

## Decisions (from the 2026-06-21 grilling)

| # | Decision | Choice |
|---|---|---|
| D1 | Capability shape | **Targeted, grammar-aware structural editors**, one per component type (the shape `form`/`ribbon` already use). **Not** a generic XPath/path primitive (that *is* the silent-corruption surface) and **not** a generic escape hatch. |
| D2 | Safety bar (mandatory gate per editor) | **T0 well-formed + T2 semantic pre-flight + T3 read-back verify.** T2 = grammar-aware (validate referenced columns/web-resources/etc. exist; protect `classid` and fixed platform references; regen dependent internal GUIDs consistently). T3 = read back after write and confirm it landed. |
| D3 | Role of the XML schema | **Build-to-grammar + ship-as-reference**, *not* a runtime XSD gate. Editors are built from the documented XSD and the schema travels with the feature for audit. Rationale: the catastrophic corruptions (`classid` mutation, #275 internal-GUID collision) are *both* well-formed AND XSD-valid — T1 would miss them — and a runtime XSD validator drags lxml/xmlschema into a stdlib-only, PyInstaller-bundled tool. T1 is optional, where a clean public XSD exists. |
| D4 | Two tracks by XML provenance | **Track 1 — documented customization XML** (FormXml, RibbonDiffXml, chart/visualization XML, dashboard FormXml, SiteMap; ≈ the published-XSD set): build candidates under the D2 bar. **Track 2 — undocumented serialization** (BPF `clientdata`, process/business-rule XAML): feasibility-only, not assumed buildable. |
| D5 | Track 2 flip-to-buildable bar | An item flips **only if ALL hold**: (a) a documented authorable format **or** a supported server-side authoring action exists — hunt for this first (the SCN-035 lesson: managed-lifecycle *looked* like rejected surgery but was supported server actions); (b) a T2/T3 validation design is sketchable; (c) real recurring agent-demand evidence. Missing any → stays rejected, *stating why per-item*. **Business rules and BPF judged independently** (BR has the smaller surface; the report wrongly lumped them). |
| D6 | Editing mechanics | **Research deliverable, framed around minimal-diff round-trip fidelity.** Export → edit one node → reimport must churn *only* that node (noisy diffs can't be reviewed; reserialized XML can silently change behavior). Recommend a mutation strategy (stdlib ElementTree vs targeted regex vs lxml) **with the PyInstaller bundle-size cost measured**. Lean: stay stdlib+regex unless ≥3 editors genuinely need lxml-only XPath/fidelity. |
| D7 | Supportability axis (new, from doc fact 2) | Each candidate scored on **two independent axes**: *grammar documented* (published XSD?) and *edit operation supported* (does MS sanction this write path — entity-field PATCH vs solution roundtrip vs designer-only?). A documented-but-unsupported edit is a yellow flag, not an automatic reject, but must be called out. |
| D8 | Build-vs-adopt / dual-target | Unchanged: build only where no scriptable dual-target (on-prem v9.x AND online) tool exists. Holds and mostly *favors building* here — the external tools (XrmToolBox, maker portal) are GUI designers, not scriptable. |
| D9 | Coverage | **Exhaustive by construction.** Phase 0 enumerates *every* customization component with an XML/serialized representation (grounded in the published XSD set + a live solution export + the SolutionComponent componenttype enumeration), classifies each Track 1 / Track 2 / out-of-domain, *then* dossiers. No reliance on a hand-picked candidate list. |
| D10 | Deliverable | Per-candidate dossier (template below) + mechanics design + research report + **review-gated build backlog** (nothing filed to GitHub until the user approves). Artifacts in `docs/research/`; this spec in `docs/superpowers/specs/`. |

## Glossary

Two terms added to `CONTEXT.md` this session: **targeted structural editor**
(the sanctioned shape) and **internal-serialization surgery** (the rejected
kind). See `CONTEXT.md` → Language → Customization XML.

## Research plan

### Phase 0 — Exhaustive enumeration (coverage spine)

Build the complete universe of customization components with an XML/serialized
form, from three independent sources so coverage is provable:

- the published XSD set (doc fact 1) — the documented-grammar universe;
- a **live solution export** from `agent-on-prem` and `agent-cloud` — every XML
  member/field actually present (FormXml, RibbonDiffXml, SiteMap, chart XML,
  dashboard FormXml, layoutxml, workflow XAML, …);
- the SolutionComponent componenttype enumeration (the CLI's own
  `solution add-component` type map + `$metadata`).

Merge → a classified inventory: each component tagged Track 1 / Track 2 /
out-of-domain, with its XML container (entity.field or zip member), published
XSD (if any), and the MS supported/unsupported note.

### Phase 1 — Track-1 candidate dossiers (parallel, one agent per component family)

Each documented-XML candidate gets the full dossier (template below). Families:
forms (tabs/sections/columns/subgrids/quick-view/quick-create/handler-wiring/
display-conditions/header-footer), dashboards, charts (data + presentation),
ribbon (enable/display rules, tooltips, app vs entity ribbon), sitemap (modern
app sitemap vs classic), views layout (edit-existing). Each agent grounds in MS
Learn + the live XSD + a live read-back from both orgs.

### Phase 2 — Track-2 feasibility (BPF, business rules — independently)

Per item: run the D5 supported-path hunt first (documented format? supported
server action? Process Trigger-style lever?), then the three-test verdict, then
"what would flip it." Output: flip / stays-rejected + reason.

### Phase 3 — Editing mechanics (cross-cutting)

Evaluate stdlib ElementTree vs targeted regex vs lxml against minimal-diff
round-trip fidelity on real exported XML from both orgs; measure the PyInstaller
bundle-size delta for lxml. Output: a recommended mutation strategy + where (if
anywhere) lxml earns its dependency.

### Phase 4 — Synthesis

Research report (mirrors the June report's shape) + prioritized build backlog →
**user review before any issue is filed**.

## Per-candidate dossier template

- **Component + XML container** — entity.field or zip member (e.g.
  `systemforms.formxml`).
- **Grammar documented?** — published XSD + MS Learn URL (axis 1, D7).
- **Edit operation supported?** — MS-sanctioned write path: entity-field PATCH /
  solution roundtrip / designer-only (axis 2, D7).
- **Edit ops to expose** — concrete verb sketches (`form add-tab`, …).
- **T2 design** — refs to validate; GUIDs/`classid`/fixed refs to protect vs
  regenerate.
- **T3 design** — read-back assertion proving the edit landed un-corrupted.
- **Round-trip fidelity** — does export/import churn this container? minimal-diff
  approach.
- **Dual-target** — works on-prem v9.x AND online? divergence?
- **Demand evidence** — frequency from the 59-scenario catalogue + community.
- **Verdict** — build-now / build-later / needs-more-research + priority.

## Agent topology (proposed)

A `Workflow` fan-out: Phase 0 inline scout (enumeration), then Phase 1 dossiers
in parallel (one agent per component family) pipelined into per-candidate
doc-grounded verification, Phase 2 + Phase 3 in parallel, Phase 4 synthesis.
Sequential `Explore`/research agents are the fallback if a workflow is overkill.

## Success criteria

- Phase 0 inventory covers the full published-XSD set **and** every XML member
  found in a live export from both orgs (coverage provable, D9).
- Every inventory item classified Track 1 / Track 2 / out-of-domain.
- Every Track-1 candidate has a complete dossier; both axes (D7) scored.
- Both Track-2 items judged independently with a flip/stays-rejected verdict.
- Mechanics design names a recommended mutation strategy with measured lxml cost.
- Report complete; backlog drafted → user review → (then) filed.

## Out of scope (this effort)

- Implementing any editor or CLI change.
- Building a generic XML/XPath primitive (rejected, D1).
- Reversing the rejection family without clearing the D5 bar per-item.
- Filing GitHub issues before user review.
