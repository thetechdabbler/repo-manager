"""Serializable domain model.

Every structure here is JSON-clean and versioned. The terminal table is one renderer
over these objects, not the primary representation. This is what lets a later
consumer (a report generator, an AI layer, another tool) read structured data
instead of scraping rendered output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

SCHEMA_VERSION = 1

DEFAULT_BRANCH_CANDIDATES = ("main", "master", "dev", "develop")


class Classification(str, Enum):
    """The single most notable fact about a repository's state.

    This is a summary for humans, not an operation decision. Whether a given
    operation may proceed is the policy engine's job (phase 2), because the answer
    differs per operation: `default-branch-unknown` blocks `switch-default` but not
    `update`.
    """

    READY = "ready"
    CURRENT = "current"
    DIRTY = "dirty"
    IN_PROGRESS = "in-progress"
    DETACHED = "detached"
    NO_UPSTREAM = "no-upstream"
    DEFAULT_BRANCH_UNKNOWN = "default-branch-unknown"
    AMBIGUOUS_REMOTE = "ambiguous-remote"
    AHEAD = "ahead"
    DIVERGED = "diverged"
    REMOTE_UNAVAILABLE = "remote-unavailable"


class Relationship(str, Enum):
    CURRENT = "current"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"


class FetchResult(str, Enum):
    """Provenance of the remote-tracking references used to compute relationship.

    `SKIPPED` means the refs are whatever the last fetch left behind and their
    staleness is unknown. Renderers must label that case rather than implying the
    numbers are current.
    """

    OK = "ok"
    TIMEOUT = "timeout"
    AUTH_REQUIRED = "auth-required"
    UNREACHABLE = "unreachable"
    SKIPPED = "skipped"


class InProgressOperation(str, Enum):
    NONE = "none"
    REBASE = "rebase"
    MERGE = "merge"
    CHERRY_PICK = "cherry-pick"
    REVERT = "revert"
    BISECT = "bisect"


class InferenceSource(str, Enum):
    PROFILE_OVERRIDE = "profile-override"
    REMOTE_HEAD = "remote-head"
    UPSTREAM_CANDIDATE = "upstream-candidate"
    LOCAL_CANDIDATE = "local-candidate"
    AMBIGUOUS = "ambiguous"


def _enum_value(v: Any) -> Any:
    return v.value if isinstance(v, Enum) else v


@dataclass
class Identity:
    name: str
    absolute_path: str
    relative_path: str
    groups: list[str] = field(default_factory=list)
    remote_name: str | None = None
    origin_url_redacted: str | None = None
    is_linked_worktree: bool = False


@dataclass
class Checkout:
    head_sha: str | None = None
    current_branch: str | None = None
    detached_sha: str | None = None
    upstream_branch: str | None = None
    default_branch: str | None = None
    default_branch_inference_source: InferenceSource = InferenceSource.AMBIGUOUS

    @property
    def is_detached(self) -> bool:
        return self.current_branch is None


@dataclass
class Worktree:
    staged_count: int = 0
    unstaged_count: int = 0
    untracked_count: int = 0
    unmerged_count: int = 0
    in_progress_operation: InProgressOperation = InProgressOperation.NONE

    @property
    def is_dirty(self) -> bool:
        return bool(
            self.staged_count
            or self.unstaged_count
            or self.untracked_count
            or self.unmerged_count
        )

    @property
    def total_changes(self) -> int:
        return (
            self.staged_count
            + self.unstaged_count
            + self.untracked_count
            + self.unmerged_count
        )


@dataclass
class RemoteState:
    fetch_attempted: bool = False
    fetch_result: FetchResult = FetchResult.SKIPPED
    fetch_error: str | None = None
    ahead_count: int | None = None
    behind_count: int | None = None
    relationship: Relationship = Relationship.UNKNOWN

    @property
    def data_is_current(self) -> bool:
        """True only when a fetch succeeded during this run."""
        return self.fetch_attempted and self.fetch_result is FetchResult.OK


@dataclass
class Commit:
    sha: str
    short_sha: str
    subject: str
    author_name: str
    committed_at: str


@dataclass
class RepositorySnapshot:
    identity: Identity
    checkout: Checkout
    worktree: Worktree
    remote: RemoteState
    classification: Classification
    last_commit: Commit | None = None
    warnings: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    @property
    def name(self) -> str:
        return self.identity.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity": {
                "name": self.identity.name,
                "absolute_path": self.identity.absolute_path,
                "relative_path": self.identity.relative_path,
                "groups": list(self.identity.groups),
                "remote_name": self.identity.remote_name,
                "origin_url_redacted": self.identity.origin_url_redacted,
                "is_linked_worktree": self.identity.is_linked_worktree,
            },
            "checkout": {
                "head_sha": self.checkout.head_sha,
                "current_branch": self.checkout.current_branch,
                "detached_sha": self.checkout.detached_sha,
                "is_detached": self.checkout.is_detached,
                "upstream_branch": self.checkout.upstream_branch,
                "default_branch": self.checkout.default_branch,
                "default_branch_inference_source": _enum_value(
                    self.checkout.default_branch_inference_source
                ),
            },
            "worktree": {
                "staged_count": self.worktree.staged_count,
                "unstaged_count": self.worktree.unstaged_count,
                "untracked_count": self.worktree.untracked_count,
                "unmerged_count": self.worktree.unmerged_count,
                "is_dirty": self.worktree.is_dirty,
                "total_changes": self.worktree.total_changes,
                "in_progress_operation": _enum_value(
                    self.worktree.in_progress_operation
                ),
            },
            "remote": {
                "fetch_attempted": self.remote.fetch_attempted,
                "fetch_result": _enum_value(self.remote.fetch_result),
                "fetch_error": self.remote.fetch_error,
                "ahead_count": self.remote.ahead_count,
                "behind_count": self.remote.behind_count,
                "relationship": _enum_value(self.remote.relationship),
                "data_is_current": self.remote.data_is_current,
            },
            "classification": _enum_value(self.classification),
            "last_commit": (
                {
                    "sha": self.last_commit.sha,
                    "short_sha": self.last_commit.short_sha,
                    "subject": self.last_commit.subject,
                    "author_name": self.last_commit.author_name,
                    "committed_at": self.last_commit.committed_at,
                }
                if self.last_commit
                else None
            ),
            "warnings": list(self.warnings),
        }


class Operation(str, Enum):
    UPDATE = "update"
    SWITCH_DEFAULT = "switch-default"
    CHECKOUT = "checkout"


class Verdict(str, Enum):
    """Outcome of one repository within an operation.

    PROCEED is the pre-execution verdict from the policy engine; the executor then
    resolves it to one of the terminal outcomes.
    """

    PROCEED = "proceed"
    SKIPPED = "skipped"
    UPDATED = "updated"
    NOOP = "noop"
    FAILED = "failed"


@dataclass
class OperationResult:
    """What happened to one repository during one operation."""

    name: str
    relative_path: str
    operation: Operation
    verdict: Verdict
    planned: str = ""
    reason: str = ""
    next_action: str | None = None
    before_branch: str | None = None
    before_head: str | None = None
    after_branch: str | None = None
    after_head: str | None = None
    commands: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def changed(self) -> bool:
        return self.before_head != self.after_head or (
            self.before_branch != self.after_branch
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "relative_path": self.relative_path,
            "operation": _enum_value(self.operation),
            "verdict": _enum_value(self.verdict),
            "planned": self.planned,
            "reason": self.reason,
            "next_action": self.next_action,
            "before": {"branch": self.before_branch, "head": self.before_head},
            "after": {"branch": self.after_branch, "head": self.after_head},
            "changed": self.changed,
            "commands": list(self.commands),
            "error": self.error,
        }


@dataclass
class OperationReport:
    """The complete result of one mutation command."""

    generated_at: str
    project_name: str
    project_root: str
    operation: Operation
    dry_run: bool
    ignore_skips: bool = False
    results: list[OperationResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def exit_code(self) -> int:
        """Highest-severity applicable code: failure (1) beats skip (3) beats ok."""
        has_failure = any(r.verdict is Verdict.FAILED for r in self.results)
        has_skip = any(r.verdict is Verdict.SKIPPED for r in self.results)
        if has_failure:
            return 1
        if has_skip and not self.ignore_skips:
            return 3
        return 0

    def totals(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.results:
            key = _enum_value(r.verdict)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "project": {"name": self.project_name, "root": self.project_root},
            "operation": _enum_value(self.operation),
            "dry_run": self.dry_run,
            "exit_code": self.exit_code(),
            "totals": self.totals(),
            "results": [r.to_dict() for r in self.results],
            "warnings": list(self.warnings),
        }


@dataclass
class StatusReport:
    """The complete result of one `status` invocation."""

    generated_at: str
    project_name: str
    project_root: str
    fetch_requested: bool
    repositories: list[RepositorySnapshot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "project": {"name": self.project_name, "root": self.project_root},
            "fetch_requested": self.fetch_requested,
            "totals": self.totals(),
            "repositories": [r.to_dict() for r in self.repositories],
            "warnings": list(self.warnings),
        }

    def totals(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.repositories:
            key = _enum_value(r.classification)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))
