"""The policy engine, asserted against the fixture matrix.

Policy is pure, so these tests need no git execution: they feed the session-scoped
snapshots straight into the decision functions.
"""

from __future__ import annotations

import pytest

from repo_manager import policy
from repo_manager.models import Verdict

# Expected update verdict per fixture state.
UPDATE_EXPECT = {
    "clean-current": Verdict.PROCEED,   # no-op
    "clean-behind": Verdict.PROCEED,    # fast-forward
    "dirty": Verdict.SKIPPED,
    "ahead": Verdict.SKIPPED,
    "diverged": Verdict.SKIPPED,
    "detached": Verdict.SKIPPED,
    "no-upstream": Verdict.SKIPPED,
    "ambiguous-default": Verdict.PROCEED,  # clean+current, current-branch no-op
    "unreachable-remote": Verdict.SKIPPED,
    "rebase-in-progress": Verdict.SKIPPED,
    "stash-ok": Verdict.SKIPPED,        # dirty
    "stash-conflict": Verdict.SKIPPED,  # dirty
}

SWITCH_EXPECT = {
    "clean-current": Verdict.PROCEED,
    "clean-behind": Verdict.PROCEED,
    "dirty": Verdict.SKIPPED,
    "ahead": Verdict.PROCEED,           # clean; default-branch work happens in exec
    "diverged": Verdict.PROCEED,        # current branch clean; exec guards default ff
    "detached": Verdict.SKIPPED,
    "no-upstream": Verdict.PROCEED,     # clean, default resolvable
    "ambiguous-default": Verdict.SKIPPED,  # default unknown
    "unreachable-remote": Verdict.SKIPPED,
    "rebase-in-progress": Verdict.SKIPPED,
    "stash-ok": Verdict.SKIPPED,
    "stash-conflict": Verdict.SKIPPED,
}


@pytest.mark.parametrize("name,expected", UPDATE_EXPECT.items())
def test_decide_update(matrix_snapshots, name, expected):
    snap = matrix_snapshots[name]
    decision = policy.decide_update(snap)
    assert decision.verdict is expected, f"{name}: reason={decision.reason}"
    if decision.verdict is Verdict.SKIPPED:
        assert decision.reason
        assert decision.next_action


@pytest.mark.parametrize("name,expected", SWITCH_EXPECT.items())
def test_decide_switch_default(matrix_snapshots, name, expected):
    snap = matrix_snapshots[name]
    decision = policy.decide_switch_default(snap)
    assert decision.verdict is expected, f"{name}: reason={decision.reason}"
    if decision.verdict is Verdict.SKIPPED:
        assert decision.reason
        assert decision.next_action


def test_in_progress_never_proceeds_for_any_operation(matrix_snapshots):
    """The core invariant: an in-progress repo is skipped by every operation."""
    snap = matrix_snapshots["rebase-in-progress"]
    assert policy.decide_update(snap).verdict is Verdict.SKIPPED
    assert policy.decide_switch_default(snap).verdict is Verdict.SKIPPED
    assert (
        policy.decide_checkout(snap, "main", True, True).verdict is Verdict.SKIPPED
    )


def test_checkout_permissive_but_skips_missing_branch(matrix_snapshots):
    snap = matrix_snapshots["clean-current"]
    assert policy.decide_checkout(snap, "main", True, False).verdict is Verdict.PROCEED
    assert (
        policy.decide_checkout(snap, "nope", False, False).verdict is Verdict.SKIPPED
    )
    # Remote-only branch is allowed: a tracking branch will be created.
    assert (
        policy.decide_checkout(snap, "feature", False, True).verdict is Verdict.PROCEED
    )


def test_update_skip_reasons_are_specific(matrix_snapshots):
    dirty = policy.decide_update(matrix_snapshots["dirty"])
    assert "change" in dirty.reason.lower()
    diverged = policy.decide_update(matrix_snapshots["diverged"])
    assert "diverged" in diverged.reason.lower()
    unreachable = policy.decide_update(matrix_snapshots["unreachable-remote"])
    assert "reach" in unreachable.reason.lower()
