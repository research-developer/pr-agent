"""
PR-Agent CLI entry point.

Commands:
    pr-agent init     - Add current repo to config
    pr-agent start    - Start polling daemon
    pr-agent stop     - Stop daemon
    pr-agent status   - Show daemon status
    pr-agent config   - View/edit config
"""

import os
import signal
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from pr_agent.cli.config import (
    Config,
    RepoConfig,
    DEFAULT_CONFIG_FILE,
    DEFAULT_LOG_DIR,
    DEFAULT_PID_FILE,
    load_config,
    save_config,
    get_credential,
    detect_github_repo,
    ensure_config_dir,
)

app = typer.Typer(
    name="pr-agent",
    help="Local PR-Agent daemon for GitHub and Railway polling.",
    add_completion=False,
)
console = Console()


def _get_pid() -> Optional[int]:
    """Get the PID of the running daemon, if any."""
    if not DEFAULT_PID_FILE.exists():
        return None
    try:
        pid = int(DEFAULT_PID_FILE.read_text().strip())
        # Check if process is still running
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        # PID file exists but process is dead
        DEFAULT_PID_FILE.unlink(missing_ok=True)
        return None


def _is_daemon_running() -> bool:
    """Check if the daemon is currently running."""
    return _get_pid() is not None


@app.command()
def init(
    path: Optional[Path] = typer.Argument(
        None,
        help="Path to repository (default: current directory)",
    ),
    github_repo: Optional[str] = typer.Option(
        None,
        "--github-repo", "-g",
        help="GitHub repo (owner/repo). Auto-detected if not provided.",
    ),
    railway_project: Optional[str] = typer.Option(
        None,
        "--railway-project", "-r",
        help="Railway project ID to link.",
    ),
    auto_review: bool = typer.Option(
        True,
        "--auto-review/--no-auto-review",
        help="Auto-run /review on new PRs.",
    ),
    auto_describe: bool = typer.Option(
        False,
        "--auto-describe/--no-auto-describe",
        help="Auto-run /describe on new PRs.",
    ),
):
    """
    Add current repository to PR-Agent config.

    Detects the GitHub repo from git remote and stores it in ~/.pr-agent/config.json
    """
    repo_path = path or Path.cwd()
    repo_path = repo_path.resolve()

    # Check if it's a git repo
    if not (repo_path / ".git").exists():
        console.print(f"[red]Error:[/red] {repo_path} is not a git repository")
        raise typer.Exit(1)

    # Detect or validate GitHub repo
    detected_repo = detect_github_repo(repo_path)
    if github_repo:
        repo_name = github_repo
    elif detected_repo:
        repo_name = detected_repo
        console.print(f"[dim]Detected GitHub repo:[/dim] {repo_name}")
    else:
        console.print("[red]Error:[/red] Could not detect GitHub repo from git remote.")
        console.print("Please specify with --github-repo owner/repo")
        raise typer.Exit(1)

    # Load existing config
    config = load_config()

    # Check for required credentials
    github_token = get_credential("github_token", config)
    if not github_token:
        console.print("[yellow]Warning:[/yellow] GitHub token not configured.")
        console.print("Set GITHUB_TOKEN environment variable or run 'pr-agent config set github_token <token>'")

    anthropic_key = get_credential("anthropic_api_key", config)
    if not anthropic_key:
        console.print("[yellow]Warning:[/yellow] Anthropic API key not configured.")
        console.print("Set ANTHROPIC_API_KEY environment variable or run 'pr-agent config set anthropic_api_key <key>'")

    # Create repo config
    repo_config = RepoConfig(
        path=str(repo_path),
        github_repo=repo_name,
        railway_project_id=railway_project,
        auto_review=auto_review,
        auto_describe=auto_describe,
    )

    # Add to config
    existing = config.get_repo(path=str(repo_path))
    if existing:
        console.print(f"[yellow]Updating existing config for:[/yellow] {repo_path}")
    else:
        console.print(f"[green]Adding repository:[/green] {repo_path}")

    config.add_repo(repo_config)
    save_config(config)

    console.print(f"[green]✓[/green] Saved to {DEFAULT_CONFIG_FILE}")


