# Recovering local work

!!! note "Planned for the next release"
    `--stash-and-update` is designed and specified but not yet shipped. This page
    describes the intended behavior; it will be marked stable when the feature lands.

By default, a dirty repository is skipped by `update` and `switch-default` so your
local work is never touched. When you do want to update on top of local changes,
`--stash-and-update` is the opt-in, conflict-safe path.

```bash
repo-manager update --stash-and-update --group backend
```

For each dirty repository it will:

1. Create a uniquely named stash, including untracked files.
2. Fast-forward using the normal safe path.
3. Restore the stash.
4. Report whether the restore was clean, conflicted, or failed.

The stash is never dropped when a restore conflicts. The stash reference and the exact
git commands to inspect or recover it are printed, so no work is ever lost silently.

An `in-progress` repository (mid rebase, merge, cherry-pick, revert, or bisect) is
never touched, even with this flag.
