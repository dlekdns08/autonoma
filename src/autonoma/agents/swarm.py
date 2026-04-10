"""Agent Swarm - manages dynamic creation and lifecycle of autonomous agents.

Integrates world systems: relationships, world events, movement, round tracking.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from autonoma.agents.base import AutonomousAgent
from autonoma.agents.director import DirectorAgent
from autonoma.agents.harness import get_harness
from autonoma.config import settings
from autonoma.event_bus import bus
from autonoma.models import (
    AgentPersona,
    Position,
    ProjectState,
    TaskStatus,
)
from autonoma.world import (
    Campfire,
    DebateArena,
    GossipNetwork,
    GuildRegistry,
    Leaderboard,
    Mood,
    NarrativeEngine,
    RelationshipGraph,
    WorldEventQueue,
    WorldEventType,
    check_achievements,
)

logger = logging.getLogger(__name__)

SPAWN_POSITIONS = [
    Position(x=5, y=3),
    Position(x=25, y=3),
    Position(x=45, y=3),
    Position(x=65, y=3),
    Position(x=5, y=10),
    Position(x=25, y=10),
    Position(x=45, y=10),
    Position(x=65, y=10),
]

AGENT_TIMEOUT_SECONDS = 90


class AgentSwarm:
    """Manages the self-organizing swarm of autonomous agents."""

    def __init__(self) -> None:
        self.agents: dict[str, AutonomousAgent] = {}
        self.director = DirectorAgent()
        self._running = False
        self._round = 0
        self._routed_message_ids: set[str] = set()

        # ── World Systems ──
        self.relationships = RelationshipGraph()
        self.world_events = WorldEventQueue()
        self.guilds = GuildRegistry()
        self.gossip = GossipNetwork()
        self.campfire = Campfire()
        self.debate_arena = DebateArena()
        self.leaderboard = Leaderboard()
        self.narrative = NarrativeEngine()

        self.director.position = Position(x=35, y=1)
        self.agents["Director"] = self.director

        bus.on("agent.spawn_requested", self._on_spawn_request)

    async def initialize(self, project: ProjectState) -> None:
        """Set up the swarm: director decomposes goal, agents get created."""
        project.agents.append(self.director.persona)
        await bus.emit("swarm.initializing", agent_count=1)

        tasks = await self.director.decompose_goal(project)

        # Wait for spawn events to process
        await asyncio.sleep(0.2)

        # Sync spawned agents into project state
        for name, agent in self.agents.items():
            if not any(a.name == name for a in project.agents):
                project.agents.append(agent.persona)

        await bus.emit(
            "swarm.ready",
            agent_count=len(self.agents),
            task_count=len(tasks),
        )

    async def run(self, project: ProjectState, max_rounds: int = 30) -> ProjectState:
        """Run the swarm's think-act loop until project is complete."""
        self._running = True
        self._round = 0

        await bus.emit("swarm.started", max_rounds=max_rounds)

        while self._running and self._round < max_rounds:
            self._round += 1

            # Update round number on all agents
            for agent in self.agents.values():
                agent._round_number = self._round

            await bus.emit("swarm.round", round=self._round, max_rounds=max_rounds)

            # ── World Event Check ──
            agent_names = [n for n in self.agents if n != "Director"]
            world_event = self.world_events.maybe_generate(self._round, agent_names)
            if world_event:
                await self._apply_world_event(world_event)

            # Director goes first
            try:
                director_result = await asyncio.wait_for(
                    self.director.think_and_act(project),
                    timeout=AGENT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning("[Swarm] Director timed out")
                director_result = {"action": "timeout"}

            if director_result.get("action") == "project_complete":
                break

            # All other agents act concurrently with individual timeouts
            other_agents = [a for name, a in self.agents.items() if name != "Director"]
            if other_agents:
                tasks = [
                    asyncio.wait_for(
                        agent.think_and_act(project),
                        timeout=AGENT_TIMEOUT_SECONDS,
                    )
                    for agent in other_agents
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Log agent results and update relationships
                for agent, result in zip(other_agents, results):
                    if isinstance(result, Exception):
                        logger.warning(f"[Swarm] Agent '{agent.name}' error: {result}")
                        await bus.emit(
                            "agent.error",
                            agent=agent.name,
                            error=str(result),
                        )
                    elif isinstance(result, dict):
                        # Track relationships from actions
                        self._track_relationships(agent, result)

            # Route messages (only new ones)
            self._route_messages(project)

            # ── Gossip spread between agents ──
            self._spread_gossip()

            # ── Guild formation check (every 5 rounds) ──
            if self._round % 5 == 0 and self._round > 0:
                await self._check_guild_formation()

            # ── Campfire ritual (every 7 rounds) ──
            if self._round % 7 == 0 and self._round > 0:
                await self._run_campfire()

            # ── Update leaderboard & check achievements ──
            self._update_world_stats()

            # Move agents toward interaction targets
            self._animate_movement(project)

            # Tick animations
            self._tick_animations()

            if project.completed:
                # Narrate project completion
                agent_names = list(self.agents.keys())
                self.narrative.narrate_project_complete(
                    project.goal or "the project", agent_names, self._round,
                )
                break

            await asyncio.sleep(0.1)

        self._running = False

        # Collect total token usage
        total_tokens = sum(a.total_tokens for a in self.agents.values())

        # Final leaderboard + epilogue
        epilogue = self.narrative.render_epilogue()
        leaderboard_text = self.leaderboard.render()

        await bus.emit(
            "swarm.finished",
            rounds=self._round,
            completed=project.completed,
            files=len(project.files),
            total_tokens=total_tokens,
            epilogue=epilogue,
            leaderboard=leaderboard_text,
        )
        return project

    def stop(self) -> None:
        self._running = False

    def spawn_agent(
        self,
        name: str,
        role: str,
        skills: list[str],
        emoji: str = "🤖",
        color: str = "cyan",
    ) -> AutonomousAgent | None:
        """Dynamically create and register a new agent."""
        if name in self.agents:
            return self.agents[name]

        if len(self.agents) >= settings.max_agents:
            logger.warning(f"[Swarm] Cannot spawn '{name}': max agents ({settings.max_agents}) reached")
            return None

        # Match role to a harness for capability enforcement
        harness = get_harness(role)

        persona = AgentPersona(
            name=name,
            emoji=emoji,
            role=role,
            skills=skills or harness.default_skills,
            color=color,
        )
        agent = AutonomousAgent(persona, harness=harness)

        # Assign spawn position
        idx = len(self.agents) - 1  # -1 for Director
        if idx < len(SPAWN_POSITIONS):
            agent.position = SPAWN_POSITIONS[idx]
        else:
            agent.position = Position(
                x=random.randint(5, 70),
                y=random.randint(3, 12),
            )

        self.agents[name] = agent

        # Narrate the spawn
        if agent.bones:
            self.narrative.narrate_spawn(
                name, agent.bones.species, role, agent.bones.rarity, self._round,
            )

        return agent

    # ── World Event Application ────────────────────────────────────────

    async def _apply_world_event(self, event) -> None:
        """Apply a world event's effects to the swarm."""
        await bus.emit(
            "world.event",
            event_type=event.event_type.value,
            title=event.title,
            description=event.description,
            round=event.round_number,
        )

        # Narrate the event
        from autonoma.world import NarrativeEvent
        self.narrative._add(
            NarrativeEvent.WORLD_EVENT,
            f"{event.title} — {event.description}",
            event.round_number,
            event.affects or [],
        )

        if event.event_type == WorldEventType.MORALE_BOOST:
            for agent in self.agents.values():
                agent.mood = Mood.HAPPY
                agent.memory.remember("Team morale boost!", "observation", self._round)
            event.resolved = True

        elif event.event_type == WorldEventType.COFFEE_BREAK:
            for agent in self.agents.values():
                agent.mood = Mood.RELAXED
                agent.stats.add_xp(5)
            event.resolved = True

        elif event.event_type == WorldEventType.INSPIRATION:
            if event.affects:
                target = self.agents.get(event.affects[0])
                if target:
                    target.stats.add_xp(25)
                    target.mood = Mood.EXCITED
                    target.memory.remember("Flash of inspiration! Bonus XP!", "success", self._round)
            event.resolved = True

        elif event.event_type == WorldEventType.CHALLENGE:
            for agent in self.agents.values():
                agent.mood = Mood.DETERMINED
            event.resolved = True

        elif event.event_type == WorldEventType.THUNDERSTORM:
            for agent in self.agents.values():
                if agent.bones and agent.bones.stats.get("patience", 5) < 4:
                    agent.mood = Mood.FRUSTRATED
                else:
                    agent.mood = Mood.WORRIED
                agent.memory.remember("A thunderstorm struck!", "observation", self._round)
            event.resolved = True

        elif event.event_type == WorldEventType.LUCKY_STAR:
            for agent in self.agents.values():
                agent.stats.add_xp(10)
                agent.mood = Mood.HAPPY
            event.resolved = True

        elif event.event_type == WorldEventType.MENTORSHIP:
            agents_by_level = sorted(
                [(n, a) for n, a in self.agents.items() if n != "Director"],
                key=lambda x: x[1].stats.level,
            )
            if len(agents_by_level) >= 2:
                mentor_name, mentor = agents_by_level[-1]
                apprentice_name, apprentice = agents_by_level[0]
                apprentice.stats.add_xp(20)
                self.relationships.record(mentor_name, apprentice_name, "mentored", positive=True)
                self.relationships.record(apprentice_name, mentor_name, "learned from", positive=True)
            event.resolved = True

        elif event.event_type == WorldEventType.TREASURE_FOUND:
            if event.affects:
                target = self.agents.get(event.affects[0])
                if target:
                    target.stats.add_xp(50)
                    target.mood = Mood.EXCITED
            event.resolved = True

        elif event.event_type == WorldEventType.FRIENDSHIP_DAY:
            for (frm, to), rel in self.relationships._graph.items():
                if rel.familiarity > 0:
                    rel.record_interaction("Friendship Day boost!", positive=True)
            for agent in self.agents.values():
                agent.mood = Mood.HAPPY
            event.resolved = True

        else:
            event.resolved = True

    # ── Relationship Tracking ──────────────────────────────────────────

    def _track_relationships(self, agent: AutonomousAgent, result: dict) -> None:
        """Update relationship graph based on agent actions + gossip observations."""
        action = result.get("action", "")

        if action == "send_message":
            target = result.get("to", "")
            if target and target != "all" and target in self.agents:
                self.relationships.record(agent.name, target, "sent message", positive=True)

        elif action == "complete_task":
            self.relationships.record("Director", agent.name, "completed task", positive=True)
            # Gossip: others observe the completion
            for other_name in self.agents:
                if other_name != agent.name and other_name != "Director":
                    self.gossip.observe(
                        other_name, agent.name, "completed a task", "positive", self._round,
                    )
            # Narrative
            species = agent.bones.species if agent.bones else "agent"
            task_title = result.get("task_id", "a task")
            self.narrative.narrate_task_complete(agent.name, task_title, species, self._round)

        elif action == "request_help":
            self.relationships.record(agent.name, "Director", "asked for help", positive=True)

        elif action == "create_file":
            # Positive gossip for creating files
            for other_name in self.agents:
                if other_name != agent.name:
                    self.gossip.observe(
                        other_name, agent.name, "created a file", "positive", self._round,
                    )

    # ── Agent Movement ─────────────────────────────────────────────────

    def _animate_movement(self, project: ProjectState) -> None:
        """Set movement targets based on agent interactions."""
        for agent in self.agents.values():
            # Move toward the agent you're talking to
            if agent.state.value == "talking" and agent.speech:
                # Find the last message recipient
                for msg in reversed(project.messages[-5:]):
                    if msg.sender == agent.name and msg.recipient in self.agents:
                        target = self.agents[msg.recipient]
                        agent.target_position = Position(
                            x=max(0, target.position.x - 5),
                            y=target.position.y,
                        )
                        break

            # Move to celebrate near Director when done
            elif agent.state.value == "celebrating":
                agent.target_position = Position(
                    x=self.director.position.x + random.randint(-8, 8),
                    y=self.director.position.y + 3,
                )

    # ── Message Routing ────────────────────────────────────────────────

    def _route_messages(self, project: ProjectState) -> None:
        """Deliver only new (unrouted) messages to agent inboxes."""
        for msg in project.messages:
            if msg.id in self._routed_message_ids:
                continue
            self._routed_message_ids.add(msg.id)

            if msg.recipient == "all":
                for agent in self.agents.values():
                    if agent.name != msg.sender:
                        agent.receive_message(msg)
            elif msg.recipient in self.agents:
                self.agents[msg.recipient].receive_message(msg)

    def _tick_animations(self) -> None:
        for agent in self.agents.values():
            agent.tick_speech()
            agent.tick_movement()

    async def _on_spawn_request(
        self,
        requester: str = "",
        name: str = "Worker",
        role: str = "general",
        skills: list[str] | None = None,
        emoji: str = "🤖",
        color: str = "cyan",
        **_: Any,
    ) -> None:
        agent = self.spawn_agent(
            name=name,
            role=role,
            skills=skills or ["coding"],
            emoji=emoji,
            color=color,
        )

        if agent:
            await bus.emit(
                "agent.spawned",
                name=name,
                role=role,
                emoji=emoji,
                requester=requester,
            )
            # Initial relationship: spawner trusts the spawned agent
            self.relationships.record(requester, name, "spawned agent", positive=True)
        else:
            await bus.emit(
                "agent.spawn_failed",
                name=name,
                reason="max_agents_reached",
                requester=requester,
            )

    # ── Gossip Spreading ──────────────────────────────────────────────

    def _spread_gossip(self) -> None:
        """Each round, agents spread gossip to nearby agents."""
        agent_names = [n for n in self.agents if n != "Director"]
        for name in agent_names:
            # Pick a random other agent to gossip with
            others = [n for n in agent_names if n != name]
            if others:
                listener = random.choice(others)
                shared = self.gossip.spread(name, listener, max_items=1)
                if shared:
                    self.agents[name].stats.gossip_shared += len(shared)

    # ── Guild Formation ───────────────────────────────────────────────

    async def _check_guild_formation(self) -> None:
        """Auto-form guilds from strong relationships."""
        agent_names = [n for n in self.agents if n != "Director"]
        formed = self.guilds.auto_form_guilds(agent_names, self.relationships, self._round)
        for guild in formed:
            members = list(guild.members.keys())
            self.narrative.narrate_guild_formed(guild.name, members, self._round)
            await bus.emit(
                "guild.formed",
                name=guild.name,
                members=members,
                synergy=guild.synergy_bonus,
                round=self._round,
            )

    # ── Campfire Ritual ───────────────────────────────────────────────

    async def _run_campfire(self) -> None:
        """Run the campfire ritual where agents share stories."""
        agent_names = [n for n in self.agents if n != "Director"]
        if not agent_names:
            return

        self.campfire.gather()
        stories_told = 0

        for name in agent_names:
            agent = self.agents[name]
            # Each agent with lessons shares one
            lessons = [m for m in agent.memory.private if m.memory_type == "lesson"]
            if lessons:
                lesson = lessons[-1]
                listeners = [n for n in agent_names if n != name]
                story = self.campfire.tell_story(
                    teller=name,
                    title=f"{name}'s lesson",
                    content=lesson.text,
                    moral=lesson.text[:50],
                    listeners=listeners,
                    round_number=self._round,
                )
                stories_told += 1
                agent.stats.campfire_stories += 1

                # Distribute as hindsight notes to listeners
                for listener_name in listeners:
                    listener = self.agents.get(listener_name)
                    if listener:
                        listener.memory.add_hindsight(
                            title=f"From {name}'s campfire story",
                            lesson=lesson.text[:100],
                            keywords=["campfire", name.lower()],
                            source_agent=name,
                            round_number=self._round,
                        )

        if stories_told > 0:
            self.narrative.narrate_campfire(stories_told, agent_names, self._round)
            await bus.emit("campfire.complete", stories=stories_told, round=self._round)

        self.campfire.dismiss()

    # ── World Stats Update ────────────────────────────────────────────

    def _update_world_stats(self) -> None:
        """Update leaderboard, check achievements and evolution."""
        for name, agent in self.agents.items():
            if name == "Director":
                continue

            agent.stats.rounds_active += 1

            # Check achievements
            newly_earned = check_achievements(agent.stats)
            for ach_id in newly_earned:
                from autonoma.world import ACHIEVEMENTS as ACH
                title = ACH[ach_id]["title"]
                self.narrative.narrate_achievement(name, title, self._round)
                # Emit for TUI
                bus._loop = None  # sync emit not available, log instead
                logger.info(f"[Achievement] {name} earned '{title}'")

            # Check evolution
            if agent.bones:
                old_species = agent.bones.species
                evolved_species, evolved_emoji = agent.bones.get_evolved_form(agent.stats.level)
                if evolved_species != old_species and not hasattr(agent, '_last_evolved_species'):
                    self.narrative.narrate_evolution(name, old_species, evolved_species, self._round)
                    agent._last_evolved_species = evolved_species
                    logger.info(f"[Evolution] {name}: {old_species} -> {evolved_species}")
                elif evolved_species != getattr(agent, '_last_evolved_species', old_species):
                    self.narrative.narrate_evolution(
                        name, getattr(agent, '_last_evolved_species', old_species),
                        evolved_species, self._round,
                    )
                    agent._last_evolved_species = evolved_species

            # Update leaderboard
            if agent.bones:
                self.leaderboard.update(
                    name, agent.stats, agent.bones,
                    self.relationships, self.gossip, self.debate_arena,
                )
