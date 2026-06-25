"""Tests for FooterVendorCheck."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

bs4 = pytest.importorskip("bs4")
httpx = pytest.importorskip("httpx")

from bs4 import BeautifulSoup

import websitescorecard.checks.footer_vendor as fv
from websitescorecard.checks.footer_vendor import FooterVendorCheck, SCRAPE_URL_COLUMN

class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """responses: url -> (status_code, html) or an Exception to raise."""

    def __init__(self, responses: dict[str, object], calls: list[str], **kwargs) -> None:
        self._responses = responses
        self._calls = calls

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def get(self, url: str) -> _FakeResponse:
        self._calls.append(url)
        outcome = self._responses.get(url)
        if outcome is None:
            raise httpx.RequestError(f"no fake route configured for {url}")
        if isinstance(outcome, BaseException):
            raise outcome
        status_code, text = outcome
        return _FakeResponse(status_code, text)


def _install_fake_httpx(monkeypatch: pytest.MonkeyPatch, responses: dict[str, object]) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        fv,
        "httpx",
        SimpleNamespace(
            Client=lambda **kw: _FakeClient(responses, calls, **kw),
            RequestError=httpx.RequestError,
            HTTPStatusError=httpx.HTTPStatusError,
        ),
    )
    return calls

@pytest.mark.parametrize(
    "url, expected",
    [
        ("example.com", ["https://example.com", "http://example.com"]),
        ("https://example.com", ["https://example.com", "http://example.com"]),
        ("http://example.com", ["http://example.com", "https://example.com"]),
    ],
)
def test_candidate_urls(url: str, expected: list[str]):
    assert fv._candidate_urls(url) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ab", True),  # too short
        ("Back", True),  # nav word
        ("Built with WordPress", True),  # CMS noise
        ("All Rights Reserved", True),  # noise phrase
        ("Acme Solutions", False),
        ("vendorhost.net", False),
    ],
)
def test_is_noise(text: str, expected: bool):
    assert fv._is_noise(text) is expected

def test_resolve_domain_name_returns_none_for_self_domain():
    assert fv._resolve_domain_name("sub.example.gov.lk", "example.gov.lk") is None


def test_resolve_domain_name_maps_known_domain_to_friendly_name():
    assert fv._resolve_domain_name("icta.lk", "example.gov.lk") == "ICTA"


def test_resolve_domain_name_returns_raw_domain_when_unmapped():
    assert fv._resolve_domain_name("vendorhost.net", "example.gov.lk") == "vendorhost.net"


def test_resolve_domain_name_skips_blocked_domains(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fv, "_BLOCKED_DOMAINS", frozenset({"blocked.example"}))
    assert fv._resolve_domain_name("blocked.example", "example.gov.lk") is None

def test_vendors_from_text_extracts_name_after_trigger_phrase():
    assert fv._vendors_from_text("Developed by Globex Technologies Pvt Ltd.") == [
        "Globex Technologies Pvt Ltd"
    ]

def test_vendors_from_text_dedupes_same_vendor_across_patterns():
    text = "Built by Acme Corp | Solution by: Acme Corp"
    assert fv._vendors_from_text(text) == ["Acme Corp"]

def test_vendors_from_text_filters_out_noise_candidates():
    assert fv._vendors_from_text("Designed by Privacy Policy") == []

def test_vendors_from_text_returns_empty_when_no_trigger_present():
    assert fv._vendors_from_text("Welcome to our website. Contact us.") == []

def test_vendors_from_links_uses_link_text_when_present():
    soup = BeautifulSoup(
        '<div><p>Website by <a href="https://creativecorp.io">Creative Corp</a></p></div>',
        "html.parser",
    )
    vendors = fv._vendors_from_links(soup.find("div"), site_domain="example.gov.lk")
    assert vendors == ["Creative Corp"]

def test_vendors_from_links_falls_back_to_resolved_href_domain_when_text_empty():
    soup = BeautifulSoup(
        '<div><p>Hosted by <a href="https://icta.lk"></a></p></div>', "html.parser"
    )
    vendors = fv._vendors_from_links(soup.find("div"), site_domain="example.gov.lk")
    assert vendors == ["ICTA"]


def test_vendors_from_links_ignores_self_links_and_missing_triggers():
    soup = BeautifulSoup(
        '<div><p>Hosted by <a href="https://example.gov.lk"></a></p>'
        '<p>Quick links: <a href="https://example.gov.lk/about">About</a></p></div>',
        "html.parser",
    )
    vendors = fv._vendors_from_links(soup.find("div"), site_domain="example.gov.lk")
    assert vendors == []

def test_vendors_from_copyright_links_extracts_domain_near_copyright_symbol():
    soup = BeautifulSoup(
        '<div>\u00a9 2024 Ministry of Foo. Site by '
        '<a href="https://creativecorp.io">CreativeCorp</a></div>',
        "html.parser",
    )
    vendors = fv._vendors_from_copyright_links(soup.find("div"), site_domain="example.gov.lk")
    assert vendors == ["creativecorp.io"]


def test_vendors_from_copyright_links_returns_empty_with_no_copyright_symbol():
    soup = BeautifulSoup(
        '<div>All rights reserved. <a href="https://creativecorp.io">CreativeCorp</a></div>',
        "html.parser",
    )
    vendors = fv._vendors_from_copyright_links(soup.find("div"), site_domain="example.gov.lk")
    assert vendors == []

@pytest.mark.parametrize(
    "html, found",
    [
        ("<footer>F</footer>", True),
        ('<div id="site-footer">F</div>', True),
        ('<div class="page-bottom">F</div>', True),
        ("<div>No footer here</div>", False),
    ],
)
def test_find_footer_element(html: str, found: bool):
    soup = BeautifulSoup(f"<html><body>{html}</body></html>", "html.parser")
    el = fv._find_footer_element(soup)
    assert (el is not None) is found

def test_extract_vendors_uses_footer_links_first():
    soup = BeautifulSoup(
        '<html><body><footer><p>Designed by '
        '<a href="https://acmevendor.com">Acme Vendor</a></p></footer></body></html>',
        "html.parser",
    )
    footer_el = fv._find_footer_element(soup)
    vendors = fv._extract_vendors(footer_el, soup, site_domain="example.gov.lk")
    assert vendors == ["Acme Vendor"]


def test_extract_vendors_falls_back_to_footer_text_when_no_links():
    soup = BeautifulSoup(
        "<html><body><footer><p>Maintained by ICT Unit</p></footer></body></html>",
        "html.parser",
    )
    footer_el = fv._find_footer_element(soup)
    vendors = fv._extract_vendors(footer_el, soup, site_domain="example.gov.lk")
    assert vendors == ["ICT Unit"]


def test_extract_vendors_falls_back_to_body_tail_when_footer_has_nothing():
    html = (
        "<html><body>"
        "<p>Lots of unrelated filler content sits here to pad out the page body.</p>"
        "<footer>\u00a9 2024 All rights reserved</footer>"
        "<p>Powered by TailVendor</p>"
        "</body></html>"
    )
    soup = BeautifulSoup(html, "html.parser")
    footer_el = fv._find_footer_element(soup)
    vendors = fv._extract_vendors(footer_el, soup, site_domain="example.gov.lk")
    assert vendors == ["TailVendor"]


def test_extract_vendors_returns_empty_for_blank_page():
    soup = BeautifulSoup("<html><body><p>Nothing interesting here.</p></body></html>", "html.parser")
    footer_el = fv._find_footer_element(soup)
    vendors = fv._extract_vendors(footer_el, soup, site_domain="example.gov.lk")
    assert vendors == []

def test_run_returns_found_when_vendor_link_in_footer(monkeypatch: pytest.MonkeyPatch):
    html = (
        "<html><body><footer><p>Designed and Developed by "
        '<a href="https://acmevendor.com">Acme Vendor</a></p></footer></body></html>'
    )
    calls = _install_fake_httpx(monkeypatch, {"https://example.gov.lk": (200, html)})

    result = FooterVendorCheck().run("https://example.gov.lk")

    assert result.status == "found"
    assert result.error == "Acme Vendor"
    assert calls == ["https://example.gov.lk"]


def test_run_returns_self_built_when_vendor_matches_internal_unit(monkeypatch: pytest.MonkeyPatch):
    html = "<html><body><footer><p>Maintained by ICT Unit</p></footer></body></html>"
    _install_fake_httpx(monkeypatch, {"https://example.gov.lk": (200, html)})

    result = FooterVendorCheck().run("https://example.gov.lk")

    assert result.status == "self_built"
    assert result.error == "ICT Unit"


def test_run_returns_unknown_when_page_loads_but_no_credit_found(monkeypatch: pytest.MonkeyPatch):
    html = "<html><body><footer>\u00a9 2024 All rights reserved</footer></body></html>"
    _install_fake_httpx(monkeypatch, {"https://example.gov.lk": (200, html)})

    result = FooterVendorCheck().run("https://example.gov.lk")

    assert result.status == "unknown"
    assert result.error is None


def test_run_returns_unreachable_when_every_fetch_fails(monkeypatch: pytest.MonkeyPatch):
    _install_fake_httpx(monkeypatch, {})

    result = FooterVendorCheck().run("https://example.gov.lk")

    assert result.status == "unreachable"
    assert result.error == "Failed to fetch page"


def test_run_returns_unreachable_when_url_cannot_be_parsed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        fv, "parse_url", lambda url: (_ for _ in ()).throw(ValueError(f"could not parse {url!r}"))
    )

    result = FooterVendorCheck().run("not-a-url")

    assert result.status == "unreachable"
    assert "could not parse" in result.error


def test_run_tries_http_fallback_when_https_fails(monkeypatch: pytest.MonkeyPatch):
    html = "<html><body><footer><p>Built by Fallback Co</p></footer></body></html>"
    calls = _install_fake_httpx(
        monkeypatch,
        {
            "https://example.gov.lk": httpx.RequestError("connection refused"),
            "http://example.gov.lk": (200, html),
        },
    )

    result = FooterVendorCheck().run("https://example.gov.lk")

    assert result.status == "found"
    assert result.error == "Fallback Co"
    assert calls == ["https://example.gov.lk", "http://example.gov.lk"]


def test_run_prefers_scrape_url_over_main_url(monkeypatch: pytest.MonkeyPatch):
    html = "<html><body><footer><p>Built by Scrape Vendor</p></footer></body></html>"
    calls = _install_fake_httpx(
        monkeypatch, {"https://example.gov.lk/about": (200, html)}
    )

    result = FooterVendorCheck().run(
        "https://example.gov.lk", scrape_url="https://example.gov.lk/about"
    )

    assert result.status == "found"
    assert result.error == "Scrape Vendor"
    assert calls == ["https://example.gov.lk/about"]


def test_run_falls_back_to_main_url_when_scrape_url_unreachable(monkeypatch: pytest.MonkeyPatch):
    html = "<html><body><footer><p>Built by Main Vendor</p></footer></body></html>"
    _install_fake_httpx(monkeypatch, {"https://example.gov.lk": (200, html)})

    result = FooterVendorCheck().run(
        "https://example.gov.lk", scrape_url="https://bad.example.gov.lk/about"
    )

    assert result.status == "found"
    assert result.error == "Main Vendor"


@pytest.mark.parametrize("scrape_url", ["   ", "https://example.gov.lk"])
def test_run_ignores_blank_or_duplicate_scrape_url(monkeypatch: pytest.MonkeyPatch, scrape_url: str):
    html = "<html><body><footer><p>Built by Main Vendor</p></footer></body></html>"
    calls = _install_fake_httpx(monkeypatch, {"https://example.gov.lk": (200, html)})

    result = FooterVendorCheck().run("https://example.gov.lk", scrape_url=scrape_url)

    assert result.status == "found"
    assert calls == ["https://example.gov.lk"]


def test_init_raises_when_required_dependencies_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fv, "_MISSING_DEPS", ["httpx", "beautifulsoup4"])

    with pytest.raises(RuntimeError, match="httpx"):
        FooterVendorCheck()


def test_scrape_url_column_constant_value():
    assert SCRAPE_URL_COLUMN == "Scrape_URL"