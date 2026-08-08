"""Profile loading, validation, and writing.

Profiles are TOML. Reads use the stdlib `tomllib`; writes use `tomlkit` so any
comments a user adds by hand survive a rewrite. Global storage follows XDG on
macOS and Linux:

    ~/.config/repo-manager/
    ├── config.toml            global settings and active project
    └── projects/<name>.toml   one profile per workspace
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

SCHEMA_VERSION = 1


class ConfigError(Exception):
    """Raised for invalid or unreadable configuration. Maps to exit code 2."""


@dataclass
class RepositoryConfig:
    path: str
    name: str
    groups: list[str] = field(default_factory=list)
    default_branch: str | None = None
    remote: str | None = None


@dataclass
class DiscoveryConfig:
    max_depth: int = 4
    include_root_repository: bool = True
    exclude: list[str] = field(default_factory=list)
    include_linked_worktrees: bool = False


@dataclass
class PolicyConfig:
    pull_mode: str = "ff-only"
    skip_dirty: bool = True
    fetch_before_update: bool = True
    fetch_before_status: bool = False
    fetch_timeout_seconds: int = 30
    jobs: int = 8


@dataclass
class Profile:
    name: str
    root: Path
    default_remote: str = "origin"
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    repositories: list[RepositoryConfig] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    source_path: Path | None = None

    def repos_in_group(self, group: str) -> list[RepositoryConfig]:
        return [r for r in self.repositories if group in r.groups]

    def all_groups(self) -> list[str]:
        seen: list[str] = []
        for r in self.repositories:
            for g in r.groups:
                if g not in seen:
                    seen.append(g)
        return sorted(seen)


# -- paths --------------------------------------------------------------------------


def config_home() -> Path:
    override = os.environ.get("REPO_MANAGER_HOME")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "repo-manager"


def projects_dir() -> Path:
    return config_home() / "projects"


def profile_path(name: str) -> Path:
    return projects_dir() / f"{name}.toml"


def global_config_path() -> Path:
    return config_home() / "config.toml"


# -- global config ------------------------------------------------------------------


def load_active_project() -> str | None:
    path = global_config_path()
    if not path.is_file():
        return None
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return data.get("active_project")


def set_active_project(name: str) -> None:
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    if path.is_file():
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    doc["active_project"] = name
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def clear_active_project() -> None:
    """Remove the active-project pointer, if set. Leaves other global settings."""
    path = global_config_path()
    if not path.is_file():
        return
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    if "active_project" in doc:
        del doc["active_project"]
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def list_profiles() -> list[str]:
    pdir = projects_dir()
    if not pdir.is_dir():
        return []
    return sorted(p.stem for p in pdir.glob("*.toml"))


def delete_profile(name: str) -> Path:
    """Delete a profile's config file. Touches no repository or workspace data.

    Returns the removed path. Raises ConfigError if the profile does not exist.
    """
    path = profile_path(name)
    if not path.is_file():
        raise ConfigError(f"no profile named '{name}' at {path}")
    path.unlink()
    return path


# -- load ---------------------------------------------------------------------------


def load_profile(name: str) -> Profile:
    path = profile_path(name)
    if not path.is_file():
        raise ConfigError(f"no profile named '{name}' at {path}")
    return load_profile_from_path(path)


def load_profile_from_path(path: Path) -> Profile:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read profile {path}: {exc}") from exc

    version = data.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ConfigError(
            f"profile {path} has schema_version {version}, "
            f"this build understands {SCHEMA_VERSION}"
        )

    project = data.get("project")
    if not project or "name" not in project or "root" not in project:
        raise ConfigError(f"profile {path} is missing [project] name/root")

    root = Path(project["root"]).expanduser()

    disc = data.get("discovery", {})
    discovery = DiscoveryConfig(
        max_depth=int(disc.get("max_depth", 4)),
        include_root_repository=bool(disc.get("include_root_repository", True)),
        exclude=list(disc.get("exclude", [])),
        include_linked_worktrees=bool(disc.get("include_linked_worktrees", False)),
    )

    pol = data.get("policy", {})
    policy = PolicyConfig(
        pull_mode=str(pol.get("pull_mode", "ff-only")),
        skip_dirty=bool(pol.get("skip_dirty", True)),
        fetch_before_update=bool(pol.get("fetch_before_update", True)),
        fetch_before_status=bool(pol.get("fetch_before_status", False)),
        fetch_timeout_seconds=int(pol.get("fetch_timeout_seconds", 30)),
        jobs=int(pol.get("jobs", 8)),
    )

    repositories: list[RepositoryConfig] = []
    seen_paths: set[str] = set()
    seen_names: set[str] = set()
    for entry in data.get("repositories", []):
        if "path" not in entry:
            raise ConfigError(f"profile {path}: a [[repositories]] entry has no path")
        rel = str(entry["path"])
        name_val = str(entry.get("name") or _name_from_path(rel))
        _validate_within_root(root, rel, path)
        if rel in seen_paths:
            raise ConfigError(f"profile {path}: duplicate repository path '{rel}'")
        if name_val in seen_names:
            raise ConfigError(f"profile {path}: duplicate repository name '{name_val}'")
        seen_paths.add(rel)
        seen_names.add(name_val)
        repositories.append(
            RepositoryConfig(
                path=rel,
                name=name_val,
                groups=list(entry.get("groups", [])),
                default_branch=entry.get("default_branch"),
                remote=entry.get("remote"),
            )
        )

    return Profile(
        name=str(project["name"]),
        root=root,
        default_remote=str(project.get("default_remote", "origin")),
        discovery=discovery,
        policy=policy,
        repositories=repositories,
        schema_version=version,
        source_path=path,
    )


def _name_from_path(rel: str) -> str:
    if rel in (".", ""):
        return "root"
    return Path(rel).name


def _validate_within_root(root: Path, rel: str, profile_file: Path) -> None:
    if os.path.isabs(rel):
        raise ConfigError(
            f"profile {profile_file}: repository path '{rel}' must be relative "
            f"to the workspace root"
        )
    resolved = (root / rel).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ConfigError(
            f"profile {profile_file}: repository path '{rel}' escapes the "
            f"workspace root {root_resolved}"
        )


# -- write --------------------------------------------------------------------------


def render_profile(profile: Profile) -> str:
    """Render a Profile to TOML text, preserving nothing but producing clean output."""
    doc = tomlkit.document()
    doc.add("schema_version", profile.schema_version)
    doc.add(tomlkit.nl())

    project = tomlkit.table()
    project.add("name", profile.name)
    project.add("root", str(profile.root))
    project.add("default_remote", profile.default_remote)
    doc.add("project", project)

    discovery = tomlkit.table()
    discovery.add("max_depth", profile.discovery.max_depth)
    discovery.add("include_root_repository", profile.discovery.include_root_repository)
    discovery.add("exclude", profile.discovery.exclude)
    discovery.add("include_linked_worktrees", profile.discovery.include_linked_worktrees)
    doc.add("discovery", discovery)

    policy = tomlkit.table()
    policy.add("pull_mode", profile.policy.pull_mode)
    policy.add("skip_dirty", profile.policy.skip_dirty)
    policy.add("fetch_before_update", profile.policy.fetch_before_update)
    policy.add("fetch_before_status", profile.policy.fetch_before_status)
    policy.add("fetch_timeout_seconds", profile.policy.fetch_timeout_seconds)
    policy.add("jobs", profile.policy.jobs)
    doc.add("policy", policy)

    repos = tomlkit.aot()
    for r in profile.repositories:
        t = tomlkit.table()
        t.add("path", r.path)
        t.add("name", r.name)
        t.add("groups", r.groups)
        if r.default_branch is not None:
            t.add("default_branch", r.default_branch)
        if r.remote is not None:
            t.add("remote", r.remote)
        repos.append(t)
    doc.add("repositories", repos)

    return tomlkit.dumps(doc)


def save_profile(profile: Profile) -> Path:
    path = profile_path(profile.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_profile(profile), encoding="utf-8")
    return path
