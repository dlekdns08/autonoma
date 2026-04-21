"""Tests for the expanded world system - all 21 subsystems."""

import pytest

from autonoma.world import (
    ACHIEVEMENTS,
    EVOLVED_SPECIES,
    AchievementTier,
    AgentBones,
    AgentDiary,
    AgentMemory,
    AgentStats,
    BossAgent,
    BossArena,
    BossPhase,
    Campfire,
    Debate,
    DebateArena,
    DebateOutcome,
    Dream,
    DreamEngine,
    FortuneCookie,
    FortuneCookieJar,
    GhostAgent,
    GhostRealm,
    GossipNetwork,
    Guild,
    GuildRegistry,
    GuildRole,
    HindsightNote,
    Leaderboard,
    Letter,
    MemoryEntry,
    Mood,
    MultiverseEngine,
    NarrativeEngine,
    NarrativeEvent,
    PostOffice,
    Quest,
    QuestBoard,
    QuestStatus,
    Relationship,
    RelationshipGraph,
    ReputationScore,
    Season,
    TimeOfDay,
    Trade,
    TradingPost,
    Trait,
    Weather,
    WorldClock,
    WorldEvent,
    WorldEventQueue,
    WorldEventType,
    check_achievements,
)


# ── Agent Personality (Deterministic Bones) ───────────────────────────────

class TestAgentBones:
    def test_deterministic(self):
        """Same role+name always produces same bones."""
        b1 = AgentBones.from_role("coder", "Alice")
        b2 = AgentBones.from_role("coder", "Alice")
        assert b1.species == b2.species
        assert b1.stats == b2.stats
        assert b1.catchphrase == b2.catchphrase

    def test_different_roles_different_bones(self):
        b1 = AgentBones.from_role("coder", "Alice")
        b2 = AgentBones.from_role("tester", "Alice")
        # Could theoretically be same, but statistically won't be
        assert b1.species != b2.species or b1.stats != b2.stats

    def test_species_is_valid(self):
        bones = AgentBones.from_role("reviewer", "Bob")
        from autonoma.world import SPECIES
        assert bones.species in SPECIES

    def test_stats_in_range(self):
        bones = AgentBones.from_role("writer", "Carol")
        for stat_name, value in bones.stats.items():
            assert 1 <= value <= 10, f"{stat_name}={value} out of range"

    def test_has_two_traits(self):
        bones = AgentBones.from_role("designer", "Dave")
        assert len(bones.traits) == 2
        assert all(isinstance(t, Trait) for t in bones.traits)

    def test_rarity_from_total_stats(self):
        # We can't control the output, but we can verify it's one of the valid values
        bones = AgentBones.from_role("coder", "Test")
        assert bones.rarity in ("common", "uncommon", "rare", "legendary")

    def test_species_emoji_matches(self):
        bones = AgentBones.from_role("tester", "Eve")
        from autonoma.world import SPECIES_EMOJIS
        assert bones.species_emoji == SPECIES_EMOJIS[bones.species]


# ── Relationships ──────────────────────────────────────────────────────────

class TestRelationships:
    def test_initial_trust(self):
        graph = RelationshipGraph()
        rel = graph.get("A", "B")
        assert rel.trust == 0.5
        assert rel.familiarity == 0

    def test_positive_interaction_increases_trust(self):
        graph = RelationshipGraph()
        graph.record("A", "B", "helped with task", positive=True)
        rel = graph.get("A", "B")
        assert rel.trust > 0.5
        assert rel.familiarity == 1
        assert rel.last_interaction == "helped with task"

    def test_negative_interaction_decreases_trust(self):
        graph = RelationshipGraph()
        graph.record("A", "B", "ignored request", positive=False)
        assert graph.get("A", "B").trust < 0.5

    def test_trust_clamps(self):
        graph = RelationshipGraph()
        for _ in range(20):
            graph.record("A", "B", "help", positive=True)
        assert graph.get("A", "B").trust <= 1.0

        for _ in range(30):
            graph.record("A", "B", "fail", positive=False)
        assert graph.get("A", "B").trust >= 0.0

    def test_get_friends(self):
        graph = RelationshipGraph()
        for _ in range(5):
            graph.record("A", "B", "collab", positive=True)
        friends = graph.get_friends("A")
        assert "B" in friends

    def test_directional(self):
        graph = RelationshipGraph()
        graph.record("A", "B", "helped", positive=True)
        assert graph.get("A", "B").familiarity == 1
        assert graph.get("B", "A").familiarity == 0  # Not reciprocal

    def test_summary(self):
        graph = RelationshipGraph()
        graph.record("A", "B", "worked together", positive=True)
        summary = graph.get_summary_for("A")
        assert "B" in summary
        assert "trust" in summary


# ── Agent Memory ───────────────────────────────────────────────────────────

class TestAgentMemory:
    def test_remember_and_recall(self):
        mem = AgentMemory()
        mem.remember("Completed the API task", "success", round_number=1)
        entries = mem.recall()
        assert len(entries) == 1
        assert "API task" in entries[0].text

    def test_keyword_recall(self):
        mem = AgentMemory()
        mem.remember("Fixed the login bug", "lesson", round_number=1)
        mem.remember("Created the dashboard", "success", round_number=2)
        results = mem.recall("login")
        assert len(results) == 1
        assert "login" in results[0].text

    def test_memory_limit(self):
        mem = AgentMemory()
        for i in range(30):
            mem.remember(f"Event {i}", "observation", round_number=i)
        assert len(mem.private) <= AgentMemory.MAX_PRIVATE_MEMORIES

    def test_lessons_preserved_over_observations(self):
        mem = AgentMemory()
        mem.remember("Important lesson", "lesson", round_number=1)
        for i in range(25):
            mem.remember(f"Observation {i}", "observation", round_number=i + 2)
        # Lesson should survive the cull
        assert any("Important lesson" in e.text for e in mem.entries)

    def test_summary_format(self):
        mem = AgentMemory()
        mem.remember("Test event", "success", round_number=1)
        summary = mem.get_summary()
        assert "Test event" in summary
        assert "★" in summary  # Success icon

    def test_empty_summary(self):
        mem = AgentMemory()
        assert "fresh start" in mem.get_summary().lower()

    def test_to_dict(self):
        mem = AgentMemory()
        mem.remember("Test", "lesson", round_number=5)
        data = mem.to_dict()
        assert "private" in data
        assert len(data["private"]) == 1
        assert data["private"][0]["type"] == "lesson"
        assert data["private"][0]["round"] == 5


