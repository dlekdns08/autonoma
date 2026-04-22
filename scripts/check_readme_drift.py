#!/usr/bin/env python3
"""Flag drift between README.md and README.ko.md.

We don't compare heading TEXT because translations legitimately differ.
We DO compare heading STRUCTURE — the sequence of heading levels
(`##` vs `###`) and the total count. That's enough to catch the
pathological case: someone adds a new `## Foo` section to README.md
without also adding a matching `## 푸` to README.ko.md, and the Korean
readme silently falls out of sync.

Exit codes:
  0 — structures match
  1 — drift detected (diff printed to stdout)

Run directly or wire into CI: ``python scripts/check_readme_drift.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def heading_levels(path: Path) -> list[int]:
    """Return the heading level (1–6) for each ATX heading in order.

    Skips headings inside fenced code blocks (```), which sometimes
    contain ``#`` characters that are not real headings.
    """
    levels: list[int] = []
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,6})\s+\S", line)
        if m:
            levels.append(len(m.group(1)))
    return levels


def main() -> int:
    en_path = ROOT / "README.md"
    ko_path = ROOT / "README.ko.md"
    if not en_path.exists() or not ko_path.exists():
        print(f"error: both {en_path.name} and {ko_path.name} must exist")
        return 2

    en = heading_levels(en_path)
    ko = heading_levels(ko_path)
    if en == ko:
        print(f"ok: {len(en)} headings match across both READMEs")
        return 0

    # Walk both sequences together so the reader can see exactly where
    # they start to disagree. Typical pattern: an English-only addition
    # leaves the tail of the Korean list offset by one.
    print("DRIFT: heading structure differs")
    print(f"  README.md    : {len(en)} headings, levels={en}")
    print(f"  README.ko.md : {len(ko)} headings, levels={ko}")
    max_len = max(len(en), len(ko))
    print()
    print("  idx | en | ko")
    for i in range(max_len):
        a = en[i] if i < len(en) else "—"
        b = ko[i] if i < len(ko) else "—"
        mark = "  " if a == b else " *"
        print(f"  {i:>3} | {a!s:>2} | {b!s:>2}{mark}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
