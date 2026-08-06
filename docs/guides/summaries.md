# Summaries

!!! note "Planned for a later release"
    `summary` is designed and specified but not yet shipped. This page describes the
    intended behavior; it will be marked stable when the feature lands.

`summary` answers "what changed across the workspace recently?" It is read-only and
produces the same structured report in terminal, JSON, and Markdown form.

```bash
repo-manager summary --since 7d
repo-manager summary --since 2026-07-01 --group backend
repo-manager summary --since 7d --markdown report.md
```

For each repository and commit range it collects commit SHAs, subjects, authors and
timestamps, files changed, insertion and deletion counts, the top-level directories
touched, and any parseable ticket references.

Summaries are date-based by design. There is no stored review cursor to go stale after
a rebase, and no history-reachability problem to reason about.
