# Summaries

`summary` answers "what changed across the workspace recently?" It is read-only and
produces the same structured report in terminal, JSON, and Markdown form.

```bash
repo <project> summary --since 7d
repo <project> summary --since 2026-07-01 --group backend
repo <project> summary --since 7d --markdown report.md
```

## The window

`--since` accepts duration shorthand (`7d`, `2w`, `24h`), an ISO date
(`2026-07-01`), or any expression git understands (`yesterday`, `2 weeks ago`). It
defaults to `7d`.

**Date rule:** commits are selected by committer date, first-parent, on each
repository's currently checked-out branch (HEAD). There is no stored review cursor, so
nothing goes stale after a rebase; the range is always derived from `--since`.

## What it collects

Per repository, and aggregated across the workspace:

- Commit SHA, author, committer date, and subject.
- Insertions, deletions, and files changed per commit and in total.
- The top-level directories touched, ordered by how many files changed in each.
- Detected references: JIRA-style ticket keys (`ABC-123`) and conventional-commit
  types (`feat`, `fix`, ...) parsed from subjects.

## Output

- Default: a terminal table with a per-repository commit breakdown.
- `--json`: the full structured `SummaryReport` (stable, versioned) for automation.
- `--markdown <path>`: a handoff-ready Markdown report, ideal for a standup note or a
  review summary.

The three renderers are views of one structure, so the JSON is the contract and the
table and Markdown never diverge from it.

## Selecting repositories

`--group`, `--repo`, and `--select` apply. For summaries, `--select` supports
`all`, `changed`, `quiet`, `name:NAME`, `search:TEXT`, and a bare name or path; use
`--group` to scope by group.
