# sync

`repo <project> sync` fetches each selected repository, then integrates its fetched
remote default branch into the branch already checked out. The default strategy is a
merge. `--rebase` is explicit.

Dirty repositories, detached heads, Git operations in progress, unavailable or
ambiguous remotes, and unknown default branches are skipped. Sync never stashes.
When merge or rebase has a conflict, the conflict stays in the repository for manual
resolution and other repositories continue processing.

--8<-- "reference/_generated/sync.md"

```bash
repo services sync --dry-run
repo services sync --yes
repo services sync --rebase --repo api --yes
```
