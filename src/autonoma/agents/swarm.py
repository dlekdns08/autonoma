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
    AgentMessage,
    AgentPersona,
    MessageType,
    Position,
    ProjectState,
    TaskStatus,
)
from autonoma.world import (
    BossArena,
    Campfire,
    DebateArena,
    DreamEngine,
    FortuneCookieJar,
    GhostRealm,
    GossipNetwork,
    GuildRegistry,
    Leaderboard,
    Mood,
    MultiverseEngine,
    NarrativeEngine,
    PostOffice,
    QuestBoard,
    RelationshipGraph,
    TradingPost,
    WorldClock,
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
        self.world_clock = WorldClock()
        self.dreams = DreamEngine()
        self.quest_board = QuestBoard()
        self.trading_post = TradingPost()
        self.boss_arena = BossArena()
        self.post_office = PostOffice()
        self.fortune_jar = FortuneCookieJar()
        self.ghost_realm = GhostRealm()
        self.multiverse = MultiverseEngine()

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

            # ── Tick World Clock ──
            clock_changes = self.world_clock.tick(self._round)
            if clock_changes:
                await bus.emit("world.clock", **clock_changes, sky=self.world_clock.sky_line)

            # Apply weather mood modifier
            mood_mod = self.world_clock.get_mood_modifier()
            if mood_mod:
                for agent in self.agents.values():
                    if random.random() < 0.3:  # 30% chance weather affects mood
                        agent.mood = mood_mod

            # Build relationship data for frontend
            relationships = []
            for (a, b), rel in self.relationships._graph.items():
                if rel.familiarity > 0:
                    relationships.append({"from": a, "to": b, "trust": rel.trust})

            await bus.emit(
                "swarm.round", round=self._round, max_rounds=max_rounds,
                sky=self.world_clock.sky_line,
                relationships=relationships,
            )

            # ── Fortune Cookies (dawn) ──
            if self.world_clock.time_of_day.value == "dawn":
                await self._distribute_fortune_cookies()

            # ── World Event Check ──
            agent_names = [n for n in self.agents if n != "Director"]
            world_event = self.world_events.maybe_generate(self._round, agent_names)
            if world_event:
                await self._apply_world_event(world_event)

            # ── Boss Fight Check ──
            await self._check_boss_fight(agent_names)

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
                        agent.stats.errors += 1
                        await bus.emit(
                            "agent.error",
                            agent=agent.name,
                            error=str(result),
                        )
                        # Ghost creation on 3+ errors
                        if agent.stats.errors >= 3 and agent.name not in [g.name for g in self.ghost_realm.ghosts]:
                            self._create_ghost(agent, "errors")
                    elif isinstance(result, dict):
                        # Track relationships from actions
                        self._track_relationships(agent, result)
                        # Check fortune cookie fulfillment
                        action = result.get("action", "")
                        self._check_fortune(agent.name, action)
                        # Check quest completion
                        self._check_quests(agent, result)

            # Route messages (only new ones)
            self._route_messages(project)

            # ── Gossip spread between agents ──
            self._spread_gossip()

            # ── Love Letters / Hate Mail ──
            self._check_letters()

            # ── Trading Post (every 4 rounds) ──
            if self._round % 4 == 0 and self._round > 0:
                self._auto_trades()

            # ── Guild formation check (every 5 rounds) ──
            if self._round % 5 == 0 and self._round > 0:
                await self._check_guild_formation()

            # ── Campfire ritual (every 7 rounds) ──
            if self._round % 7 == 0 and self._round > 0:
                await self._run_campfire()

            # ── Dreams at night ──
            if self.world_clock.is_night:
                await self._generate_dreams()

            # ── Ghost appearances ──
            await self._ghost_appearances()

            # ── Quest expiry check ──
            expired = self.quest_board.expire_quests(self._round)
            for q in expired:
                await bus.emit("quest.expired", agent=q.assigned_to, quest=q.title)

            # ── Assign new quests ──
            if self._round % 3 == 0:
                self._assign_quests()

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

        # Final leaderboard + epilogue + multiverse report
        epilogue = self.narrative.render_epilogue()
        leaderboard_text = self.leaderboard.render()
        multiverse_report = self.multiverse.get_what_if_report()
        graveyard = self.ghost_realm.get_graveyard()

        await bus.emit(
            "swarm.finished",
            rounds=self._round,
            completed=project.completed,
            files=len(project.files),
            total_tokens=total_tokens,
            epilogue=epilogue,
            leaderboard=leaderboard_text,
            multiverse=multiverse_report,
            graveyard=graveyard,
        )
        return project

    def stop(self) -> None:
        self._running = False

    async def inject_human_message(self, text: str) -> bool:
        """Deliver a human feedback message into the Director's inbox mid-run.

        Returns True if the message was delivered, False if no director exists.
        """
        director = self.agents.get("Director")
        if director is None:
            return False

        msg = AgentMessage(
            sender="human",
            recipient="Director",
            msg_type=MessageType.CHAT,
            content=f"[HUMAN FEEDBACK] {text}",
            data={"kind": "feedback", "source": "human"},
        )
        director.receive_message(msg)
        # Mark as already routed so the swarm router does not re-deliver it.
        self._routed_message_ids.add(msg.id)

        await bus.emit("human.feedback", text=text, recipient="Director")
        return True

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

    # ── Fortune Cookies ───────────────────────────────────────────────

    async def _distribute_fortune_cookies(self) -> None:
        """Give fortune cookies at dawn."""
        for name in list(self.agents.keys()):
            if name == "Director":
                continue
            cookie = self.fortune_jar.give_cookie(name, self._round)
            if cookie:
                await bus.emit("fortune.given", agent=name, fortune=cookie.fortune)
                self.agents[name].memory.remember(
                    f"🥠 Fortune: {cookie.fortune}", "observation", self._round,
                )

    def _check_fortune(self, agent_name: str, action: str) -> None:
        """Check if an action fulfills a fortune cookie."""
        cookie = self.fortune_jar.check_fulfillment(agent_name, action)
        if cookie:
            agent = self.agents.get(agent_name)
            if agent:
                agent.stats.add_xp(cookie.bonus_xp)
                agent.mood = Mood.EXCITED
                agent.memory.remember(
                    f"Fortune fulfilled! +{cookie.bonus_xp}XP!", "success", self._round,
                )
                self.quest_board.check_completion(agent_name, "fortune_fulfilled", self._round)
                logger.info(f"[Fortune] {agent_name} fulfilled: {cookie.fortune}")

    # ── Dreams ────────────────────────────────────────────────────────

    async def _generate_dreams(self) -> None:
        """Generate dreams for agents during the night phase."""
        for name, agent in self.agents.items():
            if name == "Director":
                continue
            if random.random() > 0.5:  # 50% chance of dreaming
                continue

            friends = self.relationships.get_friends(name, threshold=0.5)
            species = agent.bones.species if agent.bones else "agent"

            dream = self.dreams.generate_dream(
                agent_name=name,
                species=species,
                memories=agent.memory.private,
                mood=agent.mood,
                relationships=friends,
                round_number=self._round,
            )

            # Apply dream effects
            if dream.bonus_xp:
                xp = int(dream.bonus_xp * self.world_clock.get_xp_modifier())
                agent.stats.add_xp(xp)
            if dream.bonus_mood:
                agent.mood = dream.bonus_mood

            agent.memory.remember(f"💤 Dream: {dream.content[:60]}", "observation", self._round)

            # Diary entry
            if hasattr(agent, 'diary') and agent.diary:
                agent.diary.write(
                    "dream_reflection", agent.mood, self._round,
                    weather=self.world_clock.weather.value,
                    time_of_day="night",
                    dream=dream.content[:40],
                )

            # Check quest: prophetic dream
            if dream.dream_type == "prophetic":
                self.quest_board.check_completion(name, "prophetic_dream", self._round)

            await bus.emit(
                "agent.dream", agent=name, dream=dream.content[:60],
                dream_type=dream.dream_type,
            )

    # ── Boss Fight ────────────────────────────────────────────────────

    async def _check_boss_fight(self, agent_names: list[str]) -> None:
        """Check for boss spawning and handle combat."""
        # Check if existing boss escaped
        if self.boss_arena.check_escape(self._round):
            boss = self.boss_arena.current_boss
            if boss:
                await bus.emit("boss.escaped", name=boss.name)
                self.multiverse.record_branch(
                    self._round, f"Boss {boss.name} escaped!",
                    "Boss got away", "We defeated the boss", "boss_defeated",
                )

        # Try spawning a new boss
        if agent_names:
            avg_level = sum(
                self.agents[n].stats.level for n in agent_names if n in self.agents
            ) // max(1, len(agent_names))

            boss = self.boss_arena.maybe_spawn_boss(self._round, avg_level)
            if boss:
                await bus.emit(
                    "boss.appeared", name=boss.name, species=boss.species,
                    level=boss.level, hp=boss.max_hp,
                )
                self.narrative._add(
                    __import__("autonoma.world", fromlist=["NarrativeEvent"]).NarrativeEvent.WORLD_EVENT,
                    f"☠ BOSS APPEARED: {boss.name} the {boss.species}! ☠",
                    self._round, [], dramatic_weight=4,
                )

        # Agents attack current boss
        if self.boss_arena.current_boss and self.boss_arena.current_boss.phase.value == "fighting":
            for name in agent_names:
                agent = self.agents.get(name)
                if agent and agent.bones:
                    result = self.boss_arena.agent_attack(
                        name, agent.bones.stats, agent.stats.level,
                    )
                    if result:
                        await bus.emit("boss.damage", agent=name, message=result)

            # Check if boss was defeated
            if self.boss_arena.current_boss.phase.value == "defeated":
                boss = self.boss_arena.current_boss
                xp_reward = boss.drops.get("xp", 50)
                for name in agent_names:
                    agent = self.agents.get(name)
                    if agent:
                        agent.stats.add_xp(xp_reward)
                        agent.mood = Mood.EXCITED
                await bus.emit("boss.defeated", name=boss.name, xp_reward=xp_reward)
                self.multiverse.record_branch(
                    self._round, f"Defeated {boss.name}!",
                    "Team victory!", "Boss escaped", "boss_defeated",
                )
                # Check quest
                if self.world_clock.weather.value == "stormy":
                    for name in agent_names:
                        self.quest_board.check_completion(name, "task_in_storm", self._round)

    # ── Love Letters & Hate Mail ──────────────────────────────────────

    def _check_letters(self) -> None:
        """Auto-send letters based on relationship changes."""
        for (frm, to), rel in self.relationships._graph.items():
            if rel.familiarity < 2:
                continue
            frm_agent = self.agents.get(frm)
            if not frm_agent or not frm_agent.bones:
                continue
            letter = self.post_office.check_and_send(
                frm, to, rel.trust, frm_agent.bones.species, self._round,
            )
            if letter:
                logger.info(f"[Letter] {letter}")

    # ── Trading Post ──────────────────────────────────────────────────

    def _auto_trades(self) -> None:
        """Auto-propose trades between high-trust agents."""
        agent_names = [n for n in self.agents if n != "Director"]
        for name in agent_names:
            agent = self.agents[name]
            if not agent.bones:
                continue
            friends = self.relationships.get_friends(name, threshold=0.6)
            for friend in friends[:1]:
                friend_agent = self.agents.get(friend)
                if not friend_agent or not friend_agent.bones:
                    continue
                trade = self.trading_post.auto_trade(
                    name, friend, agent.bones.stats, friend_agent.bones.stats,
                    self.relationships.get(name, friend).trust, self._round,
                )
                if trade:
                    logger.info(f"[Trade] {trade}")

    # ── Ghost System ──────────────────────────────────────────────────

    def _create_ghost(self, agent: AutonomousAgent, cause: str) -> None:
        """Turn a fallen agent into a ghost."""
        species = agent.bones.species if agent.bones else "unknown"
        emoji = agent.bones.species_emoji if agent.bones else "👻"
        memories = [m.text for m in agent.memory.private[-5:]]
        self.ghost_realm.create_ghost(
            agent.name, species, emoji, cause, self._round, memories,
        )
        logger.info(f"[Ghost] {agent.name} became a ghost ({cause})")

    async def _ghost_appearances(self) -> None:
        """Let ghosts appear and share wisdom."""
        messages = self.ghost_realm.maybe_appear(self._round)
        for msg in messages:
            await bus.emit("ghost.appears", message=msg)

    # ── Quest System ──────────────────────────────────────────────────

    def _assign_quests(self) -> None:
        """Assign quests to agents who need them."""
        for name in self.agents:
            if name == "Director":
                continue
            quest = self.quest_board.assign_quest(name, self._round)
            if quest:
                logger.info(f"[Quest] {name} received: {quest.title}")

    def _check_quests(self, agent: AutonomousAgent, result: dict) -> None:
        """Check if agent action completes a quest."""
        action = result.get("action", "")
        condition_map = {
            "complete_task": "task_complete",
            "create_file": "create_file",
            "send_message": "send_message",
        }
        condition = condition_map.get(action)
        if condition:
            completed = self.quest_board.check_completion(agent.name, condition, self._round)
            for quest in completed:
                agent.stats.add_xp(quest.xp_reward)
                agent.mood = Mood.EXCITED
                logger.info(f"[Quest Complete] {agent.name}: {quest.title} (+{quest.xp_reward}XP)")
                # Record multiverse branch
                self.multiverse.record_branch(
                    self._round,
                    f"{agent.name} completed quest '{quest.title}'",
                    "Quest completed!", "Quest was skipped",
                    "task_complete",
                )
