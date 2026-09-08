# default

`repo <project> default` switches eligible repositories to their configured default
branch and fast-forwards it. It can change the checked-out branch.

--8<-- "reference/_generated/default.md"

Use `--stash` only when you want local changes stashed, restored after the branch
switch, and protected if restore conflicts.

```bash
repo services default --dry-run
repo services default --stash --repo api --yes
```
