"""Renderers over the report structures.

Every renderer is a pure function of a report object. The terminal table is just one
of them; JSON is the contract for automation and any downstream consumer. Nothing
here recomputes state or talks to git.
"""

from __future__ import annotations

import json
import sys

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .models import Classification, FetchResult, StatusReport

# Color per classification, chosen so the eye lands on what needs attention.
_CLASS_STYLE = {
    Classification.READY: "green",
    Classification.CURRENT: "dim green",
    Classification.DIRTY: "yellow",
    Classification.IN_PROGRESS: "bold red",
    Classification.DETACHED: "magenta",
    Classification.NO_UPSTREAM: "cyan",
    Classification.DEFAULT_BRANCH_UNKNOWN: "cyan",
    Classification.AMBIGUOUS_REMOTE: "cyan",
    Classification.AHEAD: "blue",
    Classification.DIVERGED: "red",
    Classification.REMOTE_UNAVAILABLE: "red",
}


def render_status_json(report: StatusReport) -> str:
    return json.dumps(report.to_dict(), indent=2)


def render_status_table(report: StatusReport, console: Console | None = None) -> None:
    console = console or Console()

    table = Table(
        title=f"repo-manager · {report.project_name}",
        title_style="bold",
        header_style="bold",
        expand=False,
    )
    table.add_column("Repository", overflow="fold")
    table.add_column("Branch")
    table.add_column("State")
    table.add_column("↑/↓")
    table.add_column("Changes")
    table.add_column("Last commit", overflow="fold")

    for snap in report.repositories:
        cls = snap.classification
        state = Text(cls.value, style=_CLASS_STYLE.get(cls, ""))

        if snap.checkout.is_detached:
            short = (snap.checkout.detached_sha or "")[:8]
            branch = Text(f"detached@{short}", style="magenta")
        else:
            branch = Text(snap.checkout.current_branch or "?")

        ahead_behind = _ahead_behind_cell(snap)
        changes = _changes_cell(snap)

        if snap.last_commit:
            subject = snap.last_commit.subject
            if len(subject) > 50:
                subject = subject[:47] + "…"
            last = f"{snap.last_commit.short_sha} {subject}"
        else:
            last = "-"

        table.add_row(snap.identity.relative_path, branch, state, ahead_behind, changes, last)

    console.print(table)
    _print_totals(report, console)
    _print_warnings(report, console)


def _ahead_behind_cell(snap) -> Text:
    if not snap.remote.data_is_current:
        if snap.remote.fetch_attempted:
            return Text("unreachable", style="red")
        # Never present stale local refs as current remote state.
        return Text("no-fetch", style="dim")
    a = snap.remote.ahead_count or 0
    b = snap.remote.behind_count or 0
    if a == 0 and b == 0:
        return Text("=", style="dim")
    return Text(f"↑{a} ↓{b}")


def _changes_cell(snap) -> Text:
    w = snap.worktree
    if w.in_progress_operation.value != "none":
        return Text(w.in_progress_operation.value, style="bold red")
    if not w.is_dirty:
        return Text("clean", style="dim")
    parts = []
    if w.staged_count:
        parts.append(f"{w.staged_count}s")
    if w.unstaged_count:
        parts.append(f"{w.unstaged_count}m")
    if w.untracked_count:
        parts.append(f"{w.untracked_count}?")
    if w.unmerged_count:
        parts.append(f"{w.unmerged_count}u")
    return Text(" ".join(parts), style="yellow")


def _print_totals(report: StatusReport, console: Console) -> None:
    totals = report.totals()
    if not totals:
        return
    parts = [f"{count} {name}" for name, count in totals.items()]
    console.print(Text("  ".join(parts), style="dim"))


def _print_warnings(report: StatusReport, console: Console) -> None:
    all_warnings: list[str] = list(report.warnings)
    for snap in report.repositories:
        for w in snap.warnings:
            all_warnings.append(f"{snap.identity.relative_path}: {w}")
    if not all_warnings:
        return
    console.print()
    for w in all_warnings:
        console.print(Text(f"! {w}", style="yellow"), highlight=False)


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


# -- operation rendering ------------------------------------------------------------

from .models import OperationReport, Verdict  # noqa: E402

_VERDICT_STYLE = {
    Verdict.PROCEED: "green",
    Verdict.UPDATED: "bold green",
    Verdict.NOOP: "dim",
    Verdict.SKIPPED: "yellow",
    Verdict.FAILED: "bold red",
}


def render_operation_json(report: OperationReport) -> str:
    return json.dumps(report.to_dict(), indent=2)


def render_plan_table(report: OperationReport, console: Console | None = None) -> None:
    """The pre-mutation plan: what each repository will do, and why the skips skip."""
    console = console or Console()
    verb = "Plan" if report.dry_run else "Result"
    title = f"repo-manager · {report.operation.value} · {report.project_name}"
    if report.dry_run:
        title += "  (dry run — no changes made)"

    table = Table(title=title, title_style="bold", header_style="bold")
    table.add_column("Repository", overflow="fold")
    table.add_column("Branch")
    table.add_column(verb)
    table.add_column("Detail", overflow="fold")

    for r in report.results:
        verdict = r.verdict
        vtext = Text(verdict.value, style=_VERDICT_STYLE.get(verdict, ""))
        branch = r.after_branch or r.before_branch or "?"
        detail = r.planned if verdict in (Verdict.PROCEED, Verdict.UPDATED, Verdict.NOOP) else r.reason
        if r.error:
            detail = r.error
        table.add_row(r.relative_path, branch, vtext, detail)

    console.print(table)

    parts = [f"{c} {n}" for n, c in report.totals().items()]
    if parts:
        console.print(Text("  ".join(parts), style="dim"))

    # Skip guidance: reason + next action, the design's whole point.
    skips = [r for r in report.results if r.verdict is Verdict.SKIPPED]
    failures = [r for r in report.results if r.verdict is Verdict.FAILED]
    for r in skips + failures:
        console.print()
        label = "SKIPPED" if r.verdict is Verdict.SKIPPED else "FAILED"
        style = "yellow" if r.verdict is Verdict.SKIPPED else "red"
        console.print(Text(f"{label}  {r.relative_path}", style=style), highlight=False)
        if r.reason:
            console.print(f"  Reason: {r.reason}", highlight=False)
        if r.error:
            console.print(f"  Error:  {r.error}", highlight=False)
        if r.next_action:
            console.print(f"  Next:   {r.next_action}", highlight=False)
