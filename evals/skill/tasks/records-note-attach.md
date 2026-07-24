---
id: records-note-attach
domain: records
tier: 2
source:
  type: so
  url: https://stackoverflow.com/questions/32761311
# Related-record write (pilot harvest #885 row 1): attach a note (annotation) carrying a
# body of text to an EXISTING contact — the recurring "create annotation to a contact by
# API" ask (26 votes, 23.5k views). The workflow is create-parent-then-relate: create the
# contact, then create an annotation bound to it through the polymorphic `objectid` lookup
# (objectid_contact@odata.bind), which the platform stamps with objecttypecode 'contact'.
# Host-agnostic — annotations and the objectid lookup exist on cloud and on-prem v9.1 — so `either`.
target: either
kind: do
# The verifier proves the note landed AND is attached to a contact: count:1 rules out
# did-nothing (0 rows) and an accidental duplicate note (2 rows); the row pins the exact body
# text, and objecttypecode 'contact' proves the note was RELATED to a contact rather than left
# floating (an unbound annotation carries a null objecttypecode, which never matches 'contact').
# The distinctive subject scopes the annotations query to just this task's note.
end_state:
  query:
    - query
    - odata
    - annotations
    - --filter
    - "subject eq 'EvalRec896 Onboarding Note'"
    - --select
    - subject,notetext,objecttypecode
  expect:
    count: 1
    row:
      subject: EvalRec896 Onboarding Note
      notetext: Follow up on the signed onboarding paperwork.
      objecttypecode: contact
cleanup:
  - entity: annotations
    id_field: annotationid
    filter: "subject eq 'EvalRec896 Onboarding Note'"
  - entity: contacts
    id_field: contactid
    filter: "lastname eq 'EvalRec896Note'"
---

Working against the connected Dynamics 365 org, first create a single contact whose first
name is `Ada` and whose last name is `EvalRec896Note`. Then attach a note to that contact:
give the note the title `EvalRec896 Onboarding Note` and the body text `Follow up on the
signed onboarding paperwork.`, and make sure the note is filed against (regarding) that
contact rather than left as a standalone note. Verify the note was created and is attached
to the contact.
