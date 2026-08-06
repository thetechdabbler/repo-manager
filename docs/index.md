# repo-manager

A command-line utility for working safely with a directory tree that contains many
independent Git repositories: service fleets, microservice checkouts, umbrella
workspaces. No submodules, no monorepo, no particular Git host, and no prescribed
layout required.

It answers three questions and performs two mutations:

- Which repositories are present, on what branch, in what state, and which have local work?
- Which repositories can be updated safely right now, and why can the rest not?
- What changed across the workspace in the last N days?
- Update the safe repositories on their current branch.
- Return the safe repositories to their default branch.

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
Next:    commit or stash the changes, or use --stash-and-update
```

## 60-second tour

```bash
# Scan a workspace and save a profile (no git mutation).
repo-manager init ~/work/services --name services --yes

# See the state of every repository.
repo-manager status --fetch

# Preview a safe update: what would change, and why the rest is skipped.
repo-manager update --dry-run

# Fast-forward the eligible repositories.
repo-manager update --yes
```

See [Installation](installation.md) to get started, or the
[Safety model](concepts/safety-model.md) for exactly what is and is not touched.