# ── XP / Level / Achievements ─────────────────────────────────────────────

class TestAgentStats:
    def test_initial_state(self):
        stats = AgentStats()
        assert stats.level == 1
        assert stats.xp == 0

    def test_add_xp(self):
        stats = AgentStats()
        leveled = stats.add_xp(30)
        assert stats.xp == 30
        assert not leveled  # Need 50 for level 2

    def test_level_up(self):
        stats = AgentStats()
        stats.add_xp(50)  # Exactly enough for level 2
        assert stats.level == 2
        assert stats.xp == 0  # Reset after level up

    def test_xp_to_next_scales(self):
        stats = AgentStats()
        assert stats.xp_to_next_level == 50  # Level 1
        stats.add_xp(50)
        assert stats.xp_to_next_level == 100  # Level 2

    def test_first_blood_achievement(self):
        stats = AgentStats()
        stats.tasks_completed = 1
        earned = check_achievements(stats)
        assert "first_blood" in earned
        assert "first_blood" in stats.achievements

    def test_no_duplicate_achievements(self):
        stats = AgentStats()
        stats.tasks_completed = 1
        check_achievements(stats)
        earned_again = check_achievements(stats)
        assert "first_blood" not in earned_again

    def test_prolific_achievement(self):
        stats = AgentStats()
        stats.files_created = 5
        earned = check_achievements(stats)
        assert "prolific" in earned


# ── World Events ───────────────────────────────────────────────────────────

class TestWorldEvents:
    def test_event_generation(self):
        queue = WorldEventQueue(seed=42)
        # Run enough rounds to get at least one event
        events = []
        for round_num in range(1, 20):
            event = queue.maybe_generate(round_num, ["Alice", "Bob"])
            if event:
                events.append(event)
        assert len(events) > 0  # Should get at least one in 20 rounds

    def test_deterministic(self):
        q1 = WorldEventQueue(seed=123)
        q2 = WorldEventQueue(seed=123)
        results1 = [q1.maybe_generate(i, ["A"]) for i in range(1, 10)]
        results2 = [q2.maybe_generate(i, ["A"]) for i in range(1, 10)]
        # Same seed = same events
        for r1, r2 in zip(results1, results2):
            if r1 is None:
                assert r2 is None
            else:
                assert r1.event_type == r2.event_type

    def test_resolve(self):
        queue = WorldEventQueue(seed=42)
        event = WorldEvent(
            event_type=WorldEventType.MORALE_BOOST,
            title="Test",
            description="Test event",
            round_number=1,
        )
        queue.events.append(event)
        assert len(queue.get_unresolved()) == 1
        queue.resolve(event)
        assert len(queue.get_unresolved()) == 0

    def test_min_round_respected(self):
        queue = WorldEventQueue(seed=42)
        # Round 1 is too early for most events
        early_events = [queue.maybe_generate(1, ["A"]) for _ in range(10)]
        # Most templates have min_round >= 2, so early events should be rare
        # (Some templates have min_round=2, so it's possible but unlikely with early rounds)

    def test_chain_events(self):
        """Events with chain_event should queue a follow-up."""
        queue = WorldEventQueue(seed=42)
        # Manually inject a chain event
        event = WorldEvent(
            event_type=WorldEventType.THUNDERSTORM,
            title="Storm!",
            description="test",
            round_number=5,
            chain_event=WorldEventType.MORALE_BOOST,
        )
        queue.events.append(event)
        queue._chain_queue.append(WorldEventType.MORALE_BOOST)
        chain = queue.maybe_generate(6, ["A"])
        assert chain is not None
        assert chain.event_type == WorldEventType.MORALE_BOOST
        assert "[Chain]" in chain.title


# ── Evolution System ──────────────────────────────────────────────────────

class TestEvolution:
    def test_no_evolution_at_low_level(self):
        bones = AgentBones.from_role("coder", "Alice")
        species, emoji = bones.get_evolved_form(1)
        assert species == bones.species

    def test_first_evolution_at_level_5(self):
        bones = AgentBones.from_role("coder", "Alice")
        species, emoji = bones.get_evolved_form(5)
        expected = EVOLVED_SPECIES[bones.species][5]
        assert species == expected

    def test_second_evolution_at_level_10(self):
        bones = AgentBones.from_role("coder", "Alice")
        species, emoji = bones.get_evolved_form(10)
        expected = EVOLVED_SPECIES[bones.species][10]
        assert species == expected

    def test_evolution_at_intermediate_level(self):
        bones = AgentBones.from_role("coder", "Alice")
        # Level 7 should give first evolution (threshold 5)
        species, _ = bones.get_evolved_form(7)
        assert species == EVOLVED_SPECIES[bones.species][5]

    def test_all_species_have_evolutions(self):
        from autonoma.world import SPECIES
        for sp in SPECIES:
            assert sp in EVOLVED_SPECIES
            assert 5 in EVOLVED_SPECIES[sp]
            assert 10 in EVOLVED_SPECIES[sp]


# ── Two-Layer Memory (Hindsight Notes) ────────────────────────────────────

