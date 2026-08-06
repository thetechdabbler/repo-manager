"""Assemble RepositorySnapshots and classify them.

This layer turns raw git observations into the typed model. Classification here is a
human-facing summary, not a per-operation gate; the policy engine (phase 2) decides
what may run. The rules are driven entirely by the fixture matrix in the tests.

Parallelism lives here: fetches and per-repo reads run in a thread pool, but results
are always returned in the caller's requested order so rendering is deterministic.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .git_backend import GitBackend
from .models import (
    DEFAULT_BRANCH_CANDIDATES,
    Checkout,
    Classification,
    Commit,
    FetchResult,
    Identity,
    InferenceSource,
    InProgressOperation,
    Relationship,
    RemoteState,
    RepositorySnapshot,
    Worktree,
)


@dataclass
class RepoSpec:
    """Minimal instruction for building one snapshot."""

    name: str
    absolute_path: Path
    relative_path: str
    groups: list[str]
    remote: str = "origin"
    default_branch_override: str | None = None


class RepositoryService:
    def __init__(self, backend: GitBackend | None = None, jobs: int = 8):
        self.backend = backend or GitBackend()
        self.jobs = max(1, jobs)

    # -- public API ---------------------------------------------------------------

    def snapshot_all(
        self,
        specs: list[RepoSpec],
        fetch: bool = False,
        fetch_timeout: float = 30.0,
    ) -> list[RepositorySnapshot]:
        """Snapshots in the same order as `specs`, built concurrently."""
        if not specs:
            return []
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            futures = [
                pool.submit(self.snapshot_one, spec, fetch, fetch_timeout)
                for spec in specs
            ]
            return [f.result() for f in futures]

    def snapshot_one(
        self,
        spec: RepoSpec,
        fetch: bool = False,
        fetch_timeout: float = 30.0,
    ) -> RepositorySnapshot:
        g = self.backend
        repo = spec.absolute_path
        warnings: list[str] = []

        remote = self._resolve_remote(repo, spec.remote, warnings)

        identity = Identity(
            name=spec.name,
            absolute_path=str(repo),
            relative_path=spec.relative_path,
            groups=list(spec.groups),
            remote_name=remote,
            origin_url_redacted=g.remote_url(repo, remote) if remote else None,
            is_linked_worktree=g.is_linked_worktree(repo),
        )

        in_progress = InProgressOperation(g.in_progress_operation(repo))
        staged, unstaged, untracked, unmerged = g.status_counts(repo)
        worktree = Worktree(
            staged_count=staged,
            unstaged_count=unstaged,
            untracked_count=untracked,
            unmerged_count=unmerged,
            in_progress_operation=in_progress,
        )

        branch = g.current_branch(repo)
        head = g.head_sha(repo)
        upstream = g.upstream(repo, branch) if branch else None
        default_branch, source = self._infer_default_branch(
            repo, remote, spec.default_branch_override, branch, upstream
        )
        checkout = Checkout(
            head_sha=head,
            current_branch=branch,
            detached_sha=head if branch is None else None,
            upstream_branch=upstream,
            default_branch=default_branch,
            default_branch_inference_source=source,
        )

        remote_state = self._remote_state(
            repo, remote, upstream, fetch, fetch_timeout, warnings
        )

        last = g.last_commit(repo)
        last_commit = (
            Commit(
                sha=last[0],
                short_sha=last[1],
                subject=last[2],
                author_name=last[3],
                committed_at=last[4],
            )
            if last
            else None
        )

        classification = self._classify(
            worktree=worktree,
            checkout=checkout,
            remote_state=remote_state,
            has_remote=remote is not None,
            remote_ambiguous=(remote is None and len(g.remotes(repo)) > 1),
        )

        return RepositorySnapshot(
            identity=identity,
            checkout=checkout,
            worktree=worktree,
            remote=remote_state,
            classification=classification,
            last_commit=last_commit,
            warnings=warnings,
        )

    # -- remote resolution --------------------------------------------------------

    def _resolve_remote(
        self, repo: Path, preferred: str, warnings: list[str]
    ) -> str | None:
        remotes = self.backend.remotes(repo)
        if preferred in remotes:
            return preferred
        if not remotes:
            warnings.append("no git remote configured")
            return None
        if len(remotes) == 1:
            chosen = remotes[0]
            warnings.append(
                f"remote '{preferred}' not found; using the only remote '{chosen}'"
            )
            return chosen
        # Multiple remotes, none named as preferred: ambiguous, needs config.
        warnings.append(
            f"remote '{preferred}' not found among multiple remotes "
            f"({', '.join(remotes)}); configure one in the profile"
        )
        return None

    # -- default-branch inference -------------------------------------------------

    def _infer_default_branch(
        self,
        repo: Path,
        remote: str | None,
        override: str | None,
        current_branch: str | None,
        upstream: str | None,
    ) -> tuple[str | None, InferenceSource]:
        g = self.backend

        # 1. Profile override wins outright.
        if override:
            return override, InferenceSource.PROFILE_OVERRIDE

        # 2. refs/remotes/<remote>/HEAD is a proven answer.
        if remote:
            head_branch = g.remote_head_branch(repo, remote)
            if head_branch:
                return head_branch, InferenceSource.REMOTE_HEAD

        # 3. Current branch's upstream, when it is a common default candidate.
        if upstream and remote and upstream.startswith(f"{remote}/"):
            up_branch = upstream[len(remote) + 1 :]
            if up_branch in DEFAULT_BRANCH_CANDIDATES:
                return up_branch, InferenceSource.UPSTREAM_CANDIDATE

        # 4. First existing local or remote candidate, in priority order.
        for candidate in DEFAULT_BRANCH_CANDIDATES:
            if g.ref_exists(repo, f"refs/heads/{candidate}"):
                return candidate, InferenceSource.LOCAL_CANDIDATE
            if remote and g.ref_exists(repo, f"refs/remotes/{remote}/{candidate}"):
                return candidate, InferenceSource.LOCAL_CANDIDATE

        # 5. No proven or heuristic answer.
        return None, InferenceSource.AMBIGUOUS

    # -- remote state -------------------------------------------------------------

    def _remote_state(
        self,
        repo: Path,
        remote: str | None,
        upstream: str | None,
        fetch: bool,
        fetch_timeout: float,
        warnings: list[str],
    ) -> RemoteState:
        g = self.backend
        state = RemoteState()

        if fetch and remote:
            state.fetch_attempted = True
            res = g.fetch(repo, remote, prune=True, timeout=fetch_timeout)
            if res.timed_out:
                state.fetch_result = FetchResult.TIMEOUT
                state.fetch_error = "fetch timed out"
                warnings.append(f"fetch from '{remote}' timed out")
            elif res.ok:
                state.fetch_result = FetchResult.OK
            else:
                stderr = (res.stderr or "").lower()
                if any(
                    s in stderr
                    for s in ("authentication", "permission denied", "could not read")
                ):
                    state.fetch_result = FetchResult.AUTH_REQUIRED
                else:
                    state.fetch_result = FetchResult.UNREACHABLE
                state.fetch_error = res.stderr.strip() or "fetch failed"
                warnings.append(f"fetch from '{remote}' failed")

        # Relationship is only trustworthy when we have an upstream AND, if the
        # remote is unreachable, we must not present stale numbers as current.
        if not upstream:
            state.relationship = Relationship.UNKNOWN
            return state

        fetch_failed = state.fetch_attempted and state.fetch_result is not FetchResult.OK
        if fetch_failed:
            # We tried and could not reach the remote this run: refuse to guess.
            state.relationship = Relationship.UNKNOWN
            return state

        ab = g.ahead_behind(repo, upstream)
        if ab is None:
            state.relationship = Relationship.UNKNOWN
            return state

        ahead, behind = ab
        state.ahead_count = ahead
        state.behind_count = behind
        if ahead and behind:
            state.relationship = Relationship.DIVERGED
        elif ahead:
            state.relationship = Relationship.AHEAD
        elif behind:
            state.relationship = Relationship.BEHIND
        else:
            state.relationship = Relationship.CURRENT
        return state

    # -- classification -----------------------------------------------------------

    def _classify(
        self,
        worktree: Worktree,
        checkout: Checkout,
        remote_state: RemoteState,
        has_remote: bool,
        remote_ambiguous: bool,
    ) -> Classification:
        # Order matters: earlier conditions dominate later ones.

        # 1. An in-progress operation is the most important fact; never mutate.
        if worktree.in_progress_operation is not InProgressOperation.NONE:
            return Classification.IN_PROGRESS

        # 2. Detached HEAD, regardless of dirtiness of tracked files.
        if checkout.is_detached:
            return Classification.DETACHED

        # 3. Local work present.
        if worktree.is_dirty:
            return Classification.DIRTY

        # 4. Remote identity problems.
        if remote_ambiguous:
            return Classification.AMBIGUOUS_REMOTE

        # 5. No tracking branch to compare against.
        if checkout.upstream_branch is None:
            return Classification.NO_UPSTREAM

        # 6. We could not confirm current remote state this run.
        if remote_state.relationship is Relationship.UNKNOWN:
            if remote_state.fetch_attempted and (
                remote_state.fetch_result is not FetchResult.OK
            ):
                return Classification.REMOTE_UNAVAILABLE
            if has_remote and remote_state.fetch_result is FetchResult.UNREACHABLE:
                return Classification.REMOTE_UNAVAILABLE
            # Upstream exists and refs are readable but relationship unknown:
            # treat unreachable remote (bad URL) as unavailable when detectable.
            return Classification.REMOTE_UNAVAILABLE

        # 7. Clean, tracking, known relationship.
        if remote_state.relationship is Relationship.DIVERGED:
            return Classification.DIVERGED
        if remote_state.relationship is Relationship.AHEAD:
            return Classification.AHEAD
        if remote_state.relationship is Relationship.BEHIND:
            # Default-branch inference does not gate update, only switch-default,
            # so a behind repo with an unknown default is still update-ready.
            return Classification.READY

        # 8. Clean and current. Surface an unresolved default branch only here,
        # where there is nothing more urgent to report. It gates switch-default.
        if checkout.default_branch is None:
            return Classification.DEFAULT_BRANCH_UNKNOWN
        return Classification.CURRENT
