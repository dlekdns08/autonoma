"""Managed Agents integration (Anthropic-hosted sessions).

Opt-in surfaces built on ``client.beta.*``:

- ``registry``   — one environment + one worker agent + one memory store
                   per character, ids persisted under ``data_dir``.
- ``deepwork``   — ``worker_backend=managed_agents``: a task runs as an
                   outcome-driven session (``user.define_outcome`` with a
                   rubric drafted from the task), files come back from
                   ``/mnt/session/outputs``.
- ``memory``     — sync a character's memoir into its memory store.
- ``dreams``     — consolidate a run's sessions into the character's
                   memory store ("the character dreams").
- ``deployments``— cron-scheduled autonomous runs.
- ``webhooks``   — verified Anthropic → Autonoma notifications.
"""

from autonoma.managed.client import is_configured  # noqa: F401
