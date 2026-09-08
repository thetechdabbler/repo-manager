# Installation

## Requirements

- Python 3.11 or newer
- Git on your `PATH`

## Install

The tool is a standalone CLI, best installed globally with
[pipx](https://pipx.pypa.io/) so it stays isolated from your project environments.

It is installed directly from the Git repository:

```bash
pipx install "git+https://github.com/thetechdabbler/repo-manager.git"
```

To upgrade later (pipx re-pulls from the same source):

```bash
pipx upgrade repo-manager
```

!!! warning "Not on PyPI"
    `repo-manager` is not published to the Python Package Index. Running
    `pipx install repo-manager` would fetch an unrelated, abandoned project of the
    same name and fail to build. Always install from the Git URL above.

To install a specific branch, or a local clone you are developing against:

```bash
pipx install "git+https://github.com/thetechdabbler/repo-manager.git@<branch>"
pipx install /path/to/repo-manager
```

## Verify

```bash
repo version
repo help
```

`repo help` prints a one-screen manual: every command, the repository states,
and the exit codes.

## Configuration location

Profiles are stored under your XDG config directory, one file per workspace:

```text
~/.config/repo/
├── config.toml            # global settings and the active project
└── projects/
    └── services.toml      # one profile per workspace
```

On first run, an existing `~/.config/repo-manager/` directory is moved to this new
location. If both directories exist, `repo` stops and asks you to resolve them
manually. Set `REPO_HOME` to override the new location (useful for testing or for keeping
projects in a dotfiles repository).

See [Configuration](concepts/configuration.md) for the profile schema.