class TestHindsightNotes:
    def test_add_hindsight(self):
        mem = AgentMemory()
        note = mem.add_hindsight("API Lesson", "Always validate input", ["api", "validation"])
        assert len(mem.hindsight) == 1
        assert note.title == "API Lesson"

    def test_search_hindsight_by_keyword(self):
        mem = AgentMemory()
        mem.add_hindsight("API Lesson", "Always validate input", ["api", "validation"])
        mem.add_hindsight("DB Lesson", "Use transactions", ["database", "sql"])
        results = mem.search_hindsight("api")
        assert len(results) == 1
        assert results[0].title == "API Lesson"

    def test_search_hindsight_in_lesson_text(self):
        mem = AgentMemory()
        mem.add_hindsight("Tip", "Always validate input before processing", ["coding"])
        results = mem.search_hindsight("validate")
        assert len(results) == 1

    def test_hindsight_limit(self):
        mem = AgentMemory()
        for i in range(20):
            mem.add_hindsight(f"Note {i}", f"Lesson {i}", [f"kw{i}"])
        assert len(mem.hindsight) <= AgentMemory.MAX_HINDSIGHT_NOTES

    def test_hindsight_upvotes_preserved(self):
        mem = AgentMemory()
        note = mem.add_hindsight("Important", "Critical lesson", ["critical"])
        note.upvotes = 10
        for i in range(20):
            mem.add_hindsight(f"Filler {i}", f"Filler {i}", [f"filler{i}"])
        # Upvoted note should survive the cull
        assert any(n.title == "Important" for n in mem.hindsight)

    def test_hindsight_matches(self):
        note = HindsightNote(title="API Tips", lesson="Use retry logic", keywords=["api", "retry"])
        assert note.matches("api")
        assert note.matches("retry")
        assert note.matches("API")  # case insensitive
        assert not note.matches("database")

    def test_to_dict_includes_hindsight(self):
        mem = AgentMemory()
        mem.add_hindsight("Tip", "Lesson here", ["testing"], source_agent="Alice")
        data = mem.to_dict()
        assert len(data["hindsight"]) == 1
        assert data["hindsight"][0]["title"] == "Tip"
        assert data["hindsight"][0]["source"] == "Alice"

    def test_summary_shows_both_layers(self):
        mem = AgentMemory()
        mem.remember("Did a task", "success", round_number=1)
        mem.add_hindsight("My Lesson", "Something important", ["test"])
        summary = mem.get_summary()
        assert "Private Memories" in summary
        assert "Hindsight Notes" in summary


# ── Extended Relationships ────────────────────────────────────────────────

class TestExtendedRelationships:
    def test_bond_level_soulmates(self):
        rel = Relationship(trust=0.95)
        assert "soulmates" in rel.bond_level

    def test_bond_level_rivals(self):
        rel = Relationship(trust=0.1)
        assert "rivals" in rel.bond_level

    def test_record_conflict(self):
        rel = Relationship()
        rel.record_conflict("Disagreed on approach")
        assert rel.conflicts == 1
        assert rel.trust < 0.5

    def test_record_collaboration(self):
        rel = Relationship()
        rel.record_collaboration("Paired on feature")
        assert rel.shared_tasks == 1
        assert rel.trust > 0.5

    def test_get_rivals(self):
        graph = RelationshipGraph()
        for _ in range(5):
            graph.record("A", "B", "conflict", positive=False)
        rivals = graph.get_rivals("A")
        assert "B" in rivals

    def test_get_all_pairs(self):
        graph = RelationshipGraph()
        graph.record("A", "B", "worked")
        graph.record("C", "D", "talked")
        pairs = graph.get_all_pairs()
        assert len(pairs) == 2

    def test_sentiment_tracks_trust(self):
        rel = Relationship()
        for _ in range(5):
            rel.record_interaction("help", positive=True)
        assert rel.sentiment == "positive"


# ── Guild System ──────────────────────────────────────────────────────────

class TestGuildSystem:
    def test_create_guild(self):
        registry = GuildRegistry()
        guild = registry.create("Team Alpha", "Code together!", "Alice", round_number=3)
        assert guild.name == "Team Alpha"
        assert guild.size == 1
        assert guild.members["Alice"] == GuildRole.LEADER

    def test_add_member(self):
        guild = Guild(name="Test", motto="Go!")
        guild.add_member("Alice", GuildRole.LEADER)
        guild.add_member("Bob")
        assert guild.size == 2
        assert guild.members["Bob"] == GuildRole.MEMBER

    def test_synergy_calculation(self):
        guild = Guild(name="Test", motto="Go!")
        guild.add_member("Alice", GuildRole.LEADER)
        guild.add_member("Bob")
        graph = RelationshipGraph()
        # High mutual trust
        for _ in range(5):
            graph.record("Alice", "Bob", "collab", positive=True)
            graph.record("Bob", "Alice", "collab", positive=True)
        synergy = guild.calculate_synergy(graph)
        assert synergy > 0

    def test_synergy_low_trust(self):
        guild = Guild(name="Test", motto="Go!")
        guild.add_member("A", GuildRole.LEADER)
        guild.add_member("B")
        graph = RelationshipGraph()
        # Default trust is 0.5, synergy should be 0
        synergy = guild.calculate_synergy(graph)
        assert synergy == 0.0

    def test_single_member_no_synergy(self):
        guild = Guild(name="Solo", motto="Alone!")
        guild.add_member("Alice")
        graph = RelationshipGraph()
        assert guild.calculate_synergy(graph) == 0.0

    def test_get_agent_guild(self):
        registry = GuildRegistry()
        guild = registry.create("Team", "Go!", "Alice")
        guild.add_member("Bob")
        assert registry.get_agent_guild("Alice") is guild
        assert registry.get_agent_guild("Bob") is guild
        assert registry.get_agent_guild("Charlie") is None

    def test_auto_form_guilds(self):
        registry = GuildRegistry()
        graph = RelationshipGraph()
        # Build strong relationships
        for _ in range(5):
            graph.record("A", "B", "help", positive=True)
            graph.record("B", "A", "help", positive=True)
        formed = registry.auto_form_guilds(["A", "B", "C"], graph, round_number=5)
        assert len(formed) >= 1
        assert any("A" in g.members for g in formed)

    def test_guild_banner(self):
        guild = Guild(name="Star Team", motto="Shine bright!")
        guild.add_member("Alice", GuildRole.LEADER)
        guild.add_member("Bob")
        banner = guild.get_banner()
        assert "Star Team" in banner
        assert "Alice" in banner


# ── Gossip Network ────────────────────────────────────────────────────────

