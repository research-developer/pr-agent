"""
PR-Agent CLI for local daemon management.

Commands:
    pr-agent init     - Add current repo to config
    pr-agent start    - Start polling daemon
    pr-agent stop     - Stop daemon
    pr-agent status   - Show daemon status
    pr-agent config   - View/edit config
"""

from pr_agent.cli.config import Config, load_config, save_config
from pr_agent.cli.main import main as run

__all__ = ["Config", "load_config", "save_config", "run"]
