# pyright: basic
"""E2E tests for the status/state option metadata commands."""
from __future__ import annotations

from crm.tests.e2e.coverage import covers


@covers("metadata status-add", "metadata state-relabel")
def test_status_state_model_lifecycle(backend, ephemeral_entity):
    """Add a status option and relabel a state on a throwaway custom entity
    (its statecode/statuscode are cleaned up with it). Both writes stage; a single
    publish-all at the end publishes them together."""
    from crm.core import solution as sol
    from crm.core import status_meta as sm

    # A custom entity ships statecode {0:Active, 1:Inactive} and
    # statuscode {1:Active(state 0), 2:Inactive(state 1)}.
    added = sm.add_status_value(
        backend, ephemeral_entity, state_code=0, label_text="E2E Pending",
        publish=False,
    )
    assert added["added"] is True
    assert isinstance(added["value"], int)

    relabel = sm.relabel_state_value(
        backend, ephemeral_entity, value=1, label_text="E2E Closed",
        merge_labels=True, publish=False,
    )
    assert relabel["updated"] is True

    sol.publish_all(backend)
