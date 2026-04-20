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
from autonoma.db.registry import CharacterRegistry
from autonoma.event_bus import bus
from autonoma.llm import LLMConfig
from autonoma.tracing import finish_run, start_run
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

    def __init__(
        self,
        llm_config: LLMConfig | None = None,
        registry: CharacterRegistry | None = None,
    ) -> None:
        self._llm_config = llm_config
        self.agents: dict[str, AutonomousAgent] = {}
        self.director = DirectorAgent(llm_config=llm_config)
        self._running = False
        self._round = 0
        self._routed_message_ids: set[str] = set()

        # Persistent character registry. When None we use a disabled one,
        # which short-circuits every DB call so tests and one-shot scripts
        # don't need to spin up SQLite. Real server paths pass a real one.
        self.registry: CharacterRegistry = registry or CharacterRegistry(
            enabled=settings.persistent_characters,
        )
        # List[{character_uuid, round, cause, epitaph}] — populated by
        # _create_ghost so finish_project can persist it.
        self._deaths: list[dict[str, Any]] = []
        self._wills: list[dict[str, str]] = []
        # Captured by _run_loop just before it exits so the finally-block
        # persister gets the right exit reason.
        self._last_exit_reason: str = ""

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

        model_name = self._llm_config.model if self._llm_config else settings.model
        recorder = start_run(goal=project.description or project.name, model=model_name)

        # Open a DB-persisted project row. Fire-and-forget failure: if the
        # DB is unreachable we log and degrade to disabled-registry mode
        # rather than crashing the swarm. (Important: never let persistence
        # block the run.)
        try:
            await self.registry.begin_project(
                name=project.name,
                description=project.description or "",
                goal=project.description or project.name,
                max_rounds=max_rounds,
            )
            # Opportunistically hydrate the director, and anyone already
            # spawned during initialize() before the registry was live.
            for agent in list(self.agents.values()):
                await self._hydrate_agent(agent)
        except Exception as exc:  # pragma: no cover — persistence is non-critical
            logger.warning("Registry begin_project failed, continuing without persistence: %s", exc)

        try:
            return await self._run_loop(project, max_rounds, recorder)
        finally:
            finish_run(recorder)
            await self._finish_registry(project)

    async def _hydrate_agent(self, agent: AutonomousAgent) -> None:
        """Fetch persisted state for ``agent`` from the registry and apply it
        to the in-memory instance. No-op when the registry is disabled."""
        if not self.registry.enabled:
            return
        try:
            live = await self.registry.hydrate(
                role=agent.persona.role,
                name=agent.name,
                bones_species=agent.bones.species,
                bones_species_emoji=agent.bones.species_emoji,
                bones_catchphrase=agent.bones.catchphrase,
                bones_rarity=agent.bones.rarity,
                bones_stats=agent.bones.stats,
                bones_traits=[t.value for t in agent.bones.traits],
            )
        except Exception as exc:  # pragma: no cover — non-critical
            logger.warning("Registry hydrate failed for %s: %s", agent.name, exc)
            return

        # Attach uuid so death/relationship persistence can find the right row.
        agent.character_uuid = live.character_uuid
        # Carry over the voice across sessions too — hearing the same
        # character narrated by the same voice is half the charm.
        if live.voice_id:
            agent.voice_id = live.voice_id
        else:
            # First time this character speaks: pick now, store on live so
            # finish_project persists it.
            voice = agent._resolve_voice()
            live.voice_id = voice
        # Apply persisted growth: level + total XP carry over, stats carry over
        # (they hold lifetime averages). The per-run counters (tasks_completed
        # etc. on AgentStats) intentionally restart at 0 — AgentStats tracks
        # PER-RUN metrics; lifetime totals live on the DB row.
        if not live.is_new:
            agent.stats.level = live.level
            # Preserve XP progress into the next level by re-applying earned XP
            # relative to the new threshold.
            agent.stats.xp = 0
            # Seed memory with a short legacy note so the LLM knows they've
            # been here before. Only meaningful if they actually have history.
            legacy_bits: list[str] = []
            if live.runs_survived or live.runs_died:
                legacy_bits.append(
                    f"Career: {live.runs_survived} runs survived, "
                    f"{live.runs_died} runs died."
                )
            if live.past_wills:
                legacy_bits.append(f"Past last words: {live.past_wills[0]}")
            if live.past_epitaphs:
                legacy_bits.append(f"Epitaph on file: {live.past_epitaphs[0]}")
            if legacy_bits:
                agent.memory.remember(
                    " ".join(legacy_bits), memory_type="lesson", round_number=0,
                )

    async def _finish_registry(self, project: ProjectState) -> None:
        """Persist every per-run mutation back to the DB at run end."""
        if not self.registry.enabled or self.registry.project_uuid is None:
            return
        try:
            # Sync every cached LiveCharacter with the latest in-memory stats.
            for agent in self.agents.values():
                uid = getattr(agent, "character_uuid", None)
                if not uid:
                    continue
                live = next(
                    (c for c in self.registry.cached() if c.character_uuid == uid),
                    None,
                )
                if live is None:
                    continue
                live.level = agent.stats.level
                live.total_xp_earned = agent.stats.total_xp_earned
                live.tasks_completed_lifetime += agent.stats.tasks_completed
                live.files_created_lifetime += agent.stats.files_created
                live.last_mood = agent.mood.value if agent.mood else ""
                live.stats = dict(agent.bones.stats)

            # Build relationship list — only include edges with familiarity.
            # Build a name→uuid index once (O(n)) instead of calling
            # resolve_name inside the loop (which was O(n) per edge → O(n×m)).
            name_to_uuid: dict[str, str] = {}
            for c in self.registry.cached():
                if c.name and c.character_uuid:
                    name_to_uuid[c.name] = c.character_uuid

            rel_payload: list[dict[str, Any]] = []
            for (frm, to), rel in self.relationships._graph.items():
                if rel.familiarity <= 0:
                    continue
                frm_uuid = name_to_uuid.get(frm)
                to_uuid = name_to_uuid.get(to)
                if not (frm_uuid and to_uuid):
                    continue
                rel_payload.append({
                    "from_uuid": frm_uuid,
                    "to_uuid": to_uuid,
                    "trust": rel.trust,
                    "familiarity": rel.familiarity,
                    "shared_tasks": rel.shared_tasks,
                    "conflicts": rel.conflicts,
                    "sentiment": rel.sentiment,
                    "last_interaction": rel.last_interaction[:500],
                })

            # Survivors: every hydrated character whose name wasn't marked dead.
            dead_names = {d["name"] for d in self._deaths if "name" in d}
            survivors = [
                c.character_uuid for c in self.registry.cached()
                if c.name not in dead_names
            ]
            # Map death payloads to the registry's uuids (names can die before
            # their uuid is on the payload).
            death_rows: list[dict[str, Any]] = []
            for d in self._deaths:
                uid = d.get("character_uuid") or self.registry.resolve_name(d.get("name", ""))
                if uid:
                    death_rows.append({
                        "character_uuid": uid,
                        "round": d.get("round", self._round),
                        "cause": d.get("cause", "unknown"),
                        "epitaph": d.get("epitaph", ""),
                    })
            will_rows: list[dict[str, str]] = []
            for w in self._wills:
                uid = w.get("character_uuid") or self.registry.resolve_name(w.get("name", ""))
                if uid and w.get("text"):
                    will_rows.append({"character_uuid": uid, "text": w["text"]})

            exit_reason = self._last_exit_reason or "ended"
            status = "completed" if getattr(project, "completed", False) else "incomplete"

            await self.registry.finish_project(
                status=status,
                exit_reason=exit_reason,
                rounds_used=self._round,
                final_answer=getattr(project, "final_answer", "") or "",
                survivors=survivors,
                deaths=death_rows,
                wills=will_rows,
                relationships=rel_payload,
                famous=[],  # Phase 5 will populate this
            )
        except Exception as exc:  # pragma: no cover — non-critical
            logger.warning("Registry finish_project failed: %s", exc)

    async def _run_loop(
        self,
        project: ProjectState,
        max_rounds: int,
        recorder: Any,
    ) -> ProjectState:
        await bus.emit("swarm.started", max_rounds=max_rounds)
        logger.info(
            f"[Swarm] Starting run '{project.name}' "
            f"(max_rounds={max_rounds}, tasks={len(project.tasks)}, "
            f"agents={len(self.agents)})"
        )
        exit_reason = "unknown"

        while self._running and self._round < max_rounds:
            self._round += 1

            # Update round number on all agents
            for agent in self.agents.values():
                agent._round_number = self._round

            # Reset per-round TTS char budget so this round's agents
            # each get a fair share of synthesis credits.
            if settings.tts_enabled:
                from autonoma.tts_worker import get_default_worker
                get_default_worker().reset_round_budget()

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
                exit_reason = "project_complete"
                logger.info(f"[Swarm] Director declared project_complete at round {self._round}")
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
                        await self._check_fortune(agent.name, action)
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

            if recorder is not None:
                recorder.checkpoint(self._round, project)

            if project.completed:
                # Narrate project completion
                agent_names = list(self.agents.keys())
                self.narrative.narrate_project_complete(
                    project.description or "the project", agent_names, self._round,
                )
                exit_reason = "completed_flag"
                logger.info(f"[Swarm] project.completed flag set at round {self._round}")
                break

            await asyncio.sleep(0.1)

        if exit_reason == "unknown":
            if not self._running:
                exit_reason = "stopped_externally"
            elif self._round >= max_rounds:
                exit_reason = "max_rounds_reached"

        # Stash exit_reason where run()'s finally-block persister can see it.
        self._last_exit_reason = exit_reason
        self._running = False

        # Only treat the project as completed when ALL tasks are DONE. This
        # prevents the UI's "프로젝트 완료!" banner from firing after a
        # timeout or external stop where tasks remain unfinished.
        all_tasks_done = bool(project.tasks) and all(
            t.status == TaskStatus.DONE for t in project.tasks
        )
        if not all_tasks_done and project.completed:
            logger.warning(
                f"[Swarm] Clearing stale project.completed=True flag "
                f"(exit_reason={exit_reason}, tasks_done="
                f"{sum(1 for t in project.tasks if t.status == TaskStatus.DONE)}/"
                f"{len(project.tasks)})"
            )
            project.completed = False
        elif all_tasks_done:
            project.completed = True

        # ── Terminal-state diagnostic ──────────────────────────────────────
        # Captures exactly why the run ended and what state the tasks were in.
        # Critical for debugging runs that finish with incomplete tasks.
        status_counts: dict[str, int] = {s.value: 0 for s in TaskStatus}
        for t in project.tasks:
            status_counts[t.status.value] = status_counts.get(t.status.value, 0) + 1
        unfinished = [t for t in project.tasks if t.status != TaskStatus.DONE]

        logger.info(
            f"[Swarm] Run ended: reason={exit_reason}, round={self._round}/{max_rounds}, "
            f"project.completed={project.completed}, "
            f"tasks(total={len(project.tasks)}, done={status_counts.get('done', 0)}, "
            f"open={status_counts.get('open', 0)}, assigned={status_counts.get('assigned', 0)}, "
            f"in_progress={status_counts.get('in_progress', 0)}, "
            f"blocked={status_counts.get('blocked', 0)}, review={status_counts.get('review', 0)}), "
            f"files={len(project.files)}, agents={len(self.agents)}"
        )
        for t in unfinished:
            logger.warning(
                f"[Swarm] Unfinished task '{t.title}' "
                f"status={t.status.value} assigned_to={t.assigned_to or '(none)'} "
                f"depends_on={t.depends_on or '[]'} artifacts={t.artifacts or '[]'}"
            )
        for name, agent in self.agents.items():
            if name == "Director":
                continue
            errs = getattr(agent.stats, "errors", 0)
            if errs:
                logger.warning(
                    f"[Swarm] Agent '{name}' finished with errors={errs} "
                    f"files_created={agent.stats.files_created} "
                    f"tasks_completed={agent.stats.tasks_completed}"
                )

        await bus.emit(
            "swarm.diagnostic",
            exit_reason=exit_reason,
            rounds=self._round,
            max_rounds=max_rounds,
            task_status_counts=status_counts,
            unfinished_tasks=[
                {
                    "title": t.title,
                    "status": t.status.value,
                    "assigned_to": t.assigned_to or "",
                }
                for t in unfinished
            ],
        )

        # Collect total token usage
        total_tokens = sum(a.total_tokens for a in self.agents.values())

        # Director synthesizes a Korean final answer for the user.
        try:
            final_answer = await self.director.synthesize_final_answer(project)
        except Exception as e:
            logger.warning(f"[Swarm] Final answer synthesis failed: {e}")
            final_answer = ""
        project.final_answer = final_answer

        # Final leaderboard + epilogue + multiverse report
        epilogue = self.narrative.render_epilogue()
        leaderboard_text = self.leaderboard.render()
        multiverse_report = self.multiverse.get_what_if_report()
        graveyard = self.ghost_realm.get_graveyard()

        # Distinguish real completion from timeout / external stop so the
        # frontend can render an appropriate banner. ``incomplete_reason`` is
        # only populated when completed=False and mirrors the exit_reason the
        # loop produced (max_rounds_reached, stopped_externally, etc.).
        incomplete_reason = "" if project.completed else exit_reason
        await bus.emit(
            "swarm.finished",
            rounds=self._round,
            completed=project.completed,
            incomplete_reason=incomplete_reason,
            files=len(project.files),
            total_tokens=total_tokens,
            final_answer=final_answer,
            epilogue=epilogue,
            leaderboard=leaderboard_text,
            multiverse=multiverse_report,
            graveyard=graveyard,
        )
        return project

    def stop(self) -> None:
        self._running = False

    async def inject_human_message(
        self,
        text: str,
        target: str | None = None,
    ) -> bool:
        """Deliver a human feedback message into an agent's inbox mid-run.

        When ``target`` is None the message is routed to the Director (general
        feedback). When ``target`` names an existing agent the message is
        delivered directly to that agent so the user can give specific
        instructions to individual characters.

        Returns True if the message was delivered.
        """
        # Guard: don't queue feedback into an inbox no one will read.
        # Without this, messages sent before run() / after stop() pile up in
        # the recipient's inbox and are silently dropped on next run init.
        if not self._running:
            logger.warning(
                f"[Swarm] inject_human_message dropped: swarm is not running "
                f"(target={target or 'Director'}, text_preview={text[:60]!r})"
            )
            return False

        recipient_name = target or "Director"
        recipient = self.agents.get(recipient_name)
        if recipient is None:
            # Fall back to the Director if the requested target is unknown.
            recipient = self.agents.get("Director")
            if recipient is None:
                return False
            recipient_name = "Director"

        msg = AgentMessage(
            sender="human",
            recipient=recipient_name,
            msg_type=MessageType.CHAT,
            content=f"[HUMAN FEEDBACK] {text}",
            data={"kind": "feedback", "source": "human"},
        )
        recipient.receive_message(msg)
        # Mark as already routed so the swarm router does not re-deliver it.
        self._routed_message_ids.add(msg.id)

        await bus.emit("human.feedback", text=text, recipient=recipient_name)
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
        agent = AutonomousAgent(persona, harness=harness, llm_config=self._llm_config)

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
            # Hydrate persisted growth before announcing the spawn so
            # listeners see the correct level in the first snapshot.
            await self._hydrate_agent(agent)
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

    async def _check_fortune(self, agent_name: str, action: str) -> None:
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
                # Surface the fulfilment so the frontend can "pop" the cookie
                # sprite on the stage and award a sparkle VFX.
                await bus.emit(
                    "fortune.fulfilled",
                    agent=agent_name,
                    fortune=cookie.fortune,
                    bonus_xp=cookie.bonus_xp,
                )

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
                # Boss always appears at the centre of the War Room (the
                # middle HQ room). Percent-space coords the frontend Stage
                # uses directly.
                await bus.emit(
                    "boss.appeared", name=boss.name, species=boss.species,
                    level=boss.level, hp=boss.max_hp, max_hp=boss.max_hp,
                    x=52.0, y=54.0,
                )
                self.narrative._add(
                    __import__("autonoma.world", fromlist=["NarrativeEvent"]).NarrativeEvent.WORLD_EVENT,
                    f"☠ BOSS APPEARED: {boss.name} the {boss.species}! ☠",
                    self._round, [], dramatic_weight=4,
                )

        # Agents attack current boss
        if self.boss_arena.current_boss and self.boss_arena.current_boss.phase.value == "fighting":
            boss = self.boss_arena.current_boss
            for name in agent_names:
                agent = self.agents.get(name)
                if agent and agent.bones:
                    hp_before = boss.hp
                    result = self.boss_arena.agent_attack(
                        name, agent.bones.stats, agent.stats.level,
                    )
                    if result:
                        damage = max(0, hp_before - boss.hp)
                        await bus.emit(
                            "boss.damage",
                            agent=name,
                            message=result,
                            damage=damage,
                            hp=boss.hp,
                            max_hp=boss.max_hp,
                        )

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
        # Schedule a small funeral so survivors who knew the deceased
        # speak a brief eulogy. Fire-and-forget — we never want a death
        # bookkeeping path to block on the bus.
        try:
            asyncio.create_task(self._hold_funeral(agent.name))
        except RuntimeError:
            # No running loop (e.g. unit-test path that calls
            # _create_ghost outside an event loop). The caller can
            # still test the eulogy logic via _hold_funeral directly.
            pass

        # Record the death for the persistent graveyard. We capture the
        # agent's last-known private memory as their will so the character's
        # final words travel into future runs (per design spec).
        uid = getattr(agent, "character_uuid", None)
        last_thought = agent.memory.private[-1].text if agent.memory.private else ""
        epitaph = (
            f"{agent.name} the {species} — fell at R{self._round} to {cause}. "
            f"Last thought: {last_thought[:140]}"
            if last_thought
            else f"{agent.name} the {species} — fell at R{self._round} to {cause}."
        )
        self._deaths.append({
            "character_uuid": uid,
            "name": agent.name,
            "round": self._round,
            "cause": cause,
            "epitaph": epitaph,
        })
        if last_thought:
            self._wills.append({
                "character_uuid": uid,
                "name": agent.name,
                "text": last_thought,
            })

    async def _hold_funeral(self, deceased_name: str) -> None:
        """Have the deceased's closest survivors deliver brief eulogies.

        We pull trust scores from the existing relationship graph rather
        than scoring inline — the graph is already what drives the rest
        of the social fabric, and using it here keeps "who counts as
        close" consistent with friend lists, gossip, etc.
        """
        from autonoma.dialogue_style import funeral_lines

        # Build the (survivor, trust) list. We only look at relationships
        # where the survivor has a positive bond *toward* the deceased
        # (frm = survivor, to = deceased) — eulogies are first-person.
        candidates: list[tuple[str, float]] = []
        for frm, to, rel in self.relationships.get_all_pairs():
            if to != deceased_name:
                continue
            if frm not in self.agents or frm == deceased_name:
                continue
            candidates.append((frm, rel.trust))

        lines = funeral_lines(deceased_name=deceased_name, survivors=candidates)
        if not lines:
            return

        # Mark the moment with a world event so the UI can dim/spotlight
        # if it wants to. Done first so the eulogy speech reads in the
        # right context order on the client.
        await bus.emit(
            "world.event",
            title=f"A funeral is held for {deceased_name}.",
        )
        for survivor_name, line in lines:
            survivor = self.agents.get(survivor_name)
            if survivor is None:
                continue
            try:
                await survivor._say(line, style="italic")
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug("funeral line for %s failed: %s", survivor_name, exc)
            # Tiny gap so the lines don't all fire on the same frame —
            # gives the UI room to render speech bubbles in sequence.
            await asyncio.sleep(0.4)

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
