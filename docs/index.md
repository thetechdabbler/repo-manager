<div class="rm-hero" markdown>
<span class="rm-eyebrow">Multi-repo git, safely</span>

# repo

One workspace, many repositories, no surprises. Inspect, update, and summarize a tree
of independent Git repos. Only clean, fast-forwardable repos ever change, and every
skip tells you why.

[Get started](installation.md){ .md-button .md-button--primary }
[Command reference](reference/index.md){ .md-button }
</div>

<div class="grid cards" markdown>

-   :material-download:{ .lg .middle } __Install in seconds__

    ---

    One `pipx` command from Git. Python 3.11+ and nothing else to configure.

    [:octicons-arrow-right-24: Installation](installation.md)

-   :material-eye-outline:{ .lg .middle } __See everything at once__

    ---

    Branch, state, and local work across every repository in a single table.

    [:octicons-arrow-right-24: Daily status](guides/daily-status.md)

-   :material-shield-check-outline:{ .lg .middle } __Safe by default__

    ---

    Only clean, tracking, fast-forwardable repos change. Every skip states a reason
    and a next action.

    [:octicons-arrow-right-24: Safety model](concepts/safety-model.md)

-   :material-console-line:{ .lg .middle } __Every command, documented__

    ---

    A full reference generated from the CLI itself, so it can never drift from the
    tool.

    [:octicons-arrow-right-24: Command reference](reference/index.md)

</div>

## The mental model

Two ideas explain almost everything about how the tool behaves.

**Discover broadly, operate narrowly.** `init` scans a workspace and finds every
repository. Mutations only ever touch repositories you have selected, and only when
they are safe to touch.

**Skip with a reason.** A clean, tracking worktree that can fast-forward is the only
thing updated by default. Anything else (dirty, detached, diverged, mid-rebase) is
skipped, and every skip states why and what to do next. A skipped repository is a
result, not a failure to hide.

```text
SKIPPED  services/payments-api
Reason:  4 local change(s) would be at risk
Next:    commit or stash the changes, or use --stash
```

## 60-second tour

```bash
# Scan a workspace and save a profile (no git mutation).
repo init ~/work/services --name services --yes

# See a local overview of every saved project.
repo status

# See detailed state for one project.
repo services status --fetch

# Preview a safe update: what would change, and why the rest is skipped.
repo services update --dry-run

# Fast-forward the eligible repositories.
repo services update --yes
```

See [Installation](installation.md) to get started, or the
[Safety model](concepts/safety-model.md) for exactly what is and is not touched.
