"""Deterministic, date-based commit summaries.

Read-only. For each selected repository it collects the commits on the current
branch since a cutoff, with per-commit file statistics, then aggregates. There is no
stored watermark: the range is always derived from `--since`, so nothing can go stale
after a rebase.

Date rule: selection is by committer date (git's `--since` default), first-parent, on
the currently checked-out branch (HEAD).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .git_backend import GitBackend
from .models import CommitSummary, RepositorySummary, SummaryReport
from .repository_service import RepoSpec

# Duration shorthand: 7d, 2w, 24h. Anything else is passed to git as an approxidate
# (ISO dates, "yesterday", "2 weeks ago", ...).
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([hdw])\s*$", re.IGNORECASE)
_UNIT_WORD = {"h": "hours", "d": "days", "w": "weeks"}

# JIRA-style ticket keys: ABC-123.
_TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
# Conventional-commit type prefix: feat: / fix(scope)!: ...
_CONVENTIONAL_RE = re.compile(r"^([a-z]+)(\([^)]*\))?!?:\s")


def resolve_since(value: str) -> str:
    """Turn a user `--since` value into a git-accepted date expression."""
    m = _DURATION_RE.match(value)
    if m:
        return f"{m.group(1)} {_UNIT_WORD[m.group(2).lower()]} ago"
    return value.strip()


def _top_directory(path: str) -> str:
    # numstat may render a rename as "a/{old => new}/f"; take the first real segment.
    path = path.replace("\\", "/")
    head = path.split("/", 1)[0]
    return head if "/" in path else "."


def _parse_commit_meta(subject: str) -> tuple[str | None, list[str]]:
    ctype = None
    cm = _CONVENTIONAL_RE.match(subject)
    if cm:
        ctype = cm.group(1)
    refs = sorted(set(_TICKET_RE.findall(subject)))
    return ctype, refs


def parse_log(raw: str) -> list[CommitSummary]:
    """Parse `git log --numstat` output produced by GitBackend.log_since."""
    fs = GitBackend.LOG_FS
    commits: list[CommitSummary] = []
    current: CommitSummary | None = None

    for line in raw.split("\n"):
        if line.startswith(fs):
            parts = line.split(fs)
            # parts[0] is empty (leading separator); then sha, author, date, subject.
            if len(parts) >= 5:
                sha, author, date, subject = parts[1], parts[2], parts[3], parts[4]
                ctype, refs = _parse_commit_meta(subject)
                current = CommitSummary(
                    sha=sha,
                    short_sha=sha[:9],
                    author=author,
                    date=date,
                    subject=subject,
                    commit_type=ctype,
                    references=refs,
                )
                commits.append(current)
            continue
        if not line.strip() or current is None:
            continue
        # numstat line: "<ins>\t<del>\t<path>"; binary shows "-\t-\t<path>".
        cols = line.split("\t")
        if len(cols) != 3:
            continue
        add, dele, _path = cols
        current.files_changed += 1
        if add.isdigit():
            current.insertions += int(add)
        if dele.isdigit():
            current.deletions += int(dele)
    return commits


def _paths_of(raw: str) -> list[str]:
    """All changed file paths across the range (for dedup and top-dir stats)."""
    fs = GitBackend.LOG_FS
    paths: list[str] = []
    for line in raw.split("\n"):
        if line.startswith(fs) or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) == 3:
            paths.append(cols[2])
    return paths


class SummaryService:
    def __init__(self, backend: GitBackend | None = None, jobs: int = 8):
        self.backend = backend or GitBackend()
        self.jobs = max(1, jobs)

    def summarize_one(self, spec: RepoSpec, requested_since: str) -> RepositorySummary:
        resolved = resolve_since(requested_since)
        branch = self.backend.current_branch(spec.absolute_path)
        summary = RepositorySummary(
            name=spec.name,
            relative_path=spec.relative_path,
            branch=branch,
            requested_since=requested_since,
            resolved_since=resolved,
        )

        res = self.backend.log_since(spec.absolute_path, resolved)
        if not res.ok:
            summary.warnings.append(res.stderr.strip() or "git log failed")
            return summary

        commits = parse_log(res.stdout)
        summary.commits = commits
        summary.total_insertions = sum(c.insertions for c in commits)
        summary.total_deletions = sum(c.deletions for c in commits)

        paths = _paths_of(res.stdout)
        summary.files_changed = len(set(paths))
        # Top directories ordered by number of changed files, then name.
        dir_counts: dict[str, int] = {}
        for p in paths:
            d = _top_directory(p)
            dir_counts[d] = dir_counts.get(d, 0) + 1
        summary.top_directories = [
            d for d, _ in sorted(dir_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        refs: set[str] = set()
        for c in commits:
            refs.update(c.references)
        summary.references = sorted(refs)
        return summary

    def build(
        self,
        specs: list[RepoSpec],
        requested_since: str,
        project_name: str,
        project_root: str,
    ) -> SummaryReport:
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            summaries = list(
                pool.map(lambda s: self.summarize_one(s, requested_since), specs)
            )
        # Preserve the input (config) order regardless of completion order.
        by_path = {s.relative_path: s for s in summaries}
        ordered = [by_path[s.relative_path] for s in specs]

        return SummaryReport(
            generated_at=datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            project_name=project_name,
            project_root=project_root,
            since=requested_since,
            repositories=ordered,
        )
