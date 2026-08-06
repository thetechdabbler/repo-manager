"""Operation coordinator.

Builds a plan (parallel fetch + snapshot + policy decision per repo), then executes
approved repositories serially. Every mutation is a local fast-forward or branch
switch against refs a prior fetch already updated, so execution never blocks on the
network. The result is an OperationReport, which is what the renderers consume.
"""

from __future__ import annotations

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
    ) -> list[PlanItem]:
        """Fetch + snapshot in parallel, then decide per repo. Read-only."""
        # All three operations need current remote state to decide safely.
        snapshots = self.service.snapshot_all(
            specs, fetch=True, fetch_timeout=fetch_timeout
        )
        by_path = {s.identity.relative_path: s for s in snapshots}

        plan: list[PlanItem] = []
        for spec in specs:
            snap = by_path[spec.relative_path]
            if operation is Operation.UPDATE:
                decision = policy.decide_update(snap)
                target = None
            elif operation is Operation.SWITCH_DEFAULT:
                decision = policy.decide_switch_default(snap)
                target = snap.checkout.default_branch
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
                decision = policy.decide_checkout(
                    snap, target, exists_local, exists_remote
                )
            plan.append(PlanItem(spec=spec, snapshot=snap, decision=decision, target=target))
        return plan

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
        )

        if dec.verdict is Verdict.SKIPPED:
            return result
        if dry_run:
            # PROCEED verdict is preserved: "would proceed".
            return result

        if operation is Operation.UPDATE:
            self._do_update(item, result)
        elif operation is Operation.SWITCH_DEFAULT:
            self._do_switch_default(item, result)
        else:
            self._do_checkout(item, result)

        self._record_after(item.spec.absolute_path, result)
        if result.verdict is Verdict.PROCEED:
            result.verdict = Verdict.UPDATED if result.changed else Verdict.NOOP
        return result

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