@app.command()
def start(
    foreground: bool = typer.Option(
        False,
        "--foreground", "-f",
        help="Run in foreground instead of as daemon.",
    ),
):
    """
    Start the PR-Agent polling daemon.

    The daemon polls GitHub notifications and Railway deployments for tracked repos.
    """
    if _is_daemon_running():
        pid = _get_pid()
        console.print(f"[yellow]Daemon already running[/yellow] (PID: {pid})")
        raise typer.Exit(1)

    config = load_config()

    if not config.repos:
        console.print("[red]Error:[/red] No repositories configured.")
        console.print("Run 'pr-agent init' in a repository first.")
        raise typer.Exit(1)

    # Check credentials
    github_token = get_credential("github_token", config)
    if not github_token:
        console.print("[red]Error:[/red] GitHub token not configured.")
        raise typer.Exit(1)

    ensure_config_dir()

    if foreground:
        console.print("[green]Starting PR-Agent in foreground...[/green]")
        console.print(f"Watching {len(config.repos)} repo(s)")
        console.print("Press Ctrl+C to stop")
        _run_daemon(config)
    else:
        # Fork to background
        console.print("[green]Starting PR-Agent daemon...[/green]")

        pid = os.fork()
        if pid > 0:
            # Parent process
            console.print(f"[green]✓[/green] Daemon started (PID: {pid})")
            console.print(f"  Watching: {len(config.repos)} repo(s)")
            console.print(f"  GitHub poll: {config.settings.poll_interval_github}s")
            console.print(f"  Railway poll: {config.settings.poll_interval_railway}s")
            console.print(f"  Logs: {DEFAULT_LOG_DIR}")
            return

        # Child process - become daemon
        os.setsid()
        os.chdir("/")

        # Redirect stdio to log file
        log_file = DEFAULT_LOG_DIR / "daemon.log"
        with open(log_file, "a") as f:
            os.dup2(f.fileno(), sys.stdout.fileno())
            os.dup2(f.fileno(), sys.stderr.fileno())

        # Write PID file
        DEFAULT_PID_FILE.write_text(str(os.getpid()))

        try:
            _run_daemon(config)
        finally:
            DEFAULT_PID_FILE.unlink(missing_ok=True)


def _run_daemon(config: Config):
    """
    Run the polling daemon.

    This is the main daemon loop that runs until stopped.
    """
    import asyncio
    import logging

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.settings.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("pr_agent.daemon")

    async def main():
        from pr_agent.polling.dispatcher import EventDispatcher, create_pr_agent_handler
        from pr_agent.polling.github import GitHubPoller
        from pr_agent.polling.events import EventType

        dispatcher = EventDispatcher()

        # Setup GitHub poller
        github_token = get_credential("github_token", config)
        username = config.settings.github_username or "pr-agent"

        tracked_repos = [r.github_repo for r in config.repos]

        github_poller = GitHubPoller(
            token=github_token,
            username=username,
            poll_interval=config.settings.poll_interval_github,
            tracked_repos=tracked_repos,
        )
        dispatcher.add_poller(github_poller)

        # Register handlers
        handler = create_pr_agent_handler()
        dispatcher.register_handler(EventType.GITHUB_PR_MENTION, handler)
        dispatcher.register_handler(EventType.GITHUB_PR_OPENED, handler)

        # Setup Railway pollers for repos that have it configured
        railway_token = get_credential("railway_token", config)
        if railway_token:
            from pr_agent.polling.railway import RailwayPoller

            for repo in config.repos:
                if repo.railway_project_id:
                    railway_poller = RailwayPoller(
                        token=railway_token,
                        project_id=repo.railway_project_id,
                        poll_interval=config.settings.poll_interval_railway,
                        service_ids=repo.railway_service_ids or None,
                    )
                    dispatcher.add_poller(railway_poller)

        logger.info(f"Starting daemon with {len(dispatcher.pollers)} poller(s)")

        # Handle shutdown signals
        def handle_signal(sig, frame):
            logger.info(f"Received signal {sig}, shutting down...")
            asyncio.create_task(dispatcher.stop())

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        await dispatcher.run()

    asyncio.run(main())


@app.command()
def stop():
    """
    Stop the PR-Agent polling daemon.
    """
    pid = _get_pid()

    if not pid:
        console.print("[yellow]Daemon is not running[/yellow]")
        return

    console.print(f"Stopping daemon (PID: {pid})...")

    try:
        os.kill(pid, signal.SIGTERM)

        # Wait for process to exit
        import time
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            # Force kill if still running
            console.print("[yellow]Process not responding, sending SIGKILL...[/yellow]")
            os.kill(pid, signal.SIGKILL)

        DEFAULT_PID_FILE.unlink(missing_ok=True)
        console.print("[green]✓[/green] Daemon stopped")

    except ProcessLookupError:
        console.print("[yellow]Process already stopped[/yellow]")
        DEFAULT_PID_FILE.unlink(missing_ok=True)
    except PermissionError:
        console.print("[red]Error:[/red] Permission denied. Try with sudo.")
        raise typer.Exit(1)


