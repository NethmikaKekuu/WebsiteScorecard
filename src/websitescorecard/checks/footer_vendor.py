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

_SELF_BUILT = re.compile(
    r"\b(?:ict\s+(?:directorate|unit|division|branch|centre|center)"
    r"|it\s+(?:division|unit|department)"
    r"|mis\s+(?:unit|division)"
    r"|information\s+(?:technology|systems)\s+(?:unit|division|department))\b",
    re.IGNORECASE,
)

_FOOTER_ATTR = re.compile(r"footer|bottom|copyright|credit", re.IGNORECASE)

_DOMAIN_NAME_MAP: dict[str, str] = {
    "gic.gov.lk": "ICTA (GIC)",
    "icta.lk": "ICTA",
    "slts.lk": "SLT",
}

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

_CMS_NOISE: frozenset[str] = frozenset({
    "joomla",
    "wordpress",
    "drupal",
    "wix",
    "squarespace",
    "gnu general public license",
    "bootstrap",
    "php",
})

_NAV_WORDS: frozenset[str] = frozenset({
    "top", "about", "home", "back", "next", "prev",
    "previous", "more", "read", "click", "here", "visit",
})

_BLOCKED_DOMAINS: frozenset[str] = frozenset({
    "gnu.org",
    "opensource.org",
    "w3.org",
    "creativecommons.org",
})

_FOOTER_FRACTION = 0.20
_MAX_CHARS = 6_000

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WebsiteScorecard/1.0)",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SCRAPE_URL_COLUMN = "Scrape_URL"


class FooterVendorCheck:
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

    def run(self, url: str, scrape_url: str | None = None) -> CheckResult:
        try:
            parsed = parse_url(url)
        except ValueError as exc:
            return CheckResult(status="unreachable", error=str(exc))

        site_domain = parsed.hostname

        fetch_targets: list[str] = []
        if scrape_url and scrape_url.strip():
            fetch_targets.append(scrape_url.strip())
        fetch_targets.append(url)

        for target in fetch_targets:
            html = self._fetch(target)
            if html is None:
                continue

            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()

            footer_el = _find_footer_element(soup)
            vendors = _extract_vendors(footer_el, soup, site_domain=site_domain)

            if vendors:
                combined = " & ".join(vendors)
                if _SELF_BUILT.search(combined):
                    return CheckResult(status="self_built", error=combined)
                return CheckResult(status="found", error=combined)

        any_reachable = any(self._fetch(t) is not None for t in fetch_targets)
        if not any_reachable:
            return CheckResult(status="unreachable", error="Failed to fetch page")

        return CheckResult(status="unknown", error=None)

    def _fetch(self, url: str) -> str | None:
        for candidate in _candidate_urls(url):
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

    if not vendors:
        body = soup.find("body")
        if body:
            full = body.get_text(separator=" ", strip=True)
            start = max(0, int(len(full) * (1 - _FOOTER_FRACTION)))
            vendors = _vendors_from_text(full[start:][:_MAX_CHARS])

    return vendors


def _resolve_domain_name(domain: str, site_domain: str) -> str | None:
    domain = domain.lstrip("www.")
    if domain in site_domain or site_domain in domain:
        return None
    if domain in _BLOCKED_DOMAINS:
        return None
    if domain in _DOMAIN_NAME_MAP:
        return _DOMAIN_NAME_MAP[domain]
    return domain


def _link_name(a_tag: "Tag", site_domain: str) -> str | None:
    text = a_tag.get_text(strip=True)
    if text and not _is_noise(text):
        return text
    href = a_tag.get("href", "")
    if href:
        try:
            domain = urlparse(href).netloc
            return _resolve_domain_name(domain, site_domain)
        except Exception:
            pass
    return None


def _vendors_from_links(container: "Tag", site_domain: str) -> list[str]:
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
    vendors: list[str] = []
    html_str = str(container)
    copyright_re = re.compile(r"©.*?(?=©|$)", re.IGNORECASE | re.DOTALL)
    for block_m in copyright_re.finditer(html_str):
        block = block_m.group(0)[:600]
        mini = BeautifulSoup(block, "html.parser")
        for a in mini.find_all("a"):
            href = a.get("href", "")
            if not href:
                continue
            try:
                domain = urlparse(href).netloc
            except Exception:
                continue
            name = _resolve_domain_name(domain, site_domain)
            if name and not _is_noise(name) and name not in vendors:
                vendors.append(name)
    return vendors


def _vendors_from_text(text: str) -> list[str]:
    vendors: list[str] = []
    for pattern in _TEXT_PATTERNS:
        for m in pattern.finditer(text):
            candidate = m.group(1).strip().rstrip(".,;:|")
            candidate = re.split(r"\s{2,}|\|", candidate)[0].strip()
            candidate = re.sub(r"[\s.]+[\d.]+$", "", candidate).strip()
            candidate = candidate.rstrip(".,;:|").strip()
            if candidate and not _is_noise(candidate) and candidate not in vendors:
                vendors.append(candidate)
    return vendors


def _is_noise(s: str) -> bool:
    lower = s.lower().strip()
    if len(lower) < 3:
        return True
    if lower in _NAV_WORDS:
        return True
    if any(cms in lower for cms in _CMS_NOISE):
        return True
    if lower in _BLOCKED_DOMAINS:
        return True
    return any(noise in lower for noise in _NOISE)