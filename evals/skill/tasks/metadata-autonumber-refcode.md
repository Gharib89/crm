---
id: metadata-autonumber-refcode
domain: metadata-writes
tier: 2
source:
  type: forum
  url: https://community.dynamics.com/forums/thread/details/?threadid=a26cd676-1e91-ef11-ac21-6045bda6da2f
# Metadata demand cluster (#899), pilot harvest #885 row 6 — "Autonumbering for Custom Entity"
# (a server-generated, formatted reference number without plug-in code), a perennial ask
# (siblings: Work Order autonumber, "Auto Number field not working after import"). The
# platform-idiomatic answer is a String column carrying an AutoNumberFormat, which the CLI
# ships as `metadata add-attribute --kind string --auto-number-format '<pattern>'`
# (reference/metadata-writes.md). This models the load-bearing half a static predicate can
# verify: a marker-named auto-number column exists on the target table.
# Host = the stock `contact` table, NOT a fresh custom entity, deliberately: a created custom
# entity's logical name carries the org's default publisher prefix (varies per org — `new_` on
# a stock org), so a static `metadata attributes <entity>` query could not name it. `contact`
# has a fixed logical name, so the query argument is stable; the column's OWN logical name is
# still publisher-prefixed, which the suffix matcher (`row_suffix`) absorbs exactly as
# trial-global-optionset does for a global option set's name.
# Verifier: `metadata attributes contact` lists the entity's attributes as a bare array; the
# predicate asserts one row whose LogicalName ends with `evalmeta899refcode` AND whose
# AttributeType is `String` — rules out did-nothing (no such column) and a wrong-kind partial.
# What it CANNOT read back is `AutoNumberFormat` itself: list_attributes projects
# LogicalName/SchemaName/AttributeType/IsValidFor*/RequiredLevel/SourceType/MetadataId only
# (crm/core/metadata.py), not the auto-number pattern, and the single-attribute `metadata
# attribute` show is not a list verb (returns an object, not the array evaluate_expect needs).
# So the auto-number-ness proper is the agent's demonstrated work, machine-scored only to
# existence+kind and assessed for correctness by the advisory L2 judge — the same scoring
# ceiling trial-global-optionset documents for a global option set's option order/labels.
# T2, not a baseline-trivial T1: it is add-attribute with the string-only `--auto-number-format`
# escape hatch (a bare agent reaches for a plain string/int column) plus the customization-write
# `--solution` scoping requirement — a real multi-step workflow, not one obvious command. The
# authoring-time calibration proxy is the tier; the live baseline-3/3 / skill-0/3 filter runs
# post-hoc on paired-run results, not here.
# Host-agnostic (add-attribute + auto-number formats work on cloud and on-prem v9.1) -> either.
# NOTE (cleanup): a custom attribute is metadata, not a deletable record, so the record-delete
# cleanup model leaves it; teardown needs `metadata delete-attribute contact <logical-name>`
# (agent-chosen name), so cleanup is empty and the column is definition residue a maintainer
# clears out of band — see the "Known cleanup limitation" note in README.md.
target: either
kind: do
end_state:
  query:
    - metadata
    - attributes
    - contact
  expect:
    row_suffix:
      LogicalName: evalmeta899refcode
      AttributeType: String
cleanup: []
---

Working against the connected Dynamics 365 org, the business wants every contact to carry a
human-readable reference code that the platform generates automatically on save — no plug-in
or custom code — formatted like `EVAL899-00001` (a fixed `EVAL899-` prefix followed by a
zero-padded sequence number).

Add a new auto-number column to the `contact` table with the display name `EvalMeta899 Refcode`
and the auto-number format `EVAL899-{SEQNUM:5}`, scoping the customization write to a solution,
then publish it. Confirm the column now exists on `contact` as an auto-number (string) column.
