"""Footer vendor/developer credit check.

Scrapes the footer of a webpage and attempts to identify who built it —
typically a web agency or technology vendor credited in small print.

Extraction strategy
-------------------
1. Fetch the page over HTTPS (falls back to HTTP).
2. Parse the first ``<footer>`` element; if none exists, fall back to the
   last 20 % of the raw ``<body>`` text where footer-like content usually
   lives on older gov sites.
3. Run a ranked set of regex patterns against that text:
   - Explicit credit phrases  ("Designed by", "Developed by", …)
   - "Powered by <Name>"
   - Copyright line with a company name
4. Return the first match as the vendor name, or "unknown" if nothing fires.

Result statuses
---------------
``found``       – a vendor name was extracted (stored in ``footer_vendor``).
``unknown``     – page loaded but no credit pattern matched.
``unreachable`` – the page could not be fetched (network / HTTP error).
``error``       – unexpected exception during parsing.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from websitescorecard.checks.base import CheckResult
from websitescorecard.url_utils import parse_url

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Optional dependencies – httpx + BeautifulSoup are not part of the base
# install, so we import lazily and surface a clear error if missing.
# ---------------------------------------------------------------------------

_MISSING_DEPS: list[str] = []

try:
    import httpx
except ImportError:
    _MISSING_DEPS.append("httpx")

try:
    from bs4 import BeautifulSoup
except ImportError:
    _MISSING_DEPS.append("beautifulsoup4")

# ---------------------------------------------------------------------------
# Credit-line patterns (tried in order; first match wins)
# ---------------------------------------------------------------------------

# Capture group 1 must contain the vendor name.
_CREDIT_PATTERNS: list[re.Pattern[str]] = [
    # "Designed / Developed / Built / Created / Maintained by <Name>"
    re.compile(
        r"(?:designed|developed|built|created|maintained|managed)\s+by\s+([A-Za-z0-9 &()\-\.]{2,60})",
        re.IGNORECASE,
    ),
    # "Powered by <Name>"
    re.compile(
        r"powered\s+by\s+([A-Za-z0-9 &()\-\.]{2,60})",
        re.IGNORECASE,
    ),
    # "A <Name> website" / "A <Name> product"
    re.compile(
        r"\ba\s+([A-Za-z0-9 &()\-\.]{2,40})\s+(?:website|product|solution|platform)\b",
        re.IGNORECASE,
    ),
    # Copyright line: © 2024 <CompanyName>  (skip generic gov phrases)
    re.compile(
        r"©\s*\d{4}(?:\s*[-–]\s*\d{4})?\s+([A-Za-z][A-Za-z0-9 &()\-\.]{2,60})",
        re.IGNORECASE,
    ),
]

# Noise phrases that look like credits but are not vendor names.
_NOISE_PHRASES: frozenset[str] = frozenset(
    {
        "government of sri lanka",
        "all rights reserved",
        "the government",
        "sri lanka",
        "republic of",
        "ministry",
        "department",
        "best viewed",
        "this website",
        "official website",
        "last updated",
        "site map",
    }
)

# Fraction of body text considered "footer region" when no <footer> tag found.
_FOOTER_FRACTION = 0.20

# How many characters of footer text to scan (keeps regex fast).
_MAX_FOOTER_CHARS = 4_000

# HTTP headers that make the request look like a real browser.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; WebsiteScorecard/1.0; "
        "+https://github.com/your-org/websitescorecard)"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Public check class
# ---------------------------------------------------------------------------


class FooterVendorCheck:
    """Identify the vendor/developer credited in a website's footer."""

    name = "footer_vendor"
    column = "footer_vendor"
    error_column = "footer_vendor_error"

    def __init__(self, timeout: float = 15.0) -> None:
        if _MISSING_DEPS:
            raise RuntimeError(
                f"FooterVendorCheck requires: {', '.join(_MISSING_DEPS)}. "
                f"Install with: pip install {' '.join(_MISSING_DEPS)}"
            )
        self.timeout = timeout

    def run(self, url: str) -> CheckResult:
        try:
            parsed = parse_url(url)
        except ValueError as exc:
            return CheckResult(status="unreachable", error=str(exc))

        html = self._fetch(parsed.hostname, parsed.port, url)
        if html is None:
            return CheckResult(status="unreachable", error="Failed to fetch page")

        footer_text = _extract_footer_text(html)
        vendor = _find_vendor(footer_text)

        if vendor:
            return CheckResult(status="found", error=vendor)  # vendor stored in error_column
        return CheckResult(status="unknown", error=None)

    # ------------------------------------------------------------------

    def _fetch(self, hostname: str, port: int, original_url: str) -> str | None:
        """Try HTTPS then HTTP; return raw HTML or None."""
        # Build candidate URLs – prefer the original as-is, then try scheme swap.
        candidates = _candidate_urls(original_url, hostname, port)

        for candidate in candidates:
            try:
                with httpx.Client(
                    timeout=self.timeout,
                    headers=_HEADERS,
                    follow_redirects=True,
                    verify=False,  # noqa: S501 – SSL validity is checked separately
                ) as client:
                    response = client.get(candidate)
                    if response.status_code < 400:
                        return response.text
            except (httpx.RequestError, httpx.HTTPStatusError):
                continue

        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate_urls(original_url: str, hostname: str, port: int) -> list[str]:
    """Return URLs to try, starting with the most likely."""
    raw = original_url.strip()
    if "://" not in raw:
        return [f"https://{raw}", f"http://{raw}"]

    # Already has a scheme – also try the opposite.
    if raw.startswith("https://"):
        return [raw, raw.replace("https://", "http://", 1)]
    return [raw, raw.replace("http://", "https://", 1)]


def _extract_footer_text(html: str) -> str:
    """Return condensed text from the footer region of the page."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove script/style noise first.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    footer = soup.find("footer")
    if footer:
        return footer.get_text(separator=" ", strip=True)[:_MAX_FOOTER_CHARS]

    # Fallback: bottom fraction of body text.
    body = soup.find("body")
    if body:
        text = body.get_text(separator=" ", strip=True)
        start = max(0, int(len(text) * (1 - _FOOTER_FRACTION)))
        return text[start:][:_MAX_FOOTER_CHARS]

    return html[-_MAX_FOOTER_CHARS:]


def _find_vendor(text: str) -> str | None:
    """Run ranked patterns against footer text; return first clean match."""
    for pattern in _CREDIT_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue

        candidate = m.group(1).strip().rstrip(".,;:")
        if _is_noise(candidate):
            continue

        return candidate

    return None


def _is_noise(candidate: str) -> bool:
    lower = candidate.lower()
    if len(lower) < 3:
        return True
    return any(noise in lower for noise in _NOISE_PHRASES)