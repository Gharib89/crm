---
id: feedback-note-create
domain: feedback
target: cloud
# A note (annotation) is attached to a parent record, so the workflow first creates
# a contact to hang it on. Cleanup removes the note then the contact.
end_state:
  query:
    - query
    - odata
    - annotations
    - --filter
    - "subject eq 'EvalSet571 Note'"
    - --select
    - subject
  expect:
    count: 1
    row:
      subject: EvalSet571 Note
cleanup:
  - entity: annotations
    id_field: annotationid
    filter: "subject eq 'EvalSet571 Note'"
  - entity: contacts
    id_field: contactid
    filter: "lastname eq 'EvalSet571Note'"
---

On profile `agent-cloud`, create a contact whose last name is `EvalSet571Note`,
then attach a note (annotation) to that contact whose subject is `EvalSet571 Note`
and whose body records a short comment. Confirm the note is attached to the
contact you created.
