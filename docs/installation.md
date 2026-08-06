# Installation

## Requirements

- Python 3.11 or newer
- Git on your `PATH`

## Install

The tool is a standalone CLI, best installed globally with
[pipx](https://pipx.pypa.io/) so it stays isolated from your project environments:

```bash
pipx install repo-manager
```

To upgrade later:

```bash
pipx upgrade repo-manager
```

## Verify

```bash
repo-manager version
repo-manager help
```

`repo-manager help` prints a one-screen manual: every command, the repository states,
and the exit codes.

## Configuration location

Profiles are stored under your XDG config directory, one file per workspace:

```text
~/.config/repo-manager/
├── config.toml            # global settings and the active project
└── projects/
    └── services.toml      # one profile per workspace
```

Set `REPO_MANAGER_HOME` to override that location (useful for testing or for keeping
profiles in a dotfiles repository).

See [Configuration](concepts/configuration.md) for the profile schema.