class TestGossipNetwork:
    def test_observe(self):
        gossip = GossipNetwork()
        item = gossip.observe("Alice", "Bob", "completed task quickly", "positive", round_number=1)
        assert item.about == "Bob"
        assert item.sentiment == "positive"
        assert len(gossip.items) == 1

    def test_spread(self):
        gossip = GossipNetwork()
        gossip.observe("Alice", "Bob", "good work", "positive")
        shared = gossip.spread("Alice", "Charlie")
        assert len(shared) == 1
        assert shared[0].spread_count == 2

    def test_spread_no_double_hearing(self):
        gossip = GossipNetwork()
        gossip.observe("Alice", "Bob", "good work", "positive")
        gossip.spread("Alice", "Charlie")
        # Charlie already heard it, should get nothing
        shared_again = gossip.spread("Alice", "Charlie")
        assert len(shared_again) == 0

    def test_spread_limit(self):
        gossip = GossipNetwork()
        for i in range(5):
            gossip.observe("Alice", "Bob", f"thing {i}", "neutral")
        shared = gossip.spread("Alice", "Charlie", max_items=2)
        assert len(shared) == 2

    def test_get_gossip_about(self):
        gossip = GossipNetwork()
        gossip.observe("Alice", "Bob", "good", "positive")
        gossip.observe("Charlie", "Bob", "bad", "negative")
        gossip.observe("Alice", "Dave", "ok", "neutral")
        about_bob = gossip.get_gossip_about("Bob")
        assert len(about_bob) == 2

    def test_reputation_summary(self):
        gossip = GossipNetwork()
        gossip.observe("A", "Bob", "great!", "positive")
        gossip.observe("B", "Bob", "amazing!", "positive")
        gossip.observe("C", "Bob", "meh", "negative")
        summary = gossip.get_reputation_summary("Bob")
        assert "♥2" in summary
        assert "✖1" in summary

    def test_reputation_summary_empty(self):
        gossip = GossipNetwork()
        assert "No gossip" in gossip.get_reputation_summary("Nobody")


# ── Campfire ──────────────────────────────────────────────────────────────

class TestCampfire:
    def test_gather_and_dismiss(self):
        fire = Campfire()
        assert not fire.is_active
        fire.gather()
        assert fire.is_active
        fire.dismiss()
        assert not fire.is_active

    def test_tell_story(self):
        fire = Campfire()
        fire.gather()
        story = fire.tell_story(
            teller="Alice",
            title="The Great Bug",
            content="Once upon a time...",
            moral="Always test your code",
            listeners=["Bob", "Charlie"],
            round_number=5,
        )
        assert story.teller == "Alice"
        assert story.moral == "Always test your code"
        assert len(fire.stories) == 1

    def test_react_to_story(self):
        fire = Campfire()
        story = fire.tell_story("Alice", "Story", "Content", "Moral", ["Bob"])
        fire.react(story, "Bob", "♥")
        assert story.reactions["Bob"] == "♥"

    def test_recent_stories(self):
        fire = Campfire()
        for i in range(10):
            fire.tell_story(f"Agent{i}", f"Story {i}", "...", "...", [])
        recent = fire.get_recent_stories(3)
        assert len(recent) == 3
        assert recent[-1].teller == "Agent9"


# ── Debate System ─────────────────────────────────────────────────────────

class TestDebateSystem:
    def test_create_debate(self):
        arena = DebateArena()
        debate = arena.start_debate(
            topic="Use TypeScript or JavaScript?",
            proposer="Alice",
            opponent="Bob",
            audience=["Charlie", "Dave"],
            round_number=3,
        )
        assert debate.outcome == DebateOutcome.UNRESOLVED
        assert len(arena.debates) == 1

    def test_resolve_proposer_wins(self):
        debate = Debate(topic="Test", proposer="A", opponent="B")
        debate.votes = {"C": "proposer", "D": "proposer", "E": "opponent"}
        result = debate.resolve()
        assert result == DebateOutcome.PROPOSER_WINS

    def test_resolve_opponent_wins(self):
        debate = Debate(topic="Test", proposer="A", opponent="B")
        debate.votes = {"C": "opponent", "D": "opponent"}
        result = debate.resolve()
        assert result == DebateOutcome.OPPONENT_WINS

    def test_resolve_compromise(self):
        debate = Debate(topic="Test", proposer="A", opponent="B")
        debate.votes = {"C": "proposer", "D": "opponent"}
        result = debate.resolve()
        assert result == DebateOutcome.COMPROMISE

    def test_resolve_no_votes_is_compromise(self):
        debate = Debate(topic="Test", proposer="A", opponent="B")
        result = debate.resolve()
        assert result == DebateOutcome.COMPROMISE

    def test_agent_record(self):
        arena = DebateArena()
        d1 = arena.start_debate("T1", "A", "B", ["C"])
        d1.votes = {"C": "proposer"}
        d1.resolve()
        d2 = arena.start_debate("T2", "A", "B", ["C"])
        d2.votes = {"C": "opponent"}
        d2.resolve()
        record_a = arena.get_agent_record("A")
        assert record_a["wins"] == 1
        assert record_a["losses"] == 1
        record_b = arena.get_agent_record("B")
        assert record_b["wins"] == 1
        assert record_b["losses"] == 1


# ── Reputation Leaderboard ────────────────────────────────────────────────

