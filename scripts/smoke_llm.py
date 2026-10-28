"""One-round live smoke test of the LLM layer (spends a few hundred tokens).

Exercises the real request shape end to end — strict tools with eager
input streaming, ``output_config.effort``, the system-prompt cache
breakpoint, server-side refusal fallback on supported models, and a
mid-conversation ``role: system`` operator note — against the configured
provider. Run after changing ``autonoma.llm`` or the SDK pin:

    uv run python scripts/smoke_llm.py            # worker tier (default)
    uv run python scripts/smoke_llm.py --director # planning tier
"""

from __future__ import annotations

import asyncio
import sys

from autonoma.agents.actions import build_decision_tools, decision_tools_prompt
from autonoma.config import settings
from autonoma.llm import create_llm_client, llm_config_from_settings


async def main() -> int:
    cfg = llm_config_from_settings()
    director = "--director" in sys.argv
    model = cfg.model if director else cfg.resolved_worker_model()
    effort = cfg.effort if director else cfg.worker_effort
    client = create_llm_client(cfg)
    tools = build_decision_tools(["work_on_task", "send_message"])
    system = "You are Ada, an autonomous coder on a small team." + decision_tools_prompt(
        t.name for t in tools
    )
    messages = [
        {
            "role": "user",
            "content": (
                "== SITUATION ==\nOPEN TASKS: [t1] Write README (unassigned)\n"
                "Decide your next action."
            ),
        },
        {"role": "system", "content": "Operator note (advisory): the README task is urgent."},
    ]
    print(
        f"provider={cfg.provider} model={model} effort={effort} protocol={cfg.resolved_decision_protocol()}"
    )
    final = None
    async for ev in client.stream_events(
        model=model,
        max_tokens=settings.max_tokens,
        system=system,
        messages=messages,
        cache_system_prompt=True,
        effort=effort,
        tools=tools,
    ):
        if ev.kind == "tool_input":
            sys.stdout.write(ev.text)
            sys.stdout.flush()
        elif ev.kind == "final":
            final = ev.response
    print()
    assert final is not None
    print(
        f"stop_reason={final.stop_reason} refused={final.refused} tokens={final.input_tokens}/{final.output_tokens}"
    )
    for call in final.tool_calls:
        print(f"tool_call: {call.name} {call.input}")
    if not final.tool_calls and not final.refused:
        print("WARNING: no tool call returned; text:", final.text[:200])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
