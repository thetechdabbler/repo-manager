"""Command-line interface.

Phase 1 ships two commands: `init` (discover a workspace and write a profile, no git
mutation) and `status` (read-only unless --fetch). Every command resolves a profile,
selects repositories, builds a report structure, and hands it to a renderer.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

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
from .models import Operation, ProjectStatus, ProjectsStatusReport, StatusReport
from .models import OperationResult, Verdict
from .operations import InteractivePlanItem, OperationCoordinator
from .output import (
    render_operation_json,
    render_plan_table,
    render_projects_status_json,
    render_projects_status_table,
    render_status_json,
    render_status_table,
    render_summary_json,
    render_summary_markdown,
    render_summary_table,
)
from .repository_service import RepoSpec, RepositoryService
from .summaries import SummaryService
from .interactive import InteractiveTerminalError, RadioOption, radio_select

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
            else " Run `repo init <path>` first."
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


def _spec_matches_selection(spec: RepoSpec, expr: str | None) -> bool:
    """Apply selection expressions to configured repositories without snapshots."""
    if not expr:
        return True
    for token in expr.split(","):
        token = token.strip()
        low = token.lower()
        if low == "all":
            return True
        if ":" in token:
            kind, _, value = token.partition(":")
            if kind.lower() == "group" and value in spec.groups:
                return True
            if kind.lower() == "search" and value.lower() in (
                f"{spec.name} {spec.relative_path}".lower()
            ):
                return True
            if kind.lower() == "name" and spec.name == value:
                return True
        elif token == spec.name or token == spec.relative_path:
            return True
    return False


# -- commands ------------------------------------------------------------------------


@app.command(name="project-status", hidden=True)
def project_status(
    project: str = typer.Option(..., "--project", hidden=True),
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
def status(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Show a local-only overview of every saved project."""
    rows: list[ProjectStatus] = []
    warnings: list[str] = []
    for name in config.list_profiles():
        try:
            profile = config.load_profile(name)
        except ConfigError as exc:
            warnings.append(f"{name}: {exc}")
            continue
        specs = _specs(profile, list(profile.repositories))
        missing = [spec for spec in specs if not (spec.absolute_path / ".git").exists()]
        live = [spec for spec in specs if spec not in missing]
        snapshots = RepositoryService(jobs=profile.policy.jobs).snapshot_all(
            live, fetch=False
        )
        in_progress = sum(
            snap.worktree.in_progress_operation.value != "none" for snap in snapshots
        )
        dirty = sum(
            snap.worktree.is_dirty
            and snap.worktree.in_progress_operation.value == "none"
            for snap in snapshots
        )
        clean = len(snapshots) - in_progress - dirty
        rows.append(
            ProjectStatus(
                name=profile.name,
                root=str(profile.root),
                repositories_total=len(specs),
                clean_count=clean,
                dirty_count=dirty,
                in_progress_count=in_progress,
                missing_worktree_count=len(missing),
            )
        )
    report = ProjectsStatusReport(generated_at=_now_iso(), projects=rows, warnings=warnings)
    if json_out:
        console.print_json(render_projects_status_json(report))
    else:
        render_projects_status_table(report, console)
    raise typer.Exit(EXIT_FAILURE if warnings else EXIT_OK)