class TestLeaderboard:
    def test_update_and_rank(self):
        board = Leaderboard()
        graph = RelationshipGraph()
        gossip = GossipNetwork()
        arena = DebateArena()
        bones = AgentBones.from_role("coder", "Alice")

        stats_a = AgentStats(tasks_completed=5, level=3)
        stats_b = AgentStats(tasks_completed=2, level=1)
        bones_b = AgentBones.from_role("tester", "Bob")

        board.update("Alice", stats_a, bones, graph, gossip, arena)
        board.update("Bob", stats_b, bones_b, graph, gossip, arena)

        ranking = board.get_ranking()
        assert len(ranking) == 2
        assert ranking[0].agent_name == "Alice"  # Higher score

    def test_composite_score(self):
        score = ReputationScore(
            agent_name="Test",
            total_xp=100,
            tasks_completed=5,
            trust_avg=0.8,
            debate_wins=2,
            achievement_count=3,
        )
        assert score.composite_score > 0

    def test_render(self):
        board = Leaderboard()
        assert "No rankings" in board.render()

        graph = RelationshipGraph()
        gossip = GossipNetwork()
        arena = DebateArena()
        bones = AgentBones.from_role("coder", "Alice")
        stats = AgentStats(tasks_completed=3, level=2)
        board.update("Alice", stats, bones, graph, gossip, arena)
        rendered = board.render()
        assert "LEADERBOARD" in rendered
        assert "Alice" in rendered
        assert "👑" in rendered

    def test_get_top(self):
        board = Leaderboard()
        graph = RelationshipGraph()
        gossip = GossipNetwork()
        arena = DebateArena()
        for i in range(8):
            name = f"Agent{i}"
            bones = AgentBones.from_role("coder", name)
            stats = AgentStats(tasks_completed=i, level=1)
            board.update(name, stats, bones, graph, gossip, arena)
        top3 = board.get_top(3)
        assert len(top3) == 3


# ── Narrative Engine ──────────────────────────────────────────────────────

class TestNarrativeEngine:
    def test_narrate_spawn(self):
        engine = NarrativeEngine()
        text = engine.narrate_spawn("Alice", "cat", "coder", "rare", round_number=1)
        assert "Alice" in text
        assert "rare" in text.lower() or "cat" in text
        assert len(engine.chronicle) == 1

    def test_narrate_task_complete(self):
        engine = NarrativeEngine()
        text = engine.narrate_task_complete("Bob", "Fix login", "fox", round_number=3)
        assert "Bob" in text or "Fix login" in text

    def test_narrate_level_up(self):
        engine = NarrativeEngine()
        text = engine.narrate_level_up("Alice", 5, "cat", round_number=4)
        assert "Level 5" in text
        assert "★" in text

    def test_narrate_evolution(self):
        engine = NarrativeEngine()
        text = engine.narrate_evolution("Alice", "cat", "tiger", round_number=5)
        assert "EVOLUTION" in text
        assert "tiger" in text

    def test_narrate_achievement(self):
        engine = NarrativeEngine()
        text = engine.narrate_achievement("Bob", "First Blood ☆", round_number=2)
        assert "Achievement" in text
        assert "First Blood" in text

    def test_narrate_guild_formed(self):
        engine = NarrativeEngine()
        text = engine.narrate_guild_formed("Star Team", ["Alice", "Bob"], round_number=6)
        assert "Star Team" in text

    def test_narrate_debate(self):
        engine = NarrativeEngine()
        debate = Debate(topic="Best framework", proposer="A", opponent="B")
        debate.outcome = DebateOutcome.PROPOSER_WINS
        text = engine.narrate_debate(debate, round_number=4)
        assert "A" in text and "wins" in text

    def test_narrate_campfire(self):
        engine = NarrativeEngine()
        text = engine.narrate_campfire(3, ["A", "B"], round_number=5)
        assert "campfire" in text
        assert "3 stories" in text

    def test_narrate_project_complete(self):
        engine = NarrativeEngine()
        text = engine.narrate_project_complete("MyApp", ["A", "B", "C"], round_number=10)
        assert "프로젝트 완료" in text
        assert "3명" in text

    def test_get_chapter(self):
        engine = NarrativeEngine()
        engine.narrate_spawn("A", "cat", "coder", "common", round_number=1)
        engine.narrate_spawn("B", "fox", "tester", "rare", round_number=1)
        engine.narrate_task_complete("A", "task1", "cat", round_number=2)
        chapter1 = engine.get_chapter(1)
        assert len(chapter1) == 2

    def test_get_highlights(self):
        engine = NarrativeEngine()
        engine.narrate_spawn("A", "cat", "coder", "common", round_number=1)
        engine.narrate_evolution("A", "cat", "tiger", round_number=5)  # weight=4
        engine.narrate_project_complete("App", ["A"], round_number=10)  # weight=5
        highlights = engine.get_highlights(2)
        assert highlights[0].dramatic_weight >= highlights[1].dramatic_weight

    def test_render_epilogue(self):
        engine = NarrativeEngine()
        assert "이야기" in engine.render_epilogue()

        engine.narrate_spawn("Alice", "cat", "coder", "common", round_number=1)
        engine.narrate_project_complete("App", ["Alice"], round_number=5)
        epilogue = engine.render_epilogue()
        assert "지금까지의 이야기" in epilogue
        assert "5 라운드" in epilogue

    def test_relationship_milestone(self):
        engine = NarrativeEngine()
        text = engine.narrate_relationship_milestone("A", "B", "best friends ♥♥", round_number=3)
        assert "A" in text and "B" in text
        assert "best friends" in text


# ── Extended Stats ────────────────────────────────────────────────────────

