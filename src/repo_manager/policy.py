"""The safety policy, as pure functions.

Each `decide_*` maps a RepositorySnapshot (and, for checkout, the resolved target)
to a Decision: proceed with a planned action, or skip with a reason and a next
action. No git calls, no I/O, so the whole table is unit-testable against the
fixture matrix.

This mirrors the safety table in the architecture doc. The executor in
operations.py turns a PROCEED into a concrete mutation and a terminal verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    Classification,
    InProgressOperation,
    Relationship,
    RepositorySnapshot,
    Verdict,
)


@dataclass
class Decision:
    verdict: Verdict  # PROCEED or SKIPPED
    planned: str = ""
    reason: str = ""
    next_action: str | None = None
    via_stash: bool = False  # executor should stash, mutate, then restore


def _proceed(planned: str, via_stash: bool = False) -> Decision:
    return Decision(verdict=Verdict.PROCEED, planned=planned, via_stash=via_stash)


def _skip(reason: str, next_action: str) -> Decision:
    return Decision(verdict=Verdict.SKIPPED, reason=reason, next_action=next_action)


def _in_progress_skip(snap: RepositorySnapshot) -> Decision | None:
    op = snap.worktree.in_progress_operation
    if op is not InProgressOperation.NONE:
        return _skip(
            f"a {op.value} is in progress",
            f"finish or abort the {op.value} (e.g. git {op.value} --abort)",
        )
    return None


# -- update -------------------------------------------------------------------------
#
# Update fetches first, so the snapshot handed here already reflects current remote
# state. It touches only the current branch and never switches.


def decide_update(snap: RepositorySnapshot, stash: bool = False) -> Decision:
    if (d := _in_progress_skip(snap)) is not None:
        return d

    cls = snap.classification
    w = snap.worktree

    if cls is Classification.DIRTY:
        if stash:
            # Dirtiness is no longer the blocker; decide on the remote relationship,
            # exactly as a clean repo would be judged.
            return _stash_update_decision(snap)
        return _skip(
            f"{w.total_changes} local change(s) would be at risk",
            "commit or stash the changes, or use --stash",
        )
    if cls is Classification.DETACHED:
        return _skip(
            "HEAD is detached",
            "check out a branch first (repo <project> checkout)",
        )
    if cls is Classification.NO_UPSTREAM:
        return _skip(
            "the current branch has no upstream",
            "set an upstream with git branch --set-upstream-to",
        )
    if cls is Classification.AMBIGUOUS_REMOTE:
        return _skip(
            "several remotes exist and none is named 'origin'",
            "set 'remote' for this repository in the profile",
        )
    if cls is Classification.REMOTE_UNAVAILABLE:
        return _skip(
            "the remote could not be reached",
            "check network or credentials, then retry",
        )
    if cls is Classification.DIVERGED:
        a = snap.remote.ahead_count or 0
        b = snap.remote.behind_count or 0
        return _skip(
            f"local and upstream have diverged (↑{a} ↓{b})",
            "reconcile manually with rebase or merge",
        )
    if cls is Classification.AHEAD:
        return _skip(
            "ahead of upstream; nothing to fast-forward",
            "push your local commits when ready",
        )
    if cls is Classification.READY:
        b = snap.remote.behind_count or 0
        return _proceed(f"fast-forward {snap.checkout.upstream_branch} ({b} behind)")
    if cls is Classification.CURRENT:
        return _proceed("already up to date")

    # DEFAULT_BRANCH_UNKNOWN reaches here only when clean and current, so update
    # of the current branch is still a safe no-op.
    return _proceed("already up to date")


def _stash_update_decision(snap: RepositorySnapshot) -> Decision:
    """Decide an update for a dirty repo under --stash.

    Only the fast-forward-able case actually stashes; every other remote state gets
    the same skip a clean repo would, because stashing cannot make it updatable.
    """
    upstream = snap.checkout.upstream_branch
    rel = snap.remote.relationship

    if upstream is None:
        return _skip(
            "the current branch has no upstream",
            "set an upstream with git branch --set-upstream-to",
        )
    if rel is Relationship.UNKNOWN:
        return _skip(
            "the remote could not be reached",
            "check network or credentials, then retry",
        )
    if rel is Relationship.DIVERGED:
        a = snap.remote.ahead_count or 0
        b = snap.remote.behind_count or 0
        return _skip(
            f"local and upstream have diverged (↑{a} ↓{b})",
            "reconcile manually; stashing cannot resolve a divergence",
        )
    if rel is Relationship.AHEAD:
        return _skip(
            "ahead of upstream; nothing to fast-forward",
            "push your local commits when ready",
        )
    if rel is Relationship.BEHIND:
        b = snap.remote.behind_count or 0
        return _proceed(
            f"stash, fast-forward {upstream} ({b} behind), restore", via_stash=True
        )
    # CURRENT: nothing to update, so no need to disturb the worktree at all.
    return _proceed("already up to date")


# -- default ------------------------------------------------------------------------
#
# Gates on worktree/HEAD/default-known/remote-availability only. Whether the default
# branch itself can fast-forward is resolved by the executor after switching, since
# the snapshot describes the *current* branch, not the default.


def decide_switch_default(snap: RepositorySnapshot, stash: bool = False) -> Decision:
    if (d := _in_progress_skip(snap)) is not None:
        return d

    cls = snap.classification
    default = snap.checkout.default_branch

    if default is None or cls is Classification.DEFAULT_BRANCH_UNKNOWN:
        return _skip(
            "the default branch could not be determined",
            "set default_branch for this repository in the profile",
        )
    if cls is Classification.DIRTY:
        if stash:
            if snap.remote.fetch_attempted and not snap.remote.data_is_current:
                return _skip(
                    "the remote could not be reached",
                    "check network or credentials, then retry",
                )
            return _proceed(
                f"stash, switch to {default} and fast-forward, restore",
                via_stash=True,
            )
        return _skip(
            f"{snap.worktree.total_changes} local change(s) block a branch switch",
            "commit or stash the changes first",
        )
    if cls is Classification.DETACHED:
        return _skip(
            "HEAD is detached; a switch could orphan the checked-out commit",
            f"check out {default} explicitly if that is intended",
        )
    if cls is Classification.AMBIGUOUS_REMOTE:
        return _skip(
            "several remotes exist and none is named 'origin'",
            "set 'remote' for this repository in the profile",
        )
    if cls is Classification.REMOTE_UNAVAILABLE:
        return _skip(
            "the remote could not be reached, so the default cannot be brought current",
            "check network or credentials, then retry",
        )

    already = snap.checkout.current_branch == default
    if already:
        return _proceed(f"already on {default}; fast-forward if behind")
    return _proceed(f"switch to {default} and fast-forward")


# -- sync ---------------------------------------------------------------------------


def decide_sync(snap: RepositorySnapshot, source_ref: str | None) -> Decision:
    """Integrate the fetched remote default branch into the current branch.

    Sync deliberately does not require an upstream for the current branch. A local
    feature branch can still receive the project default branch. It never stashes.
    """
    if (d := _in_progress_skip(snap)) is not None:
        return d
    if snap.worktree.is_dirty:
        return _skip(
            f"{snap.worktree.total_changes} local change(s) would be at risk",
            "commit or stash the changes, then retry sync",
        )
    if snap.checkout.is_detached:
        return _skip("HEAD is detached", "check out a branch first")
    if snap.classification is Classification.AMBIGUOUS_REMOTE:
        return _skip(
            "several remotes exist and none is named 'origin'",
            "set 'remote' for this repository in the project configuration",
        )
    if snap.classification is Classification.REMOTE_UNAVAILABLE:
        return _skip(
            "the remote could not be reached",
            "check network or credentials, then retry",
        )
    if not snap.checkout.default_branch:
        return _skip(
            "the default branch could not be determined",
            "set default_branch for this repository in the project configuration",
        )
    if not source_ref:
        return _skip(
            "the fetched remote default branch does not exist",
            "check the remote and default_branch configuration",
        )
    return _proceed(f"integrate {source_ref} into {snap.checkout.current_branch}")


# -- checkout -----------------------------------------------------------------------
#
# Permissive by design: it only pre-skips an in-progress operation and a target that
# exists nowhere. Overwrite risk from a dirty tree is left to git, which refuses the
# switch; the executor reports that refusal as a failure with git's own message.


def decide_checkout(
    snap: RepositorySnapshot,
    target: str | None,
    exists_local: bool,
    exists_remote: bool,
) -> Decision:
    if (d := _in_progress_skip(snap)) is not None:
        return d

    if target is None:
        return _skip(
            "the default branch could not be determined",
            "set default_branch in the profile, or name a branch explicitly",
        )
    if snap.checkout.current_branch == target and exists_local:
        return _proceed(f"already on {target}")
    if not exists_local and not exists_remote:
        return _skip(
            f"branch '{target}' exists neither locally nor on the remote",
            "check the branch name, or fetch first",
        )
    if exists_local:
        return _proceed(f"switch to local branch {target}")
    return _proceed(f"create tracking branch {target} from the remote")
