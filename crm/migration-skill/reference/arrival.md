# Arrival checklist: reads on the cloud org after the upgrade

Step 4 of the cloud-first route, run once against the finished cloud org. An
upgrade chain can leave state that `crm inventory diff` does not show. Each item
below is a read; a bad answer is a question for the team that ran the hops
(`reference/handover.md`) or an unknown; the source stays read-only.
Run the reads through a read-only profile; `crm query odata` takes the entity
set and the filter shown, and the `crm` skill owns the rest of its usage.

| Item | Read | Bad answer | Next step | Source |
|---|---|---|---|---|
| **Processes and plug-in steps whose state changed** | Read the diff's `changed` entries of kind `process` and `step`; no new read. | A `fields.state` change. Process: 0 Draft, 1 Activated, 2 Suspended. Step: 0 Enabled, 1 Disabled. | Ask whether the team disabled it to pass a hop; if not, record an unknown. Re-enable only after verification proves the component. | Measured: `crm metadata picklist workflow statecode` and `sdkmessageprocessingstep statecode`; the `state` field is recorded by `crm inventory extract`. |
| **Forms inactive in the cloud** | Read the diff's `changed` `form` entries (`fields.active`, see `reference/inventory.md`). | `fields.active` true on the source, false in the cloud. | Activate or retire the form; the upgrade leaves that choice to the customer. | `reference/inventory.md`. |
| **Duplicate detection rules not published** | `crm dup list`, then keep the rows whose `statuscode` is not 2. | Any row left: `statecode` 0 (Inactive) or `statuscode` 0 (Unpublished) or 1 (Publishing). | Publish the rule, then re-read: a rule imported through a solution arrives unpublished. | Measured: `crm metadata picklist duplicaterule statuscode`. [Learn: duplicate detection rules in solutions](https://learn.microsoft.com/power-platform/admin/duplicate-detection-rules-solutions). |
| **Mailboxes not on server-side synchronization** | `crm query odata mailboxes` with the filter `statecode eq 0 and (incomingemaildeliverymethod eq 1 or incomingemaildeliverymethod eq 3 or outgoingemaildeliverymethod eq 1 or actdeliverymethod eq 1)`. | Any row: method 1 is Dynamics 365 for Outlook (not available online), 3 is Forward Mailbox (supported, not recommended). | Move the mailbox to server-side synchronization and test and enable it. | [Learn: set incoming and outgoing email synchronization](https://learn.microsoft.com/power-platform/admin/set-incoming-outgoing-email-synchronization), [Learn: the Email Router stopped working in April 2021](https://learn.microsoft.com/power-platform/admin/integrate-synchronize-your-email-system). |
| **Plug-in trace log setting** | `crm query odata organizations` selecting `plugintracelogsetting`. | 0 (Off): verification cannot collect traces. 1 is Exception, 2 is All. | Set it to 2 for the verification window and back after; the commands are in the `crm` skill's automation page. | Measured: `crm metadata picklist organization plugintracelogsetting`. [Learn: write and register a plug-in](https://learn.microsoft.com/power-apps/developer/data-platform/tutorial-write-plug-in). |

## Unknowns this page cannot close

- **A mailbox value of 2 can still hide an Email Router setup.** The cloud org
  labels value 2 "Server-Side Synchronization" whatever the source said, so the
  read cannot tell the two apart: ask the team that ran the hops which mailboxes
  were on the Email Router.
- **Custom SQL-based reports** have no read here. A SQL data provider is normal
  for stock reports on a cloud org, so `crm report get` alone flags false
  positives. Use the handover page's SQL-report request until the census lands.

Done when each item is answered `clean`, with the bad rows listed, or recorded
as an unknown.
