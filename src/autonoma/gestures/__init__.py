"""Self-authored character gestures.

Instead of hand-writing every gesture as TypeScript (``GESTURES`` in
``VRMCharacter.tsx``), a character's signature moves are *composed*: an
LLM choreographer picks procedural motion families and parameters that
fit the character and the trigger (``dsl`` → ``families`` → ``expand``),
the result is validated against joint limits and velocity caps
(``validate``), and stored as an ordinary mocap clip the stage already
knows how to play (``service``). The hard-coded gestures remain the
fallback when nothing has been generated yet.
"""

from autonoma.gestures.dsl import FaceCue, GestureScore, Segment  # noqa: F401
from autonoma.gestures.expand import expand_score  # noqa: F401
from autonoma.gestures.validate import GestureValidationError, validate_clip  # noqa: F401
