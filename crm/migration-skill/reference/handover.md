# Handover: what to request from the team that ran the hops

Step 5 of the cloud-first route. Another team moved the database through every
upgrade stage, and you saw none of them. What they know is evidence you cannot
extract; ask for each item below. **A missing item is an unknown**, reported as
one, never filled by guess.

| Request | What it lets you conclude |
|---|---|
| **One read-only `crm inventory extract` at the 9.1 stage**, with usage, in the same scope as the other two. | Splits the 2011 to cloud diff into two (2011 to 9.1, 9.1 to cloud), so a `removed` or `changed` entry is tied to the hop where it appeared. Without it, no entry is tied to a hop. |
| **The components they disabled, removed or changed** to pass each hop and the on-premises to online validation, with the hop. | Explains a `removed` or `changed` entry as a deliberate act, not a loss. A diff entry absent from this list is unexplained. |
| **The upgrade logs** of each hop and of the validation. | Names the components the upgrade tooling itself dropped or rewrote, which nobody may have recorded by hand. |
| **Whether forms were merged** during the upgrade. | Tells a form merged into another from one that was lost: if yes, a `removed` form may be a merge; if no, it is lost. |
| **Which SQL objects and SQL-based reports were dropped.** | Reads a `removed` SQL-based report or SQL-backed component as dropped on purpose, and names the behavior the cloud org no longer has, so verification does not wait on it. |

Ask for the answers in writing, and record each beside the diff it explains.
When the team cannot supply an item, list it as an unknown in the report with
the entry or conclusion it would have settled.
