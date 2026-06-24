"""Footer vendor/developer credit check.

Extraction strategy
-------------------
1. Fetch the page (HTTPS first, HTTP fallback).
2. Find the footer region — <footer> tag, or any element whose id/class
   contains "footer"/"bottom"/"copyright"/"credit", or last 20% of <body>.
3. PRIMARY: scan footer HTML for <a> tags near credit trigger phrases.
   - If <a> has visible text  → use that as the vendor name.
   - If <a> has only an image → extract the domain from href as vendor name.
4. FALLBACK: extended regex against plain footer text for un-hyperlinked names.
5. COPYRIGHT FALLBACK: if the © line links to an external domain that is NOT
   the institution's own domain, treat the link text / href domain as vendor.
6. Self-built detection: ICT Directorate / IT Unit etc. → status "self_built".
7. Multiple vendors joined with " & ".

Result statuses
---------------
``found``       – external vendor name(s) in footer_vendor_error column.
``self_built``  – built by the institution's own IT unit.
``unknown``     – page loaded but no credit found.
``unreachable`` – page could not be fetched.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from websitescorecard.checks.base import CheckResult
from websitescorecard.url_utils import parse_url

_MISSING_DEPS: list[str] = []

try:
    import httpx
except ImportError:
    _MISSING_DEPS.append("httpx")

try:
    from bs4 import BeautifulSoup, Tag
except ImportError:
    _MISSING_DEPS.append("beautifulsoup4")

# ---------------------------------------------------------------------------
# Credit trigger phrases
# ---------------------------------------------------------------------------

_CREDIT_TRIGGERS = re.compile(
    r"(?:"
    r"designed\s*(?:&amp;|&|and)?\s*developed\s+by"
    r"|developed\s+(?:in\s+association\s+with|by)"
    r"|concept[,\s]+design\s*(?:&amp;|&|and)?\s*development\s+by"
    r"|designed\s+by"
    r"|developed\s+by"
    r"|built\s+by"
    r"|created\s+by"
    r"|maintained\s+by"
    r"|managed\s+by"
    r"|powered\s+by"
    r"|hosted\s+by"
    r"|website\s+by"
    r"|solution\s+by:?"
    r")",
    re.IGNORECASE,
)

# Plain-text fallback (capture group 1 = vendor name).
_TEXT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:designed\s*(?:&|and)?\s*developed|developed|designed|built"
        r"|created|maintained|managed|powered|hosted|website)"
        r"\s+(?:in\s+association\s+with\s+|by\s+)"
        r"([A-Za-z0-9][A-Za-z0-9 &()\-\.]{1,60})",
        re.IGNORECASE,
    ),
    re.compile(
        r"concept[,\s]+design\s*(?:&|and)?\s*development\s+by\s+"
        r"([A-Za-z0-9][A-Za-z0-9 &()\-\.]{1,60})",
        re.IGNORECASE,
    ),
    re.compile(
        r"solution\s+by:?\s*([A-Za-z0-9][A-Za-z0-9 &()\-\.]{1,60})",
        re.IGNORECASE,
    ),
]

# Self-built: vendor is the institution's own IT unit.
_SELF_BUILT = re.compile(
    r"\b(?:ict\s+(?:directorate|unit|division|branch|centre|center)"
    r"|it\s+(?:division|unit|department)"
    r"|mis\s+(?:unit|division)"
    r"|information\s+(?:technology|systems)\s+(?:unit|division|department))\b",
    re.IGNORECASE,
)

# Footer-like id/class substrings.
_FOOTER_ATTR = re.compile(r"footer|bottom|copyright|credit", re.IGNORECASE)

# Noise phrases — not vendor names.
_NOISE: frozenset[str] = frozenset({
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
    "privacy policy",
    "terms",
    "contact",
})

_FOOTER_FRACTION = 0.20
_MAX_CHARS = 6_000

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WebsiteScorecard/1.0)",
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

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        footer_el = _find_footer_element(soup)
        vendors = _extract_vendors(footer_el, soup, site_domain=parsed.hostname)

        if not vendors:
            return CheckResult(status="unknown", error=None)

        combined = " & ".join(vendors)
        if _SELF_BUILT.search(combined):
            return CheckResult(status="self_built", error=combined)

        return CheckResult(status="found", error=combined)

    def _fetch(self, hostname: str, port: int, original_url: str) -> str | None:
        for candidate in _candidate_urls(original_url):
            try:
                with httpx.Client(
                    timeout=self.timeout,
                    headers=_HEADERS,
                    follow_redirects=True,
                    verify=False,  # noqa: S501
                ) as client:
                    r = client.get(candidate)
                    if r.status_code < 400:
                        return r.text
            except (httpx.RequestError, httpx.HTTPStatusError):
                continue
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate_urls(url: str) -> list[str]:
    raw = url.strip()
    if "://" not in raw:
        return [f"https://{raw}", f"http://{raw}"]
    if raw.startswith("https://"):
        return [raw, raw.replace("https://", "http://", 1)]
    return [raw, raw.replace("http://", "https://", 1)]


def _find_footer_element(soup: "BeautifulSoup") -> "Tag | None":
    el = soup.find("footer")
    if el:
        return el
    for el in soup.find_all(True):
        el_id = el.get("id", "")
        el_class = " ".join(el.get("class", []))
        if _FOOTER_ATTR.search(el_id) or _FOOTER_ATTR.search(el_class):
            return el
    return None


def _extract_vendors(
    footer_el: "Tag | None",
    soup: "BeautifulSoup",
    site_domain: str,
) -> list[str]:
    vendors: list[str] = []

    if footer_el:
        vendors = _vendors_from_links(footer_el, site_domain)
        if not vendors:
            text = footer_el.get_text(separator=" ", strip=True)[:_MAX_CHARS]
            vendors = _vendors_from_text(text)
        if not vendors:
            vendors = _vendors_from_copyright_links(footer_el, site_domain)

    # Body fallback
    if not vendors:
        body = soup.find("body")
        if body:
            full = body.get_text(separator=" ", strip=True)
            start = max(0, int(len(full) * (1 - _FOOTER_FRACTION)))
            vendors = _vendors_from_text(full[start:][:_MAX_CHARS])

    return vendors


def _link_name(a_tag: "Tag", site_domain: str) -> str | None:
    """
    Extract a vendor name from an <a> tag.
    - Prefers visible link text.
    - Falls back to the href domain if the link only wraps an image.
    Returns None if the result is noise or points back to the site itself.
    """
    text = a_tag.get_text(strip=True)

    if text and not _is_noise(text):
        return text

    # Image-only link — use the href domain as the name.
    href = a_tag.get("href", "")
    if href:
        try:
            domain = urlparse(href).netloc.lstrip("www.")
            # Skip if it's the site's own domain or clearly internal.
            if domain and site_domain not in domain and domain not in site_domain:
                return domain
        except Exception:
            pass

    return None


def _vendors_from_links(container: "Tag", site_domain: str) -> list[str]:
    """Find <a> tags that follow a credit trigger phrase in the HTML."""
    vendors: list[str] = []
    html_str = str(container)

    for m in _CREDIT_TRIGGERS.finditer(html_str):
        snippet = html_str[m.end(): m.end() + 400]
        mini = BeautifulSoup(snippet, "html.parser")
        for a in mini.find_all("a"):
            name = _link_name(a, site_domain)
            if name and name not in vendors:
                vendors.append(name)

    return vendors


def _vendors_from_copyright_links(container: "Tag", site_domain: str) -> list[str]:
    """
    Last resort: find © lines that contain an external <a> tag.
    e.g. "Copyright © 2026 <a href='https://onzdev.com'>Ones and Zeros</a>"
    Only fires when the linked domain is NOT the site's own domain.
    """
    vendors: list[str] = []
    html_str = str(container)

    # Find blocks containing a copyright symbol.
    copyright_re = re.compile(r"©.*?(?=©|$)", re.IGNORECASE | re.DOTALL)
    for block_m in copyright_re.finditer(html_str):
        block = block_m.group(0)[:600]
        mini = BeautifulSoup(block, "html.parser")
        for a in mini.find_all("a"):
            href = a.get("href", "")
            if not href:
                continue
            try:
                domain = urlparse(href).netloc.lstrip("www.")
            except Exception:
                continue
            # Skip internal / same-domain links
            if not domain or site_domain in domain or domain in site_domain:
                continue
            name = _link_name(a, site_domain)
            if name and not _is_noise(name) and name not in vendors:
                vendors.append(name)

    return vendors


def _vendors_from_text(text: str) -> list[str]:
    vendors: list[str] = []
    for pattern in _TEXT_PATTERNS:
        for m in pattern.finditer(text):
            candidate = m.group(1).strip().rstrip(".,;:|")
            candidate = re.split(r"\s{2,}|\|", candidate)[0].strip()
            if candidate and not _is_noise(candidate) and candidate not in vendors:
                vendors.append(candidate)
    return vendors


def _is_noise(s: str) -> bool:
    lower = s.lower().strip()
    if len(lower) < 3:
        return True
    return any(noise in lower for noise in _NOISE)