@app.command()
def init(
    path: Path = typer.Argument(..., help="Workspace root to scan."),
    name: Optional[str] = typer.Option(None, help="Profile name; defaults to the dir name."),
    max_depth: int = typer.Option(4, help="Maximum discovery depth."),
    remote: str = typer.Option("origin", help="Default remote name."),
    include_linked: bool = typer.Option(
        False, "--include-linked-worktrees", help="Include linked worktrees."
    ),
    include_nested: bool = typer.Option(
        False,
        "--include-nested",
        help="Descend into repos to find independent clones nested in an umbrella repo.",
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
        descend_into_repositories=include_nested,
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
            descend_into_repositories=include_nested,
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
    sync_rebase: bool = False,
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
        sync_rebase=sync_rebase,
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


@dataclass
class InteractiveOutcome:
    relative_path: str
    action: str
    result: OperationResult | None = None
    detail: str = ""


def _interactive_choices(
    item: InteractivePlanItem,
) -> list[RadioOption]:
    choices = [RadioOption("skip", "Skip")]
    labels = {
        "update": "Update current branch",
        "sync": "Sync default branch into current branch",
        "default": "Switch to default branch and update",
        "stash-sync": "Stash local changes, then sync and restore",
        "discard-sync": "Discard local changes, then sync",
    }
    for choice in ("update", "sync", "default", "stash-sync", "discard-sync"):
        plan = item.plans.get(choice)
        if plan is None:
            continue
        if plan.decision.verdict is Verdict.PROCEED:
            choices.append(RadioOption(choice, labels[choice]))
    return choices


def _interactive_repo_info(item: InteractivePlanItem) -> None:
    snap = item.snapshot
    branch = snap.checkout.current_branch or (
        f"detached@{(snap.checkout.detached_sha or '')[:8]}"
    )
    if not snap.remote.data_is_current:
        ahead_behind = "unreachable" if snap.remote.fetch_attempted else "no-fetch"
    else:
        ahead = snap.remote.ahead_count or 0
        behind = snap.remote.behind_count or 0
        ahead_behind = "=" if not ahead and not behind else f"↑{ahead} ↓{behind}"
    worktree = snap.worktree
    changes = "clean" if not worktree.is_dirty else (
        f"{worktree.total_changes} local change(s)"
    )
    last_commit = (
        f"{snap.last_commit.short_sha} {snap.last_commit.subject}"
        if snap.last_commit
        else "-"
    )
    console.print()
    heading = Text()
    heading.append(snap.name, style="bold cyan")
    heading.append(f"  {snap.identity.relative_path}", style="dim")
    console.print(heading)

    details = Text("  Branch: ")
    details.append(branch, style="bold magenta")
    details.append(
        f"  State: {snap.classification.value}  "
        f"Ahead/behind: {ahead_behind}  Changes: {changes}"
    )
    console.print(details)
    console.print(f"  Last commit: {last_commit}")
    for warning in snap.warnings:
        console.print(f"  [yellow]! {warning}[/yellow]", highlight=False)
    unavailable = [
        item.plans[operation].decision.reason
        for operation in ("update", "sync", "default")
        if item.plans[operation].decision.verdict is Verdict.SKIPPED
    ]
    if unavailable and len(_interactive_choices(item)) == 1:
        console.print(f"  [yellow]Why no update action: {unavailable[0]}[/yellow]")


def _render_interactive_results(
    outcomes: list[InteractiveOutcome], project_name: str, dry_run: bool
) -> None:
    title = f"repo · interactive update · {project_name}"
    if dry_run:
        title += "  (dry run — no changes made)"
    table = Table(title=title, title_style="bold", header_style="bold")
    table.add_column("Repository")
    table.add_column("Action")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")
    for outcome in outcomes:
        if outcome.result is None:
            result = "skipped"
            detail = outcome.detail
        else:
            result = outcome.result.verdict.value
            detail = (
                outcome.result.planned
                if outcome.result.verdict in (Verdict.PROCEED, Verdict.UPDATED, Verdict.NOOP)
                else outcome.result.error or outcome.result.reason
            )
        table.add_row(outcome.relative_path, outcome.action, result, detail)
    console.print()
    console.print(table)


def _interactive_terminal_available() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _run_interactive_update(
    profile: Profile,
    specs: list[RepoSpec],
    select: Optional[str],
    dry_run: bool,
    jobs: Optional[int],
    stash: bool,
    yes: bool,
) -> None:
    missing = [s for s in specs if not (s.absolute_path / ".git").exists()]
    live = [s for s in specs if s not in missing]
    if not live and not missing:
        raise _fail("no repositories to operate on", EXIT_USAGE)

    worker_count = jobs or profile.policy.jobs
    coordinator = OperationCoordinator(backend=GitBackend(), jobs=worker_count)
    interactive_plan = coordinator.build_interactive_plan(
        live,
        fetch_timeout=float(profile.policy.fetch_timeout_seconds),
        stash=stash,
    )
    if select:
        interactive_plan = [
            item for item in interactive_plan if selection_matches(item.snapshot, select)
        ]
    selected_missing = [
        spec for spec in missing if not select or _spec_matches_selection(spec, select)
    ]

    by_path = {item.spec.relative_path: item for item in interactive_plan}
    outcomes: list[InteractiveOutcome] = []
    failed = False
    for spec in specs:
        item = by_path.get(spec.relative_path)
        if item is None:
            if select and not _spec_matches_selection(spec, select):
                continue
            if spec in selected_missing:
                console.print()
                console.print(f"[bold]{spec.name}[/bold]  {spec.relative_path}")
                console.print("  [yellow]Missing Git worktree; only Skip is available.[/yellow]")
                try:
                    choice = radio_select(
                        "What should happen?",
                        [RadioOption("skip", "Skip")],
                    )
                except (InteractiveTerminalError, KeyboardInterrupt) as exc:
                    if isinstance(exc, InteractiveTerminalError):
                        raise _fail(str(exc))
                    raise typer.Exit(130)
                outcomes.append(
                    InteractiveOutcome(
                        relative_path=spec.relative_path,
                        action="Skip" if choice == "skip" else choice,
                        detail="missing Git worktree",
                    )
                )
            continue

        _interactive_repo_info(item)
        try:
            choice = radio_select(
                "What should happen?",
                _interactive_choices(item),
            )
        except InteractiveTerminalError as exc:
            raise _fail(str(exc))
        except KeyboardInterrupt:
            raise typer.Exit(130)

        if choice == "skip":
            outcomes.append(
                InteractiveOutcome(
                    relative_path=item.spec.relative_path,
                    action="Skip",
                    detail="skipped by user",
                )
            )
            continue

        if choice in {"stash-sync", "discard-sync"}:
            if choice == "discard-sync" and not dry_run and not yes:
                if not Confirm.ask(
                    f"Discard local tracked and untracked changes in "
                    f"'{item.spec.relative_path}' and sync?",
                    default=False,
                ):
                    outcomes.append(
                        InteractiveOutcome(
                            relative_path=item.spec.relative_path,
                            action="Skip",
                            detail="discard cancelled",
                        )
                    )
                    continue
            operation = Operation.SYNC
            plan = item.plans[choice]
        else:
            operation = Operation(choice)
            plan = item.plans[choice]
        report = coordinator.execute(
            [plan],
            operation,
            project_name=profile.name,
            project_root=str(profile.root),
            dry_run=dry_run,
        )
        result = report.results[0]
        outcomes.append(
            InteractiveOutcome(
                relative_path=item.spec.relative_path,
                action=next(
                    option.label
                    for option in _interactive_choices(item)
                    if option.value == choice
                ),
                result=result,
            )
        )
        if result.verdict is Verdict.FAILED:
            failed = True
            console.print(
                f"[red]Failed[/red] {item.spec.relative_path}: "
                f"{result.error or 'operation failed'}",
                highlight=False,
            )

    if select and not interactive_plan and not selected_missing:
        raise _fail(f"no repositories matched selection '{select}'", EXIT_USAGE)
    _render_interactive_results(outcomes, profile.name, dry_run)
    raise typer.Exit(EXIT_FAILURE if failed else EXIT_OK)


@app.command(hidden=True)
def update(
    project: str = typer.Option(..., "--project", hidden=True),
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
    stash: bool = typer.Option(
        False,
        "--stash",
        help="For dirty repos: stash, fast-forward, then restore (conflict-safe).",
    ),
    jobs: Optional[int] = typer.Option(None, help="Parallel workers for fetches."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        help="Choose an update action for each repository with arrow keys.",
    ),
) -> None:
    """Fast-forward the current branch of eligible repositories. Never switches branches."""
    if interactive:
        if json_out:
            raise _fail("--interactive cannot be combined with --json")
        if not _interactive_terminal_available():
            raise _fail("--interactive requires a terminal")
        profile = _resolve_profile(project)
        selected = _select(profile, group, repo)
        _run_interactive_update(
            profile, _specs(profile, selected), select, dry_run, jobs, stash, yes
        )
        return
    _run_mutation(
        Operation.UPDATE, project, group, repo, select, dry_run, yes, ignore_skips,
        jobs, json_out, stash=stash,
    )


@app.command(name="default", hidden=True)
def default(
    project: str = typer.Option(..., "--project", hidden=True),
    group: Optional[str] = typer.Option(None, help="Limit to a repository group."),
    repo: Optional[str] = typer.Option(None, help="Limit to one repository."),
    select: Optional[str] = typer.Option(None, help="Selection expression."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview; make no changes."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    ignore_skips: bool = typer.Option(
        False, "--ignore-skips", help="Exit 0 even when repos are safety-skipped."
    ),
    stash: bool = typer.Option(
        False,
        "--stash",
        help="For dirty repos: stash, switch and fast-forward, then restore.",
    ),
    jobs: Optional[int] = typer.Option(None, help="Parallel workers for fetches."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Switch each eligible repository to its default branch and fast-forward it."""
    _run_mutation(
        Operation.DEFAULT, project, group, repo, select, dry_run, yes,
        ignore_skips, jobs, json_out, stash=stash,
    )


@app.command(hidden=True)
def sync(
    project: str = typer.Option(..., "--project", hidden=True),
    group: Optional[str] = typer.Option(None, help="Limit to a repository group."),
    repo: Optional[str] = typer.Option(None, help="Limit to one repository."),
    select: Optional[str] = typer.Option(None, help="Selection expression."),
    rebase: bool = typer.Option(False, "--rebase", help="Rebase instead of merging."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview; make no changes."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    ignore_skips: bool = typer.Option(False, "--ignore-skips"),
    jobs: Optional[int] = typer.Option(None, help="Parallel workers for fetches."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Merge the fetched remote default branch into the current branch."""
    _run_mutation(
        Operation.SYNC, project, group, repo, select, dry_run, yes,
        ignore_skips, jobs, json_out, sync_rebase=rebase,
    )


@app.command(hidden=True)
def checkout(
    branch: str = typer.Option(
        ..., "--branch", help="Branch to check out, or 'default' for each repo's default."
    ),
    project: str = typer.Option(..., "--project", hidden=True),
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


@app.command(hidden=True)
def summary(
    since: str = typer.Option(
        "7d", "--since", help="Window: 7d, 2w, 24h, an ISO date, or 'yesterday'."
    ),
    project: str = typer.Option(..., "--project", hidden=True),
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


@app.command(name="projects")
def projects() -> None:
    """List saved projects and mark the active project."""
    active = config.load_active_project()
    names = config.list_profiles()
    if not names:
        console.print("No projects yet. Run `repo init <path>`.")
        raise typer.Exit(EXIT_OK)
    for n in names:
        mark = " [green](active)[/green]" if n == active else ""
        console.print(f"- {n}{mark}")


@app.command(name="remove")
def remove(
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
[bold]repo[/bold] work safely across many independent Git repositories

[bold]Getting started[/bold]
  repo init <path> [--name N] [--yes]           Scan a workspace, save a project
  repo status                                    Show a local overview of all projects
  repo services update --dry-run                 Preview a safe fast-forward update
  repo projects                                  List saved projects

[bold]Commands[/bold]
  init            Discover repositories and save a profile. No git mutation.
  status          Show local counts for every saved project.
  projects        List known projects.
  remove          Remove a saved project (config only; never a repository).
  <project> status    Report detailed repository state. Read-only unless --fetch.
  <project> update    Fast-forward the current branch only. Never switches branches.
  <project> sync      Merge the remote default branch into the current branch.
  <project> default   Switch to the default branch and fast-forward it.
  <project> checkout  Check out a branch across repositories.
  <project> summary   Summarize commits across repositories since a date.
  version         Print the version.
  help            Show this manual.

[bold]Common options[/bold]
  --group G       Limit to repositories tagged with group G.
  --repo R        Limit to a single repository (by name or path).
  --select EXPR   Filter by expression: all, clean, dirty, ready, ahead,
                  group:NAME, search:TEXT, name:NAME (comma-separated union).
  --fetch         Fetch before computing ahead/behind (project status only).
  --since W       Summary window: 7d, 2w, 24h, an ISO date, or 'yesterday'.
  --markdown P    Write a Markdown summary to path P (summary only).
  --stash         Dirty repos: stash, fast-forward, restore (update/default).
  --rebase        Rebase instead of merge (sync only).
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
  default-branch-unknown  Could not infer a default branch; blocks default and sync.
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
  repo init ~/work/services --name services --yes
  repo status
  repo services status --fetch
  repo services update --dry-run
  repo services update --group backend --yes
  repo services sync --rebase --select clean --dry-run
  repo services default --stash --repo api --yes
  repo services checkout --branch default --repo api
  repo services summary --since 7d --markdown standup.md

Run [bold]repo <project> <command> --help[/bold] for a project command's options.
"""


@app.command(name="help")
def help_() -> None:
    """Show a quick manual with commands, states, and examples."""
    console.print(_MANUAL, highlight=False)


_PROJECT_COMMANDS = {
    "status": "project-status",
    "update": "update",
    "sync": "sync",
    "default": "default",
    "checkout": "checkout",
    "summary": "summary",
}
_GLOBAL_COMMANDS = {"status", "projects", "init", "remove", "version", "help"}
_LEGACY_COMMANDS = {"repo-manager", "switch-default", "profiles", "forget"}


def normalize_argv(args: list[str]) -> list[str]:
    """Translate the public project-first grammar to hidden Typer commands."""
    if not args:
        return args
    first = args[0]
    if first in _GLOBAL_COMMANDS:
        return args
    if first in _LEGACY_COMMANDS or first in _PROJECT_COMMANDS:
        raise ValueError(
            "project commands use `repo <project> <command>`. "
            "Examples: `repo services update` and `repo services default`."
        )
    if first.startswith("-"):
        return args
    if len(args) < 2 or args[1] not in _PROJECT_COMMANDS:
        raise ValueError(
            f"unknown command. Use `repo {first} status`, or run `repo help`."
        )
    command = args[1]
    return [_PROJECT_COMMANDS[command], "--project", first, *args[2:]]


def main() -> None:
    try:
        args = normalize_argv(sys.argv[1:])
    except ValueError as exc:
        err_console.print(f"[red]error:[/red] {exc}", highlight=False)
        raise SystemExit(EXIT_USAGE)
    try:
        app(args=args, prog_name="repo")
    except ConfigError as exc:
        err_console.print(f"[red]error:[/red] {exc}", highlight=False)
        raise SystemExit(EXIT_USAGE) from exc


if __name__ == "__main__":
    main()
