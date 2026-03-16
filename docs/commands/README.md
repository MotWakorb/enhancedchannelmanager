# ECM Claude Code Commands

Custom slash commands for [Claude Code](https://claude.com/claude-code) that help you work with ECM's auto-creation rules.

## Available Commands

| Command | Description |
|-|-|
| `/analyze-rules` | Analyze auto-creation YAML rules and/or execution logs for issues |

## Installation

Download the command file to your Claude Code commands directory:

```bash
mkdir -p ~/.claude/commands
curl -o ~/.claude/commands/analyze-rules.md \
  https://raw.githubusercontent.com/MotWakorb/enhancedchannelmanager/dev/docs/commands/analyze-rules.md
```

The command is available immediately — no restart needed.

## Usage

### Analyze YAML rules
```
/analyze-rules <paste your YAML rules here>
```

### Analyze YAML rules + execution log
```
/analyze-rules Here are my rules:
<paste YAML>

And here is the execution log:
<paste log>
```

### Analyze execution log only
```
/analyze-rules Here is the execution log from my auto-creation run:
<paste log>
```

## Updating

To get the latest version:

```bash
curl -o ~/.claude/commands/analyze-rules.md \
  https://raw.githubusercontent.com/MotWakorb/enhancedchannelmanager/dev/docs/commands/analyze-rules.md
```
