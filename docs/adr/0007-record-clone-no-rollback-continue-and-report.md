# Record clone: no rollback, continue-and-report for child failures

A record clone with `--with-children` (#231) creates the parent, then each
direct child row. When a child create fails, the verb does **not** roll back
by deleting what it already created, and does **not** abort: it continues with
the remaining children and finishes with an operational failure whose envelope
carries `meta.created` (the parent id plus per-entity child ids) and a
failures list (entity, source id, reason).

Rollback-by-delete was rejected because cleanup deletes are themselves
destructive writes that can fail partway (plugins, cascade rules,
permissions), and create-side plugin effects cannot be unfired — a "rollback"
that can fail is a lie in the contract. Abort-on-first-failure was rejected
because, without rollback, aborting also leaves partial state; it buys the
appearance of consistency, not consistency. Continuing maximises the useful
outcome (109 of 110 rows cloned, failures named) and the recovery path is to
fix the cause and clone the failed rows individually — never to re-run the
whole verb, since the parent already exists.
