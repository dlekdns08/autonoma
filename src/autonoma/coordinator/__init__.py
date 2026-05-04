"""Swarm-vs-swarm matchmaking coordinator (feature #1, 2026-05).

Pairs two Autonoma instances on the same goal, scores them by KPIs
(rounds used, tasks done, file count), and ranks them via ELO. The
local battle scaffold in ``routers/swarm_battle.py`` runs the contest
inside one process; this coordinator runs across instances.
"""
