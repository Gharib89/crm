# pyright: basic
"""E2E tests for the status/state model metadata commands."""
from __future__ import annotations

from crm.tests.e2e.coverage import covers


@covers("metadata status-add", "metadata state-relabel", "metadata set-transitions")
def test_status_state_model_lifecycle(backend, ephemeral_entity):
    """Add a status option, relabel a state, and write a transition graph on a
    throwaway custom entity (its statecode/statuscode are cleaned up with it)."""
    from crm.core import status_meta as sm

    # A custom entity ships statecode {0:Active, 1:Inactive} and
    # statuscode {1:Active(state 0), 2:Inactive(state 1)}.
    added = sm.add_status_value(
        backend, ephemeral_entity, state_code=0, label_text="E2E Pending",
        publish=True,
    )
    assert added["added"] is True
    new_value = added["value"]

    relabel = sm.relabel_state_value(
        backend, ephemeral_entity, value=1, label_text="E2E Closed",
        merge_labels=True, publish=True,
    )
    assert relabel["updated"] is True

    # Allow Active(1) → the freshly added status, and → Inactive(2).
    out = sm.set_status_transitions(
        backend, ephemeral_entity,
        transitions=[(1, new_value), (1, 2)], publish=True,
    )
    assert out["updated"] is True
    assert out["transitions_set"] == [1]
