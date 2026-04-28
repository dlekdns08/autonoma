"""Agent-callable external tools.

Each module in this package exposes a pure function that an agent
action handler can invoke. Tools that talk to external services
(``git_pr``, future GitHub/Slack/...) live here so the sandbox module
stays focused on "run untrusted code safely".
"""

from autonoma.agents.tools.fetch_url import FetchResult, fetch_url
from autonoma.agents.tools.git_pr import GitPRResult, open_pull_request

__all__ = [
    "FetchResult",
    "GitPRResult",
    "fetch_url",
    "open_pull_request",
]
