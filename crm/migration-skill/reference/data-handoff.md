# Data hand-off: who moves the rows, the traps, and the evidence

The business-data half of a migration. `crm` has no row mover for a `crm2011`
profile, and its `data import` suits only a small set. You do not load the bulk
rows: you **hand each data job to the right tool**, warn the operator of the
traps, bracket the load with `crm` verbs, and accept the load only on a
comparison. The `crm` skill's bulk-data page says how to run the verbs.

## Which tool for which data job

| Data job | Hand it to |
|---|---|
| Historical or bulk rows read from CRM 2011 over its SOAP endpoint | **KingswaySoft SSIS integration toolkit for CRM** |
| Bulk rows into the cloud org | **Azure Data Factory** |
| Reference and configuration data (option-set-like lookup tables, settings rows) | **Configuration Migration Tool**, or **`pac data`**. Both are for configuration data, not large volumes. `pac data` exists only in the .NET Framework build of the Power Platform CLI. |
| A small org-to-org copy of one table | **XrmToolBox Data Transporter** |
| A small set where the source GUIDs must survive | `crm data import` from a JSONL export |

Record the chosen tool, its operator and the row scope of each job as an open
item. Where the tool is missing, report the job as not covered.

## Traps the loader's operator must know

State each to the operator before the load, with its source. These are the
platform's rules, so a loader's success message does not reveal a breach.

1. **The documented route for `createdon` is `overriddencreatedon`.** In a
   Dataverse data import, map the source column to `overriddencreatedon`; the
   loading user needs the `prvOverrideCreatedOnCreatedBy` privilege. `modifiedon`, `createdby` and
   `modifiedby` cannot be imported at all, so the target shows the load time
   and the loading user there. Source:
   <https://learn.microsoft.com/power-apps/developer/data-platform/run-data-import>
2. **Audit history has no import route.** The audit table is read-only. Extract
   the source's audit rows through the change-history messages and archive
   them outside the target, and report the history as not migrated. Source:
   <https://learn.microsoft.com/power-apps/developer/data-platform/auditing/retrieve-audit-data>
3. **Create the record active, then set its state with `statecode` and
   `statuscode` together.** On a state update, always send the wanted
   `statuscode` with `statecode`: with `statecode` alone the system sets the
   default status reason, so a custom status reason is lost. (The page covers
   updates; creating active first is this skill's guidance, not a quote.)
   Source:
   <https://learn.microsoft.com/power-apps/developer/data-platform/special-update-operation-behavior>
4. **The Configuration Migration Tool matches on the primary name when no
   uniqueness fields are set.** A matching record is updated on the target, so
   two records sharing a name overwrite each other. Have the operator define
   uniqueness fields for every table before the import. Source:
   <https://learn.microsoft.com/power-platform/admin/manage-configuration-data>

`pac data` is a front end to the same tool, so it carries trap 4 too. Source:
<https://learn.microsoft.com/power-platform/developer/cli/reference/data>

## The `crm` verbs that bracket an external load

In this order. Steps 2, 4 and 5 pin the target profile; step 1 pins the source
profile; step 3 uses the loader's own connection.

1. The source baseline, a file of the tables' rows in scope. A source a Web API
   profile reaches (an on-premises 9.x stage, a cloud org) gets `data export`.
   A `crm2011` source cannot be exported: ask the loader's operator, or the
   source database owner, for a CSV or JSONL file of the same rows and columns,
   and record its origin. With no such file, the job is an open item.
2. `data load-window begin`, which turns off, on the tables in scope, the
   custom (non-Microsoft) plug-in steps, activated classic workflows and
   published duplicate-detection rules, and writes a state file. Business
   rules and business process flows stay on. Preview it first with its dry run.
3. The external load, run by its operator.
4. `data load-window end` with that state file, restoring the logic. Re-run it
   until it exits 0.
5. `data export` of the target, same tables and columns.
6. `data compare` of the two files on a key that is unique in both.

If `begin` reports a component it could not turn off, decide whether that
component may stay on during the load, or run `end` to abort.

## The evidence rule

**The data arrived only when a `data compare` result shows it.** A loader's own
success message, a row count in its log or an exit 0 proves the platform
accepted a request, not that the rows match.

- Read `equal` and the counts; a differing result still exits 0.
- A key that repeats or is blank is unpaired and uncounted as matched:
  widen the key before trusting a matched count.
- Trap 1 shows up as column mismatches on paired rows: `modifiedon`,
  `createdby`, `modifiedby`, and `createdon` when it was not mapped. Leave
  those columns out of both exports, or list them as known mismatches.
- Audit history is never in a business table's export, so the compare cannot
  show it. Record it as not migrated, outside the compare.
- With no comparison, report the job as **loaded, not proven**, an unknown.
