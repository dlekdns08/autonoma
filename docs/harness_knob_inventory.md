# Harness Knob Inventory — Phase 0 Scan

> Source: read-only audit of `src/autonoma/agents/{base,director,swarm}.py` plus supporting modules.
> **Total: 68 behavioral knobs** (44 numeric/boolean, 24 algorithmic/branch).
> This document is the input to Phase 1 (`HarnessPolicy` model design).

## Summary by section

| Section     | Cat A (numeric/bool) | Cat B (algorithmic/enum) | Total |
|-------------|----------------------|---------------------------|-------|
| LOOP        | 3                    | 2                         | 5     |
| ACTION      | 8                    | 3                         | 11    |
| DECISION    | 1                    | 5                         | 6     |
| MEMORY      | 4                    | 1                         | 5     |
| SPAWN       | 2                    | 2                         | 4     |
| ROUTING     | 1                    | 1                         | 2     |
| SAFETY      | 0                    | 2                         | 2     |
| MOOD        | 3                    | 2                         | 5     |
| TERMINATION | 1                    | 2                         | 3     |
| OTHER       | 21                   | 4                         | 25    |
| **Total**   | **44**               | **24**                    | **68**|

## Key numeric/boolean knobs (Category A)

- **Timeouts**: agent 90s, LLM 60s, sandbox wall-time 8s
- **Limits**: inbox 50 msgs, agents 8 max, memory 20 private / 15 hindsight
- **TTS budgets**: 800 chars/round, 20 000 chars/session
- **XP rewards**: task 30 XP, file 15 XP, events 5–50 XP
- **Relationship thresholds**: friends ≥ 0.7, rivals ≤ 0.3, guild ≥ 0.6
- **Periodic intervals (round modulo)**: trading 4, guilds 5, campfire 7, quests 3
- **World events**: 8 XP rewards (morale, inspiration, treasure, mentorship…)

## Key algorithmic branches (Category B)

- **JSON extraction fallback chain**: direct parse → markdown fence → brace block
- **LLM error handling**: connection backoff vs rate-limit sleep vs parse-error fallback
- **Stall detection**: 3-round counter → auto-unblock (REVIEW approve → dependency clear)
- **Message priority**: `task_assign (0) > help_request (1) > review (3) > chat (9)`
- **Action dispatch mode**: strict vs permissive harness enforcement
- **Mood reactions**: weather affect 30 %, sentiment ≥ 0.7 positive / ≤ 0.3 negative
- **Completion detection**: all-tasks-done required vs accept incomplete
- **Guild formation**: auto-form from high-trust clusters
- **Exit condition**: project_complete / max_rounds_reached / stopped_externally

## Surprising / non-obvious behaviors

1. **Weather mood mod** (`swarm.py:358`) — 30 % chance per round
2. **Three-round stall escalation** (`director.py:390-442`) — auto-approves stuck REVIEW tasks, then force-clears dependencies
3. **Periodic sync** (`swarm.py:444-469`) — Trading 4 / Guilds 5 / Campfire 7 / Quests 3
4. **Trust never decays passively** — only +0.1 / −0.15 per interaction
5. **Legendary/rare spawns get 2× drama weight** in narrative

## UI exposure recommendation

### Default (always visible)
- `loop.max_rounds`
- `action.sandbox_wall_time_sec`
- `xp.level_threshold_multiplier`

### Advanced (behind toggle)
- `memory.max_private_memories`
- `periodics.trading_post_interval_rounds`
- `dreams.night_dream_probability`
- Most of the `OTHER` section

### Admin-only (dangerous)
- `safety.harness_enforcement_level`
- `termination.all_tasks_done_required`
- `spawn.max_agents` (upper bound)

## Dangerous combos to reject in validation

| Input                                          | Effect                          |
|-----------------------------------------------|---------------------------------|
| `loop.max_rounds = 1`                         | Project times out immediately   |
| `spawn.max_agents = 0`                        | Director can't spawn anything   |
| `action.sandbox_memory_mb = 0`                | All code execution fails        |
| `safety.harness_enforcement_level = OFF`      | Bypasses every constraint       |
| `termination.all_tasks_done_required = false` | Incomplete projects auto-complete |

Enforce bounds: `max_rounds ≥ 10`, `max_agents ≥ 1`, `sandbox_memory_mb ≥ 64`.

## Implementation notes for Phase 1+

- Policy delivered via `SwarmPolicy` Pydantic model, fields 1-to-1 with knob list.
- Merge order at swarm init: **defaults → global config → per-user preset → per-run inline overrides**.
- Emit `swarm.initialized` event with full policy snapshot for audit trail.
- XP/threshold changes may retroactively affect persisted character state — needs policy versioning (revisit in Phase 7).
- Exact file:line references per knob were produced during the scan but not persisted in this summary pass; re-run grep during Phase 2/3 refactor when we need them.
