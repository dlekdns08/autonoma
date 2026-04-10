"""Tests for agent swarm."""

import pytest

from autonoma.agents.swarm import AgentSwarm
from autonoma.models import Position


def test_swarm_init():
    swarm = AgentSwarm()
    assert "Director" in swarm.agents
    assert swarm.director is not None


def test_spawn_agent():
    swarm = AgentSwarm()
    agent = swarm.spawn_agent("Coder", "writes code", ["python"], emoji="⚡", color="green")
    assert agent.name == "Coder"
    assert "Coder" in swarm.agents
    assert agent.persona.emoji == "⚡"


def test_spawn_duplicate():
    swarm = AgentSwarm()
    a1 = swarm.spawn_agent("Coder", "writes code", ["python"])
    a2 = swarm.spawn_agent("Coder", "writes code", ["python"])
    assert a1 is a2


def test_spawn_positions():
    swarm = AgentSwarm()
    for i in range(5):
        swarm.spawn_agent(f"Agent{i}", "helper", ["general"])
    assert len(swarm.agents) == 6  # 5 + Director


def test_tick_animations():
    swarm = AgentSwarm()
    agent = swarm.spawn_agent("Mover", "moves", ["moving"])
    agent.position = Position(x=0, y=0)
    agent.target_position = Position(x=10, y=10)

    swarm._tick_animations()
    assert agent.position.x > 0
    assert agent.position.y > 0
