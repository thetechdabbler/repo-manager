# forget

Remove a saved profile. `forget` deletes only the profile's config file. It never
touches a repository or the workspace on disk.

## What it does

`forget` prints the exact config file it will remove and the workspace path it leaves
untouched, asks for confirmation, then deletes the profile TOML. If the removed profile
was the active one, the active-project pointer is cleared. No repository, working tree,
or Git history is affected.

There is no command that deletes repositories or workspace directories; that is out of
scope by design, the same reason a destructive discard is not offered.

```mermaid
flowchart TD
  A["repo-manager forget NAME"] --> B{"Profile exists?"}
  B -- "no" --> E["Error: no such profile (exit 2)"]
  B -- "yes" --> C["Show config file + untouched workspace"]
  C --> D{"--yes or confirmed?"}
  D -- "no" --> Q["Cancel, change nothing"]
  D -- "yes" --> F["Delete profile TOML"]
  F --> G{"Was it active?"}
  G -- "yes" --> H["Clear active-project pointer"]
  G -- "no" --> I["Leave active pointer as-is"]
  H --> R["Done"]
  I --> R
```

## How the options change it

- **`--yes` / `-y`** skips the confirmation prompt. Required to run non-interactively;
  without a TTY and without `--yes`, `forget` refuses rather than deleting silently.

## Options

--8<-- "reference/_generated/forget.md"

## Examples

```bash
repo-manager forget old-workspace        # confirms, then removes the profile
repo-manager forget old-workspace --yes  # non-interactive
```
