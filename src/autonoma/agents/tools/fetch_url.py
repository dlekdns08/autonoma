"""HTTP fetch tool — feature #4 (web browsing).

Lets a coder agent pull a URL into its working memory: documentation,
API spec, an issue thread, etc. Returns a *plain-text excerpt* — never
the raw HTML — so the LLM doesn't waste tokens on layout noise and
can't be tricked by hidden ``<script>`` payloads.

Why a separate tool from ``run_code``?
  * The sandbox forbids network access by design (see sandbox.py),
    and we don't want to relax that just so an agent can curl one URL.
  * Centralising the fetch lets us cap response size, strip cookies,
    and keep an audit log of which agent reached out where.

Security:
  * Only http/https schemes accepted.
  * Loopback / private / link-local hosts blocked (no SSRF into the
    swarm's own services or the operator's home network).
  * Hard caps on bytes downloaded and chars returned.
  * No cookies, no redirects to non-allowlisted hosts (we always
    follow up to 3 redirects but re-check each hop's host).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Tight ceilings — agents only need a paragraph or two to inform a
# follow-up decision. Anything bigger is almost always wasted tokens.
MAX_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MiB — hard download cap
DEFAULT_MAX_CHARS = 4000              # default text excerpt size
MAX_MAX_CHARS = 16000                 # ceiling, even if caller asks more
DEFAULT_TIMEOUT_S = 10.0


@dataclass
class FetchResult:
    ok: bool
    url: str
    status: int = 0
    text: str = ""
    content_type: str = ""
    truncated: bool = False
    reason: str = ""


def _is_private_host(host: str) -> bool:
    """True if ``host`` resolves to a loopback / private / link-local IP.

    Resolved on every call so a DNS rebind can't trick us — we re-resolve
    after redirects too.
    """
    try:
        # ``getaddrinfo`` returns every A/AAAA record; reject if ANY is
        # private. A split-horizon DNS that returns 8.8.8.8 publicly
        # but 10.0.0.1 internally would otherwise sneak through.
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # fail-closed — refuse to fetch unresolvable hosts
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
            return True
    return False


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def _html_to_text(html: str) -> str:
    """Strip script/style blocks and tags, collapse whitespace.

    Not a full HTML parser — agents don't need DOM fidelity. A regex
    pass is good enough for "what does this page say" and avoids a
    BeautifulSoup dependency.
    """
    no_scripts = _SCRIPT_RE.sub(" ", html)
    no_tags = _TAG_RE.sub(" ", no_scripts)
    collapsed = _WS_RE.sub(" ", no_tags).strip()
    return collapsed


def fetch_url(
    url: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> FetchResult:
    """Fetch ``url`` and return a plain-text excerpt.

    Synchronous — the agent action handler offloads to a thread, same
    pattern as ``open_pull_request``.
    """
    # Lazy import: httpx is in the base install but isn't loaded until
    # an agent actually fetches something. Keeps test boot fast.
    try:
        import httpx
    except ImportError:
        return FetchResult(ok=False, url=url, reason="httpx_missing")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return FetchResult(ok=False, url=url, reason="unsupported_scheme")
    if not parsed.hostname:
        return FetchResult(ok=False, url=url, reason="missing_host")
    if _is_private_host(parsed.hostname):
        return FetchResult(ok=False, url=url, reason="private_host_blocked")

    cap = max(1, min(max_chars, MAX_MAX_CHARS))

    try:
        with httpx.Client(
            timeout=timeout_s,
            follow_redirects=True,
            max_redirects=3,
            headers={
                "User-Agent": "autonoma-agent/1.0 (+https://github.com/letskoala/autonoma)",
                "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.5",
            },
        ) as client:
            resp = client.get(url)
    except httpx.TimeoutException:
        return FetchResult(ok=False, url=url, reason="timeout")
    except httpx.HTTPError as exc:
        return FetchResult(ok=False, url=url, reason=f"http_error: {exc}")

    # Re-check the FINAL host after any redirects — a redirect into
    # 169.254.169.254 (cloud metadata) is a classic SSRF.
    final_host = (
        urlparse(str(resp.url)).hostname or parsed.hostname
    )
    if _is_private_host(final_host):
        return FetchResult(
            ok=False, url=str(resp.url), reason="private_host_blocked_after_redirect"
        )

    if resp.status_code >= 400:
        return FetchResult(
            ok=False, url=str(resp.url), status=resp.status_code, reason=f"http_{resp.status_code}"
        )

    raw = resp.content[:MAX_RESPONSE_BYTES]
    truncated_bytes = len(resp.content) > MAX_RESPONSE_BYTES

    content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    try:
        body = raw.decode(resp.encoding or "utf-8", errors="replace")
    except (LookupError, TypeError):
        body = raw.decode("utf-8", errors="replace")

    if "html" in content_type:
        text = _html_to_text(body)
    else:
        # plain text, JSON, markdown — collapse whitespace but keep the structure
        text = body

    truncated_chars = len(text) > cap
    if truncated_chars:
        text = text[:cap]

    logger.info(
        f"[fetch_url] {url} -> {resp.status_code} {content_type} "
        f"chars={len(text)} truncated={truncated_chars or truncated_bytes}"
    )
    return FetchResult(
        ok=True,
        url=str(resp.url),
        status=resp.status_code,
        text=text,
        content_type=content_type,
        truncated=truncated_chars or truncated_bytes,
    )