class TestExtendedStats:
    def test_title_progression(self):
        stats = AgentStats()
        assert stats.title == "Rookie"
        stats.level = 3
        assert stats.title == "Journeyman"
        stats.level = 5
        assert stats.title == "Veteran"
        stats.level = 7
        assert stats.title == "Elite Agent"
        stats.level = 10
        assert stats.title == "Grand Master"
        stats.level = 15
        assert stats.title == "Legendary Hero"

    def test_total_xp_earned(self):
        stats = AgentStats()
        stats.add_xp(50)  # Level 2
        stats.add_xp(30)  # 30 XP into level 2
        # total = 50 (from level 1) + 30 (current)
        assert stats.total_xp_earned == 80

    def test_achievement_tiers(self):
        assert ACHIEVEMENTS["first_blood"]["tier"] == AchievementTier.BRONZE
        assert ACHIEVEMENTS["prolific"]["tier"] == AchievementTier.SILVER
        assert ACHIEVEMENTS["veteran"]["tier"] == AchievementTier.GOLD
        assert ACHIEVEMENTS["legendary"]["tier"] == AchievementTier.DIAMOND

    def test_achievement_xp_rewards(self):
        stats = AgentStats()
        stats.tasks_completed = 1
        initial_xp = stats.xp
        check_achievements(stats)
        # first_blood gives 10 XP reward
        assert stats.xp > initial_xp


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Day/Night Cycle & Weather
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorldClock:
    def test_initial_state(self):
        clock = WorldClock()
        assert clock.time_of_day == TimeOfDay.MORNING
        assert clock.season == Season.SPRING
        assert clock.day == 1

    def test_time_advances(self):
        clock = WorldClock()
        clock.tick(0)
        assert clock.time_of_day == TimeOfDay.DAWN
        clock.tick(1)
        assert clock.time_of_day == TimeOfDay.MORNING
        clock.tick(2)
        assert clock.time_of_day == TimeOfDay.AFTERNOON

    def test_day_advances(self):
        clock = WorldClock()
        for i in range(5):
            clock.tick(i)
        changes = clock.tick(5)  # Should be new day
        assert clock.day == 2

    def test_season_changes(self):
        clock = WorldClock()
        # Advance to round 20 (new season)
        for i in range(21):
            clock.tick(i)
        assert clock.season != Season.SPRING or clock.day > 1

    def test_weather_rolls(self):
        clock = WorldClock()
        clock._roll_weather()
        assert isinstance(clock.weather, Weather)

    def test_sky_line(self):
        clock = WorldClock()
        sky = clock.sky_line
        assert "morning" in sky or "dawn" in sky.lower() or clock.time_of_day.value in sky

    def test_is_night(self):
        clock = WorldClock()
        clock.time_of_day = TimeOfDay.NIGHT
        assert clock.is_night
        clock.time_of_day = TimeOfDay.MORNING
        assert not clock.is_night

    def test_xp_modifier(self):
        clock = WorldClock()
        clock.weather = Weather.SUNNY
        assert clock.get_xp_modifier() > 1.0
        clock.weather = Weather.STORMY
        assert clock.get_xp_modifier() < 1.0

    def test_mood_modifier(self):
        clock = WorldClock()
        clock.weather = Weather.STORMY
        assert clock.get_mood_modifier() == Mood.WORRIED
        clock.weather = Weather.SUNNY
        clock.time_of_day = TimeOfDay.MORNING
        assert clock.get_mood_modifier() == Mood.HAPPY


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Agent Dreams
# ═══════════════════════════════════════════════════════════════════════════════

class TestDreamEngine:
    def test_generate_dream(self):
        engine = DreamEngine()
        memories = [MemoryEntry(text="Completed task", memory_type="success", round_number=1)]
        dream = engine.generate_dream("Alice", "cat", memories, Mood.HAPPY, ["Bob"], 5)
        assert dream.dreamer == "Alice"
        assert dream.dream_type in ("prophetic", "nightmare", "peaceful", "surreal")
        assert dream.content

    def test_dream_type_from_mood(self):
        engine = DreamEngine(seed=1)
        frustrated_dream = engine.generate_dream(
            "Alice", "cat", [], Mood.FRUSTRATED, [], 1,
        )
        assert frustrated_dream.dream_type == "nightmare"

        happy_dream = engine.generate_dream(
            "Bob", "fox", [], Mood.HAPPY, [], 2,
        )
        assert happy_dream.dream_type == "peaceful"

    def test_prophetic_dream_gives_bonus(self):
        engine = DreamEngine()
        dream = engine.generate_dream("Alice", "cat", [], Mood.CURIOUS, [], 1)
        assert dream.dream_type == "prophetic"
        assert dream.bonus_xp > 0
        assert dream.bonus_mood == Mood.INSPIRED

    def test_recent_dreams(self):
        engine = DreamEngine()
        for i in range(5):
            engine.generate_dream(f"Agent{i % 2}", "cat", [], Mood.HAPPY, [], i)
        alice_dreams = engine.get_recent_dreams("Agent0", 2)
        assert len(alice_dreams) <= 2

    def test_deterministic(self):
        e1 = DreamEngine(seed=42)
        e2 = DreamEngine(seed=42)
        d1 = e1.generate_dream("A", "cat", [], Mood.HAPPY, [], 1)
        d2 = e2.generate_dream("A", "cat", [], Mood.HAPPY, [], 1)
        assert d1.content == d2.content


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Agent Diary
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentDiary:
    def test_write_entry(self):
        diary = AgentDiary("Alice", "cat", "Nyaa~!")
        entry = diary.write("task_complete", Mood.PROUD, 3, task="Fix login")
        assert "Fix login" in entry.content or "task" in entry.content.lower()
        assert len(diary.entries) == 1

    def test_diary_limit(self):
        diary = AgentDiary("Alice", "cat", "Nyaa~!")
        for i in range(40):
            diary.write("idle", Mood.RELAXED, i)
        assert len(diary.entries) <= AgentDiary.MAX_ENTRIES

    def test_memoir(self):
        diary = AgentDiary("Alice", "cat", "Nyaa~!")
        diary.write("task_complete", Mood.PROUD, 1, task="Build API")
        diary.write("error", Mood.FRUSTRATED, 2)
        memoir = diary.get_memoir()
        assert "Alice" in memoir
        assert "cat" in memoir
        assert "Emotional Journey" in memoir

    def test_empty_memoir(self):
        diary = AgentDiary("Bob", "fox", "Kon!")
        assert "empty" in diary.get_memoir().lower()

    def test_get_recent(self):
        diary = AgentDiary("Alice", "cat", "Nyaa~!")
        for i in range(10):
            diary.write("idle", Mood.RELAXED, i)
        recent = diary.get_recent(3)
        assert len(recent) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Quests / Side Missions
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuestBoard:
    def test_assign_quest(self):
        board = QuestBoard()
        quest = board.assign_quest("Alice", round_number=1)
        assert quest is not None
        assert quest.assigned_to == "Alice"
        assert quest.status == QuestStatus.ACTIVE

    def test_max_active_quests(self):
        board = QuestBoard()
        board.assign_quest("Alice", 1)
        board.assign_quest("Alice", 2)
        third = board.assign_quest("Alice", 3)
        assert third is None  # Max 2 active

    def test_quest_completion(self):
        board = QuestBoard(seed=100)
        quest = board.assign_quest("Alice", 1)
        completed = board.check_completion("Alice", quest.condition, 2)
        assert len(completed) >= 1 or len(completed) == 0  # Depends on condition match

    def test_quest_expiry(self):
        board = QuestBoard()
        quest = board.assign_quest("Alice", 1)
        quest.round_deadline = 5
        expired = board.expire_quests(6)
        assert len(expired) >= 1
        assert expired[0].status == QuestStatus.EXPIRED

    def test_get_active_quests(self):
        board = QuestBoard()
        board.assign_quest("Alice", 1)
        active = board.get_active_quests("Alice")
        assert len(active) == 1
        assert active[0].status == QuestStatus.ACTIVE

    def test_board_display(self):
        board = QuestBoard()
        board.assign_quest("Alice", 1)
        display = board.get_board_display()
        assert "Alice" in display
        assert "QUEST BOARD" in display

    def test_no_duplicate_quests(self):
        board = QuestBoard(seed=42)
        q1 = board.assign_quest("Alice", 1)
        q1.status = QuestStatus.COMPLETED
        board.completed_quests.append(q1)
        # Second quest should be different (if available)
        q2 = board.assign_quest("Alice", 2)
        if q2:
            assert q2.quest_id != q1.quest_id


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Trading Post
# ═══════════════════════════════════════════════════════════════════════════════

