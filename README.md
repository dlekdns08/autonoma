# Autonoma

**Self-Organizing Agent Swarm** with animated terminal UI.

```
     ╔═╗╦ ╦╔╦╗╔═╗╔╗╔╔═╗╔╦╗╔═╗
     ╠═╣║ ║ ║ ║ ║║║║║ ║║║║╠═╣
     ╩ ╩╚═╝ ╩ ╚═╝╝╚╝╚═╝╩ ╩╩ ╩
     Self-Organizing Agent Swarm
```

## Concept

Describe what you want to build. Autonoma's Director agent autonomously:

1. **Decomposes** your goal into tasks
2. **Spawns** specialized agents with creative names
3. **Assigns** work based on agent skills
4. **Monitors** progress and adjusts the plan
5. **Celebrates** when everything is done

All visualized in a **comic-style animated TUI** where you see agents think, talk, and work.

## The Animated TUI

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 🌟 AUTONOMA Self-Organizing Agent Swarm │ my-project │ Round 5/30          │
├────────────────────────────────────────────┬─────────────────────────────────┤
│  🎬 Stage                                 │  📋 Tasks                       │
│                                            │  ✅ Design architecture          │
│   ┌──────────────┐                         │  🔄 Implement core module       │
│   │ Let me think │    ┌─────────────────┐  │  📌 Write tests                 │
│   └──────┬───────┘    │ Writing main.py │  │  ⬜ Create documentation        │
│          ╰            └────────┬────────┘  │  ⬜ Final review                │
│         👑💭           ⚡⌨                  │                                 │
│         /|\            /|\                  │  ██████░░░░ 40% (2/5 done)     │
│         / \            / \                  ├─────────────────────────────────┤
│       Director        Coder                │  📁 Files                       │
│                                            │  📂 src/                        │
│        🎨              🧪                   │    📄 main.py                   │
│        /|\             /|\                  │    📄 models.py                 │
│        / \             / \                  │  📄 README.md                   │
│      Designer        Tester                │                                 │
├────────────────────────────────────────────┤                                 │
│  💬 Activity                               │                                 │
│  14:23:01 👑 Director: Plan ready!         │                                 │
│  14:23:02 ⚡ Coder: Writing main.py...     │                                 │
│  14:23:05 📄 Coder created src/main.py     │                                 │
└────────────────────────────────────────────┴─────────────────────────────────┘
```

## What Makes It Special

### 1. Fully Autonomous
- **No predefined roles** — the Director decides what agents are needed
- **Self-assigning tasks** — agents pick up work that matches their skills  
- **Dynamic spawning** — new agents are created when the workload demands it
- **Agent communication** — agents talk to each other, ask for help, negotiate

### 2. Comic-Style Animation
- **ASCII character sprites** that change based on state (thinking, working, celebrating)
- **Speech bubbles** that appear and fade
- **Movement** — agents move around the stage
- **Frame-by-frame animation** at configurable tick rate

## Quick Start

```bash
cd autonoma
uv sync

export ANTHROPIC_API_KEY=sk-ant-xxxxx

# Build something
autonoma build "A REST API for managing bookmarks with tags and search"

# Interactive mode
autonoma interactive

# Demo
autonoma demo
```

## Architecture

```
src/autonoma/
├── models.py          # Core data models (Persona, Task, Message, Position...)
├── config.py          # Settings
├── event_bus.py       # Async pub/sub with wildcards
├── cli.py             # Click CLI (build, interactive, demo)
├── agents/
│   ├── base.py        # AutonomousAgent (think→decide→act loop)
│   ├── director.py    # DirectorAgent (decomposes goals, spawns agents)
│   └── swarm.py       # AgentSwarm (lifecycle, routing, animation)
├── tui/
│   ├── sprites.py     # ASCII sprites, speech bubbles, animation frames
│   └── renderer.py    # Rich Layout animated dashboard
├── engine/
│   └── runner.py      # AutonomaEngine (unified swarm + TUI + workspace)
└── workspace/
    └── manager.py     # File output manager
```

## How Agents Decide

Each agent runs an autonomous **think→act** loop:

1. **Observe**: Build a situation report (tasks, messages, files, team status)
2. **Decide**: Ask Claude to choose the next action from: `work_on_task`, `create_file`, `send_message`, `request_help`, `spawn_agent`, `complete_task`, `celebrate`
3. **Act**: Execute the chosen action, updating project state
4. **Repeat**: Until all tasks are done

The Director has special powers: decomposing goals and spawning agents.

## Tests

```bash
uv run pytest tests/ -v
```
