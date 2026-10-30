"""Swarm evaluation harness — measure configuration changes with numbers.

Runs a fixed suite of goals headlessly under named *variants* (model
tiers, advisor, decision protocol, Director interventions, deep-work
backend …), records per-run metrics, and writes a comparison report.
Spends real API money unless ``--dry-run``; a hard ``--budget-usd`` is
required and enforced across the whole evaluation.

    uv run python -m autonoma.eval --dry-run
    uv run python -m autonoma.eval --variants baseline,advisor --repeats 2 --budget-usd 5
"""
