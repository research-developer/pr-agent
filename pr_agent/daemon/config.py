"""
Configuration management for PR-Agent CLI.

Handles loading/saving config from ~/.pr-agent/config.json
"""

import json
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


# Default config directory
DEFAULT_CONFIG_DIR = Path.home() / ".pr-agent"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_LOG_DIR = DEFAULT_CONFIG_DIR / "logs"
DEFAULT_PID_FILE = DEFAULT_CONFIG_DIR / "daemon.pid"


class Credentials(BaseModel):
    """API credentials for various services."""

    github_token: Optional[str] = Field(None, description="GitHub personal access token")
    anthropic_api_key: Optional[str] = Field(None, description="Anthropic API key")
    openai_api_key: Optional[str] = Field(None, description="OpenAI API key")
    railway_token: Optional[str] = Field(None, description="Railway API token")


class Settings(BaseModel):
    """Global settings for the daemon."""

    poll_interval_github: int = Field(30, description="GitHub poll interval in seconds")
    poll_interval_railway: int = Field(60, description="Railway poll interval in seconds")
    model: str = Field("claude-sonnet-4-20250514", description="Default AI model")
    log_level: str = Field("INFO", description="Logging level")
    github_username: Optional[str] = Field(None, description="GitHub bot username")


class RepoConfig(BaseModel):
    """Configuration for a tracked repository."""

    path: str = Field(..., description="Local path to repository")
    github_repo: str = Field(..., description="GitHub repo (owner/repo)")

    # Railway integration (optional)
    railway_project_id: Optional[str] = Field(None, description="Railway project ID")
    railway_service_ids: list[str] = Field(
        default_factory=list, description="Railway service IDs to monitor"
    )

    # Auto-commands on PR open
    auto_review: bool = Field(True, description="Auto-run /review on new PRs")
    auto_describe: bool = Field(False, description="Auto-run /describe on new PRs")


class Config(BaseModel):
    """
    Root configuration model for PR-Agent CLI.

    Stored at ~/.pr-agent/config.json
    """

    version: str = Field("1.0", description="Config version")
    credentials: Credentials = Field(
        default_factory=Credentials, description="API credentials"
    )
    settings: Settings = Field(default_factory=Settings, description="Global settings")
    repos: list[RepoConfig] = Field(
        default_factory=list, description="Tracked repositories"
    )

    def get_repo(self, path: Optional[str] = None, github_repo: Optional[str] = None) -> Optional[RepoConfig]:
        """
        Find a repo config by path or GitHub repo name.

        Args:
            path: Local path to search for
            github_repo: GitHub repo name to search for

        Returns:
            RepoConfig if found, None otherwise
        """
        for repo in self.repos:
            if path and repo.path == path:
                return repo
            if github_repo and repo.github_repo == github_repo:
                return repo
        return None

    def add_repo(self, repo: RepoConfig) -> None:
        """
        Add or update a repository config.

        Args:
            repo: RepoConfig to add
        """
        # Check if repo already exists
        existing = self.get_repo(path=repo.path)
        if existing:
            # Update existing
            idx = self.repos.index(existing)
            self.repos[idx] = repo
        else:
            self.repos.append(repo)

    def remove_repo(self, path: str) -> bool:
        """
        Remove a repository from config.

        Args:
            path: Local path of repo to remove

        Returns:
            True if removed, False if not found
        """
        repo = self.get_repo(path=path)
        if repo:
            self.repos.remove(repo)
            return True
        return False


def ensure_config_dir() -> Path:
    """
    Ensure the config directory exists.

    Returns:
        Path to config directory
    """
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_CONFIG_DIR


def load_config(config_path: Optional[Path] = None) -> Config:
    """
    Load configuration from file.

    Args:
        config_path: Path to config file (default: ~/.pr-agent/config.json)

    Returns:
        Config object (empty config if file doesn't exist)
    """
    path = config_path or DEFAULT_CONFIG_FILE

    if not path.exists():
        return Config()

    try:
        with open(path, "r") as f:
            data = json.load(f)
        return Config.model_validate(data)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config file: {e}")
    except Exception as e:
        raise ValueError(f"Failed to load config: {e}")


def save_config(config: Config, config_path: Optional[Path] = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Config object to save
        config_path: Path to config file (default: ~/.pr-agent/config.json)
    """
    path = config_path or DEFAULT_CONFIG_FILE
    ensure_config_dir()

    with open(path, "w") as f:
        json.dump(config.model_dump(), f, indent=2)


def get_credential(name: str, config: Optional[Config] = None) -> Optional[str]:
    """
    Get a credential from config or environment.

    Environment variables take precedence over config file.

    Args:
        name: Credential name (e.g., "github_token")
        config: Config object (will load if not provided)

    Returns:
        Credential value or None
    """
    # Check environment first
    env_name = name.upper()
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value

    # Fall back to config
    if config is None:
        config = load_config()

    return getattr(config.credentials, name, None)


def detect_github_repo(path: Path) -> Optional[str]:
    """
    Detect GitHub repo from git remote.

    Args:
        path: Path to git repository

    Returns:
        GitHub repo name (owner/repo) or None
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None

        url = result.stdout.strip()

        # Parse GitHub URL
        # https://github.com/owner/repo.git
        # git@github.com:owner/repo.git
        if "github.com" in url:
            if url.startswith("git@"):
                # git@github.com:owner/repo.git
                parts = url.split(":")[-1]
            else:
                # https://github.com/owner/repo.git
                parts = url.split("github.com/")[-1]

            # Remove .git suffix
            repo = parts.rstrip(".git")
            return repo

        return None
    except Exception:
        return None