@app.command()
def status():
    """
    Show PR-Agent daemon status.
    """
    config = load_config()
    pid = _get_pid()

    # Status header
    if pid:
        console.print(f"[green]Daemon:[/green] Running (PID: {pid})")
    else:
        console.print("[yellow]Daemon:[/yellow] Stopped")

    console.print()

    # Configuration
    console.print("[bold]Configuration:[/bold]")
    console.print(f"  Config file: {DEFAULT_CONFIG_FILE}")
    console.print(f"  Log directory: {DEFAULT_LOG_DIR}")
    console.print(f"  GitHub poll: {config.settings.poll_interval_github}s")
    console.print(f"  Railway poll: {config.settings.poll_interval_railway}s")
    console.print(f"  Model: {config.settings.model}")

    console.print()

    # Credentials status
    console.print("[bold]Credentials:[/bold]")
    github_token = get_credential("github_token", config)
    anthropic_key = get_credential("anthropic_api_key", config)
    railway_token = get_credential("railway_token", config)

    console.print(f"  GitHub token: {'[green]✓[/green]' if github_token else '[red]✗[/red]'}")
    console.print(f"  Anthropic key: {'[green]✓[/green]' if anthropic_key else '[red]✗[/red]'}")
    console.print(f"  Railway token: {'[green]✓[/green]' if railway_token else '[dim]not set[/dim]'}")

    console.print()

    # Repos table
    console.print("[bold]Tracked Repositories:[/bold]")
    if not config.repos:
        console.print("  [dim]No repositories configured. Run 'pr-agent init' to add one.[/dim]")
    else:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Path")
        table.add_column("GitHub")
        table.add_column("Railway")
        table.add_column("Auto")

        for repo in config.repos:
            auto_flags = []
            if repo.auto_review:
                auto_flags.append("review")
            if repo.auto_describe:
                auto_flags.append("describe")

            table.add_row(
                repo.path,
                repo.github_repo,
                repo.railway_project_id or "-",
                ", ".join(auto_flags) if auto_flags else "-",
            )

        console.print(table)


@app.command()
def config(
    action: str = typer.Argument(
        "show",
        help="Action: show, set, get",
    ),
    key: Optional[str] = typer.Argument(
        None,
        help="Config key (e.g., 'github_token', 'settings.poll_interval')",
    ),
    value: Optional[str] = typer.Argument(
        None,
        help="Value to set",
    ),
):
    """
    View or edit PR-Agent configuration.

    Examples:
        pr-agent config                          # Show all config
        pr-agent config get github_token         # Get a credential
        pr-agent config set github_token ghp_xxx # Set a credential
    """
    cfg = load_config()

    if action == "show":
        # Show full config (redact secrets)
        import json
        data = cfg.model_dump()

        # Redact credentials
        for cred_key in data.get("credentials", {}):
            val = data["credentials"][cred_key]
            if val:
                data["credentials"][cred_key] = val[:8] + "..." if len(val) > 8 else "***"

        console.print_json(json.dumps(data, indent=2))

    elif action == "get":
        if not key:
            console.print("[red]Error:[/red] Key required for 'get' action")
            raise typer.Exit(1)

        # Check credentials first
        if hasattr(cfg.credentials, key):
            val = getattr(cfg.credentials, key)
            if val:
                console.print(val[:8] + "..." if len(val) > 8 else val)
            else:
                console.print("[dim]not set[/dim]")
        elif hasattr(cfg.settings, key):
            console.print(str(getattr(cfg.settings, key)))
        else:
            console.print(f"[red]Error:[/red] Unknown key: {key}")
            raise typer.Exit(1)

    elif action == "set":
        if not key or value is None:
            console.print("[red]Error:[/red] Key and value required for 'set' action")
            raise typer.Exit(1)

        # Set credential or setting
        if hasattr(cfg.credentials, key):
            setattr(cfg.credentials, key, value)
            save_config(cfg)
            console.print(f"[green]✓[/green] Set credentials.{key}")
        elif hasattr(cfg.settings, key):
            # Try to convert to appropriate type
            current = getattr(cfg.settings, key)
            if isinstance(current, int):
                value = int(value)
            elif isinstance(current, bool):
                value = value.lower() in ("true", "1", "yes")
            setattr(cfg.settings, key, value)
            save_config(cfg)
            console.print(f"[green]✓[/green] Set settings.{key}")
        else:
            console.print(f"[red]Error:[/red] Unknown key: {key}")
            raise typer.Exit(1)

    else:
        console.print(f"[red]Error:[/red] Unknown action: {action}")
        console.print("Valid actions: show, get, set")
        raise typer.Exit(1)


@app.command()
def remove(
    path: Optional[Path] = typer.Argument(
        None,
        help="Path to repository to remove (default: current directory)",
    ),
):
    """
    Remove a repository from PR-Agent config.
    """
    repo_path = path or Path.cwd()
    repo_path = repo_path.resolve()

    cfg = load_config()

    if cfg.remove_repo(str(repo_path)):
        save_config(cfg)
        console.print(f"[green]✓[/green] Removed {repo_path} from config")
    else:
        console.print(f"[yellow]Repository not found in config:[/yellow] {repo_path}")


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
