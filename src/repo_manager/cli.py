"""Command-line interface.

Phase 1 ships two commands: `init` (discover a workspace and write a profile, no git
mutation) and `status` (read-only unless --fetch). Every command resolves a profile,
selects repositories, builds a report structure, and hands it to a renderer.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from . import __version__, config
from .config import (
    ConfigError,
    DiscoveryConfig,
    PolicyConfig,
    Profile,
    RepositoryConfig,
)
from .discovery import DEFAULT_EXCLUDES, discover_worktrees
from .git_backend import GitBackend
from .models import Operation, StatusReport
from .operations import OperationCoordinator
from .output import (
    render_operation_json,
    render_plan_table,
    render_status_json,
    render_status_table,
    render_summary_json,
    render_summary_markdown,
    render_summary_table,
)
from .repository_service import RepoSpec, RepositoryService
from .summaries import SummaryService

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Work safely with a directory tree of many independent Git repositories.",
)

console = Console()
err_console = Console(stderr=True)

# Exit codes (see the architecture doc).
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_SKIPPED = 3


def _fail(message: str, code: int = EXIT_USAGE) -> "typer.Exit":
    err_console.print(f"[red]error:[/red] {message}", highlight=False)
    return typer.Exit(code)


# -- profile / selection helpers ----------------------------------------------------


def _resolve_profile(name: Optional[str]) -> Profile:
    target = name or config.load_active_project()
    if not target:
        available = config.list_profiles()
        hint = (
            f" Known profiles: {', '.join(available)}."
            if available
            else " Run `repo-manager init <path>` first."
        )
        raise _fail(f"no project selected and no active project set.{hint}")
    try:
        return config.load_profile(target)
    except ConfigError as exc:
        raise _fail(str(exc))


def _select(
    profile: Profile, group: Optional[str], repo: Optional[str]
) -> list[RepositoryConfig]:
    if repo and group:
        raise _fail("pass at most one of --repo and --group")
    if repo:
        matches = [r for r in profile.repositories if r.name == repo or r.path == repo]
        if not matches:
            raise _fail(f"no repository named '{repo}' in profile '{profile.name}'")
        return matches
    if group:
        matches = profile.repos_in_group(group)
        if not matches:
            groups = ", ".join(profile.all_groups()) or "none defined"
            raise _fail(
                f"no repositories in group '{group}'. Groups: {groups}"
            )
        return matches
    return list(profile.repositories)


def _specs(profile: Profile, selected: list[RepositoryConfig]) -> list[RepoSpec]:
    specs = []
    for r in selected:
        abs_path = (profile.root / r.path).resolve()
        specs.append(
            RepoSpec(
                name=r.name,
                absolute_path=abs_path,
                relative_path=r.path,
                groups=r.groups,
                remote=r.remote or profile.default_remote,
                default_branch_override=r.default_branch,
            )
        )
    return specs


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# -- selection expressions ----------------------------------------------------------
#
# A comma-separated union of tokens, matched against each snapshot:
#   all | clean | <classification> | group:NAME | search:TEXT | name:NAME | <bare>


def _token_matches(snap, token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    low = token.lower()
    if low == "all":
        return True
    if low == "clean":
        return (
            not snap.worktree.is_dirty
            and snap.worktree.in_progress_operation.value == "none"
        )
    if low == snap.classification.value:
        return True
    if ":" in token:
        kind, _, value = token.partition(":")
        kind = kind.lower()
        if kind == "group":
            return value in snap.identity.groups
        if kind == "search":
            hay = f"{snap.name} {snap.identity.relative_path}".lower()
            return value.lower() in hay
        if kind == "name":
            return snap.name == value
        return False
    return snap.name == token or snap.identity.relative_path == token


def selection_matches(snap, expr: str | None) -> bool:
    if not expr:
        return True
    return any(_token_matches(snap, tok) for tok in expr.split(","))


def _summary_token_matches(rs, token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    low = token.lower()
    if low == "all":
        return True
    if low == "changed":
        return rs.commit_count > 0
    if low == "quiet":
        return rs.commit_count == 0
    if ":" in token:
        kind, _, value = token.partition(":")
        if kind.lower() == "search":
            return value.lower() in f"{rs.name} {rs.relative_path}".lower()
        if kind.lower() == "name":
            return rs.name == value
        return False
    return rs.name == token or rs.relative_path == token


def selection_matches_summary(rs, expr: str | None) -> bool:
    """Selection for summaries. Supports all/changed/quiet/name:/search:/<bare>.

    Group filtering happens earlier via --group, since a summary row carries no
    group membership."""
    if not expr:
        return True
    return any(_summary_token_matches(rs, tok) for tok in expr.split(","))


# -- commands ------------------------------------------------------------------------


@app.command()
def status(
    project: Optional[str] = typer.Option(None, help="Profile name; defaults to active."),
    group: Optional[str] = typer.Option(None, help="Limit to a repository group."),
    repo: Optional[str] = typer.Option(None, help="Limit to one repository."),
    fetch: bool = typer.Option(False, "--fetch", help="Fetch before computing remote state."),
    jobs: Optional[int] = typer.Option(None, help="Parallel workers for reads/fetches."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Show repository status. Read-only unless --fetch is passed."""
    profile = _resolve_profile(project)
    selected = _select(profile, group, repo)
    specs = _specs(profile, selected)

    missing = [s for s in specs if not (s.absolute_path / ".git").exists()]
    warnings: list[str] = [
        f"{s.relative_path}: not a git worktree on disk ({s.absolute_path})"
        for s in missing
    ]

    worker_count = jobs or profile.policy.jobs
    do_fetch = fetch or profile.policy.fetch_before_status
    service = RepositoryService(jobs=worker_count)
    snapshots = service.snapshot_all(
        [s for s in specs if s not in missing],
        fetch=do_fetch,
        fetch_timeout=float(profile.policy.fetch_timeout_seconds),
    )

    report = StatusReport(
        generated_at=_now_iso(),
        project_name=profile.name,
        project_root=str(profile.root),
        fetch_requested=do_fetch,
        repositories=snapshots,
        warnings=warnings,
    )

    if json_out:
        console.print_json(render_status_json(report))
    else:
        render_status_table(report, console)

    # Phase 1 status is read-only reporting; a missing worktree is the only failure.
    raise typer.Exit(EXIT_FAILURE if missing else EXIT_OK)


