"""Operation coordinator.

Builds a plan (parallel fetch + snapshot + policy decision per repo), then executes
approved repositories serially. Every mutation is a local fast-forward or branch
switch against refs a prior fetch already updated, so execution never blocks on the
network. The result is an OperationReport, which is what the renderers consume.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import policy
from .git_backend import GitBackend
from .models import (
    Operation,
    OperationReport,
    OperationResult,
    RepositorySnapshot,
    Verdict,
)
from .policy import Decision
from .repository_service import RepoSpec, RepositoryService


@dataclass
class PlanItem:
    spec: RepoSpec
    snapshot: RepositorySnapshot
    decision: Decision
    target: str | None = None  # resolved branch for checkout
    sync_rebase: bool = False
    preparation: str | None = None


@dataclass
class InteractivePlanItem:
    """One fetched repository with the safe plans available to interactive mode."""

    spec: RepoSpec
    snapshot: RepositorySnapshot
    plans: dict[str, PlanItem]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class OperationCoordinator:
    def __init__(self, backend: GitBackend | None = None, jobs: int = 8):
        self.backend = backend or GitBackend()
        self.jobs = jobs
        self.service = RepositoryService(backend=self.backend, jobs=jobs)

    # -- planning -----------------------------------------------------------------

    def build_plan(
        self,
        specs: list[RepoSpec],
        operation: Operation,
        fetch_timeout: float = 30.0,
        checkout_branch: str | None = None,
        stash: bool = False,
        sync_rebase: bool = False,
    ) -> list[PlanItem]:
        """Fetch + snapshot in parallel, then decide per repo. Read-only."""
        # All three operations need current remote state to decide safely.
        snapshots = self.service.snapshot_all(
            specs, fetch=True, fetch_timeout=fetch_timeout
        )
        by_path = {s.identity.relative_path: s for s in snapshots}

        return [
            self._plan_from_snapshot(
                spec,
                by_path[spec.relative_path],
                operation,
                checkout_branch=checkout_branch,
                stash=stash,
                sync_rebase=sync_rebase,
            )
            for spec in specs
        ]

    def build_interactive_plan(
        self,
        specs: list[RepoSpec],
        fetch_timeout: float = 30.0,
        stash: bool = False,
    ) -> list[InteractivePlanItem]:
        """Fetch once and derive the safe update, sync, and default choices."""
        snapshots = self.service.snapshot_all(
            specs, fetch=True, fetch_timeout=fetch_timeout
        )
        by_path = {s.identity.relative_path: s for s in snapshots}
        result: list[InteractivePlanItem] = []
        for spec in specs:
            snapshot = by_path[spec.relative_path]
            plans = {
                operation.value: self._plan_from_snapshot(
                    spec, snapshot, operation, stash=stash
                )
                for operation in (Operation.UPDATE, Operation.SYNC, Operation.DEFAULT)
            }
            if snapshot.worktree.is_dirty:
                stash_sync = self._plan_from_snapshot(
                    spec, snapshot, Operation.SYNC, stash=True
                )
                if stash_sync.decision.verdict is Verdict.PROCEED:
                    plans["stash-sync"] = stash_sync
                    discard_sync = PlanItem(
                        spec=stash_sync.spec,
                        snapshot=stash_sync.snapshot,
                        decision=Decision(
                            verdict=Verdict.PROCEED,
                            planned=stash_sync.decision.planned.replace(
                                "stash, ", "discard local changes, "
                            ),
                        ),
                        target=stash_sync.target,
                        sync_rebase=stash_sync.sync_rebase,
                        preparation="discard",
                    )
                    plans["discard-sync"] = discard_sync
            result.append(
                InteractivePlanItem(spec=spec, snapshot=snapshot, plans=plans)
            )
        return result

    def _plan_from_snapshot(
        self,
        spec: RepoSpec,
        snap: RepositorySnapshot,
        operation: Operation,
        checkout_branch: str | None = None,
        stash: bool = False,
        sync_rebase: bool = False,
        preparation: str | None = None,
    ) -> PlanItem:
        if operation is Operation.UPDATE:
            decision = policy.decide_update(snap, stash=stash)
            target = None
        elif operation is Operation.DEFAULT:
            decision = policy.decide_switch_default(snap, stash=stash)
            target = snap.checkout.default_branch
        elif operation is Operation.SYNC:
            remote = snap.identity.remote_name
            default = snap.checkout.default_branch
            target = (
                f"{remote}/{default}"
                if remote
                and default
                and self.backend.branch_exists_remote(
                    spec.absolute_path, remote, default
                )
                else None
            )
            decision = policy.decide_sync(snap, target, stash=stash)
        else:  # CHECKOUT
            target = self._resolve_checkout_target(snap, checkout_branch)
            remote = snap.identity.remote_name
            exists_local = bool(
                target and self.backend.branch_exists_local(spec.absolute_path, target)
            )
            exists_remote = bool(
                target
                and remote
                and self.backend.branch_exists_remote(
                    spec.absolute_path, remote, target
                )
            )
            decision = policy.decide_checkout(snap, target, exists_local, exists_remote)
        return PlanItem(
            spec=spec,
            snapshot=snap,
            decision=decision,
            target=target,
            sync_rebase=sync_rebase,
            preparation=preparation,
        )

    def _resolve_checkout_target(
        self, snap: RepositorySnapshot, branch: str | None
    ) -> str | None:
        if branch is None or branch == "default":
            return snap.checkout.default_branch
        return branch

    # -- execution ----------------------------------------------------------------

    def execute(
        self,
        plan: list[PlanItem],
        operation: Operation,
        project_name: str,
        project_root: str,
        dry_run: bool,
        ignore_skips: bool = False,
    ) -> OperationReport:
        results: list[OperationResult] = []
        for item in plan:
            results.append(self._execute_one(item, operation, dry_run))

        return OperationReport(
            generated_at=_now_iso(),
            project_name=project_name,
            project_root=project_root,
            operation=operation,
            dry_run=dry_run,
            ignore_skips=ignore_skips,
            results=results,
        )

    def _execute_one(
        self, item: PlanItem, operation: Operation, dry_run: bool
    ) -> OperationResult:
        snap = item.snapshot
        dec = item.decision
        result = OperationResult(
            name=snap.name,
            relative_path=snap.identity.relative_path,
            operation=operation,
            verdict=dec.verdict,
            planned=dec.planned,
            reason=dec.reason,
            next_action=dec.next_action,
            before_branch=snap.checkout.current_branch,
            before_head=snap.checkout.head_sha,
            after_branch=snap.checkout.current_branch,
            after_head=snap.checkout.head_sha,
            source_branch=item.target if operation is Operation.SYNC else None,
        )

        if dec.verdict is Verdict.SKIPPED:
            return result
        if dry_run:
            # PROCEED verdict is preserved: "would proceed".
            return result

        if item.preparation == "discard":
            discard = self.backend.discard_local_changes(item.spec.absolute_path)
            result.commands.extend(["reset --hard HEAD", "clean -fd"])
            if not discard.ok:
                result.verdict = Verdict.FAILED
                result.error = discard.stderr.strip() or "could not discard local changes"
            else:
                self._do_sync(item, result)
        elif dec.via_stash:
            self._do_stash_operation(item, operation, result)
        elif operation is Operation.UPDATE:
            self._do_update(item, result)
        elif operation is Operation.DEFAULT:
            self._do_switch_default(item, result)
        elif operation is Operation.SYNC:
            self._do_sync(item, result)
        else:
            self._do_checkout(item, result)

        self._record_after(item.spec.absolute_path, result)
        if result.verdict is Verdict.PROCEED:
            result.verdict = Verdict.UPDATED if result.changed else Verdict.NOOP
        return result

    # -- stash flow ---------------------------------------------------------------

    _stash_counter = 0

    def _next_stash_token(self) -> str:
        # Unique per process run; avoids clashing with any pre-existing stashes.
        OperationCoordinator._stash_counter += 1
        return f"{os.getpid()}-{OperationCoordinator._stash_counter}"

    def _do_stash_operation(
        self, item: PlanItem, operation: Operation, result: OperationResult
    ) -> None:
        """Stash local work, run the core mutation, then restore.

        The stash is created with a unique, findable message. On a conflicting
        restore the stash is retained and recovery instructions are attached, so no
        local work is ever lost.
        """
        repo = item.spec.absolute_path
        rel = item.snapshot.identity.relative_path
        message = f"repo auto-stash: {rel} #{self._next_stash_token()}"

        push = self.backend.stash_push(repo, message)
        result.commands.append("stash push --include-untracked")
        if not push.ok:
            result.verdict = Verdict.FAILED
            result.error = push.stderr.strip() or "could not create stash"
            return
        result.stashed = True
        ref = self.backend.stash_find(repo, message) or "stash@{0}"
        result.stash_reference = ref

        # Run the core mutation. These set verdict FAILED on failure.
        if operation is Operation.UPDATE:
            self._do_update(item, result)
        elif operation is Operation.DEFAULT:
            self._do_switch_default(item, result)
        else:
            self._do_sync(item, result)

        core_failed = result.verdict is Verdict.FAILED

        # Always attempt to return the worktree to the user, whatever happened.
        restore = self._restore_stash(repo, ref, result)

        if restore != "clean":
            recovery = (
                f"your changes are safe in a stash. Inspect with "
                f"`git -C {repo} stash list` (entry: '{message}'); re-apply after "
                f"resolving with `git -C {repo} stash pop {ref}`"
            )
            if core_failed:
                # The mutation itself failed; keep its error, note the stash too.
                result.next_action = recovery
            else:
                result.verdict = Verdict.FAILED
                result.error = (
                    "the operation succeeded but restoring your local changes "
                    "conflicted"
                )
                result.next_action = recovery

    def _restore_stash(self, repo: Path, ref: str, result: OperationResult) -> str:
        """Apply then drop the stash. On conflict the stash is left in place."""
        apply = self.backend.stash_apply(repo, ref)
        result.commands.append(f"stash apply {ref}")
        if apply.ok:
            self.backend.stash_drop(repo, ref)
            result.commands.append(f"stash drop {ref}")
            result.restore = "clean"
            return "clean"
        text = f"{apply.stdout}\n{apply.stderr}".lower()
        result.restore = "conflict" if "conflict" in text else "error"
        return result.restore

    # -- per-operation executors --------------------------------------------------

    def _do_update(self, item: PlanItem, result: OperationResult) -> None:
        snap = item.snapshot
        upstream = snap.checkout.upstream_branch
        behind = snap.remote.behind_count or 0
        if not upstream or behind == 0:
            return  # already current; nothing to merge. Stays PROCEED -> NOOP.
        res = self.backend.merge_ff_only(item.spec.absolute_path, upstream)
        result.commands.append(f"merge --ff-only {upstream}")
        if not res.ok:
            result.verdict = Verdict.FAILED
            result.error = res.stderr.strip() or "fast-forward failed"

    def _do_switch_default(self, item: PlanItem, result: OperationResult) -> None:
        repo = item.spec.absolute_path
        remote = item.snapshot.identity.remote_name
        default = item.target
        if not default:
            result.verdict = Verdict.FAILED
            result.error = "no default branch resolved"
            return

        if item.snapshot.checkout.current_branch != default:
            if self.backend.branch_exists_local(repo, default):
                res = self.backend.switch(repo, default)
                result.commands.append(f"switch {default}")
            elif remote and self.backend.branch_exists_remote(repo, remote, default):
                res = self.backend.switch_create_tracking(
                    repo, default, f"{remote}/{default}"
                )
                result.commands.append(f"switch -c {default} --track {remote}/{default}")
            else:
                result.verdict = Verdict.FAILED
                result.error = f"default branch '{default}' not found locally or on remote"
                return
            if not res.ok:
                result.verdict = Verdict.FAILED
                result.error = res.stderr.strip() or "branch switch failed"
                return

        # Now on the default branch. Fast-forward it if it tracks a remote.
        upstream = self.backend.upstream(repo, default)
        if not upstream:
            result.next_action = f"{default} has no upstream; not fast-forwarded"
            return
        ab = self.backend.ahead_behind(repo, upstream)
        if ab is None:
            return
        ahead, behind = ab
        if ahead and behind:
            result.verdict = Verdict.FAILED
            result.error = (
                f"{default} has diverged from {upstream} (↑{ahead} ↓{behind}); "
                f"reconcile manually"
            )
            return
        if behind:
            res = self.backend.merge_ff_only(repo, upstream)
            result.commands.append(f"merge --ff-only {upstream}")
            if not res.ok:
                result.verdict = Verdict.FAILED
                result.error = res.stderr.strip() or "fast-forward failed"

    def _do_sync(self, item: PlanItem, result: OperationResult) -> None:
        repo = item.spec.absolute_path
        source = item.target
        if not source:
            result.verdict = Verdict.FAILED
            result.error = "no fetched remote default branch resolved"
            return

        if item.sync_rebase:
            res = self.backend.rebase(repo, source)
            result.commands.append(f"rebase {source}")
            conflict_command = "rebase --continue"
        else:
            res = self.backend.merge(repo, source)
            result.commands.append(f"merge --no-edit {source}")
            conflict_command = "merge --continue"

        if res.ok:
            return

        result.verdict = Verdict.FAILED
        active = self.backend.in_progress_operation(repo)
        if active in {"merge", "rebase"}:
            result.error = f"sync {active} conflict"
            result.next_action = (
                f"resolve conflicts, then run `git -C {repo} {conflict_command}` "
                f"or `git -C {repo} {active} --abort`"
            )
        else:
            result.error = res.stderr.strip() or f"{result.operation.value} failed"

    def _do_checkout(self, item: PlanItem, result: OperationResult) -> None:
        repo = item.spec.absolute_path
        remote = item.snapshot.identity.remote_name
        target = item.target
        if not target:
            result.verdict = Verdict.FAILED
            result.error = "no target branch resolved"
            return
        if item.snapshot.checkout.current_branch == target:
            return  # already there -> NOOP
        if self.backend.branch_exists_local(repo, target):
            res = self.backend.switch(repo, target)
            result.commands.append(f"switch {target}")
        elif remote and self.backend.branch_exists_remote(repo, remote, target):
            res = self.backend.switch_create_tracking(repo, target, f"{remote}/{target}")
            result.commands.append(f"switch -c {target} --track {remote}/{target}")
        else:
            result.verdict = Verdict.FAILED
            result.error = f"branch '{target}' not found"
            return
        if not res.ok:
            result.verdict = Verdict.FAILED
            result.error = res.stderr.strip() or "checkout failed"

    def _record_after(self, repo: Path, result: OperationResult) -> None:
        result.after_branch = self.backend.current_branch(repo)
        result.after_head = self.backend.head_sha(repo)
