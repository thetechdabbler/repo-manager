"""Profile load, validate, and round-trip."""

from __future__ import annotations

import pytest

from repo_manager import config
from repo_manager.config import (
    ConfigError,
    DiscoveryConfig,
    PolicyConfig,
    Profile,
    RepositoryConfig,
    load_profile_from_path,
    render_profile,
)


def _profile(root) -> Profile:
    return Profile(
        name="demo",
        root=root,
        default_remote="origin",
        discovery=DiscoveryConfig(max_depth=3, exclude=[".git", "node_modules"]),
        policy=PolicyConfig(jobs=4),
        repositories=[
            RepositoryConfig(path=".", name="root", groups=["env"], default_branch="main"),
            RepositoryConfig(
                path="svc/api", name="api", groups=["services"], default_branch="dev"
            ),
        ],
    )


def test_render_and_reparse_roundtrip(tmp_path):
    (tmp_path / "svc" / "api").mkdir(parents=True)
    profile = _profile(tmp_path)
    text = render_profile(profile)

    out = tmp_path / "demo.toml"
    out.write_text(text, encoding="utf-8")
    reloaded = load_profile_from_path(out)

    assert reloaded.name == "demo"
    assert reloaded.policy.jobs == 4
    assert reloaded.discovery.max_depth == 3
    assert {r.name for r in reloaded.repositories} == {"root", "api"}
    assert reloaded.repos_in_group("services")[0].name == "api"
    # New discovery flag round-trips with its default.
    assert reloaded.discovery.descend_into_repositories is False


def test_hand_written_comments_survive_rewrite(tmp_path):
    """tomlkit preserves comments on values it does not touch."""
    out = tmp_path / "demo.toml"
    out.write_text(render_profile(_profile(tmp_path)), encoding="utf-8")

    import tomlkit

    doc = tomlkit.parse(out.read_text(encoding="utf-8"))
    doc["project"]["name"].comment("# team workspace")  # type: ignore[attr-defined]
    out.write_text(tomlkit.dumps(doc), encoding="utf-8")

    assert "# team workspace" in out.read_text(encoding="utf-8")


def test_path_escaping_root_is_rejected(tmp_path):
    out = tmp_path / "bad.toml"
    out.write_text(
        f'schema_version = 1\n'
        f'[project]\nname="x"\nroot="{tmp_path}"\n'
        f'[[repositories]]\npath="../escape"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="escapes the workspace root"):
        load_profile_from_path(out)


def test_absolute_path_is_rejected(tmp_path):
    out = tmp_path / "bad.toml"
    out.write_text(
        f'schema_version = 1\n[project]\nname="x"\nroot="{tmp_path}"\n'
        f'[[repositories]]\npath="/etc"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must be relative"):
        load_profile_from_path(out)


def test_duplicate_names_rejected(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    out = tmp_path / "dup.toml"
    out.write_text(
        f'schema_version = 1\n[project]\nname="x"\nroot="{tmp_path}"\n'
        f'[[repositories]]\npath="a"\nname="same"\n'
        f'[[repositories]]\npath="b"\nname="same"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="duplicate repository name"):
        load_profile_from_path(out)


def test_wrong_schema_version_rejected(tmp_path):
    out = tmp_path / "v.toml"
    out.write_text(
        f'schema_version = 99\n[project]\nname="x"\nroot="{tmp_path}"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="schema_version"):
        load_profile_from_path(out)


def test_missing_project_section_rejected(tmp_path):
    out = tmp_path / "empty.toml"
    out.write_text("schema_version = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="missing"):
        load_profile_from_path(out)


def test_active_project_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_MANAGER_HOME", str(tmp_path / "home"))
    assert config.load_active_project() is None
    config.set_active_project("demo")
    assert config.load_active_project() == "demo"
    # Setting a second time preserves the file structure.
    config.set_active_project("other")
    assert config.load_active_project() == "other"


def test_delete_profile_removes_only_the_config(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_MANAGER_HOME", str(tmp_path / "home"))
    (tmp_path / "svc" / "api").mkdir(parents=True)
    config.save_profile(_profile(tmp_path))
    path = config.profile_path("demo")
    assert path.is_file()

    removed = config.delete_profile("demo")
    assert removed == path
    assert not path.exists()
    assert "demo" not in config.list_profiles()
    # The workspace itself is untouched.
    assert (tmp_path / "svc" / "api").is_dir()


def test_delete_missing_profile_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_MANAGER_HOME", str(tmp_path / "home"))
    with pytest.raises(ConfigError, match="no profile named"):
        config.delete_profile("ghost")


def test_clear_active_project(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_MANAGER_HOME", str(tmp_path / "home"))
    config.set_active_project("demo")
    assert config.load_active_project() == "demo"
    config.clear_active_project()
    assert config.load_active_project() is None
    # Idempotent even when nothing is set.
    config.clear_active_project()
