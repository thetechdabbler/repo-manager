"""Find Git worktrees below a workspace root.

Discovery is broad but conservative: it walks the tree, honors an exclusion list and
a depth limit, detects linked worktrees (a `.git` file rather than directory), and
never recurses into a repository it has already found. It returns candidates; the
user selects which become part of a profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .git_backend import GitBackend

DEFAULT_EXCLUDES = (
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "outputs",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".tox",
)


@dataclass
class DiscoveredRepo:
    absolute_path: Path
    relative_path: str
    is_linked_worktree: bool = False


@dataclass
class DiscoveryResult:
    repositories: list[DiscoveredRepo] = field(default_factory=list)
    linked_worktrees: list[DiscoveredRepo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def discover_worktrees(
    root: Path,
    max_depth: int = 4,
    exclude: tuple[str, ...] | list[str] = DEFAULT_EXCLUDES,
    backend: GitBackend | None = None,
    include_linked_worktrees: bool = False,
) -> DiscoveryResult:
    backend = backend or GitBackend()
    root = root.resolve()
    exclude_set = set(exclude)
    result = DiscoveryResult()

    if not root.is_dir():
        result.warnings.append(f"workspace root is not a directory: {root}")
        return result

    def rel(p: Path) -> str:
        r = p.relative_to(root)
        return "." if str(r) == "." else str(r)

    # Iterative walk so we can prune found repositories and depth cleanly.
    # Each stack entry is (path, depth).
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()

        dot_git = current / ".git"
        if dot_git.exists():
            is_linked = backend.is_linked_worktree(current)
            if backend.is_worktree(current):
                repo = DiscoveredRepo(
                    absolute_path=current,
                    relative_path=rel(current),
                    is_linked_worktree=is_linked,
                )
                if is_linked and not include_linked_worktrees:
                    result.linked_worktrees.append(repo)
                    result.warnings.append(
                        f"skipped linked worktree (shares a .git dir): {rel(current)}"
                    )
                else:
                    result.repositories.append(repo)
                # Do not descend into a repository's own tree.
                continue

        if depth >= max_depth:
            continue

        try:
            children = sorted(
                (c for c in current.iterdir() if c.is_dir() and not c.is_symlink()),
                key=lambda c: c.name,
            )
        except (PermissionError, OSError) as exc:
            result.warnings.append(f"could not read {rel(current)}: {exc}")
            continue

        for child in children:
            if child.name in exclude_set:
                continue
            stack.append((child, depth + 1))

    result.repositories.sort(key=lambda r: r.relative_path)
    result.linked_worktrees.sort(key=lambda r: r.relative_path)
    return result