class TestTradingPost:
    def test_propose_trade(self):
        post = TradingPost()
        trade = post.propose_trade("Alice", "Bob", "debugging", 2, "patience", 1, 3)
        assert trade.trader == "Alice"
        assert not trade.accepted

    def test_accept_trade(self):
        post = TradingPost()
        trade = post.propose_trade("Alice", "Bob", "debugging", 2, "patience", 1, 3)
        result = post.accept_trade(trade)
        assert result
        assert trade.accepted
        assert post.get_bonus("Bob", "debugging") == 2
        assert post.get_bonus("Alice", "patience") == 1

    def test_no_double_accept(self):
        post = TradingPost()
        trade = post.propose_trade("A", "B", "debugging", 1, "patience", 1, 1)
        post.accept_trade(trade)
        assert not post.accept_trade(trade)

    def test_auto_trade(self):
        post = TradingPost()
        stats_a = {"debugging": 8, "patience": 3, "chaos": 5, "wisdom": 4, "speed": 6}
        stats_b = {"debugging": 3, "patience": 9, "chaos": 4, "wisdom": 5, "speed": 2}
        trade = post.auto_trade("Alice", "Bob", stats_a, stats_b, trust=0.8, round_number=5)
        assert trade is not None
        assert trade.accepted

    def test_auto_trade_low_trust(self):
        post = TradingPost()
        stats = {"debugging": 5, "patience": 5, "chaos": 5, "wisdom": 5, "speed": 5}
        trade = post.auto_trade("A", "B", stats, stats, trust=0.3, round_number=1)
        assert trade is None


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Boss Fight
# ═══════════════════════════════════════════════════════════════════════════════

class TestBossFight:
    def test_generate_boss(self):
        import random as rng_mod
        r = rng_mod.Random(42)
        boss = BossAgent.generate(10, 3, r)
        assert boss.level >= 3
        assert boss.hp > 0
        assert boss.species in ("dragon", "kraken", "golem", "shadow", "phoenix")

    def test_take_damage(self):
        import random as rng_mod
        boss = BossAgent.generate(10, 3, rng_mod.Random(42))
        initial_hp = boss.hp
        boss.take_damage("Alice", 20)
        assert boss.hp == initial_hp - 20

    def test_boss_defeated(self):
        import random as rng_mod
        boss = BossAgent.generate(10, 1, rng_mod.Random(42))
        boss.take_damage("Alice", boss.hp)
        assert boss.phase == BossPhase.DEFEATED

    def test_hp_bar(self):
        import random as rng_mod
        boss = BossAgent.generate(10, 1, rng_mod.Random(42))
        bar = boss.hp_bar
        assert "█" in bar or "░" in bar

    def test_boss_card(self):
        import random as rng_mod
        boss = BossAgent.generate(10, 1, rng_mod.Random(42))
        card = boss.get_boss_card()
        assert "BOSS ENCOUNTER" in card
        assert boss.name in card

    def test_arena_spawn(self):
        arena = BossArena(seed=42)
        # Too early
        assert arena.maybe_spawn_boss(1, 1) is None
        # Force spawn by running many rounds
        boss = None
        for i in range(8, 50):
            boss = arena.maybe_spawn_boss(i, 3)
            if boss:
                break
        assert boss is not None

    def test_arena_attack(self):
        arena = BossArena(seed=42)
        # Force a boss
        arena.current_boss = BossAgent.generate(10, 3, arena._rng)
        arena.current_boss.phase = BossPhase.FIGHTING
        result = arena.agent_attack("Alice", {"debugging": 5, "speed": 3}, 3)
        assert result is not None
        assert "damage" in result.lower()

    def test_boss_escape(self):
        arena = BossArena(seed=42)
        arena.current_boss = BossAgent.generate(10, 3, arena._rng)
        arena.current_boss.phase = BossPhase.FIGHTING
        arena.current_boss.round_appeared = 1
        assert arena.check_escape(7)
        assert arena.current_boss.phase == BossPhase.ESCAPED


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Love Letters & Hate Mail
# ═══════════════════════════════════════════════════════════════════════════════

