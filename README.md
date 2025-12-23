# PR-Agent Local Daemon

A local polling-based PR agent that monitors GitHub notifications and Railway deployments, running as a background service on your machine.

> **Fork Note**: This is a fork of [Qodo's PR-Agent](https://github.com/qodo-ai/pr-agent) adapted for local daemon operation with polling instead of webhooks.

## Features

- **GitHub Notification Polling** - Responds to @mentions on PRs with AI-powered reviews, descriptions, and suggestions
- **Railway Deployment Monitoring** - Tracks deployment status and can analyze failures
- **Local Daemon** - Runs as a background service, no webhook infrastructure needed
- **Multi-Repo Support** - Monitor multiple repositories from a single daemon
- **Simple CLI** - Easy setup and management

## Quick Start

```bash
# Install
pip install -e .

# Initialize a repository
cd ~/projects/myapp
pr-agent-daemon init

# Configure credentials (or set environment variables)
pr-agent-daemon config set github_token ghp_xxx
pr-agent-daemon config set anthropic_api_key sk-ant-xxx

# Start the daemon
pr-agent-daemon start

# Check status
pr-agent-daemon status
```

## CLI Commands

The daemon uses `pr-agent-daemon` command (separate from the original `pr-agent` CLI):

| Command | Description |
|---------|-------------|
| `pr-agent-daemon init` | Add current repo to config |
| `pr-agent-daemon start` | Start polling daemon |
| `pr-agent-daemon stop` | Stop daemon |
| `pr-agent-daemon status` | Show daemon status and tracked repos |
| `pr-agent-daemon config` | View/edit configuration |
| `pr-agent-daemon remove` | Remove repo from config |

The original `pr-agent` command remains available for direct PR reviews:
```bash
pr-agent --pr_url=<URL> review
```

## Configuration

Configuration is stored at `~/.pr-agent/config.json`:

```json
{
  "version": "1.0",
  "credentials": {
    "github_token": "ghp_xxx",
    "anthropic_api_key": "sk-ant-xxx",
    "railway_token": "railway_xxx"
  },
  "settings": {
    "poll_interval_github": 30,
    "poll_interval_railway": 60,
    "model": "claude-sonnet-4-20250514",
    "log_level": "INFO",
    "github_username": "my-bot"
  },
  "repos": [
    {
      "path": "/Users/you/projects/myapp",
      "github_repo": "owner/myapp",
      "railway_project_id": "abc123",
      "auto_review": true,
      "auto_describe": false
    }
  ]
}
```

### Environment Variables

Credentials can also be set via environment variables (takes precedence over config file):

- `GITHUB_TOKEN` - GitHub personal access token
- `ANTHROPIC_API_KEY` - Anthropic API key
- `RAILWAY_TOKEN` - Railway API token

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 pr-agent-daemon CLI                      │
├─────────────────────────────────────────────────────────┤
│  init   - Add repo to config                            │
│  start  - Start polling daemon                          │
│  stop   - Stop daemon                                   │
│  status - Show status                                   │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│              ~/.pr-agent/                                │
│  config.json  - Repos, credentials, settings            │
│  daemon.pid   - PID file                                │
│  logs/        - Log files                               │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                 Polling Daemon                           │
├─────────────────────────────────────────────────────────┤
│  GitHubPoller   - Poll notifications for @mentions      │
│  RailwayPoller  - Poll deployment status changes        │
│  EventDispatcher - Route events to handlers             │
│  PRAgent        - Generate AI responses                 │
└─────────────────────────────────────────────────────────┘
```

## Usage Examples

### Respond to PR Mentions

When someone mentions your bot on a PR:

```
@my-bot /review
```

The daemon will:
1. Detect the notification via polling
2. Parse the command (`/review`)
3. Run PR-Agent's review tool
4. Post the response as a comment

### Available Commands

When mentioned on a PR, the bot responds to:

| Command | Description |
|---------|-------------|
| `/review` | Comprehensive code review |
| `/describe` | Generate PR description |
| `/improve` | Suggest code improvements |
| `/ask <question>` | Answer questions about the PR |
| `/update_changelog` | Update changelog |
| `/add_docs` | Generate documentation |

### Railway Integration

Link a Railway project to get deployment notifications:

```bash
pr-agent-daemon init --railway-project abc123
```

The daemon will monitor deployments and can:
- Alert on failed deployments
- Analyze crash logs
- Correlate deployments with recent PRs

## Development

### Project Structure

```
pr_agent/
├── daemon/                # Daemon CLI commands
│   ├── config.py         # Config management
│   └── main.py           # Typer CLI
├── polling/              # Polling infrastructure
│   ├── base.py          # Abstract BasePoller
│   ├── events.py        # Event models
│   ├── github.py        # GitHub notification poller
│   ├── railway.py       # Railway deployment poller
│   └── dispatcher.py    # Event routing
├── cli.py               # Original PR-Agent CLI
└── ...                   # Original PR-Agent tools
```

### Running in Development

```bash
# Run in foreground with debug output
pr-agent-daemon start --foreground

# Or run directly
python -m pr_agent.daemon.main start -f
```

## Requirements

- Python 3.12+
- GitHub personal access token with `notifications` and `repo` scopes
- Anthropic API key (or OpenAI API key)
- Railway API token (optional, for deployment monitoring)

## License

Apache 2.0 - See [LICENSE](LICENSE)

## Acknowledgments

Based on [Qodo PR-Agent](https://github.com/qodo-ai/pr-agent), the original AI-powered PR assistant.
