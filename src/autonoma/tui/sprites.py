"""Kawaii character sprites and speech bubble rendering for the animated TUI.

Cute ASCII art agents with expressive animations and comic-style bubbles.
"""

from __future__ import annotations

import unicodedata
from collections import deque

from autonoma.models import AgentState, TaskStatus


# ── Kawaii Agent Sprites (multi-line ASCII art per state) ─────────────────────

SPRITES: dict[AgentState, list[str]] = {
    AgentState.IDLE: [
        "  {e}  ",
        " (^ ^) ",
        " /|  |\\ ",
        "  d  b ",
    ],
    AgentState.THINKING: [
        "  {e} ?",
        " (o.o) ",
        " /|  |\\ ",
        "  d  b ",
    ],
    AgentState.WORKING: [
        "  {e}  ",
        " (>.<) ",
        " /|  |=",
        "  d  b ",
    ],
    AgentState.TALKING: [
        "  {e}  ",
        " (^o^) ",
        " \\|  |/",
        "  d  b ",
    ],
    AgentState.MOVING: [
        "  {e}  ",
        " (^_^) ",
        " /|  |\\ ",
        "   >> ",
    ],
    AgentState.CELEBRATING: [
        "\\({e})/",
        " (^w^) ",
        "  |  | ",
        " d    b",
    ],
    AgentState.ERROR: [
        "  {e} !",
        " (x_x) ",
        " /|  |\\ ",
        "  d  b ",
    ],
    AgentState.SPAWNING: [
        " *{e}* ",
        " (@.@) ",
        " /|  |\\ ",
        "  d  b ",
    ],
}

WORK_FRAMES = [
    ["  {e}  ", " (>.<) ", " /|  |=", "  d  b "],
    ["  {e}  ", " (>_<) ", " /| |= ", "  d  b "],
    ["  {e}  ", " (>.>) ", " /|  |~", "  d  b "],
    ["  {e}  ", " (<.<) ", " /|  |=", "  d  b "],
]

CELEBRATE_FRAMES = [
    ["\\({e})/", " (^w^) ", "  |  | ", " d    b"],
    [" ({e}) ", " (^o^) ", " \\|  |/", "  d  b "],
    ["\\({e})/", " (*^*)  ", "  |  | ", " d    b"],
    [" ({e}) ", " (^v^) ", " \\|  |/", "  d  b "],
]

THINKING_FRAMES = [
    ["  {e} ?", " (o.o) ", " /|  |\\ ", "  d  b "],
    ["  {e} !", " (O.O) ", " /|  |\\ ", "  d  b "],
    ["  {e} .", " (-.o) ", " /|  |\\ ", "  d  b "],
    ["  {e} ~", " (o.-) ", " /|  |\\ ", "  d  b "],
]

TALKING_FRAMES = [
    ["  {e}  ", " (^o^) ", " \\|  |/", "  d  b "],
    ["  {e}  ", " (^O^) ", " \\|  |/", "  d  b "],
    ["  {e}  ", " (^.^) ", " \\|  |/", "  d  b "],
    ["  {e}  ", " (^o^) ", " \\|  |/", "  d  b "],
]

SPAWNING_FRAMES = [
    [" *{e}* ", " (@.@) ", " /|  |\\ ", "  d  b "],
    [" +{e}+ ", " (*.*) ", " /|  |\\ ", "  d  b "],
    [" *{e}* ", " (@o@) ", " /|  |\\ ", "  d  b "],
    [" +{e}+ ", " (*.o) ", " /|  |\\ ", "  d  b "],
]


# ── Kawaii Emoticons for status messages ─────────────────────────────────────

MOOD_EMOTES = {
    "happy": "(^w^)",
    "thinking": "(o.o)?",
    "working": "(>.<)b",
    "excited": "(*^*)!",
    "error": "(x_x)",
    "done": "(^v^)v",
    "helping": "(^o^)/",
    "waiting": "(-_-)zzZ",
    "confused": "(o_O)?",
    "proud": "(^_~)b",
}


def _display_width(s: str) -> int:
    """Calculate the display width of a string, accounting for wide chars (CJK, emoji)."""
    width = 0
    for ch in s:
        cat = unicodedata.east_asian_width(ch)
        if cat in ("W", "F"):
            width += 2
        else:
            width += 1
    return width


def get_sprite(state: AgentState, emoji: str, frame: int = 0) -> list[str]:
    """Get the sprite lines for a given state, with emoji substituted."""
    if state == AgentState.WORKING:
        frames = WORK_FRAMES
    elif state == AgentState.CELEBRATING:
        frames = CELEBRATE_FRAMES
    elif state == AgentState.THINKING:
        frames = THINKING_FRAMES
    elif state == AgentState.TALKING:
        frames = TALKING_FRAMES
    elif state == AgentState.SPAWNING:
        frames = SPAWNING_FRAMES
    else:
        return [line.format(e=emoji) for line in SPRITES.get(state, SPRITES[AgentState.IDLE])]

    idx = frame % len(frames)
    return [line.format(e=emoji) for line in frames[idx]]


# ── Kawaii Speech Bubble ──────────────────────────────────────────────────────

def render_speech_bubble(text: str, max_width: int = 30, mood: str = "") -> list[str]:
    r"""Render a kawaii comic-style speech bubble above the character.

    .~*~.~*~.~*~.~*~.~*~.~*~.
    : Hello, I'm here! (^w^) :
    '*~.~*~.~*~.~*~.~*~.~*~*'
                \
    """
    text = text.strip() if text else ""
    if not text:
        return []

    # Add mood emoticon if specified
    if mood and mood in MOOD_EMOTES:
        text = f"{text} {MOOD_EMOTES[mood]}"

    # Word wrap
    words = text.split()
    lines: list[str] = []
    current = ""
    inner_width = max_width - 4
    for word in words:
        if len(word) > inner_width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:inner_width])
            continue
        if len(current) + len(word) + 1 > inner_width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)

    if not lines:
        return []

    width = max(len(line) for line in lines) + 2
    width = max(width, 6)

    # Kawaii bubble border patterns
    top_border = "." + "~" * width + "."
    bot_border = "'" + "~" * width + "'"

    result = []
    result.append(top_border)
    for line in lines:
        result.append(": " + line.ljust(width - 2) + " :")
    result.append(bot_border)
    result.append(" " * (width // 2) + "\\")

    return result


# ── Name Tag ───────────────────────────────────────────────────────────────

def render_nametag(name: str, color: str, max_len: int = 10) -> str:
    """Render a truncated name tag with kawaii brackets."""
    truncated = name[:max_len]
    return f"[{color}]~{truncated}~[/]"


# ── Status Bar Decorations ────────────────────────────────────────────────

KAWAII_BORDERS = {
    "top": ".~*~.~*~.~*~.~*~.~*~.~*~.~*~.~*~.",
    "bottom": "'~*~'~*~'~*~'~*~'~*~'~*~'~*~'~*~'",
    "divider": "- - - - - - - - - - - -",
}

KAWAII_PROGRESS = {
    "filled": "★",
    "empty": "☆",
    "pointer": "♪",
}

KAWAII_STATUS_ICONS = {
    TaskStatus.OPEN: "☆",
    TaskStatus.ASSIGNED: "♪",
    TaskStatus.IN_PROGRESS: "♫",
    TaskStatus.REVIEW: "♥",
    TaskStatus.DONE: "★",
    TaskStatus.BLOCKED: "✖",
}