@app.command()
def init(
    path: Path = typer.Argument(..., help="Workspace root to scan."),
    name: Optional[str] = typer.Option(None, help="Profile name; defaults to the dir name."),
    max_depth: int = typer.Option(4, help="Maximum discovery depth."),
    remote: str = typer.Option("origin", help="Default remote name."),
    include_linked: bool = typer.Option(
        False, "--include-linked-worktrees", help="Include linked worktrees."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive selection; take all."),
    activate: bool = typer.Option(True, help="Set this profile as the active project."),
) -> None:
    """Discover a workspace and write a profile. Performs no git mutation."""
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise _fail(f"workspace root is not a directory: {root}")

    profile_name = name or root.name
    if config.profile_path(profile_name).exists() and not yes:
        if not Confirm.ask(
            f"Profile '{profile_name}' exists. Overwrite?", default=False
        ):
            raise typer.Exit(EXIT_OK)

    backend = GitBackend()
    result = discover_worktrees(
        root,
        max_depth=max_depth,
        exclude=DEFAULT_EXCLUDES,
        backend=backend,
        include_linked_worktrees=include_linked,
    )

    for w in result.warnings:
        err_console.print(f"[yellow]![/yellow] {w}", highlight=False)

    if not result.repositories:
        raise _fail(f"no git repositories found under {root}", EXIT_FAILURE)

    # Snapshot (no fetch) to record branch and inferred default per repo.
    service = RepositoryService(backend=backend, jobs=8)
    specs = [
        RepoSpec(
            name=("root" if r.relative_path == "." else Path(r.relative_path).name),
            absolute_path=r.absolute_path,
            relative_path=r.relative_path,
            groups=[],
            remote=remote,
        )
        for r in result.repositories
    ]
    snapshots = service.snapshot_all(specs, fetch=False)

    chosen = _init_select(snapshots, interactive=not yes and sys.stdin.isatty())
    if not chosen:
        raise _fail("no repositories selected", EXIT_OK)

    repositories = []
    used_names: set[str] = set()
    for snap in chosen:
        base = snap.name
        unique = base
        n = 2
        while unique in used_names:
            unique = f"{base}-{n}"
            n += 1
        used_names.add(unique)
        repositories.append(
            RepositoryConfig(
                path=snap.identity.relative_path,
                name=unique,
                groups=[],
                default_branch=snap.checkout.default_branch,
                remote=None,
            )
        )

    profile = Profile(
        name=profile_name,
        root=root,
        default_remote=remote,
        discovery=DiscoveryConfig(
            max_depth=max_depth,
            exclude=list(DEFAULT_EXCLUDES),
            include_linked_worktrees=include_linked,
        ),
        policy=PolicyConfig(),
        repositories=repositories,
    )

    console.print(f"\n[bold]Profile preview:[/bold] {profile_name}")
    _preview_profile(profile, snapshots)

    if not yes and sys.stdin.isatty():
        if not Confirm.ask("Save this profile?", default=True):
            raise typer.Exit(EXIT_OK)

    saved = config.save_profile(profile)
    if activate:
        config.set_active_project(profile_name)
    console.print(f"[green]saved[/green] {saved}")


def _init_select(snapshots, interactive: bool):
    """Return the chosen snapshots. Non-interactive default takes all."""
    if not interactive:
        return list(snapshots)

    table = Table(title="Discovered repositories", header_style="bold")
    table.add_column("#")
    table.add_column("Path")
    table.add_column("Branch")
    table.add_column("Default (source)")
    for i, snap in enumerate(snapshots, 1):
        default = snap.checkout.default_branch or "?"
        src = snap.checkout.default_branch_inference_source.value
        branch = snap.checkout.current_branch or "detached"
        table.add_row(str(i), snap.identity.relative_path, branch, f"{default} ({src})")
    console.print(table)

    raw = typer.prompt(
        "Select repositories (comma-separated numbers, or 'all')", default="all"
    )
    if raw.strip().lower() == "all":
        return list(snapshots)
    chosen = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            idx = int(tok)
        except ValueError:
            continue
        if 1 <= idx <= len(snapshots):
            chosen.append(snapshots[idx - 1])
    return chosen


def _preview_profile(profile: Profile, snapshots) -> None:
    table = Table(header_style="bold")
    table.add_column("Path")
    table.add_column("Name")
    table.add_column("Default branch (inference)")
    by_path = {s.identity.relative_path: s for s in snapshots}
    for r in profile.repositories:
        snap = by_path.get(r.path)
        src = (
            snap.checkout.default_branch_inference_source.value if snap else "unknown"
        )
        table.add_row(r.path, r.name, f"{r.default_branch or '?'} ({src})")
    console.print(table)


def _run_mutation(
    operation: Operation,
    project: Optional[str],
    group: Optional[str],
    repo: Optional[str],
    select: Optional[str],
    dry_run: bool,
    yes: bool,
    ignore_skips: bool,
    jobs: Optional[int],
    json_out: bool,
    checkout_branch: Optional[str] = None,
    stash: bool = False,
) -> None:
    profile = _resolve_profile(project)
    selected = _select(profile, group, repo)
    specs = _specs(profile, selected)

    missing = [s for s in specs if not (s.absolute_path / ".git").exists()]
    live = [s for s in specs if s not in missing]
    if not live:
        raise _fail("no repositories to operate on", EXIT_USAGE)

    worker_count = jobs or profile.policy.jobs
    coordinator = OperationCoordinator(backend=GitBackend(), jobs=worker_count)
    plan = coordinator.build_plan(
        live,
        operation,
        fetch_timeout=float(profile.policy.fetch_timeout_seconds),
        checkout_branch=checkout_branch,
        stash=stash,
    )

    if select:
        plan = [item for item in plan if selection_matches(item.snapshot, select)]
        if not plan:
            raise _fail(f"no repositories matched selection '{select}'", EXIT_USAGE)

    # Confirmation before any change. Dry-run never asks.
    would_change = [i for i in plan if i.decision.verdict.value == "proceed"]
    if not dry_run and would_change:
        if not yes:
            if not sys.stdin.isatty():
                raise _fail(
                    "refusing to mutate non-interactively without --yes "
                    "(or use --dry-run to preview)",
                    EXIT_USAGE,
                )
            _preview_plan(coordinator, plan, operation, profile)
            if not Confirm.ask(
                f"Proceed with {len(would_change)} repository operation(s)?",
                default=False,
            ):
                raise typer.Exit(EXIT_OK)

    report = coordinator.execute(
        plan,
        operation,
        project_name=profile.name,
        project_root=str(profile.root),
        dry_run=dry_run,
        ignore_skips=ignore_skips,
    )
    for s in missing:
        report.warnings.append(f"{s.relative_path}: not a git worktree on disk")

    if json_out:
        console.print_json(render_operation_json(report))
    else:
        render_plan_table(report, console)
        for w in report.warnings:
            err_console.print(f"[yellow]![/yellow] {w}", highlight=False)

    raise typer.Exit(EXIT_FAILURE if missing else report.exit_code())


def _preview_plan(coordinator, plan, operation, profile) -> None:
    from .models import OperationReport

    preview = OperationReport(
        generated_at=_now_iso(),
        project_name=profile.name,
        project_root=str(profile.root),
        operation=operation,
        dry_run=True,
        results=[
            coordinator._execute_one(item, operation, dry_run=True) for item in plan
        ],
    )
    render_plan_table(preview, console)


@app.command()
def update(
    project: Optional[str] = typer.Option(None, help="Profile name; defaults to active."),
    group: Optional[str] = typer.Option(None, help="Limit to a repository group."),
    repo: Optional[str] = typer.Option(None, help="Limit to one repository."),
    select: Optional[str] = typer.Option(
        None, help="Selection expression, e.g. 'ready', 'dirty', 'group:libraries'."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview; make no changes."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    ignore_skips: bool = typer.Option(
        False, "--ignore-skips", help="Exit 0 even when repos are safety-skipped."
    ),
    stash_and_update: bool = typer.Option(
        False,
        "--stash-and-update",
        help="For dirty repos: stash, fast-forward, then restore (conflict-safe).",
    ),
    jobs: Optional[int] = typer.Option(None, help="Parallel workers for fetches."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Fast-forward the current branch of eligible repositories. Never switches branches."""
    _run_mutation(
        Operation.UPDATE, project, group, repo, select, dry_run, yes, ignore_skips,
        jobs, json_out, stash=stash_and_update,
    )


@app.command(name="switch-default")
def switch_default(
    project: Optional[str] = typer.Option(None, help="Profile name; defaults to active."),
    group: Optional[str] = typer.Option(None, help="Limit to a repository group."),
    repo: Optional[str] = typer.Option(None, help="Limit to one repository."),
    select: Optional[str] = typer.Option(None, help="Selection expression."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview; make no changes."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    ignore_skips: bool = typer.Option(
        False, "--ignore-skips", help="Exit 0 even when repos are safety-skipped."
    ),
    stash_and_update: bool = typer.Option(
        False,
        "--stash-and-update",
        help="For dirty repos: stash, switch and fast-forward, then restore.",
    ),
    jobs: Optional[int] = typer.Option(None, help="Parallel workers for fetches."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Switch each eligible repository to its default branch and fast-forward it."""
    _run_mutation(
        Operation.SWITCH_DEFAULT, project, group, repo, select, dry_run, yes,
        ignore_skips, jobs, json_out, stash=stash_and_update,
    )


@app.command(name="sync", hidden=True)
def sync(
    project: Optional[str] = typer.Option(None),
    group: Optional[str] = typer.Option(None),
    repo: Optional[str] = typer.Option(None),
    select: Optional[str] = typer.Option(None),
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    ignore_skips: bool = typer.Option(False, "--ignore-skips"),
    stash_and_update: bool = typer.Option(False, "--stash-and-update"),
    jobs: Optional[int] = typer.Option(None),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Deprecated alias for switch-default."""
    err_console.print(
        "[yellow]note:[/yellow] 'sync' is deprecated; use 'switch-default'.",
        highlight=False,
    )
    _run_mutation(
        Operation.SWITCH_DEFAULT, project, group, repo, select, dry_run, yes,
        ignore_skips, jobs, json_out, stash=stash_and_update,
    )


@app.command()
def checkout(
    branch: str = typer.Option(
        ..., "--branch", help="Branch to check out, or 'default' for each repo's default."
    ),
    project: Optional[str] = typer.Option(None, help="Profile name; defaults to active."),
    group: Optional[str] = typer.Option(None, help="Limit to a repository group."),
    repo: Optional[str] = typer.Option(None, help="Limit to one repository."),
    select: Optional[str] = typer.Option(None, help="Selection expression."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview; make no changes."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    ignore_skips: bool = typer.Option(
        False, "--ignore-skips", help="Exit 0 even when repos are safety-skipped."
    ),
    jobs: Optional[int] = typer.Option(None, help="Parallel workers for fetches."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Check out a branch across selected repositories, creating tracking branches as needed."""
    _run_mutation(
        Operation.CHECKOUT, project, group, repo, select, dry_run, yes, ignore_skips,
        jobs, json_out, checkout_branch=branch,
    )


@app.command()
def summary(
    since: str = typer.Option(
        "7d", "--since", help="Window: 7d, 2w, 24h, an ISO date, or 'yesterday'."
    ),
    project: Optional[str] = typer.Option(None, help="Profile name; defaults to active."),
    group: Optional[str] = typer.Option(None, help="Limit to a repository group."),
    repo: Optional[str] = typer.Option(None, help="Limit to one repository."),
    select: Optional[str] = typer.Option(None, help="Selection expression."),
    markdown: Optional[Path] = typer.Option(
        None, "--markdown", help="Write a Markdown report to this path."
    ),
    jobs: Optional[int] = typer.Option(None, help="Parallel workers."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Summarize commits across repositories since a date. Read-only."""
    profile = _resolve_profile(project)
    selected = _select(profile, group, repo)
    specs = _specs(profile, selected)

    missing = [s for s in specs if not (s.absolute_path / ".git").exists()]
    live = [s for s in specs if s not in missing]
    if not live:
        raise _fail("no repositories to summarize", EXIT_USAGE)

    worker_count = jobs or profile.policy.jobs
    service = SummaryService(backend=GitBackend(), jobs=worker_count)
    report = service.build(
        live, since, project_name=profile.name, project_root=str(profile.root)
    )
    for s in missing:
        report.warnings.append(f"{s.relative_path}: not a git worktree on disk")

    if select:
        report.repositories = [
            r for r in report.repositories if selection_matches_summary(r, select)
        ]

    if markdown is not None:
        markdown.write_text(render_summary_markdown(report), encoding="utf-8")
        console.print(f"Wrote Markdown report to {markdown}", highlight=False)

    if json_out:
        console.print_json(render_summary_json(report))
    elif markdown is None:
        render_summary_table(report, console)

    for w in report.warnings:
        err_console.print(f"[yellow]![/yellow] {w}", highlight=False)

    raise typer.Exit(EXIT_FAILURE if missing else EXIT_OK)


@app.command()
def profiles() -> None:
    """List known profiles and mark the active one."""
    active = config.load_active_project()
    names = config.list_profiles()
    if not names:
        console.print("No profiles yet. Run `repo-manager init <path>`.")
        raise typer.Exit(EXIT_OK)
    for n in names:
        mark = " [green](active)[/green]" if n == active else ""
        console.print(f"- {n}{mark}")


@app.command()
def forget(
    name: str = typer.Argument(..., help="Profile to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Remove a saved profile. Deletes only its config file, never any repository."""
    path = config.profile_path(name)
    if not path.is_file():
        available = config.list_profiles()
        hint = f" Known profiles: {', '.join(available)}." if available else ""
        raise _fail(f"no profile named '{name}'.{hint}")

    # Load for display, but still allow removing an unloadable/corrupt profile.
    try:
        profile = config.load_profile(name)
        root_line = str(profile.root)
    except ConfigError:
        root_line = None

    console.print("This removes the profile config only. No repository is touched.")
    console.print(f"  Profile: {name}")
    console.print(f"  File:    {path}")
    if root_line:
        console.print(f"  Workspace (left untouched): {root_line}")

    if not yes:
        if not sys.stdin.isatty():
            raise _fail(
                "refusing to remove a profile non-interactively without --yes",
                EXIT_USAGE,
            )
        if not Confirm.ask(f"Remove profile '{name}'?", default=False):
            raise typer.Exit(EXIT_OK)

    active = config.load_active_project()
    config.delete_profile(name)
    if active == name:
        config.clear_active_project()
        console.print(f"Cleared the active project (was '{name}').")
    console.print(f"[green]Removed[/green] profile '{name}'.", highlight=False)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


_MANUAL = """\
[bold]repo-manager[/bold] — work safely across many independent Git repositories

[bold]Getting started[/bold]
  repo-manager init <path> [--name N] [--yes]   Scan a workspace, write a profile
  repo-manager status [--fetch]                 Show state of every repo
  repo-manager update --dry-run                 Preview a safe fast-forward update
  repo-manager profiles                         List profiles, mark the active one

[bold]Commands[/bold]
  init            Discover repositories and save a profile. No git mutation.
  status          Report each repository's state. Read-only unless --fetch.
  update          Fast-forward the current branch of eligible repos. Never switches.
  switch-default  Switch each eligible repo to its default branch and fast-forward.
  checkout        Check out a branch across repos, creating tracking branches as needed.
  summary         Summarize commits across repos since a date (read-only).
  profiles        List known profiles.
  forget          Remove a saved profile (config only; never a repository).
  version         Print the version.
  help            Show this manual.

[bold]Common options[/bold]
  --project N     Use profile N instead of the active one.
  --group G       Limit to repositories tagged with group G.
  --repo R        Limit to a single repository (by name or path).
  --select EXPR   Filter by expression: all, clean, dirty, ready, ahead,
                  group:NAME, search:TEXT, name:NAME (comma-separated union).
  --fetch         Fetch before computing ahead/behind (status only).
  --since W       Summary window: 7d, 2w, 24h, an ISO date, or 'yesterday'.
  --markdown P    Write a Markdown summary to path P (summary only).
  --stash-and-update  Dirty repos: stash, fast-forward, restore (update/switch-default).
  --dry-run       Preview a mutation; make no changes.
  --yes / -y      Skip the confirmation prompt (required non-interactively).
  --ignore-skips  Exit 0 even when repositories are safety-skipped.
  --json          Emit machine-readable JSON instead of a table.
  --jobs N        Parallel workers for reads and fetches (default 8).

[bold]Repository states[/bold]
  ready                 Clean, tracking, behind. The only default-updatable state.
  current               Clean and up to date with upstream.
  dirty                 Uncommitted staged / unstaged / untracked changes.
  in-progress           Mid rebase, merge, cherry-pick, revert, or bisect. Never touched.
  detached              HEAD is a commit, not a branch.
  ahead                 Local commits not on the remote. No fast-forward possible.
  diverged              Both sides moved. Needs explicit action.
  no-upstream           Branch has no tracking configuration.
  default-branch-unknown  Could not infer a default branch; blocks switch-default.
  ambiguous-remote      Several remotes, none named 'origin'. Configure one.
  remote-unavailable    Fetch failed. Remote state is unknown, never guessed.

[dim]Note: without --fetch, the ahead/behind column shows 'no-fetch' rather than
possibly-stale numbers. Stale remote refs are never presented as current state.[/dim]

[bold]Changes column[/bold]
  Ns staged   Nm modified   N? untracked   Nu unmerged

[bold]Exit codes[/bold]
  0  All good or already current.
  1  One or more repositories failed.
  2  Invalid command, configuration, or selection.
  3  One or more operations were safety-skipped.

[bold]Examples[/bold]
  repo-manager init ~/work/services --name services --yes
  repo-manager status --fetch
  repo-manager update --dry-run
  repo-manager update --group backend --yes
  repo-manager switch-default --select clean --dry-run
  repo-manager checkout --branch default --repo api
  repo-manager summary --since 7d --markdown standup.md

Run [bold]repo-manager <command> --help[/bold] for the full option list of any command.
"""


@app.command(name="help")
def help_() -> None:
    """Show a quick manual with commands, states, and examples."""
    console.print(_MANUAL, highlight=False)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
