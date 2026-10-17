"""Projects: one per niche, each with its own settings, prompts and drafts."""

from __future__ import annotations

from typing import Optional

from .config import (
    RESOURCE_ROOT,
    ConfigError,
    Settings,
    build_settings,
    default_prompt,
    default_settings,
    read_yaml_project,
)
from .db import Database, loads
from .logging_setup import get_logger

log = get_logger()
STARTER_CONFIG = RESOURCE_ROOT / "config.yaml"


def settings_for(project) -> Settings:
    """Validated Settings for a project row (plus API keys from .env)."""
    return build_settings(
        loads(project["settings_json"], {}),
        project["name"],
        project["article_instructions"],
        project["image_instructions"],
        project_id=project["id"],
    )


def create_project(db: Database, name: str, copy_from: Optional[int] = None) -> int:
    """New project with default settings, or a copy of another project's settings and prompts."""
    name = (name or "").strip()
    if not name:
        raise ConfigError("Give the project a name.")
    if db.project_by_name(name):
        raise ConfigError(f"A project called '{name}' already exists.")
    if copy_from:
        source = db.project(copy_from)
        if source is None:
            raise ConfigError("The project to copy from was not found.")
        return db.create_project(name, loads(source["settings_json"], {}),
                                 source["article_instructions"], source["image_instructions"])
    return db.create_project(name, default_settings(), default_prompt("article"), default_prompt("image"))


def import_yaml(db: Database, path, name: Optional[str] = None) -> int:
    """Create a project from an old-style config.yaml file."""
    yaml_name, data, article_prompt, image_prompt = read_yaml_project(path)
    name = name or yaml_name
    if db.project_by_name(name):
        raise ConfigError(f"A project called '{name}' already exists.")
    return db.create_project(name, data, article_prompt, image_prompt)


def ensure_a_project(db: Database) -> None:
    """On first launch, create a starter project from config.yaml (or defaults)."""
    if db.projects():
        return
    if STARTER_CONFIG.exists():
        try:
            import_yaml(db, STARTER_CONFIG)
            log.info("Created your first project from config.yaml.")
            return
        except ConfigError as exc:
            log.warning("Could not import config.yaml (%s); starting with defaults.", exc)
    create_project(db, "My first project")


def resolve_project(db: Database, name: Optional[str]):
    """Find a project by name for the command line; with one project, the name is optional."""
    projects = db.projects()
    if name:
        project = db.project_by_name(name)
        if project is None:
            raise ConfigError(f"No project called '{name}'. Projects: {', '.join(p['name'] for p in projects)}")
        return project
    if len(projects) == 1:
        return projects[0]
    raise ConfigError(
        "You have several projects; choose one with --project \"Name\". "
        f"Projects: {', '.join(p['name'] for p in projects)}"
    )