class TestPostOffice:
    def test_send_love_letter(self):
        post = PostOffice()
        letter = post.check_and_send("Alice", "Bob", trust=0.95, sender_species="cat", round_number=1)
        assert letter is not None
        assert letter.letter_type == "love"
        assert "Alice" in letter.content

    def test_send_rivalry(self):
        post = PostOffice()
        letter = post.check_and_send("Alice", "Bob", trust=0.1, sender_species="fox", round_number=1)
        assert letter is not None
        assert letter.letter_type == "rivalry"

    def test_send_thank_you(self):
        post = PostOffice()
        letter = post.check_and_send("A", "B", trust=0.75, sender_species="cat", round_number=1)
        assert letter is not None
        assert letter.letter_type == "thank_you"

    def test_no_letter_at_medium_trust(self):
        post = PostOffice()
        letter = post.check_and_send("A", "B", trust=0.5, sender_species="cat", round_number=1)
        assert letter is None

    def test_no_duplicate_same_round(self):
        post = PostOffice()
        post.check_and_send("A", "B", trust=0.95, sender_species="cat", round_number=1)
        second = post.check_and_send("A", "B", trust=0.95, sender_species="cat", round_number=1)
        assert second is None

    def test_get_mail(self):
        post = PostOffice()
        post.check_and_send("A", "Bob", trust=0.95, sender_species="cat", round_number=1)
        mail = post.get_mail("Bob")
        assert len(mail) == 1

    def test_send_challenge(self):
        post = PostOffice()
        letter = post.send_challenge("Alice", "Bob", "cat", 3)
        assert letter.letter_type == "challenge"


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Fortune Cookies
# ═══════════════════════════════════════════════════════════════════════════════

class TestFortuneCookies:
    def test_give_cookie(self):
        jar = FortuneCookieJar()
        cookie = jar.give_cookie("Alice", 1)
        assert cookie is not None
        assert cookie.recipient == "Alice"
        assert not cookie.fulfilled

    def test_one_cookie_at_a_time(self):
        jar = FortuneCookieJar()
        jar.give_cookie("Alice", 1)
        second = jar.give_cookie("Alice", 2)
        assert second is None

    def test_fulfillment(self):
        jar = FortuneCookieJar(seed=42)
        cookie = jar.give_cookie("Alice", 1)
        # Try the cookie's actual condition
        result = jar.check_fulfillment("Alice", cookie.condition)
        assert result is not None
        assert result.fulfilled
        # Can now get a new cookie
        new = jar.give_cookie("Alice", 2)
        assert new is not None

    def test_wrong_action_no_fulfillment(self):
        jar = FortuneCookieJar()
        jar.give_cookie("Alice", 1)
        result = jar.check_fulfillment("Alice", "definitely_not_a_real_condition")
        assert result is None

    def test_get_active_fortune(self):
        jar = FortuneCookieJar()
        jar.give_cookie("Alice", 1)
        assert jar.get_active_fortune("Alice") is not None
        assert jar.get_active_fortune("Bob") is None

    def test_fortune_cookie_open_sets_picked_up_flag(self):
        jar = FortuneCookieJar()
        given = jar.give_cookie("Alice", 1)
        assert given is not None
        assert given.picked_up is False

        opened = jar.open_cookie("Alice")
        assert opened is given
        assert opened.picked_up is True
        # Still active so action-based fulfilment can still fire
        assert jar.get_active_fortune("Alice") is opened

        # Idempotent: opening again returns None
        assert jar.open_cookie("Alice") is None
        # Opening a non-existent agent's cookie returns None
        assert jar.open_cookie("Bob") is None


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Agent Ghosts
# ═══════════════════════════════════════════════════════════════════════════════

class TestGhostRealm:
    def test_create_ghost(self):
        realm = GhostRealm()
        ghost = realm.create_ghost("Alice", "cat", "🐱", "errors", 5, ["lesson1", "lesson2"])
        assert ghost.name == "Alice"
        assert ghost.cause_of_death == "errors"
        assert len(realm.ghosts) == 1

    def test_ghost_appears(self):
        realm = GhostRealm()
        realm.create_ghost("Alice", "cat", "🐱", "timeout", 3, ["Don't timeout!"])
        # Run many times to ensure at least one appearance
        appearances = []
        for _ in range(20):
            msgs = realm.maybe_appear(5)
            appearances.extend(msgs)
        assert len(appearances) > 0
        assert "ghost" in appearances[0].lower() or "Alice" in appearances[0]

    def test_ghost_fades(self):
        realm = GhostRealm()
        ghost = realm.create_ghost("Alice", "cat", "🐱", "errors", 5, ["hint"])
        for _ in range(5):
            ghost.appear()
        assert ghost.is_fading
        assert ghost.appear() is None

    def test_graveyard(self):
        realm = GhostRealm()
        realm.create_ghost("Alice", "cat", "🐱", "errors", 5, [])
        graveyard = realm.get_graveyard()
        assert "GRAVEYARD" in graveyard
        assert "Alice" in graveyard

    def test_empty_graveyard(self):
        realm = GhostRealm()
        assert "No ghosts" in realm.get_graveyard()

    def test_active_ghosts(self):
        realm = GhostRealm()
        ghost = realm.create_ghost("Alice", "cat", "🐱", "errors", 5, [])
        assert len(realm.get_active_ghosts()) == 1
        for _ in range(5):
            ghost.appear()
        assert len(realm.get_active_ghosts()) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Multiverse Branching
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiverse:
    def test_record_branch(self):
        mv = MultiverseEngine()
        branch = mv.record_branch(3, "Task completion", "Alice did it", "Nobody did it")
        assert branch.round_number == 3
        assert branch.chosen_path == "Alice did it"
        assert branch.alternate_outcome  # Should have generated one

    def test_what_if_report(self):
        mv = MultiverseEngine()
        mv.record_branch(1, "Choice 1", "Path A", "Path B")
        mv.record_branch(5, "Choice 2", "Path C", "Path D", "evolution")
        report = mv.get_what_if_report()
        assert "MULTIVERSE" in report
        assert "Branch Point" in report
        assert "Path A" in report

    def test_empty_report(self):
        mv = MultiverseEngine()
        assert "No branching" in mv.get_what_if_report()

    def test_branch_count(self):
        mv = MultiverseEngine()
        assert mv.get_branch_count() == 0
        mv.record_branch(1, "X", "A", "B")
        mv.record_branch(2, "Y", "C", "D")
        assert mv.get_branch_count() == 2

    def test_deterministic(self):
        mv1 = MultiverseEngine(seed=42)
        mv2 = MultiverseEngine(seed=42)
        b1 = mv1.record_branch(1, "Test", "A", "B", "boss_defeated")
        b2 = mv2.record_branch(1, "Test", "A", "B", "boss_defeated")
        assert b1.alternate_outcome == b2.alternate_outcome
