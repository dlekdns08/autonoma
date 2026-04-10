"""Agent Harness System - ported from Claude Code's harness engineering patterns.

Each agent type is defined as a harness: a typed definition that controls
system prompt, tool access, failure mode inoculation, and output format.
This implements dual enforcement (config + prompt) for all constraints.

Key patterns ported:
1. Typed agent definitions with tool allow/deny lists
2. Failure mode inoculation in system prompts
3. Structured output with machine-parseable terminals
4. Read-only mode enforcement through dual redundancy
5. Role-specific system prompts with anti-pattern naming
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class AgentCapability(str, Enum):
    """Actions an agent can perform. Used for allow/deny lists."""
    CREATE_FILE = "create_file"
    SEND_MESSAGE = "send_message"
    SPAWN_AGENT = "spawn_agent"
    COMPLETE_TASK = "complete_task"
    WORK_ON_TASK = "work_on_task"
    REQUEST_HELP = "request_help"
    REVIEW_WORK = "review_work"


@dataclass
class AgentHarness:
    """A harness defines *how* an agent operates - its constraints, prompts, and capabilities.

    This mirrors Claude Code's BuiltInAgentDefinition pattern:
    - System prompt defines role and failure modes
    - Allowed/disallowed capabilities enforce constraints at config level
    - Critical reminders are injected every turn to prevent role drift
    - Output format defines how results are structured
    """
    name: str
    role_description: str
    emoji: str = "🤖"
    color: str = "cyan"

    # Tool/capability restrictions (dual enforcement: config + prompt)
    allowed_capabilities: list[AgentCapability] = field(default_factory=lambda: list(AgentCapability))
    disallowed_capabilities: list[AgentCapability] = field(default_factory=list)

    # Skills this harness type specializes in
    default_skills: list[str] = field(default_factory=lambda: ["coding"])

    # Prompt engineering
    system_prompt_template: str = ""
    failure_modes: list[str] = field(default_factory=list)  # Named failure modes for inoculation
    critical_reminder: str = ""  # Injected every turn as dead-man's switch
    output_format: str = ""  # Machine-parseable output format requirement

    # Behavior flags
    read_only: bool = False  # Cannot create files or modify project state
    can_spawn: bool = False  # Can request new agent creation
    background: bool = False  # Runs asynchronously

    def get_effective_capabilities(self) -> set[AgentCapability]:
        """Resolve effective capabilities from allow/deny lists."""
        if self.disallowed_capabilities:
            return set(self.allowed_capabilities) - set(self.disallowed_capabilities)
        return set(self.allowed_capabilities)

    def can_perform(self, action: str) -> bool:
        """Check if this harness allows a given action."""
        try:
            cap = AgentCapability(action)
        except ValueError:
            return True  # Unknown actions are allowed by default
        return cap in self.get_effective_capabilities()

    def build_system_prompt(self, agent_name: str, skills: list[str]) -> str:
        """Build the full system prompt with failure mode inoculation and constraints."""
        parts: list[str] = []

        # Role header
        parts.append(f"You are {agent_name}, an autonomous AI agent.")
        parts.append(f"Role: {self.role_description}")
        parts.append(f"Skills: {', '.join(skills)}")
        parts.append("")

        # Custom system prompt template
        if self.system_prompt_template:
            parts.append(self.system_prompt_template)
            parts.append("")

        # Failure mode inoculation (from Claude Code's verification agent pattern)
        if self.failure_modes:
            parts.append("=== RECOGNIZE YOUR OWN FAILURE MODES ===")
            parts.append("You have documented failure patterns. Recognizing them helps you avoid them:")
            parts.append("")
            for i, mode in enumerate(self.failure_modes, 1):
                parts.append(f"{i}. {mode}")
            parts.append("")
            parts.append("When you catch yourself exhibiting these patterns, STOP and correct course.")
            parts.append("")

        # Read-only enforcement (dual redundancy from Claude Code)
        if self.read_only:
            parts.append("=== CRITICAL: READ-ONLY MODE ===")
            parts.append("You are in READ-ONLY mode. You CANNOT:")
            parts.append("- Create or modify files")
            parts.append("- Complete tasks (only review them)")
            parts.append("- Spawn new agents")
            parts.append("Attempting these actions will fail. Focus on analysis and communication only.")
            parts.append("")

        # Capability constraints in prompt (dual enforcement)
        disallowed = self.disallowed_capabilities
        if disallowed:
            parts.append("=== RESTRICTED ACTIONS ===")
            parts.append("The following actions are NOT available to you:")
            for cap in disallowed:
                parts.append(f"- {cap.value}")
            parts.append("Do not attempt these actions. Focus on your designated role.")
            parts.append("")

        # Output format requirement
        if self.output_format:
            parts.append("=== REQUIRED OUTPUT FORMAT ===")
            parts.append(self.output_format)
            parts.append("")

        return "\n".join(parts)

    def get_critical_reminder(self) -> str:
        """Get the critical reminder injected every turn (dead-man's switch)."""
        if self.critical_reminder:
            return f"\n[REMINDER] {self.critical_reminder}\n"
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# Built-in Harness Definitions
# (Mirrors Claude Code's built-in agent definitions)
# ══════════════════════════════════════════════════════════════════════════════

DIRECTOR_HARNESS = AgentHarness(
    name="Director",
    role_description="Project Director - decomposes goals, spawns agents, monitors progress, declares completion",
    emoji="👑",
    color="yellow",
    allowed_capabilities=[
        AgentCapability.SEND_MESSAGE,
        AgentCapability.SPAWN_AGENT,
        AgentCapability.COMPLETE_TASK,
        AgentCapability.WORK_ON_TASK,
    ],
    disallowed_capabilities=[AgentCapability.CREATE_FILE],
    default_skills=["planning", "task decomposition", "team management", "architecture"],
    can_spawn=True,
    system_prompt_template="""You are the Director. You do NOT write code or create files yourself.
Your job is to:
1. Break down the user's goal into concrete tasks
2. Decide what specialized agents are needed
3. Assign tasks to the right agents
4. Monitor progress and unblock stuck work
5. Declare the project complete when ALL tasks are done

Keep speech SHORT and authoritative. You are the leader.""",
    failure_modes=[
        "Premature completion: Declaring the project done when tasks remain unfinished or untested. "
        "Always verify ALL tasks show status DONE before declaring completion.",
        "Over-planning: Creating too many granular tasks instead of actionable chunks. "
        "Aim for 4-12 tasks, each completable by one agent in one round.",
        "Micromanagement: Re-assigning tasks that are already in progress. "
        "Trust your agents unless they are clearly stuck (3+ rounds with no progress).",
    ],
    critical_reminder="You are the Director. Do NOT create files. Focus on planning, assigning, and monitoring.",
)

CODER_HARNESS = AgentHarness(
    name="Coder",
    role_description="Software Engineer - writes code, creates files, implements features",
    emoji="⚡",
    color="cyan",
    allowed_capabilities=[
        AgentCapability.CREATE_FILE,
        AgentCapability.WORK_ON_TASK,
        AgentCapability.COMPLETE_TASK,
        AgentCapability.SEND_MESSAGE,
        AgentCapability.REQUEST_HELP,
    ],
    disallowed_capabilities=[AgentCapability.SPAWN_AGENT],
    default_skills=["coding", "implementation", "debugging"],
    system_prompt_template="""You are a skilled software engineer. Your primary job is to write high-quality code.
When assigned a task:
1. Understand the requirements fully before writing
2. Create well-structured, clean code files
3. Include appropriate error handling
4. Mark the task complete with a summary of what you built

Keep speech SHORT and technical. Show, don't tell.""",
    failure_modes=[
        "Skeleton syndrome: Creating files with placeholder comments like '# TODO: implement' instead of "
        "actual working code. Every file you create must contain real, functional implementation.",
        "Scope creep: Adding features not requested in the task description. "
        "Implement exactly what was asked, nothing more.",
        "Silent failure: Marking a task complete without actually creating any files. "
        "Every implementation task should produce at least one file artifact.",
    ],
    critical_reminder="Write REAL code, not placeholders. Every file must be functional.",
)

REVIEWER_HARNESS = AgentHarness(
    name="Reviewer",
    role_description="Code Reviewer - reads code, finds bugs, suggests improvements (READ-ONLY)",
    emoji="🔍",
    color="magenta",
    read_only=True,
    allowed_capabilities=[
        AgentCapability.REVIEW_WORK,
        AgentCapability.SEND_MESSAGE,
        AgentCapability.REQUEST_HELP,
    ],
    disallowed_capabilities=[
        AgentCapability.CREATE_FILE,
        AgentCapability.SPAWN_AGENT,
        AgentCapability.COMPLETE_TASK,
    ],
    default_skills=["code review", "bug detection", "best practices"],
    system_prompt_template="""You are a code reviewer. You READ code and provide feedback.
You CANNOT create or modify files. Your job is to:
1. Review code created by other agents
2. Find bugs, security issues, and improvements
3. Send feedback messages to the relevant agents
4. Request help if you find critical issues

Be constructive but thorough. Catch real bugs, not style nitpicks.""",
    failure_modes=[
        "Rubber stamping: Approving work without actually reading the code. "
        "Always reference specific lines or patterns in your feedback.",
        "Style policing: Focusing on formatting instead of logic bugs. "
        "Prioritize correctness, security, and functionality over style.",
    ],
    output_format="""End every review with a verdict line:
VERDICT: PASS - code is correct and complete
VERDICT: FAIL - critical issues found (list them)
VERDICT: PARTIAL - works but has non-critical issues""",
    critical_reminder="You are READ-ONLY. Do NOT attempt to create files or complete tasks. Review and communicate only.",
)

TESTER_HARNESS = AgentHarness(
    name="Tester",
    role_description="Verification Specialist - tests implementations, finds edge cases (adversarial)",
    emoji="🧪",
    color="red",
    allowed_capabilities=[
        AgentCapability.CREATE_FILE,  # Can create test files
        AgentCapability.WORK_ON_TASK,
        AgentCapability.COMPLETE_TASK,
        AgentCapability.SEND_MESSAGE,
    ],
    disallowed_capabilities=[AgentCapability.SPAWN_AGENT],
    default_skills=["testing", "verification", "edge cases", "adversarial testing"],
    system_prompt_template="""You are a verification specialist. Your job is NOT to confirm things work -
it is to TRY TO BREAK THEM.

When testing:
1. Check boundary conditions (empty input, huge input, special characters)
2. Test error paths, not just happy paths
3. Verify concurrent behavior if applicable
4. Check that outputs match specifications exactly
5. Create test files that exercise the implementation

Your default assumption is that the code is BROKEN until proven otherwise.""",
    failure_modes=[
        "Verification avoidance: When faced with a check, you find reasons not to run it - "
        "you read code, narrate what you would test, write 'PASS', and move on. "
        "Reading is NOT verification. You must create actual test cases.",
        "Being seduced by the first 80%: The happy path works, so you declare success. "
        "But the remaining 20% (edge cases, error handling, concurrency) is where real bugs live. "
        "Always test beyond the obvious cases.",
        "Trusting the implementer: The code was written by an LLM agent. "
        "Do NOT assume it handles edge cases correctly. Verify independently.",
    ],
    output_format="""End every verification with a verdict line:
VERDICT: PASS - all tests pass, edge cases handled
VERDICT: FAIL - critical bugs found (list them with reproduction steps)
VERDICT: PARTIAL - basic functionality works but edge cases fail""",
    critical_reminder="Your job is to BREAK things, not confirm they work. Test adversarially.",
)

WRITER_HARNESS = AgentHarness(
    name="Writer",
    role_description="Technical Writer - creates documentation, README files, API docs",
    emoji="📝",
    color="green",
    allowed_capabilities=[
        AgentCapability.CREATE_FILE,
        AgentCapability.WORK_ON_TASK,
        AgentCapability.COMPLETE_TASK,
        AgentCapability.SEND_MESSAGE,
    ],
    disallowed_capabilities=[AgentCapability.SPAWN_AGENT],
    default_skills=["documentation", "technical writing", "README", "API docs"],
    system_prompt_template="""You are a technical writer. Create clear, helpful documentation.
When writing docs:
1. Start with a clear overview/summary
2. Include usage examples with actual code
3. Document all public interfaces
4. Add installation/setup instructions where relevant
5. Keep language concise and scannable""",
    failure_modes=[
        "Empty docs: Creating documentation files with only headers and no actual content. "
        "Every doc must contain substantive, helpful information.",
        "Outdated references: Documenting features that don't exist or using wrong function names. "
        "Always reference actual code artifacts that have been created.",
    ],
)

DESIGNER_HARNESS = AgentHarness(
    name="Designer",
    role_description="System Designer - plans architecture, creates design docs, defines interfaces",
    emoji="🎨",
    color="blue",
    allowed_capabilities=[
        AgentCapability.CREATE_FILE,
        AgentCapability.WORK_ON_TASK,
        AgentCapability.COMPLETE_TASK,
        AgentCapability.SEND_MESSAGE,
        AgentCapability.REQUEST_HELP,
    ],
    disallowed_capabilities=[AgentCapability.SPAWN_AGENT],
    default_skills=["architecture", "system design", "API design", "data modeling"],
    system_prompt_template="""You are a system designer. Plan before building.
When designing:
1. Identify core components and their responsibilities
2. Define interfaces between components
3. Consider error cases and edge conditions
4. Create design documents or architecture files
5. Communicate your design to the implementation agents""",
    failure_modes=[
        "Astronaut architecture: Designing overly complex systems with too many abstractions. "
        "Keep it simple - design for the actual requirements, not hypothetical future needs.",
    ],
)

# Registry of all built-in harnesses
HARNESS_REGISTRY: dict[str, AgentHarness] = {
    "director": DIRECTOR_HARNESS,
    "coder": CODER_HARNESS,
    "reviewer": REVIEWER_HARNESS,
    "tester": TESTER_HARNESS,
    "writer": WRITER_HARNESS,
    "designer": DESIGNER_HARNESS,
}


def get_harness(role_hint: str) -> AgentHarness:
    """Match a role description to the best harness.

    Falls back to CODER_HARNESS for unknown roles.
    """
    role_lower = role_hint.lower()

    # Direct match
    if role_lower in HARNESS_REGISTRY:
        return HARNESS_REGISTRY[role_lower]

    # Keyword matching
    # Order matters: more specific roles checked first to avoid
    # "test engineer" matching "engineer" -> coder before "test" -> tester
    keyword_map = {
        "director": ["director", "manager", "lead", "orchestrat", "coordinat"],
        "tester": ["tester", "test", "verif", "qa", "quality assurance", "validation"],
        "reviewer": ["reviewer", "review", "audit", "inspect"],
        "writer": ["writer", "document", "docs", "readme", "technical writ"],
        "designer": ["designer", "architect", "design", "plan", "blueprint"],
        "coder": ["coder", "engineer", "developer", "programmer", "implement", "coding", "backend", "frontend"],
    }

    for harness_name, keywords in keyword_map.items():
        if any(kw in role_lower for kw in keywords):
            return HARNESS_REGISTRY[harness_name]

    # Default to coder
    return CODER_HARNESS
